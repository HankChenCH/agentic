"""看门狗任务：周期对账卡死文档（由 beat 调度，间隔见 task.yaml reap_interval_seconds）。

两层恢复与 broker 层（acks_late + visibility_timeout 重投）互补：
- processing 超时判死（执行体已死且消息未重投，如硬超时强杀 reject_on_worker_lost=false）：
  mark_reaped 计数重投，达上限置 failed + 原因（毒丸保护），走人工 retry；
- pending 超时判消息丢失（Redis 无持久化重启 / dispatch 失败）：直接补发，
  队列中残留的重复消息由 claim 门闸幂等吸收。

重投决策在 DocumentIngestionService.reap_stuck_documents（domain 层），
实际 .delay 在本任务（tasks 层，domain 禁向上 import tasks）。
"""

from datetime import datetime, timedelta, timezone

import wireup.integration.celery
from wireup import Injected

from app.cmd.task_executor.main import celery_app
from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.services import DocumentIngestionService
from app.tasks.knowledge import process_document


@celery_app.task(name="agentic.knowledge.reap_stuck_documents")
@wireup.integration.celery.inject
def reap_stuck_documents(
    ingestion_service: Injected[DocumentIngestionService],
    logger_factory: Injected[LoggerFactory],
    app_config: Injected[AppConfig],
) -> dict:
    logger = logger_factory.get_logger(__name__)
    task_cfg = app_config.task
    now = datetime.now(timezone.utc)
    result = ingestion_service.reap_stuck_documents(
        stale_processing=now - timedelta(seconds=task_cfg.stale_processing_seconds),
        stale_pending=now - timedelta(seconds=task_cfg.stale_pending_seconds),
        max_attempts=task_cfg.max_reap_attempts,
    )
    for kb_id, doc_id in result["dispatch"]:
        process_document.delay(str(kb_id), str(doc_id))
    if result["dispatch"] or result["finalized"]:
        logger.warning(
            "reap cycle: redispatched %d document(s) %s, finalized %d %s",
            len(result["dispatch"]),
            [str(doc_id) for _, doc_id in result["dispatch"]],
            len(result["finalized"]),
            [str(doc_id) for doc_id in result["finalized"]],
        )
    return {
        "dispatched": len(result["dispatch"]),
        "finalized": [str(doc_id) for doc_id in result["finalized"]],
    }
