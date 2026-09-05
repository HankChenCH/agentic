"""知识库管理用例：库/文档/分段的管理面编排 + 摄取任务派发。

api 层唯一消费面；存在性/归属/状态校验在领域服务（4001/4004 业务异常
透出）。`.delay` 派发经 ``IngestionDispatcher`` 端口反转——application 不
import tasks，派发失败不回滚受理（文档停留 pending，retry 端点可补发），
只记录告警。
"""

import logging
from dataclasses import dataclass
from uuid import UUID

from wireup import injectable

from app.domain.knowledge import (
    DocumentIngestionService,
    IngestionDispatcher,
    KnowledgeBaseService,
    KnowledgeDocumentService,
    KnowledgeSegmentService,
)

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（非 DI 侧小件惯例）
logger = logging.getLogger(__name__)


@injectable
@dataclass
class KnowledgeAppService:
    """知识库管理用例门面。"""

    knowledge_base_service: KnowledgeBaseService
    knowledge_document_service: KnowledgeDocumentService
    segment_service: KnowledgeSegmentService
    ingestion_service: DocumentIngestionService
    dispatcher: IngestionDispatcher

    # ---------------- 知识库 ----------------

    def create_knowledge_base(self, request, user_id: UUID):
        return self.knowledge_base_service.create_knowledge(request, user_id)

    def list_knowledge_bases(self, page: int, page_size: int, user_id: UUID):
        return self.knowledge_base_service.list_knowledge(page, page_size, user_id)

    def describe_knowledge_base(self, kb_id: UUID, user_id: UUID):
        return self.knowledge_base_service.describe_knowledge(kb_id, user_id)

    def update_knowledge_base(self, kb_id: UUID, request, user_id: UUID):
        return self.knowledge_base_service.update_knowledge(kb_id, request, user_id)

    def delete_knowledge_base(self, kb_id: UUID, user_id: UUID):
        return self.knowledge_base_service.delete_knowledge(kb_id, user_id)

    def set_knowledge_base_enabled(self, kb_id: UUID, *, enabled: bool, user_id: UUID):
        return self.knowledge_base_service.set_knowledge_enabled(kb_id, enabled=enabled, user_id=user_id)

    # ---------------- 文档 ----------------

    def create_document(self, kb_id: UUID, user_id: UUID, *, filename: str,
                        content_type: str | None, stream, name: str | None, description: str):
        """上传受理：流式转存对象存储后补发处理任务（失败不回滚，留 pending）。"""
        doc = self.knowledge_document_service.create_document(
            kb_id, user_id,
            filename=filename, content_type=content_type, stream=stream,
            name=name, description=description,
        )
        self._dispatch_processing(kb_id, doc.id)
        return doc

    def retry_document(self, kb_id: UUID, user_id: UUID, doc_id: UUID):
        """补发处理任务：failed/pending 允许（如 worker 掉线导致的任务丢失）。"""
        doc = self.ingestion_service.retry_document(kb_id, user_id, doc_id)
        self._dispatch_processing(kb_id, doc_id)
        return doc

    def list_documents(self, kb_id: UUID, user_id: UUID, page: int, page_size: int):
        return self.knowledge_document_service.list_documents(kb_id, user_id, page, page_size)

    def describe_document(self, kb_id: UUID, user_id: UUID, doc_id: UUID):
        return self.knowledge_document_service.describe_document(kb_id, user_id, doc_id)

    def read_document_file(self, kb_id: UUID, user_id: UUID, doc_id: UUID):
        return self.knowledge_document_service.read_document_file(kb_id, user_id, doc_id)

    def update_document(self, kb_id: UUID, user_id: UUID, doc_id: UUID, request):
        return self.knowledge_document_service.update_document(kb_id, user_id, doc_id, request)

    def delete_document(self, kb_id: UUID, user_id: UUID, doc_id: UUID):
        return self.knowledge_document_service.delete_document(kb_id, user_id, doc_id)

    def set_document_enabled(self, kb_id: UUID, user_id: UUID, doc_id: UUID, *, enabled: bool):
        return self.knowledge_document_service.set_document_enabled(
            kb_id, user_id, doc_id, enabled=enabled
        )

    # ---------------- 分段 ----------------

    def list_segments(self, kb_id: UUID, user_id: UUID, doc_id: UUID,
                      page: int, page_size: int, *, keyword: str | None):
        return self.segment_service.list_segments(
            kb_id, user_id, doc_id, page, page_size, keyword=keyword
        )

    def create_segment(self, kb_id: UUID, user_id: UUID, doc_id: UUID, request):
        return self.segment_service.create_segment(kb_id, user_id, doc_id, request)

    def describe_segment(self, kb_id: UUID, user_id: UUID, doc_id: UUID, segment_id: UUID):
        return self.segment_service.describe_segment(kb_id, user_id, doc_id, segment_id)

    def update_segment_content(self, kb_id: UUID, user_id: UUID, doc_id: UUID, segment_id: UUID, request):
        return self.segment_service.update_segment_content(
            kb_id, user_id, doc_id, segment_id, request
        )

    def delete_segment(self, kb_id: UUID, user_id: UUID, doc_id: UUID, segment_id: UUID):
        return self.segment_service.delete_segment(kb_id, user_id, doc_id, segment_id)

    def set_segment_enabled(self, kb_id: UUID, user_id: UUID, doc_id: UUID,
                            segment_id: UUID, *, enabled: bool):
        return self.segment_service.set_segment_enabled(
            kb_id, user_id, doc_id, segment_id, enabled=enabled
        )

    def rechunk_document(self, kb_id: UUID, user_id: UUID, doc_id: UUID):
        """整篇重分段受理：稳定态文档置回 pending 并补发处理任务。"""
        doc = self.segment_service.rechunk_document(kb_id, user_id, doc_id)
        self._dispatch_processing(kb_id, doc_id)
        return doc

    # ---------------- 派发 ----------------

    def _dispatch_processing(self, kb_id: UUID, doc_id: UUID) -> None:
        try:
            self.dispatcher.dispatch_processing(kb_id, doc_id)
        except Exception:
            logger.exception("failed to dispatch process_document task for doc %s", doc_id)
