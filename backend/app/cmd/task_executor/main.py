"""任务执行器入口：Celery worker 的纯启动器。

任务机制（celery_app 实例与 conf）住 adapters/tasking——任务模块与发送方
进程都从那里取实例，本文件只做 worker 特有的装配：sync 容器（wireup 的
Celery 集成按 task_prerun/postrun 信号自动管理任务级 scope 并在 worker
关闭时 close 容器）、指标挂钩与 prefork 单例丢弃。命令行参数见 __main__.py。
"""

import wireup.integration.celery
from celery.signals import worker_init, worker_process_init

from app.adapters.tasking import celery_app
from app.cmd.task_executor.metrics import setup_metrics
from app.core.config import AppConfig
from app.core.container import build_sync_container, reset_singleton_cache
from app.core.logging import setup_logging

# 日志先于一切（与 HTTP 入口一致）；.env 由 core/config/loader.py 在首次读取配置时加载
setup_logging()

container = build_sync_container()

# 启动即实例化 AppConfig：配置缺失/校验失败让进程起不来，而非等首个任务。
# AppConfig 为纯数据、无连接资源，prefork 子进程继承安全；Engine 等重资源由
# wireup 惰性解析——各子进程在首个任务时才各自创建，避免 fork 前占用连接池。
app_config = container.get(AppConfig)

wireup.integration.celery.setup(container, celery_app)

# Prometheus 指标：装配延迟到 worker_init 信号（仅真正运行 worker 时触发，
# prefork 前执行——env 须在 fork 前就位）。本模块会被 celery worker 入口
# 导入，导入必须无副作用（副作用都在信号回调里）。
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
