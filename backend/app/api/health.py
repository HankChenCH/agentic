"""健康检查端点：GET /health 就绪探针（k8s/lb/监控用）。

升级为 readiness：除进程能响应外，还逐一探测真实运行时依赖——
db（连接池 SELECT 1）与 redis（默认 entry 的 PING，信号存储/Celery
broker 的连接事实同源 redis.yaml）。任一依赖不可用返回 503 + 各组件
状态明细，全绿返回 200 信封。依赖探测逐项 try/except 收敛为 up/down，
异常只记日志不穿透（探针必须自洽返回，不落入全局异常处理器的 500）。

探针本身仍不鉴权（与 /auth 同级的公开面）；探测超时沿用各基建 entry
自身的 socket 超时配置（redis 默认 5s）。
"""

import logging

import redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text
from wireup import Injected

from app.models.schema.response.biz_response import Response

router = APIRouter(tags=["Health"])

# 模块级非 DI 代码走 stdlib logger（app 域由 InterceptHandler 桥接进统一 sinks）
_std_logger = logging.getLogger(__name__)


def _check_db(engine: Engine) -> None:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


def _check_redis(client: redis.Redis) -> None:
    # 与信号存储/Celery broker 同源（redis.yaml default entry，经
    # infrastructures 的 create_default_redis 注入）；lazy 客户端的首个命令
    # 即建连，PING 失败如实上抛
    client.ping()


def probe(engine: Engine, redis_client: redis.Redis) -> tuple[str, dict[str, str]]:
    """逐项探测依赖，返回 (整体状态, 各组件状态)。任一 down 则整体 unavailable。"""
    components: dict[str, str] = {}
    for name, check in (
        ("db", lambda: _check_db(engine)),
        ("redis", lambda: _check_redis(redis_client)),
    ):
        try:
            check()
            components[name] = "up"
        except Exception:
            _std_logger.warning("readiness probe failed on %s", name, exc_info=True)
            components[name] = "down"
    status = "ok" if all(v == "up" for v in components.values()) else "unavailable"
    return status, components


@router.get("/health")
def health(
    engine: Injected[Engine],
    redis_client: Injected[redis.Redis],
):
    status, components = probe(engine, redis_client)
    if status == "ok":
        return Response.success({"status": "ok", "components": components}).to_dict()
    return JSONResponse(
        status_code=503,
        content=Response.fail(
            503,
            "服务依赖不可用",
            response={"status": "unavailable", "components": components},
        ).to_dict(),
    )
