"""知识库文档处理任务：Celery 接线（重试分类/超时/载荷）——编排全部在
application 摄取用例（IngestionAppService → DocumentIngestionService：
解析→分块→嵌入→写向量）。

载荷只传 ID（可序列化），worker 侧重新加载领域对象；处理失败时文档已由
服务置 failed（含 error_message），任务继续抛错供 worker 日志留痕。
存在性校验在服务内（require_kb/require_document）：库/文档被并发删除
时，任务日志拿到 4001/4004 语义明确的业务错误。

可靠性口径（acks_late 下消息会被 broker 重投：worker 崩溃/可见性超时）：
- 幂等由服务层 claim 门闸保证（已在跑/已完成/删除中的文档跳过，卡死的
  processing 按 stale 阈值接管），stale_before 由本任务按配置换算传入；
- 瞬时故障（基础设施抖动）按指数退避 autoretry，业务错误（4001/4004/4005/4006
  等永久失败）永不重试（dont_autoretry_for 兜底）。
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import wireup.integration.celery
from sqlalchemy.exc import SQLAlchemyError
from weaviate.exceptions import WeaviateBaseError
from wireup import Injected

from app.adapters.tasking import celery_app
from app.application import IngestionAppService
from app.core.config import AppConfig
from app.core.exceptions import BusinessError, InfrastructureError
from app.core.logging import LoggerFactory

# 瞬时（可重试）异常面：基础设施抖动。MinerU 解析器已统一抛 InfrastructureError
# （含 httpx 上传/轮询错误的包装）；其余覆盖 DB、嵌入模型（httpx）与向量库连接。
# SoftTimeLimitExceeded 刻意不在面内：确定性超时重试只是放大占用——落 failed
# 走人工 retry / 看门狗。
TRANSIENT_EXCEPTIONS: tuple[type[Exception], ...] = (
    InfrastructureError,
    SQLAlchemyError,
    TimeoutError,
    ConnectionError,
    httpx.HTTPError,
    WeaviateBaseError,
)


@celery_app.task(
    name="agentic.knowledge.process_document",
    bind=True,
    autoretry_for=TRANSIENT_EXCEPTIONS,
    dont_autoretry_for=(BusinessError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=3,
)
@wireup.integration.celery.inject
def process_document(
    self,
    kb_id: str,
    doc_id: str,
    app_service: Injected[IngestionAppService],
    logger_factory: Injected[LoggerFactory],
    app_config: Injected[AppConfig],
) -> dict:
    logger = logger_factory.get_logger(__name__)
    logger.info(
        "document %s processing started (kb %s, attempt %d)",
        doc_id,
        kb_id,
        self.request.retries + 1,
    )
    # claim 门闸的 processing 接管阈值：判死窗口 = now - stale_processing_seconds
    stale_before = datetime.now(timezone.utc) - timedelta(
        seconds=app_config.task.stale_processing_seconds
    )
    doc = app_service.process_document(UUID(kb_id), UUID(doc_id), stale_before=stale_before)
    logger.info(
        "document %s processing finished: status=%s seg_num=%s",
        doc_id,
        doc.status.value,
        doc.seg_num,
    )
    return {"doc_id": doc_id, "status": doc.status.value, "seg_num": doc.seg_num}
