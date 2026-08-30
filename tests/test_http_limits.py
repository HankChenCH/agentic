"""HTTP 边缘策略：限流中间件、请求体上限中间件与 CORS 配置校验。

中间件均为纯 ASGI，直接以手工 scope/receive/send 驱动（不经完整应用）；
信封/异常处理器联动用最小 FastAPI 应用 + TestClient 验证（httpx 依赖已有）。
"""

import asyncio
import json
import re
from typing import Sequence

import pytest
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException
from starlette.testclient import TestClient

import app.api.middleware as middleware_module
from app.api.exception_handlers import register_exception_handlers
from app.api.middleware import (
    BodySizeLimitMiddleware,
    BodySizeSpec,
    RateLimitMiddleware,
    RateLimitSpec,
)
from app.core.config import CorsConfig

CLIENT = ("203.0.113.7", 51234)


def make_scope(method="POST", path="/agentic/chat", headers=(), client=CLIENT):
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": list(headers),
        "client": client,
        "server": ("testserver", 80),
    }


class CapturingApp:
    """透传应用：计数调用、完整消费请求体、回 200；receive 异常记录后重抛。"""

    def __init__(self):
        self.calls = 0
        self.received = []
        self.read_errors: list[BaseException] = []

    async def __call__(self, scope, receive, send):
        self.calls += 1
        try:
            while True:
                message = await receive()
                self.received.append(message)
                if message["type"] != "http.request" or not message.get("more_body"):
                    break
        except BaseException as exc:  # noqa: BLE001 测试需捕获穿透的一切
            self.read_errors.append(exc)
            raise
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


class SendRecorder:
    def __init__(self):
        self.messages = []

    async def __call__(self, message):
        self.messages.append(message)

    @property
    def status(self):
        return self.messages[0]["status"]

    def header(self, name: str) -> str | None:
        for key, value in self.messages[0].get("headers", []):
            if key.decode().lower() == name.lower():
                return value.decode()
        return None

    def json_body(self) -> dict:
        return json.loads(b"".join(m.get("body", b"") for m in self.messages[1:]))


def receive_from(messages: Sequence[dict]):
    iterator = iter(messages)

    async def receive():
        try:
            return next(iterator)
        except StopIteration:
            return {"type": "http.disconnect"}

    return receive


def run(coro):
    return asyncio.run(coro)


def rate_spec(requests=2, window=60.0):
    return RateLimitSpec(
        methods=frozenset({"POST"}),
        path_pattern=re.compile(r"^/agentic/chat"),
        requests=requests,
        window_seconds=window,
    )


def body_spec(max_bytes=10):
    return BodySizeSpec(
        methods=frozenset({"POST"}),
        path_pattern=re.compile(r"^/agentic/chat"),
        max_bytes=max_bytes,
    )


async def call(middleware, scope, messages=()):
    send = SendRecorder()
    await middleware(scope, receive_from(messages), send)
    return send


# ---------- RateLimitMiddleware ----------


def test_rate_limit_allows_within_window_then_rejects():
    app = CapturingApp()
    middleware = RateLimitMiddleware(app, specs=[rate_spec()])

    for _ in range(2):
        run(call(middleware, make_scope()))
    assert app.calls == 2

    send = run(call(middleware, make_scope()))
    assert app.calls == 2  # 拒绝不进路由
    assert send.status == 429
    assert send.json_body()["error_code"] == 429
    retry_after = send.header("Retry-After")
    assert retry_after is not None and int(retry_after) >= 1


def test_rate_limit_counts_clients_independently():
    app = CapturingApp()
    middleware = RateLimitMiddleware(app, specs=[rate_spec(requests=1)])
    run(call(middleware, make_scope(client=("198.51.100.1", 1))))
    send = run(call(middleware, make_scope(client=("198.51.100.2", 2))))
    assert send.status == 200
    run(call(middleware, make_scope(client=("198.51.100.2", 2))))
    assert run(call(middleware, make_scope(client=("198.51.100.2", 2)))).status == 429


def test_rate_limit_window_expiry_admits_again(monkeypatch):
    clock = {"now": 0.0}
    monkeypatch.setattr(middleware_module, "monotonic", lambda: clock["now"])
    app = CapturingApp()
    middleware = RateLimitMiddleware(app, specs=[rate_spec(requests=1, window=60.0)])

    assert run(call(middleware, make_scope())).status == 200
    assert run(call(middleware, make_scope())).status == 429
    clock["now"] = 60.1  # 窗口滑出
    assert run(call(middleware, make_scope())).status == 200


def test_rate_limit_scopes_by_method_and_path():
    app = CapturingApp()
    middleware = RateLimitMiddleware(app, specs=[rate_spec(requests=1)])
    assert run(call(middleware, make_scope())).status == 200
    # 同路径他方法、他路径同方法均不在作用域内
    assert run(call(middleware, make_scope(method="GET"))).status == 200
    assert run(call(middleware, make_scope(path="/agentic/conversation"))).status == 200
    assert run(call(middleware, make_scope())).status == 429
    assert app.calls == 3


def test_rate_limit_non_http_scope_passthrough():
    app = CapturingApp()
    middleware = RateLimitMiddleware(app, specs=[rate_spec()])
    run(middleware({"type": "lifespan"}, receive_from([]), SendRecorder()))
    assert app.calls == 1


# ---------- BodySizeLimitMiddleware ----------


def test_body_size_rejects_by_content_length_without_reading():
    app = CapturingApp()
    middleware = BodySizeLimitMiddleware(app, specs=[body_spec(max_bytes=10)])
    scope = make_scope(headers=[(b"content-length", b"11")])
    send = run(call(middleware, scope, messages=({"type": "http.request", "body": b"x" * 11},)))
    assert app.calls == 0  # 未进路由、未读请求体
    assert send.status == 413
    assert send.json_body()["error_code"] == 413


def test_body_size_allows_matching_content_length():
    app = CapturingApp()
    middleware = BodySizeLimitMiddleware(app, specs=[body_spec(max_bytes=10)])
    scope = make_scope(headers=[(b"content-length", b"10")])
    send = run(call(middleware, scope, messages=({"type": "http.request", "body": b"x" * 10},)))
    assert send.status == 200
    assert app.calls == 1


def test_body_size_raises_midstream_without_content_length():
    """无 Content-Length（分块传输）：实收累计超限在读取处抛 HTTPException(413)。"""
    app = CapturingApp()
    middleware = BodySizeLimitMiddleware(app, specs=[body_spec(max_bytes=10)])
    messages = [
        {"type": "http.request", "body": b"x" * 6, "more_body": True},
        {"type": "http.request", "body": b"x" * 5, "more_body": True},
    ]
    with pytest.raises(HTTPException) as exc_info:
        run(call(middleware, make_scope(), messages))
    assert exc_info.value.status_code == 413
    assert len(app.read_errors) == 1


def test_body_size_sticky_after_exceeded():
    """已判定超限后，后续 receive 持续抛出（消费方吞异常继续读也拿不到体）。"""
    errors: list[HTTPException] = []

    async def sticky_app(scope, receive, send):
        for _ in range(2):
            try:
                await receive()
            except HTTPException as exc:
                errors.append(exc)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = BodySizeLimitMiddleware(sticky_app, specs=[body_spec(max_bytes=4)])
    messages = [
        {"type": "http.request", "body": b"x" * 5, "more_body": True},
        {"type": "http.request", "body": b"y" * 5, "more_body": False},
    ]
    run(call(middleware, make_scope(), messages))
    assert len(errors) == 2  # 第二次读同样被拒，且不再消费底层消息


def test_body_size_scopes_by_method_and_path():
    app = CapturingApp()
    middleware = BodySizeLimitMiddleware(app, specs=[body_spec(max_bytes=10)])
    oversized = [(b"content-length", b"999")]
    assert run(call(middleware, make_scope(method="GET", headers=oversized))).status == 200
    assert run(call(middleware, make_scope(path="/memory/graph", headers=oversized))).status == 200
    assert app.calls == 2


# ---------- 与 FastAPI 全局异常处理器联动（信封 + CORS 白名单行为） ----------


def build_edge_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)
    app.add_middleware(
        BodySizeLimitMiddleware,
        specs=[BodySizeSpec(methods=frozenset({"POST"}), path_pattern=re.compile(r"^/agentic/chat"), max_bytes=10)],
    )
    app.add_middleware(
        RateLimitMiddleware,
        specs=[RateLimitSpec(methods=frozenset({"POST"}), path_pattern=re.compile(r"^/agentic/chat"), requests=5, window_seconds=60.0)],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID", "Authorization"],
    )

    @app.post("/agentic/chat")
    async def chat(request: Request):
        return {"size": len(await request.body())}

    return app


def test_edge_app_body_limit_envelope():
    client = TestClient(build_edge_app())
    assert client.post("/agentic/chat", content=b"12345").json() == {"size": 5}

    # Content-Length 已知超限：中间件直发 413 信封
    response = client.post("/agentic/chat", content=b"x" * 11)
    assert response.status_code == 413
    assert response.json()["error_code"] == 413

    # 分块传输（无 Content-Length）：读取处 HTTPException(413) → 全局处理器信封
    def chunked():
        yield b"x" * 6
        yield b"x" * 6

    response = client.post("/agentic/chat", content=chunked())
    assert response.status_code == 413
    assert response.json()["error_code"] == 413


def test_edge_app_rate_limit_envelope():
    client = TestClient(build_edge_app())
    for _ in range(5):
        assert client.post("/agentic/chat", content=b"a").status_code == 200
    response = client.post("/agentic/chat", content=b"a")
    assert response.status_code == 429
    assert response.json()["error_code"] == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_edge_app_cors_whitelist():
    client = TestClient(build_edge_app())
    allowed = client.post(
        "/agentic/chat", content=b"a", headers={"Origin": "http://localhost:5173"}
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert allowed.headers["access-control-allow-credentials"] == "true"

    blocked = client.post(
        "/agentic/chat", content=b"a", headers={"Origin": "http://evil.example"}
    )
    assert "access-control-allow-origin" not in blocked.headers

    preflight = client.options(
        "/agentic/chat",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert "POST" in preflight.headers["access-control-allow-methods"]

    # 白名单外的请求方法在预检即被拒
    bad_method = client.options(
        "/agentic/chat",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
        },
    )
    assert bad_method.status_code == 400


# ---------- CorsConfig 校验 ----------


def test_cors_config_splits_comma_separated_origins():
    config = CorsConfig(origins="https://a.example, https://b.example")
    assert config.origins == ["https://a.example", "https://b.example"]


def test_cors_config_accepts_yaml_list():
    config = CorsConfig(origins=["http://localhost:5173"])
    assert config.origins == ["http://localhost:5173"]


@pytest.mark.parametrize(
    "origins",
    [
        "",
        ",",
        "*",
        "localhost:5173",
        "http://localhost:5173/",
        "ftp://host",
        ["http://a.example", "https://b.example/"],
    ],
)
def test_cors_config_rejects_malformed_origins(origins):
    with pytest.raises(ValueError):
        CorsConfig(origins=origins)
