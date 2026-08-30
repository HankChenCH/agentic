"""wireup 容器工厂：多入口（HTTP / 任务执行器）共享的注入装配。

wireup 的 FastAPI 集成要求 async 容器、Celery 集成要求 sync 容器，
两种应用不能复用同一个容器实例——共享的是 injectables 注册表：
所有入口统一从本模块取容器，保证各进程内的装配一致。

注意：本模块导入 services/components 等业务包（它们反向依赖
core.config / core.logging），因此不要在会被业务包导入的模块里
import 本模块，以免循环导入。
"""

from typing import TYPE_CHECKING

import wireup

from app import agents, components, infrastructures, packages, repositories, services
from app.core.config import AppConfig
from app.core.logging import LoggerFactory

if TYPE_CHECKING:
    from wireup.ioc.container.async_container import AsyncContainer
    from wireup.ioc.container.sync_container import SyncContainer


def _injectables() -> list:
    return [
        AppConfig,
        LoggerFactory,  # core 不在扫描包列表内，显式注册
        services,
        components,
        packages,
        repositories,
        infrastructures,
        agents,
    ]


def build_async_container() -> "AsyncContainer":
    """HTTP 应用用：wireup.integration.fastapi.setup 要求 async 容器。"""
    return wireup.create_async_container(injectables=_injectables())


def build_sync_container() -> "SyncContainer":
    """任务执行器用：wireup.integration.celery.setup 要求 sync 容器。"""
    return wireup.create_sync_container(injectables=_injectables())


def reset_singleton_cache(container: "SyncContainer") -> None:
    """清空容器的单例缓存，所有单例在下次解析时重建。

    Celery prefork 加固用：worker 子进程经 fork 继承父进程内存后，丢弃
    fork 前已创建的单例——Engine / 向量库客户端等持有 TCP 连接，fork 后
    父子进程共用同一批 socket 会相互踩踏。正常情况下重资源是 lazy 的
    （父进程只解析过纯数据的 AppConfig），此处清空属防御性：即使未来有人
    在 fork 前触发了重资源创建，子进程也会丢弃重建而非共用。solo 等
    不 fork 的 pool 不触发 worker_process_init，天然不受影响。

    wireup 无公开 API，此处直接清其内部存储；升级 wireup 时需回归验证。
    """
    container._global_scope_objects.clear()
