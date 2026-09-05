"""知识库摄取用例：任务体（process_document）与看门狗对账（reap）的编排。

tasks 层唯一消费面（任务函数只做 Celery 接线：重试分类/超时/载荷序列化）；
摄取管线在 ``DocumentIngestionService``（解析→分块→嵌入→写向量，claim
门闸幂等）。补发派发经 ``IngestionDispatcher`` 端口——application 不
import tasks。
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from wireup import injectable

from app.domain.knowledge import DocumentIngestionService, IngestionDispatcher
from app.models.domain.knowledge import KnowledgeDocument


@injectable
@dataclass
class IngestionAppService:
    """摄取用例门面。"""

    ingestion_service: DocumentIngestionService
    dispatcher: IngestionDispatcher

    def process_document(self, kb_id: UUID, doc_id: UUID, *, stale_before: datetime) -> KnowledgeDocument:
        """处理单文档（幂等：pending/failed 抢占、卡死 processing 接管、其余跳过）。"""
        return self.ingestion_service.process_document(kb_id, doc_id, stale_before=stale_before)

    def reap_stuck_documents(
        self, *, stale_processing: datetime, stale_pending: datetime, max_attempts: int,
    ) -> dict:
        """看门狗对账：产出补发/收口决策并派发补发（派发失败如实上抛，
        由 beat 周期兜底重试）。返回 ``{"dispatched", "finalized"}``。"""
        result = self.ingestion_service.reap_stuck_documents(
            stale_processing=stale_processing,
            stale_pending=stale_pending,
            max_attempts=max_attempts,
        )
        for kb_id, doc_id in result["dispatch"]:
            self.dispatcher.dispatch_processing(kb_id, doc_id)
        return {
            "dispatched": len(result["dispatch"]),
            "finalized": [str(doc_id) for doc_id in result["finalized"]],
        }
