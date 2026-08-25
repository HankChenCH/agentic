"""任务执行器入口：Celery 应用。

容器装配来自 app.core.container（sync 容器 + wireup 的 Celery 集成，
后者按 task_prerun/postrun 信号自动管理任务级 scope 并在 worker 关闭时
close 容器）；本文件只保留任务队列特有部分：broker/backend 与任务注册。
任务本体在 app/tasks 包（每个模块自动注册，见其 __init__）；
命令行参数见 __main__.py。
"""

import wireup.integration.celery
from celery import Celery

from app.core.config import AppConfig
from app.core.container import build_sync_container
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
    broker_url=app_config.task.broker,
    result_backend=app_config.task.backend,
    # 任务载荷只需 JSON 可序列化的原始数据，禁用 pickle
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)

wireup.integration.celery.setup(container, celery_app)
