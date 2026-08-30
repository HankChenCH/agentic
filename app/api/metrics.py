"""Prometheus 运维监控指标：``/metrics`` 采集端点 + HTTP 请求指标中间件。

端点与 :mod:`app.api.health` 同款纯函数路由（不鉴权、不依赖容器），
Prometheus 抓取格式输出；单进程部署用默认 REGISTRY，设置了
``PROMETHEUS_MULTIPROC_DIR``（多 worker / Celery prefork 场景）时自动切换
``MultiProcessCollector`` 聚合各进程落盘的指标。

:class:`MetricsMiddleware` 为纯 ASGI 中间件（与 ``api/middleware.py`` 风格一致，
SSE 友好）：按「方法 + 路由模板」记请求数 / 时延直方图 / 在途请求 Gauge。
路由模板取自 Starlette 路由匹配而非原始 path，防止标签基数随请求参数爆炸；
匹配不到路由（404 等）落 ``unmatched``。
"""

import os
from time import monotonic

from fastapi import APIRouter, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.multiprocess import MultiProcessCollector
from starlette.routing import Match
from starlette.types import ASGIApp, Receive, Scope, Send

router = APIRouter(tags=["Metrics"])

HTTP_REQUESTS_TOTAL = Counter(
    "agentic_http_requests_total",
    "HTTP 请求总数（handler 为路由模板；404 等未匹配路由落 unmatched）。",
    ["method", "handler", "status"],
)
HTTP_REQUEST_DURATION = Histogram(
    "agentic_http_request_duration_seconds",
    "HTTP 请求处理耗时（秒）。",
    ["method", "handler"],
)
HTTP_REQUESTS_IN_PROGRESS = Gauge(
    "agentic_http_requests_in_progress",
    "当前在途 HTTP 请求数。",
    ["method", "handler"],
)

# /metrics 自身不计数（抓取流量会污染计数且 handler 固定无信息量）
_UNSCOPED_PATHS = frozenset({"/metrics", "/health"})


def _exposition() -> tuple[bytes, str]:
    registry = REGISTRY
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        registry = CollectorRegistry()
        MultiProcessCollector(registry)
    return generate_latest(registry), CONTENT_TYPE_LATEST


@router.get("/metrics")
def metrics():
    content, media_type = _exposition()
    return Response(content=content, media_type=media_type)


def _route_template(scope: Scope) -> str:
    """匹配 Starlette 路由表返回路径模板（如 ``/agentic/run/cancel``）；
    无路由上下文或未命中返回 ``unmatched``。"""
    app = scope.get("app")
    routes = getattr(app, "routes", None) if app is not None else None
    if not routes:
        return "unmatched"
    for route in routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "path", "unmatched")
    return "unmatched"


class MetricsMiddleware:
    """HTTP 请求指标中间件（计数在响应结束 / 异常路径都收敛，SSE 长流
    以流结束时刻记一次总耗时）。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path in _UNSCOPED_PATHS:
            await self.app(scope, receive, send)
            return

        status_holder = {"status": "500"}
        method = scope["method"]
        handler = _route_template(scope)
        HTTP_REQUESTS_IN_PROGRESS.labels(method=method, handler=handler).inc()
        start = monotonic()

        async def send_metrics(message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = str(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_metrics)
        finally:
            HTTP_REQUESTS_IN_PROGRESS.labels(method=method, handler=handler).dec()
            HTTP_REQUEST_DURATION.labels(method=method, handler=handler).observe(
                monotonic() - start
            )
            HTTP_REQUESTS_TOTAL.labels(
                method=method, handler=handler, status=status_holder["status"]
            ).inc()
