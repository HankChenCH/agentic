"""知识库文档聚合管理：上传校验、CRUD、启停与三段式删除。

文档是知识库的子资源：每个文档作用域方法先做父库归属/可见性锚定再
require_document，404 语义区分 4001（库不存在或不可见）/4004（文档不存在）。
读路径（详情/列表/文件预览）属主或公开库可见，写路径仅属主可操作。
"""

import hashlib
import io
import posixpath
from dataclasses import dataclass
from typing import BinaryIO
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
    require_owned_kb,
    require_visible_kb,
    sanitize_filename,
)

# 流式转存的单次读取块大小：消费方（copyfileobj / obstore）无参 read() 时同样被截断到该值
_UPLOAD_CHUNK_BYTES = 1024 * 1024


class _UploadTooLargeError(Exception):
    """内部信号：流式转存途中实读字节超过上限（分块谎报长度/不可 seek 的流）。"""


class _CountingDigestReader:
    """读穿透流包装：转存路径上增量累计大小并计算 sha256，超限立即中断读取。

    ``read(size)`` 无参/负参时截断为单块大小，保证任意消费方都不会整段
    读入内存；``seek``/``tell`` 委托底层流——obstore ``put`` 的输入校验
    要求 file-like 具备这两个方法（探测流长以决定是否 multipart，只实现
    ``read`` 会被拒：Unexpected input for PutInput），copyfileobj 则不用。
    obstore 探长后必先 seek 回起点再单遍顺序读取，计数/摘要不受影响。
    """

    def __init__(self, source: BinaryIO, max_bytes: int) -> None:
        self._source = source
        self._max_bytes = max_bytes
        self._digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = _UPLOAD_CHUNK_BYTES
        chunk = self._source.read(size)
        if not chunk:
            return b""
        self.size += len(chunk)
        if self.size > self._max_bytes:
            raise _UploadTooLargeError(
                f"streamed {self.size} bytes exceeds limit {self._max_bytes}"
            )
        self._digest.update(chunk)
        return chunk

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._source.seek(offset, whence)

    def tell(self) -> int:
        return self._source.tell()

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


def _probe_stream_size(stream: BinaryIO) -> int | None:
    """可 seek 的流（如 UploadFile 的 SpooledTemporaryFile）零拷贝取总长；不可 seek 返回 None。"""
    try:
        if not stream.seekable():
            return None
        position = stream.tell()
        size = stream.seek(0, io.SEEK_END)
        stream.seek(position)
        return size
    except (OSError, ValueError):
        return None


@injectable
@dataclass
class KnowledgeDocumentService:
    kb_repo: KnowledgeBaseRepository  # 父资源归属/可见性锚定（require_owned_kb / require_visible_kb）
    document_repo: KnowledgeDocumentRepository
    vector_index: KnowledgeVectorIndex
    object_store: KnowledgeObjectStore
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def create_document(
        self,
        kb_id: UUID,
        user_id: UUID,
        filename: str,
        content_type: str | None,
        stream: BinaryIO,
        name: str | None = None,
        description: str = "",
    ) -> KnowledgeDocument:
        require_owned_kb(self.kb_repo, kb_id, user_id)
        doc_id = uuid4()
        safe_name = sanitize_filename(filename, doc_id)
        # 解析流水线仅支持 PDF：文件名后缀强校验（解析侧也依赖后缀识别格式）
        if posixpath.splitext(safe_name)[1].lower() not in ALLOWED_UPLOAD_SUFFIXES:
            raise KnowledgeDocumentInvalidError(
                f"unsupported file type: '{safe_name}', allowed suffixes: {sorted(ALLOWED_UPLOAD_SUFFIXES)}"
            )
        # 可 seek 的流先零拷贝探长：空文件/超限在转存前拒绝（网络谎报长度由
        # 转存途中的计数包装兜底）
        declared_size = _probe_stream_size(stream)
        if declared_size == 0:
            raise KnowledgeDocumentInvalidError("uploaded file is empty")
        if declared_size is not None and declared_size > MAX_UPLOAD_BYTES:
            raise KnowledgeDocumentInvalidError(
                f"uploaded file size {declared_size} exceeds limit {MAX_UPLOAD_BYTES} bytes"
            )
        # doc_path 创建后不可变：后台解析/向量化流水线以此 key 从对象存储读回原始文件
        doc_path = self.object_store.document_key(kb_id, doc_id, safe_name)
        reader = _CountingDigestReader(stream, MAX_UPLOAD_BYTES)
        try:
            self.object_store.put(doc_path, reader)
        except _UploadTooLargeError:
            # 转存途中超限：清掉已写入的残缺对象再按业务错误拒绝
            self.object_store.delete_best_effort(doc_path)
            raise KnowledgeDocumentInvalidError(
                f"uploaded file size exceeds limit {MAX_UPLOAD_BYTES} bytes"
            )
        except Exception as exc:
            # 写入失败同样不留守残缺对象（残缺文件会被后台流水线读回）
            self.object_store.delete_best_effort(doc_path)
            raise InfrastructureError(f"failed to store uploaded file to object storage: {doc_path}") from exc
        if reader.size == 0:
            # 不可 seek 的空流只能转存后判空
            self.object_store.delete_best_effort(doc_path)
            raise KnowledgeDocumentInvalidError("uploaded file is empty")
        doc = KnowledgeDocument(
            id=doc_id,
            kb_id=kb_id,
            doc_path=doc_path,
            name=name or safe_name,
            description=description,
            mime_type=content_type,
            file_size=reader.size,
            checksum=reader.hexdigest(),
            status=KnowledgeStatus.PENDING,
        )
        try:
            return self.document_repo.create_document(doc)
        except Exception:
            # 行写入失败时回滚已上传对象，避免孤儿对象残留
            self.object_store.delete_best_effort(doc_path)
            raise

    def describe_document(self, kb_id: UUID, user_id: UUID, doc_id: UUID) -> KnowledgeDocument:
        require_visible_kb(self.kb_repo, kb_id, user_id)
        return require_document(self.document_repo, kb_id, doc_id)

    def read_document_file(self, kb_id: UUID, user_id: UUID, doc_id: UUID) -> tuple[KnowledgeDocument, bytes]:
        """读回原始文件字节（前端预览用）。

        不限制状态：原始文件在上传时即落对象存储（先于解析流水线），
        pending/processing/failed 同样可预览；对象缺失（如删除流程已清理）
        统一包成 InfrastructureError。
        """
        require_visible_kb(self.kb_repo, kb_id, user_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        try:
            return doc, self.object_store.read(doc.doc_path)
        except Exception as exc:
            raise InfrastructureError(f"failed to read document object: {doc.doc_path}") from exc

    def update_document(
        self, kb_id: UUID, user_id: UUID, doc_id: UUID, request: KnowledgeDocumentUpdateRequest
    ) -> KnowledgeDocument:
        require_owned_kb(self.kb_repo, kb_id, user_id)
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

    def list_documents(self, kb_id: UUID, user_id: UUID, page: int, page_size: int) -> dict:
        require_visible_kb(self.kb_repo, kb_id, user_id)
        items, total = self.document_repo.list_documents(kb_id, page, page_size)
        return page_envelope(items, total, page, page_size)

    def delete_document(self, kb_id: UUID, user_id: UUID, doc_id: UUID) -> KnowledgeDocument:
        """三段式删除，同 KnowledgeBaseService.delete_knowledge：标记 deleting → 清理对象 → 删行（含 doc_num 递减）。"""
        require_owned_kb(self.kb_repo, kb_id, user_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        if doc.status != KnowledgeStatus.DELETING:
            doc.status = KnowledgeStatus.DELETING
            doc = self.document_repo.update_document(doc)
        self.vector_index.delete_segments(kb_id, self.document_repo.list_segment_ids_by_doc(doc_id))
        # 原始文件与解析产物（assets/derived）都在文档前缀下，按前缀整体清理
        self.object_store.delete_prefix_best_effort(self.object_store.doc_prefix(kb_id, doc_id))
        self.document_repo.delete_document(kb_id, doc_id)
        return doc

    def set_document_enabled(
        self, kb_id: UUID, user_id: UUID, doc_id: UUID, enabled: bool
    ) -> KnowledgeDocument:
        require_owned_kb(self.kb_repo, kb_id, user_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        check_status_transition(doc.status, enabled, KnowledgeDocumentStatusError)
        target = KnowledgeStatus.ENABLED if enabled else KnowledgeStatus.DISABLED
        if doc.status != target:
            doc.status = target
            doc = self.document_repo.update_document(doc)
        return doc
