"""HTTP 入口：FastAPI 应用工厂。

容器装配来自 app.core.container（async 容器），本文件只保留 HTTP
特有部分：路由、全局异常处理器、CORS。建表/迁移不在启动路径——
由 `python -m app.cmd.admin db upgrade` 负责（Alembic 管理，见
alembic.ini + migrations/）。命令行参数见 __main__.py。
"""

from contextlib import asynccontextmanager
import logging

from sqlalchemy import Engine

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import wireup.integration.fastapi

from app.core.config import AppConfig
from app.core.container import build_async_container
from app.core.logging import setup_logging
from app.api.exception_handlers import register_exception_handlers
from app.api.v1.endpoints import agent_knowledge, agentic, knowledge, memory
from app.infrastructures.vector import VectorStoreFactory

# .env 由 core/config/loader.py 在首次读取配置时加载（AGENTIC_ENV_FILE 可指定路径）

# wireup 容器在 setup() 之后才能注入 FastAPI，故用闭包延迟访问 lifespan 内的容器。
_container_holder: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动即实例化 AppConfig：环境变量缺失/校验失败让进程起不来，而非等首个请求。
    # 不再建表：表结构由 Alembic 迁移管理（admin db upgrade），库未迁移会在
    # 首个请求时报错——部署流程需先执行迁移。
    container = _container_holder["container"]
    await container.get(AppConfig)
    yield

    # 优雅关闭：释放 Engine 连接池与向量库客户端（与 Celery 侧的
    # container.close() 对齐）。best-effort：清理失败只记日志，不阻断退出。
    # Engine 此前未被用过时，这里会惰性构建一次再 dispose（无连接可释放）。
    logger = logging.getLogger(__name__)
    try:
        (await container.get(Engine)).dispose()
    except Exception:
        logger.warning("db engine dispose failed on shutdown", exc_info=True)
    try:
        (await container.get(VectorStoreFactory)).close()
    except Exception:
        logger.warning("vector store clients close failed on shutdown", exc_info=True)


def create_app():
    # 日志先于一切：后续异常处理器、各模块 logger 都依赖这套配置
    setup_logging()

    server = FastAPI(lifespan=lifespan)

    container = build_async_container()
    wireup.integration.fastapi.setup(container, server)
    _container_holder["container"] = container

    server.include_router(agentic.router)
    server.include_router(knowledge.router)
    server.include_router(agent_knowledge.router)
    server.include_router(memory.router)

    # 全局异常处理器（AOP）：统一 Response 信封 + 按环境（dev/test/prod）区分响应详略
    register_exception_handlers(server)

    origins = [
        "*",
    ]

    server.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return server


server = create_app()
