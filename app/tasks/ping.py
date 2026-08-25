"""冒烟任务：验证 worker 进程内 wireup 注入链路可用。"""

import wireup.integration.celery
from wireup import Injected

from app.cmd.task_executor.main import celery_app
from app.core.config import AppConfig
from app.core.logging import LoggerFactory


@celery_app.task(name="agentic.ping")
@wireup.integration.celery.inject
def ping(app_config: Injected[AppConfig], logger_factory: Injected[LoggerFactory]) -> dict:
    """不触碰 DB/LLM，仅确认 AppConfig / LoggerFactory 可注入并返回。"""
    logger = logger_factory.get_logger(__name__)
    logger.info("ping 任务执行, app=%s environment=%s", app_config.name, app_config.environment)
    return {"app": app_config.name, "environment": app_config.environment}
