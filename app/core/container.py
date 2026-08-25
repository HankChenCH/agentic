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

from app import agents, components, infrastructures, repositories, services
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
