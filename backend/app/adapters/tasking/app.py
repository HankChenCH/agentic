"""Celery 应用实例与任务队列 conf：任务机制的单一事实源。

住 adapters/tasking 而非 cmd 入口的原因：``app.tasks`` 的任务模块在装饰器
期就需要 celery_app 实例，而发送方进程（HTTP 应用）与 worker 进程都会导入
任务模块——实例若住在 cmd/task_executor.main，tasks 层就被迫逆向依赖 cmd
入口层。此处直接无参构造 AppConfig（default_factory 即触发 YAML/env 加载，
与容器解析同一条路径），conf 在导入期装配完成；worker 特有的容器接线、
wireup 集成与信号挂钩仍归 cmd/task_executor/main（纯启动器）。

broker/backend 以驱动+key 引用声明（task.yaml → redis.yaml providers），
引用失效在导入期 fail-fast（发送方进程同样生效）。
"""

from celery import Celery

from app.core.config import AppConfig, resolve_url

app_config = AppConfig()

celery_app = Celery(
    "agentic",
    include=["app.tasks"],
)
celery_app.conf.update(
    # broker/backend 以驱动+key 引用声明（task.yaml → redis.yaml providers），
    # 引用失效在导入期 fail-fast（发送方进程导入本模块时同样生效）
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
