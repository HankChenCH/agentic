"""HTTP 边缘中间件（均为纯 ASGI，SSE 友好——不经过 BaseHTTPMiddleware
的缓冲包装；接线顺序见 ``app/cmd/http/main.py``）：

- :class:`RequestIDMiddleware` — 每个请求生成/沿用 ``X-Request-ID`` 并注入日志上下文；
- :class:`BodySizeLimitMiddleware` — 请求体字节上限：``Content-Length``
  超限不读体直接 413；传输中累计超限（分块传输/谎报长度）由 receive
  包装在读取处抛 ``HTTPException(413)``，经全局 ``http_exception_handler``
  统一为信封响应（multipart 解析器只捕 MultiPartException/OSError，
  ``Request.stream`` 不吞自定义异常，异常可穿透到 ExceptionMiddleware）。

限流不经过边缘中间件：应用内不限流，由网关层（反向代理/API 网关）
实现（原 fastapi-limiter 端点依赖方案已移除，见 README「边缘策略」）。
"""

import logging
import re
from dataclasses import dataclass
from typing import Sequence
from uuid import uuid4

from loguru import logger
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.models.schema.response.biz_response import Response

# 模块级非 DI 代码走 stdlib logger（app 域由 InterceptHandler 桥接进统一 sinks）
_std_logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"


def _new_request_id() -> str:
    return uuid4().hex[:16]


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(
            (k.decode("latin-1").lower(), v.decode("latin-1")) for k, v in scope.get("headers", [])
        )
        request_id = headers.get(REQUEST_ID_HEADER.lower()) or _new_request_id()

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                response_headers = list(message.get("headers", []))
                response_headers.append(
                    (REQUEST_ID_HEADER.encode("latin-1"), request_id.encode("latin-1"))
                )
                message = {**message, "headers": response_headers}
            await send(message)

        # 结构化日志（JSON 文件 sink）自动携带 extra.request_id；
        # 控制台格式串显式引用（见 logging.yaml）
        with logger.contextualize(request_id=request_id):
            await self.app(scope, receive, send_with_request_id)


# ---------- 请求体大小上限 ----------


@dataclass(frozen=True)
class BodySizeSpec:
    """一条请求体上限规则：方法 + 路径正则圈定作用域，字节上限。"""

    methods: frozenset[str]
    path_pattern: re.Pattern[str]
    max_bytes: int


def _body_limit_message(max_bytes: int) -> str:
    return f"请求体超过大小上限 {max_bytes} 字节"


class BodySizeLimitMiddleware:
    """请求体字节上限（路由作用域，见 :class:`BodySizeSpec`）：

    - ``Content-Length`` 已知且超限：不读请求体直接回 413 信封
      （uvicorn/h11 支持响应先于请求体完成，超限时自动关闭连接）；
    - 无/谎报 ``Content-Length``：包装 ``receive`` 按实收字节累计，
      超限在消费方读取处抛 ``HTTPException(413)``，由全局
      ``http_exception_handler`` 统一为信封响应——multipart 的文件
      部分由 Starlette spool 落盘，此处计数针对网络实收字节，
      与服务层单文件上限（support.MAX_UPLOAD_BYTES）双保险。
    """

    def __init__(self, app: ASGIApp, *, specs: Sequence[BodySizeSpec]) -> None:
        self.app = app
        self.specs = tuple(specs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        spec = _match_scope(self.specs, scope)
        if spec is None:
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > spec.max_bytes:
            _std_logger.warning(
                "request body rejected by content-length on %s %s: %s > %s",
                scope["method"], scope["path"], declared, spec.max_bytes,
            )
            await JSONResponse(
                status_code=413,
                content=Response.fail(413, _body_limit_message(spec.max_bytes)).to_dict(),
            )(scope, receive, send)
            return

        max_bytes = spec.max_bytes
        state = {"received": 0, "exceeded": False}

        async def limited_receive() -> Message:
            # 已判定超限后持续抛出：消费方即便吞掉首次异常继续读，也不会拿到后续体
            if state["exceeded"]:
                raise HTTPException(status_code=413, detail=_body_limit_message(max_bytes))
            message = await receive()
            if message["type"] == "http.request":
                state["received"] += len(message.get("body", b""))
                if state["received"] > max_bytes:
                    state["exceeded"] = True
                    raise HTTPException(status_code=413, detail=_body_limit_message(max_bytes))
            return message

        await self.app(scope, limited_receive, send)


def _match_scope(specs, scope: Scope):
    """首个方法与路径（scope["path"]，不含 query）均匹配的规则；路径锚定由各规则的正则自控。"""
    method = scope["method"]
    path = scope["path"]
    for spec in specs:
        if method in spec.methods and spec.path_pattern.search(path):
            return spec
    return None


def _content_length(scope: Scope) -> int | None:
    for key, value in scope.get("headers", []):
        if key == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None  # 非法长度交由上层按坏请求处理
    return None

