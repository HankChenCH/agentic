"""HTTP 集成测试共享基建：真实 ``create_app()`` + TestClient 的装配套路。

被 test_http_integration / test_memory_admin_api / test_attachments_api /
test_stats_api 等模块共用。要点（详见原 test_http_integration 文档注释）：
- 环境变量必须在任何 app.* 导入前就位（本模块 import 期完成，消费方必须
  先 import 本模块再 import 任何 app 模块）；SQLITE_DB_PATH 强制赋值防止
  .env 的开发库路径灌入；配置 lru_cache 清空后再跑迁移建表；
- wireup 容器 override 必须在 TestClient 启动前 set（lifespan 即解析
  AgenticService 单例）；TestClient 的 portal 线程装不了 signal.signal，
  换 no-op 替身；
- Redis 缺失不阻断：取消标志读取降级为「视为未取消」，鉴权 sweep 中
  触不到服务层的端点照常 401 短路。
"""

import json
import os
import signal
import tempfile
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

# 环境变量就位必须先于 app.* 导入（E402 豁免同 test_http_integration 原口径）
_TMP_DIR = tempfile.mkdtemp(prefix="agentic-http-it-")
os.environ["SQLITE_DB_PATH"] = os.path.join(_TMP_DIR, "agentic.db")
os.environ.setdefault("DEEPSEEK_API_KEY", "ci-dummy")
os.environ.setdefault("AUTH_JWT_SECRET", "http-it-secret-0123456789abcdef-change-me")

import pytest  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.agents import AgentFactory  # noqa: E402
from app.api import deps  # noqa: E402
from app.commands.db import upgrade_cmd  # noqa: E402
from app.core.config import get_environment  # noqa: E402
from app.core.config.loader import read_config  # noqa: E402
from app.cmd.http.main import create_app  # noqa: E402
from app.application.turn_finalizer import TurnFinalizer  # noqa: E402

for _clear in (read_config.cache_clear, get_environment.cache_clear, deps._auth_config.cache_clear):
    _clear()

# 鉴权 sweep 的公开路由：注册/登录/刷新是取票或换票入口，/health /metrics 供探针与抓取
_PUBLIC_PATHS = {"/auth/register", "/auth/login", "/auth/refresh", "/health", "/metrics"}


class Deltas:
    """假消息投影：可逐 delta 迭代（ag-ui 流式侧），也可整体转 str（落库侧）。"""

    def __init__(self, *chunks):
        self._chunks = list(chunks)

    def __iter__(self):
        return iter(self._chunks)

    def __str__(self):
        return "".join(self._chunks)

    def __bool__(self):
        return bool(self._chunks)


class FakeChatModelStream:
    """messages 投影 item 的最小替身（两个 translator 只读这些公开面）。"""

    def __init__(self, text=""):
        self.reasoning = Deltas()
        self.text = Deltas(text)
        self.tool_calls = type("ToolCalls", (), {"get": lambda self: []})()
        self.output_message = None


class FakeRun:
    """agent.stream() 返回值替身：interleave 逐条产出 (频道, item)。"""

    def __init__(self, items=()):
        self._items = list(items)

    def interleave(self, *names):
        yield from self._items


class ScriptedAgent:
    """可编程 agent 替身：stream() 记录 AgentRunContext（供 replay 断言），
    回放 ``answer`` 预设文本。测试用例间通过改 answer 切换回答。"""

    supports_vision = False

    def __init__(self):
        self.answer = ""
        self.contexts = []
        # 编排层用量上下文读取的模型实例替身（BaseAgent.model 口径）
        self.model = SimpleNamespace(model_name="scripted-chat-model")

    def stream(self, context):
        self.contexts.append(context)
        return FakeRun([("messages", FakeChatModelStream(self.answer))])


class ScriptedAgentFactory:
    default_agentic_id = "builtin:demo"

    def __init__(self, agent):
        self._agent = agent

    def create(self, agentic_id):
        return self._agent

    def is_registered(self, agentic_id):
        return True


class NoopTurnFinalizer:
    """收尾加工替身：真实实现会在后台线程调标题 LLM 与记忆巩固（Weaviate upsert）。"""

    def run(self, *args, **kwargs):
        pass


_agent = ScriptedAgent()


def build_app(extra_overrides=()):
    """建 app 并注入替身：默认换掉 AgentFactory 与 TurnFinalizer（免 LLM、
    免后台收尾线程）；``extra_overrides`` 为 (类型, 实现) 序列，供域内测试
    追加（如 MemoryVectorIndexPort → FakeMemoryVectorIndex）。"""
    upgrade_cmd("head")  # 临时库建表：缓存已清，迁移链读到本模块的 SQLITE_DB_PATH
    app = create_app()
    container = app.state.wireup_container
    # 替身必须在 TestClient 启动前注册：lifespan 解析 AgenticService 时即生效
    container.override.set(AgentFactory, ScriptedAgentFactory(_agent))
    container.override.set(TurnFinalizer, NoopTurnFinalizer())
    for type_, implementation in extra_overrides:
        container.override.set(type_, implementation)
    return app


@contextmanager
def http_client_for(app):
    """TestClient 生命周期 + signal 替身（portal 线程限制，见模块 docstring）。"""
    import app.cmd.http.main as http_main

    original_signal = http_main.signal
    http_main.signal = SimpleNamespace(
        SIGTERM=signal.SIGTERM,
        SIGINT=signal.SIGINT,
        signal=lambda sig, handler: handler,
    )
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        http_main.signal = original_signal


@pytest.fixture(scope="module")
def client():
    with http_client_for(build_app()) as test_client:
        yield test_client


# ---------- HTTP 交互辅助 ----------


def sse_events(text):
    """完整 SSE 响应体（data: {...}\\n\\n 序列，间或夹 ": ping" 心跳注释帧）
    → ag-ui 事件 dict 列表。注释块与 @ag-ui/client 同口径跳过。"""
    return [
        json.loads(frame.removeprefix("data: ").strip())
        for frame in text.split("\n\n")
        if frame.strip() and not frame.startswith(":")
    ]


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def register_and_login(client, username, password="password123"):
    """注册（同账号重复注册断言 409）→ 登录取 token，返回 (headers, token)。"""
    resp = client.post("/auth/register", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error_code"] == 0
    assert body["response"]["user"]["username"] == username

    conflict = client.post("/auth/register", json={"username": username, "password": password})
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == 5001

    resp = client.post("/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["response"]["token"]
    return auth_headers(token), token


def run_request(thread_id, run_id, text, *, branch_base_message_id=None, prior_messages=()):
    """构造 run 请求体。``prior_messages`` 为本地消息列表（服务端只取末条作为
    当前提问，整表仅投给 open_turn 做重试自动检测——普通追加的 payload 用户数
    = 活跃链轮次数 + 1，不会被误判为重试）。"""
    messages = [
        {"id": f"prior-{run_id}-{i}", "role": role, "content": content}
        for i, (role, content) in enumerate(prior_messages)
    ]
    messages.append({"id": f"user-{run_id}", "role": "user", "content": text})
    return {
        "threadId": str(thread_id),
        "runId": run_id,
        "messages": messages,
        "forwardedProps": {"branch": {"baseMessageId": branch_base_message_id}} if branch_base_message_id else None,
    }


def post_run(client, headers, thread_id, run_id, text, *, branch_base_message_id=None, prior_messages=()):
    resp = client.post(
        "/agentic/run",
        headers=headers,
        json=run_request(
            thread_id, run_id, text,
            branch_base_message_id=branch_base_message_id,
            prior_messages=prior_messages,
        ),
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = sse_events(resp.text)
    assert events[0]["type"] == "RUN_STARTED"
    assert events[-1]["type"] == "RUN_FINISHED"
    return events


def history_of(client, token, thread_id):
    resp = client.get(f"/agentic/conversation/{thread_id}/history", headers=auth_headers(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error_code"] == 0
    return body["response"]


def active_chain_of(history):
    """按 parent_turn_id 链从 active_turn_id 回溯出的旧→新活跃链。"""
    by_id = {turn["turn_id"]: turn for turn in history["items"]}
    chain, seen = [], set()
    current = by_id.get(history["active_turn_id"])
    while current is not None and current["turn_id"] not in seen:
        seen.add(current["turn_id"])
        chain.append(current)
        current = by_id.get(current["parent_turn_id"])
    chain.reverse()
    return chain


def text_of_message(message):
    return "".join(part.get("text", "") for part in message["content"] if part.get("type") == "text")


def api_routes(routes):
    """递归收集 APIRoute：本 FastAPI 版本把 include 的 router 包成 _IncludedRouter
    （完整路径已烘焙进 original_router.routes 的 APIRoute），不再平铺进 app.routes。"""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from api_routes(nested.routes)


def new_username(tag="u"):
    return f"it_{tag}_{uuid4().hex[:8]}"
