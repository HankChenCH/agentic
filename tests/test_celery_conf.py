"""Celery 可靠性配置冒烟：conf 接线、任务 autoretry 面、beat 调度项。

导入 app.cmd.task_executor.main 会构建 sync 容器（无外连——引擎/客户端惰性），
CI 以哑值密钥跑同款导入路径（见 .github/workflows/ci.yml）。
"""

from app.cmd.task_executor.main import app_config, celery_app
from app.core.exceptions import BusinessError, InfrastructureError
from app.tasks.knowledge import TRANSIENT_EXCEPTIONS, process_document
from app.tasks.maintenance import reap_stuck_documents

REAP_TASK_NAME = "agentic.knowledge.reap_stuck_documents"


def test_worker_reliability_conf_wiring():
    conf = celery_app.conf
    assert conf.task_acks_late is True
    assert conf.task_reject_on_worker_lost is False
    assert conf.worker_prefetch_multiplier == 1
    # broker 与 result backend 的重投窗口必须一致（Celery Redis 后端要求）
    assert conf.broker_transport_options["visibility_timeout"] == app_config.task.visibility_timeout
    assert conf.result_backend_transport_options["visibility_timeout"] == app_config.task.visibility_timeout
    # 窗口必须盖过最长任务时长，否则活任务会被误重投
    assert app_config.task.visibility_timeout > app_config.task.time_limit
    assert app_config.task.stale_processing_seconds > app_config.task.time_limit


def test_process_document_autoretry_surface():
    assert process_document.max_retries == 3
    # 瞬时面以基础设施错误打底；业务异常（永久失败）永不重试
    assert InfrastructureError in TRANSIENT_EXCEPTIONS
    assert process_document.dont_autoretry_for == (BusinessError,)
    assert not any(issubclass(BusinessError, exc) for exc in process_document.autoretry_for)


def test_reap_task_scheduled_by_beat():
    # reap_interval_seconds=0 时 main.py 不挂调度项；随仓配置默认开启
    if app_config.task.reap_interval_seconds > 0:
        entry = celery_app.conf.beat_schedule["agentic-knowledge-reap-stuck-documents"]
        assert entry["task"] == reap_stuck_documents.name == REAP_TASK_NAME
        assert entry["schedule"] == app_config.task.reap_interval_seconds
    else:
        assert REAP_TASK_NAME not in celery_app.conf.beat_schedule
