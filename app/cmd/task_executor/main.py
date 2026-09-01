"""任务执行器入口：Celery 应用。

容器装配来自 app.core.container（sync 容器 + wireup 的 Celery 集成，
后者按 task_prerun/postrun 信号自动管理任务级 scope 并在 worker 关闭时
close 容器）；本文件只保留任务队列特有部分：broker/backend 与任务注册。
任务本体在 app/tasks 包（每个模块自动注册，见其 __init__）；
命令行参数见 __main__.py。
"""

import wireup.integration.celery
from celery import Celery
from celery.signals import worker_init, worker_process_init

from app.cmd.task_executor.metrics import setup_metrics
from app.core.config import AppConfig, resolve_url
from app.core.container import build_sync_container, reset_singleton_cache
from app.core.logging import setup_logging

# 日志先于一切（与 HTTP 入口一致）；.env 由 core/config/loader.py 在首次读取配置时加载
setup_logging()

container = build_sync_container()

# 启动即实例化 AppConfig：配置缺失/校验失败让进程起不来，而非等首个任务。
# AppConfig 为纯数据、无连接资源，prefork 子进程继承安全；Engine 等重资源由
# wireup 惰性解析——各子进程在首个任务时才各自创建，避免 fork 前占用连接池。
app_config = container.get(AppConfig)

celery_app = Celery(
    "agentic",
    include=["app.tasks"],
)
celery_app.conf.update(
    # broker/backend 以驱动+key引用声明（task.yaml → redis.yaml providers），
    # 引用失效在此启动期 fail-fast（发送方进程导入本模块时同样生效）
    broker_url=resolve_url(app_config.task.broker, redis=app_config.redis),
    result_backend=resolve_url(app_config.task.backend, redis=app_config.redis),
    # 任务载荷只需 JSON 可序列化的原始数据，禁用 pickle
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 任务超时：软超时抛 SoftTimeLimitExceeded 可收尾，硬超时强杀；防止长任务占死 worker
    task_soft_time_limit=app_config.task.soft_time_limit,
    task_time_limit=app_config.task.time_limit,
    # 晚 ack：任务执行完才确认，worker 崩溃/断连时未 ack 消息由 broker 重投
    # （幂等由任务侧 claim 门闸保证）；reject_on_worker_lost=False 让硬超时强杀
    # 不进入「强杀→重投」毒丸循环，进程内死亡落 DB 状态交看门狗有界恢复
    task_acks_late=app_config.task.acks_late,
    task_reject_on_worker_lost=app_config.task.reject_on_worker_lost,
    worker_prefetch_multiplier=app_config.task.prefetch_multiplier,
    # broker 连接套用 redis entry 的 socket 超时，避免 Redis 抖动时 worker 收发挂死；
    # visibility_timeout 是未 ack 消息的重投窗口（须 > time_limit），worker 整进程死亡
    # （kill -9/断电）后消息在此窗口后被重投——broker 与 result backend 两处须一致
    # （Celery Redis 后端的文档化要求）
    broker_transport_options={
        "socket_timeout": app_config.redis.providers[app_config.task.broker.provider].socket_timeout,
        "socket_connect_timeout": app_config.redis.providers[app_config.task.broker.provider].socket_connect_timeout,
        "visibility_timeout": app_config.task.visibility_timeout,
    },
    result_backend_transport_options={
        "visibility_timeout": app_config.task.visibility_timeout,
    },
)

# 看门狗：beat 周期对账卡死文档（processing 超时判死→计数重投；pending 消息丢失→补发）。
# 任务名与 app/tasks/maintenance.py 的注册名一致（字符串字面量，beat 不导入任务模块）；
# reap_interval_seconds=0 关闭（不挂调度项）。
if app_config.task.reap_interval_seconds > 0:
    celery_app.conf.beat_schedule = {
        "agentic-knowledge-reap-stuck-documents": {
            "task": "agentic.knowledge.reap_stuck_documents",
            "schedule": app_config.task.reap_interval_seconds,
        },
    }

wireup.integration.celery.setup(container, celery_app)

# Prometheus 指标：装配延迟到 worker_init 信号（仅真正运行 worker 时触发，
# prefork 前执行——env 须在 fork 前就位）。本模块会被 app.tasks 的任务模块
# 导入（celery_app），而该链条在 HTTP 应用等进程中同样触发，故导入必须无副作用。
worker_init.connect(
    lambda **_: setup_metrics(app_config.metrics.enabled, app_config.metrics.worker_metrics_port),
    weak=False,
)


@worker_process_init.connect
def _discard_inherited_singletons(**_kwargs):
    """prefork 子进程启动即丢弃继承的单例缓存（含潜在的重资源连接）。

    见 core.container.reset_singleton_cache；仅 prefork 池触发（fork 才有
    继承问题），solo 池不触发也不需要。
    """
    reset_singleton_cache(container)
