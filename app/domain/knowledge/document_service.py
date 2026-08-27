"""知识库文档聚合管理：上传校验、CRUD、启停与三段式删除。

文档是知识库的子资源：每个文档作用域方法先 require_kb 再
require_document，404 语义区分 4001（库不存在）/4004（文档不存在）。
"""

import hashlib
import posixpath
from dataclasses import dataclass
from uuid import UUID, uuid4

from wireup import injectable

from .vector_index import KnowledgeVectorIndex
from app.core.exceptions import InfrastructureError
from app.core.logging import LoggerFactory
from app.exceptions import (
    KnowledgeDocumentInvalidError,
    KnowledgeDocumentStatusError,
)
from app.models.domain.knowledge import KnowledgeDocument, KnowledgeStatus
from app.models.schema.request.knowledge import KnowledgeDocumentUpdateRequest
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from .object_store import KnowledgeObjectStore
from .support import (
    ALLOWED_UPLOAD_SUFFIXES,
    MAX_UPLOAD_BYTES,
    check_status_transition,
    page_envelope,
    require_document,
    require_kb,
    sanitize_filename,
)


@injectable
@dataclass
class KnowledgeDocumentService:
    kb_repo: KnowledgeBaseRepository  # 父资源存在性锚定（require_kb）
    document_repo: KnowledgeDocumentRepository
    vector_index: KnowledgeVectorIndex
    object_store: KnowledgeObjectStore
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def create_document(
        self,
        kb_id: UUID,
        filename: str,
        content_type: str | None,
        data: bytes,
        name: str | None = None,
        description: str = "",
    ) -> KnowledgeDocument:
        require_kb(self.kb_repo, kb_id)
        if len(data) == 0:
            raise KnowledgeDocumentInvalidError("uploaded file is empty")
        if len(data) > MAX_UPLOAD_BYTES:
            raise KnowledgeDocumentInvalidError(
                f"uploaded file size {len(data)} exceeds limit {MAX_UPLOAD_BYTES} bytes"
            )
        doc_id = uuid4()
        safe_name = sanitize_filename(filename, doc_id)
        # 解析流水线仅支持 PDF：文件名后缀强校验（解析侧也依赖后缀识别格式）
        if posixpath.splitext(safe_name)[1].lower() not in ALLOWED_UPLOAD_SUFFIXES:
            raise KnowledgeDocumentInvalidError(
                f"unsupported file type: '{safe_name}', allowed suffixes: {sorted(ALLOWED_UPLOAD_SUFFIXES)}"
            )
        # doc_path 创建后不可变：后台解析/向量化流水线以此 key 从对象存储读回原始文件
        doc_path = self.object_store.document_key(kb_id, doc_id, safe_name)
        try:
            self.object_store.put(doc_path, data)
        except Exception as exc:
            raise InfrastructureError(f"failed to store uploaded file to object storage: {doc_path}") from exc
        doc = KnowledgeDocument(
            id=doc_id,
            kb_id=kb_id,
            doc_path=doc_path,
            name=name or safe_name,
            description=description,
            mime_type=content_type,
            file_size=len(data),
            checksum=hashlib.sha256(data).hexdigest(),
            status=KnowledgeStatus.PENDING,
        )
        try:
            return self.document_repo.create_document(doc)
        except Exception:
            # 行写入失败时回滚已上传对象，避免孤儿对象残留
            self.object_store.delete_best_effort(doc_path)
            raise

    def describe_document(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument:
        require_kb(self.kb_repo, kb_id)
        return require_document(self.document_repo, kb_id, doc_id)

    def read_document_file(self, kb_id: UUID, doc_id: UUID) -> tuple[KnowledgeDocument, bytes]:
        """读回原始文件字节（前端预览用）。

        不限制状态：原始文件在上传时即落对象存储（先于解析流水线），
        pending/processing/failed 同样可预览；对象缺失（如删除流程已清理）
        统一包成 InfrastructureError。
        """
        require_kb(self.kb_repo, kb_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        try:
            return doc, self.object_store.read(doc.doc_path)
        except Exception as exc:
            raise InfrastructureError(f"failed to read document object: {doc.doc_path}") from exc

    def update_document(
        self, kb_id: UUID, doc_id: UUID, request: KnowledgeDocumentUpdateRequest
    ) -> KnowledgeDocument:
        require_kb(self.kb_repo, kb_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        if doc.status == KnowledgeStatus.DELETING:
            raise KnowledgeDocumentStatusError("cannot update a knowledge document being deleted")
        if request.name is not None:
            doc.name = request.name
        if request.description is not None:
            doc.description = request.description
        if request.weight is not None:
            doc.weight = request.weight
        return self.document_repo.update_document(doc)

    def list_documents(self, kb_id: UUID, page: int, page_size: int) -> dict:
        require_kb(self.kb_repo, kb_id)
        items, total = self.document_repo.list_documents(kb_id, page, page_size)
        return page_envelope(items, total, page, page_size)

    def delete_document(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument:
        """三段式删除，同 KnowledgeBaseService.delete_knowledge：标记 deleting → 清理对象 → 删行（含 doc_num 递减）。"""
        require_kb(self.kb_repo, kb_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        if doc.status != KnowledgeStatus.DELETING:
            doc.status = KnowledgeStatus.DELETING
            doc = self.document_repo.update_document(doc)
        self.vector_index.delete_segments(kb_id, self.document_repo.list_segment_ids_by_doc(doc_id))
        # 原始文件与解析产物（assets/derived）都在文档前缀下，按前缀整体清理
        self.object_store.delete_prefix_best_effort(self.object_store.doc_prefix(kb_id, doc_id))
        self.document_repo.delete_document(kb_id, doc_id)
        return doc

    def set_document_enabled(self, kb_id: UUID, doc_id: UUID, enabled: bool) -> KnowledgeDocument:
        require_kb(self.kb_repo, kb_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        check_status_transition(doc.status, enabled, KnowledgeDocumentStatusError)
        target = KnowledgeStatus.ENABLED if enabled else KnowledgeStatus.DISABLED
        if doc.status != target:
            doc.status = target
            doc = self.document_repo.update_document(doc)
        return doc
