"""HTTP 入口：FastAPI 应用工厂。

容器装配来自 app.core.container（async 容器），本文件只保留 HTTP
特有部分：路由、全局异常处理器、边缘策略中间件（CORS 白名单/限流/
请求体上限，数值见 http.yaml）。建表/迁移不在启动路径——
由 `python -m app.cmd.admin db upgrade` 负责（Alembic 管理，见
alembic.ini + migrations/）。命令行参数见 __main__.py。
"""

import re
from contextlib import asynccontextmanager
import logging
import signal

from sqlalchemy import Engine

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import wireup.integration.fastapi

from app.api.deps import require_user
from app.core.config import AppConfig, HttpConfig, MetricsConfig, load_section
from app.core.container import build_async_container
from app.core.logging import setup_logging
from app.services.orchestration.chat_orchestrator import ChatOrchestrator
from app.api.exception_handlers import register_exception_handlers
from app.api.health import router as health_router
from app.api.metrics import MetricsMiddleware, router as metrics_router
from app.api.middleware import (
    BodySizeLimitMiddleware,
    BodySizeSpec,
    RateLimitMiddleware,
    RateLimitSpec,
    RequestIDMiddleware,
)
from app.api.v1.endpoints import agent_knowledge, agentic, auth, knowledge, memory
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

    # 优雅关闭信号监听：SIGTERM/SIGINT 时先为在途对话流点亮取消标志（复用
    # 显式取消通道，流式循环在帧级检查点 ≤0.5s 收口——轮次置 CANCELED、半截
    # 消息不落库），再链回 uvicorn 自带的处理器进入正常排空。uvicorn 的信号
    # 处理器在 Server.run 的 capture_signals 内先于 lifespan 安装，故 lifespan
    # 里拿到的是它的；退出时恢复，避免重复运行 lifespan 时层层套娃。
    orchestrator = await container.get(ChatOrchestrator)
    logger = logging.getLogger(__name__)
    _uvicorn_handlers: dict[int, object] = {}

    def _handle_shutdown_signal(signum, frame):
        orchestrator.begin_shutdown()
        prev = _uvicorn_handlers.get(signum)
        if callable(prev):
            prev(signum, frame)

    for sig in (signal.SIGTERM, signal.SIGINT):
        _uvicorn_handlers[sig] = signal.signal(sig, _handle_shutdown_signal)
    try:
        yield
    finally:
        for sig, handler in _uvicorn_handlers.items():
            try:
                signal.signal(sig, handler)
            except (TypeError, ValueError):
                logger.warning("恢复信号处理器失败 signum=%s", sig, exc_info=True)

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

    # 边缘策略配置在装配期读取（CORS/限流/请求体上限需在中间件构造时注入；
    # 与 lifespan 急切解析 AppConfig 的 fail-fast 语义一致，配置错误进程起不来）
    http_config = load_section("http.yaml", HttpConfig)
    metrics_config = load_section("metrics.yaml", MetricsConfig)

    server = FastAPI(lifespan=lifespan)

    container = build_async_container()
    wireup.integration.fastapi.setup(container, server)
    _container_holder["container"] = container

    # 用户侧行程与知识库/会话/记忆端点按 router 挂 JWT 鉴权（声明式，无路径
    # 白名单）；/auth（取票入口）、/health（存活探针）保持公开。端点内经
    # Depends(require_user) 取 UserPrincipal（依赖结果每请求缓存，验签只跑
    # 一次）。knowledge/agent_knowledge 的归属与可见性校验在知识库领域服务层。
    server.include_router(agentic.router, dependencies=[Depends(require_user)])
    server.include_router(knowledge.router, dependencies=[Depends(require_user)])
    server.include_router(agent_knowledge.router, dependencies=[Depends(require_user)])
    server.include_router(memory.router, dependencies=[Depends(require_user)])
    server.include_router(auth.router)
    server.include_router(health_router)
    # /metrics 供 Prometheus 抓取：公开（与 /health 同级），enabled=False 时不挂载
    if metrics_config.enabled:
        server.include_router(metrics_router)

    # 全局异常处理器（AOP）：统一 Response 信封 + 按环境（dev/test/prod）区分响应详略
    register_exception_handlers(server)

    # 限流/请求体上限作用域：chat 前缀含 /chat/cancel；上传为 multipart 文档创建端点
    chat_paths = re.compile(r"^/agentic/chat")
    upload_paths = re.compile(r"^/knowledge/[^/]+/document$")
    post_only = frozenset({"POST"})

    # 中间件后 add 者在外层（请求链 CORS → RequestID → RateLimit → BodySize → 路由）：
    # 429/413 拒绝响应向外穿透时仍能补上 X-Request-ID 与跨域头，
    # 且限流拒绝的告警日志落在 RequestID 的日志上下文内。
    server.add_middleware(
        BodySizeLimitMiddleware,
        specs=[
            BodySizeSpec(methods=post_only, path_pattern=chat_paths, max_bytes=http_config.max_body_bytes.chat),
            BodySizeSpec(methods=post_only, path_pattern=upload_paths, max_bytes=http_config.max_body_bytes.upload),
        ],
    )
    server.add_middleware(
        RateLimitMiddleware,
        specs=[
            RateLimitSpec(
                methods=post_only,
                path_pattern=chat_paths,
                requests=http_config.rate_limit.chat.requests,
                window_seconds=http_config.rate_limit.chat.window_seconds,
            ),
            RateLimitSpec(
                methods=post_only,
                path_pattern=upload_paths,
                requests=http_config.rate_limit.upload.requests,
                window_seconds=http_config.rate_limit.upload.window_seconds,
            ),
        ],
    )
    server.add_middleware(RequestIDMiddleware)
    # 请求指标：add 在 RequestID 之后 → 外层为 Metrics，请求链 CORS → Metrics →
    # RequestID → RateLimit → BodySize → 路由；/metrics 与 /health 自身不计数
    # （豁免逻辑在中间件内），enabled=False 时连同 /metrics 端点一起关闭
    if metrics_config.enabled:
        server.add_middleware(MetricsMiddleware)

    # CORS 白名单：源来自 http.yaml（CORS_ORIGINS 逗号分隔覆盖），精确匹配不带通配；
    # 方法/方法头按实际使用面收敛（客户端当前仅发 Content-Type，X-Request-ID/Authorization 为预留）
    server.add_middleware(
        CORSMiddleware,
        allow_origins=http_config.cors.origins,
        allow_credentials=http_config.cors.allow_credentials,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "Authorization"],
    )

    return server


server = create_app()
