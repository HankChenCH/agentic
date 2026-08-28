"""HTTP 入口：FastAPI 应用工厂。

容器装配来自 app.core.container（async 容器），本文件只保留 HTTP
特有部分：路由、全局异常处理器、CORS、lifespan 建表。
命令行参数见 __main__.py。
"""

from contextlib import asynccontextmanager

from sqlalchemy import Engine
from sqlmodel import SQLModel

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import wireup.integration.fastapi

from app.core.config import AppConfig
from app.core.container import build_async_container
from app.core.logging import setup_logging
from app.api.exception_handlers import register_exception_handlers
from app.api.v1.endpoints import agent_knowledge, agentic, knowledge, memory

# 确保所有 SQLModel 表模型被导入，以便 SQLModel.metadata 能收集到它们
import app.models.domain.agentic  # noqa: F401
import app.models.domain.knowledge  # noqa: F401
import app.models.domain.memory  # noqa: F401 记忆 v2 四表

# .env 由 core/config/loader.py 在首次读取配置时加载（AGENTIC_ENV_FILE 可指定路径）

# wireup 容器在 setup() 之后才能注入 FastAPI，故用闭包延迟访问 lifespan 内的容器。
_container_holder: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时建表：从容器取出单例 Engine，DDL 仅对 table=True 的模型生效
    container = _container_holder["container"]
    # 启动即实例化 AppConfig：环境变量缺失/校验失败让进程起不来，而非等首个请求
    await container.get(AppConfig)
    engine = await container.get(Engine)
    SQLModel.metadata.create_all(engine)
    yield


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
