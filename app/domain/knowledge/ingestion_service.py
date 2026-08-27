"""文档摄取流水线：解析 → 分块 → 嵌入 → 写向量 → 状态收尾。

process_document 由 Celery 任务调用（也会被未来的定时对账等后台入口
复用），因此存在性/状态校验与 HTTP 路径完全一致（require_kb /
require_document）：库被并发删除时任务日志拿到的是 4001 而非含糊的
4004，可正确追踪失败原因。
"""

import posixpath
from dataclasses import dataclass
from uuid import UUID, uuid4

from wireup import injectable

from app.core.logging import LoggerFactory
from app.exceptions import (
    KnowledgeDocumentInvalidError,
    KnowledgeDocumentNotFoundError,
    KnowledgeDocumentStatusError,
)
from .vector_index import KnowledgeVectorIndex
from app.infrastructures.document_parser import DocumentParser
from app.models.domain.knowledge import DocumentSegment, KnowledgeDocument, KnowledgeStatus
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from .document_chunker import SegmentDraft, chunk_document
from .object_store import KnowledgeObjectStore
from .support import ERROR_MESSAGE_MAX, RETRY_ALLOWED, require_document, require_kb


@injectable
@dataclass
class DocumentIngestionService:
    kb_repo: KnowledgeBaseRepository  # 父资源存在性锚定（require_kb）
    document_repo: KnowledgeDocumentRepository
    object_store: KnowledgeObjectStore
    document_parser: DocumentParser
    vector_index: KnowledgeVectorIndex
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def process_document(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument:
        """解析 → 分块 → 嵌入 → 写向量 → 状态收尾（由 Celery 任务调用）。

        任一步失败：文档置 failed 并记录 error_message（截断），任务向上抛
        供日志留痕；重试先删旧向量再整体重写分段（replace_segments），幂等。
        """
        require_kb(self.kb_repo, kb_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        if doc.status == KnowledgeStatus.DELETING:
            raise KnowledgeDocumentStatusError("cannot process a knowledge document being deleted")
        doc.status = KnowledgeStatus.PROCESSING
        doc.error_message = None
        doc = self.document_repo.update_document(doc)
        try:
            self._ingest(doc)
            self.document_repo.complete_document(doc_id)
        except Exception as exc:
            self.logger.error("document %s processing failed", doc_id, exc_info=True)
            failed = self.document_repo.get_document(kb_id, doc_id)
            if failed is not None:
                failed.status = KnowledgeStatus.FAILED
                failed.error_message = str(exc)[:ERROR_MESSAGE_MAX]
                self.document_repo.update_document(failed)
            raise
        result = self.document_repo.get_document(kb_id, doc_id)
        if result is None:
            raise KnowledgeDocumentNotFoundError(f"knowledge document not found: {doc_id}")
        return result

    def retry_document(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument:
        """重试校验：仅 failed/pending 可补发处理任务，处理中/就绪拒绝。"""
        require_kb(self.kb_repo, kb_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        if doc.status not in RETRY_ALLOWED:
            raise KnowledgeDocumentStatusError(
                f"cannot retry in status '{doc.status.value}': allowed statuses are "
                f"{', '.join(sorted(s.value for s in RETRY_ALLOWED))}"
            )
        return doc

    # ---------- 内部 ----------

    def _ingest(self, doc: KnowledgeDocument) -> None:
        data = self.object_store.read(doc.doc_path)
        # 解析侧按文件名识别格式：doc_path 尾段即上传时的安全文件名（必带 .pdf 后缀）
        parsed = self.document_parser.parse(data, posixpath.basename(doc.doc_path))
        self._store_assets(doc, parsed)
        drafts = chunk_document(parsed)
        if not drafts:
            raise KnowledgeDocumentInvalidError("document parsing produced no indexable content")
        # 先清旧向量再重写分段：segment.id 即向量对象 UUID，行与向量始终一一对应
        self.vector_index.delete_segments(doc.kb_id, self.document_repo.list_segment_ids_by_doc(doc.id))
        segments = [self._build_segment(doc, position, draft) for position, draft in enumerate(drafts)]
        self.document_repo.replace_segments(doc.id, segments)
        self.vector_index.add_segments(doc.kb_id, segments)

    def _build_segment(self, doc: KnowledgeDocument, position: int, draft: SegmentDraft) -> DocumentSegment:
        # 资产名（zip 相对路径）改写为对象存储完整 key，检索/展示侧可直接取用
        assets = [
            self.object_store.asset_key(doc.kb_id, doc.id, name)
            for name in draft.meta.get("assets", [])
        ]
        return DocumentSegment(
            id=uuid4(),
            kb_id=doc.kb_id,
            doc_id=doc.id,
            position=position,
            content=draft.content,
            word_count=draft.word_count,
            meta={**draft.meta, "assets": assets},
            status=KnowledgeStatus.PENDING,
        )

    def _store_assets(self, doc: KnowledgeDocument, parsed) -> None:
        # 资产/预览转存 best-effort：失败不阻断入库，缺图只影响展示不影响检索
        for name, blob in parsed.assets.items():
            self.object_store.put_best_effort(self.object_store.asset_key(doc.kb_id, doc.id, name), blob)
        if parsed.md_content:
            self.object_store.put_best_effort(
                self.object_store.derived_markdown_key(doc.kb_id, doc.id),
                parsed.md_content.encode("utf-8"),
            )
