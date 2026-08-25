"""知识库文档处理任务：编排全部在 DocumentIngestionService（解析→分块→嵌入→写向量）。

载荷只传 ID（可序列化），worker 侧重新加载领域对象；处理失败时文档已由
服务置 failed（含 error_message），任务继续抛错供 worker 日志留痕。
存在性校验在服务内（require_kb/require_document）：库/文档被并发删除
时，任务日志拿到 4001/4004 语义明确的业务错误。
"""

from uuid import UUID

import wireup.integration.celery
from wireup import Injected

from app.cmd.task_executor.main import celery_app
from app.core.logging import LoggerFactory
from app.services import DocumentIngestionService


@celery_app.task(name="agentic.knowledge.process_document")
@wireup.integration.celery.inject
def process_document(
    kb_id: str,
    doc_id: str,
    ingestion_service: Injected[DocumentIngestionService],
    logger_factory: Injected[LoggerFactory],
) -> dict:
    logger = logger_factory.get_logger(__name__)
    logger.info("document %s processing started (kb %s)", doc_id, kb_id)
    doc = ingestion_service.process_document(UUID(kb_id), UUID(doc_id))
    logger.info(
        "document %s processing finished: status=%s seg_num=%s",
        doc_id,
        doc.status.value,
        doc.seg_num,
    )
    return {"doc_id": doc_id, "status": doc.status.value, "seg_num": doc.seg_num}
