"""首个真实 HTTP 集成测试：真实 ``create_app()`` + TestClient 全链路走通。

覆盖两条线：
1. 主流程：注册 → 登录 → run（SSE）→ 重试出兄弟分支 → activate 切换活跃叶子
   → replay 只含活跃链（history 派生链 / 服务层 replay_history / 后续 run 的
   真实 LLM 上下文三重断言）；
2. 鉴权 sweep：除公开路由（/auth/register、/auth/login、/health、/metrics）
   外，全部业务路由无 token 一律 401 + error_code=5002。

装配要点（不碰 docker 中间件，网络零依赖）：
- SQLITE_DB_PATH 指向模块级临时 SQLite 文件，走完整 Alembic 迁移建表
  （应用启动不建表，模式见 test_admin_db.py）；
- wireup 容器 override 替换 AgentFactory（可编程 agent 替身，免 LLM）与
  TurnFinalizer（noop，免后台线程的标题 LLM + 记忆巩固）——必须在
  TestClient 启动前 set，lifespan 启动即解析 AgenticService 单例；
- 配置 lru_cache（read_config / get_environment / _auth_config）在环境变量
  就位后清空，否则上一份插值结果会遮蔽本次（同 test_admin_db.py 的口径）；
- Redis 缺失不阻断：run 流内的取消标志读取降级为"视为未取消"（领域层
  宽容策略），知识库/记忆端点在鉴权 sweep 中 401 短路，触不到服务层。
"""

import asyncio
import json
import os
import re
import signal
import tempfile
from types import SimpleNamespace
from uuid import UUID, uuid4

# 环境变量必须在任何 app.* 导入（含 import 期 create_app() 的配置读取）之前就位，
# 故本模块的 app 导入全部排在 os.environ 设置之后（E402 由 noqa 豁免）。
# SQLITE_DB_PATH 必须强制赋值而非 setdefault：全量套件里按字母序更早的测试模块
# 可能已触发 load_dotenv（.env 的 SQLITE_DB_PATH=data/agentic.db 会灌进进程环境）
# ——setdefault 会保留开发库路径，整个模块就写到开发库去了（实测回归）。
# 密钥/哑 API key 用 setdefault 即可：.env 不含 AUTH_JWT_SECRET，且先导入的
# test_auth_deps 已固化同一密钥并据此签发 token——只需各上下文内自洽。
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
from app.domain.conversation.conversation_service import (  # noqa: E402
    ConversationService,
)
from app.application.turn_finalizer import TurnFinalizer  # noqa: E402
from app.cmd.http.main import create_app  # noqa: E402

for _clear in (read_config.cache_clear, get_environment.cache_clear, deps._auth_config.cache_clear):
    _clear()

# 鉴权 sweep 的公开路由：注册/登录是取票入口，/health /metrics 供探针与抓取
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


@pytest.fixture(scope="module")
def client():
    upgrade_cmd("head")  # 临时库建表：缓存已清，迁移链读到本模块的 SQLITE_DB_PATH
    app = create_app()
    container = app.state.wireup_container
    # 两个替身必须在 TestClient 启动前注册：lifespan 解析 AgenticService 时即生效
    container.override.set(AgentFactory, ScriptedAgentFactory(_agent))
    container.override.set(TurnFinalizer, NoopTurnFinalizer())
    # TestClient 在 portal 线程里跑 lifespan：signal.signal 仅限主线程，换成 no-op
    # 替身（注册/恢复 handlers 的语义在测试进程内无意义，返回 handler 对齐默认值口径）
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


def register_and_login(client, username):
    """注册（同账号重复注册断言 409）→ 登录取 token，返回 (headers, token)。"""
    resp = client.post("/auth/register", json={"username": username, "password": "password123"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error_code"] == 0
    assert body["response"]["user"]["username"] == username

    conflict = client.post("/auth/register", json={"username": username, "password": "password123"})
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == 5001

    resp = client.post("/auth/login", json={"username": username, "password": "password123"})
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


def replay_text_of(messages):
    return "\n".join(str(m.content) for m in messages)


def test_run_retry_activate_replay_keeps_only_active_chain(client):
    """主流程：run → 重试出兄弟分支 → activate 切回旧轮 → replay 只含活跃链。"""
    headers, token = register_and_login(client, f"it_{uuid4().hex[:8]}")
    thread_id = uuid4()

    # 首次 run：新会话第一轮 T1（无 forwardedProps，走自动检测不命中 → 全新轮次）
    _agent.answer = "旧答案"
    events = post_run(client, headers, thread_id, "run-1", "问题一")
    assert [e["type"] for e in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[2]["delta"] == "旧答案"

    history = history_of(client, token, thread_id)
    assert history["total"] == 1
    assert history["active_turn_id"] is not None
    t1 = history["items"][0]
    assert t1["status"] == "completed"
    assert t1["parent_turn_id"] is None
    assert t1["attempt_no"] == 1
    assert text_of_message(t1["messages"][0]) == "问题一"
    t1_answer_id = next(m["message_id"] for m in t1["messages"] if m["role"] == "assistant")

    # 重试：显式 branch 信号（T1 的 assistant message id）→ T2 与 T1 成兄弟，
    # attempt_no=2；完成后活跃叶子推进到 T2
    _agent.answer = "新答案"
    post_run(client, headers, thread_id, "run-2", "问题一", branch_base_message_id=t1_answer_id)

    history = history_of(client, token, thread_id)
    assert history["total"] == 2
    assert history["active_turn_id"] != t1["turn_id"]
    t2 = next(t for t in history["items"] if t["turn_id"] == history["active_turn_id"])
    assert t2["parent_turn_id"] is None
    assert t2["attempt_no"] == 2

    # activate：把活跃叶子切回 T1（T1 是活跃叶子的兄弟，在末梢扇形内，合法）
    resp = client.post(
        f"/agentic/conversation/{thread_id}/activate-turn",
        headers=headers,
        json={"turnId": t1["turn_id"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["response"]["current_turn_id"] == t1["turn_id"]

    # replay 断言 ①history：活跃链只含 T1；items 同时含 T2 属末梢扇形语义
    history = history_of(client, token, thread_id)
    assert history["active_turn_id"] == t1["turn_id"]
    assert [t["turn_id"] for t in active_chain_of(history)] == [t1["turn_id"]]
    assert {t["turn_id"] for t in history["items"]} == {t1["turn_id"], t2["turn_id"]}

    # replay 断言 ②服务层：replay_history（run 链路组装 LLM 上下文的同一函数）
    # 只回活跃链上的 COMPLETED 轮次——被替换的"新答案"不出现
    container = client.app.state.wireup_container
    conversations = asyncio.run(container.get(ConversationService))
    replayed = conversations.replay_history(thread_id=thread_id, base_turn_id=UUID(t1["turn_id"]))
    replayed_text = replay_text_of(replayed)
    assert "问题一" in replayed_text and "旧答案" in replayed_text
    assert "新答案" not in replayed_text

    # replay 断言 ③端到端：激活后续聊，编排层投给 agent 的上下文即 replay 产物。
    # payload 故意携带被替换的"新答案"（客户端本地历史不准的场景）：服务端权威
    # replay 只认库里的活跃链——若回放的是 payload，"新答案"就会漏进上下文。
    # 若只发单条消息则命中"重试最新一轮"自动检测（payload 用户数==活跃链轮次数），
    # 被截断成兄弟轮次，故按前端普通追加语义携带完整本地历史。
    _agent.answer = "续聊回答"
    post_run(
        client, headers, thread_id, "run-3", "问题二",
        prior_messages=[("user", "问题一"), ("assistant", "新答案")],
    )
    context = _agent.contexts[-1]
    context_text = replay_text_of(context.messages)
    assert "问题一" in context_text and "旧答案" in context_text
    assert "新答案" not in context_text
    assert context.messages[-1].content == "问题二"  # 末条恒为当前轮用户消息


def test_run_rejects_duplicate_run_id(client):
    """run_id 幂等：同一会话重复提交同一 runId（传输层重试/双击重放场景），
    第二次 run 以 RUN_ERROR 拒绝且不产生新轮次——硬保证来自
    uq_turn_thread_run 唯一约束，领域层翻译为 DuplicateRunError。"""
    headers, token = register_and_login(client, f"it_{uuid4().hex[:8]}")
    thread_id = uuid4()

    _agent.answer = "第一次回答"
    post_run(client, headers, thread_id, "run-dup", "问题")
    assert history_of(client, token, thread_id)["total"] == 1

    _agent.answer = "不应出现的重复回答"
    resp = client.post(
        "/agentic/run",
        headers=headers,
        json=run_request(thread_id, "run-dup", "问题"),
    )
    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[1]["message"] == "请勿重复提交：相同请求已在本会话中提交过"
    # 轮次与回答都不变：重复请求既没开新轮次，也没触发 agent 二次执行
    history = history_of(client, token, thread_id)
    assert history["total"] == 1
    assert _agent.contexts[-1].messages[-1].content == "问题"


def _api_routes(routes):
    """递归收集 APIRoute：本 FastAPI 版本把 include 的 router 包成 _IncludedRouter
    （完整路径已烘焙进 original_router.routes 的 APIRoute），不再平铺进 app.routes。"""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
            continue
        nested = getattr(route, "original_router", None)
        if nested is not None:
            yield from _api_routes(nested.routes)


def test_all_business_routes_require_auth(client):
    """鉴权 sweep：除公开路由外，全部业务路由无 token 一律 401 + error_code=5002。

    路由级 Depends(require_user) 在路径/请求体解析之前短路，路径参数与空 JSON
    体不会改变响应（401 先于 422）。"""
    checked = []
    for route in _api_routes(client.app.routes):
        if route.path in _PUBLIC_PATHS:
            continue
        for method in sorted(route.methods & {"GET", "POST", "PATCH", "DELETE", "PUT"}):
            path = re.sub(r"\{[^}]+\}", "x", route.path)
            body = {} if method in {"POST", "PATCH", "PUT", "DELETE"} else None
            resp = client.request(method, path, json=body)
            assert resp.status_code == 401, f"{method} {route.path} 未拦截：{resp.status_code} {resp.text}"
            assert resp.json()["error_code"] == 5002, f"{method} {route.path} 错误码口径漂移"
            checked.append(f"{method} {route.path}")

    # 覆盖面下限：业务路由（agentic/attachments/knowledge/memory/stats/auth）合计远超此数，
    # 收集逻辑意外变空时在此失败
    assert len(checked) >= 40


def test_stats_usage_endpoints_smoke(client):
    """用量统计端点冒烟：空库返回零值汇总/空序列/空流水（鉴权后个人视角）。"""
    headers, _ = register_and_login(client, "stats_user")

    resp = client.get("/stats/usage/summary", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error_code"] == 0
    assert body["response"]["totals"] == {
        "key": "total", "calls": 0, "promptTokens": 0, "completionTokens": 0, "totalTokens": 0,
    }
    assert body["response"]["byScene"] == []
    assert body["response"]["byModel"] == []

    resp = client.get("/stats/usage/daily", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["response"]["items"] == []

    resp = client.get("/stats/usage/records", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()["response"]
    assert body["items"] == []
    assert body["total"] == 0
    assert body["page"] == 1

    # 非法时间范围（start >= end）：6001/400
    resp = client.get(
        "/stats/usage/summary",
        headers=headers,
        params={"start": "2026-09-02T00:00:00Z", "end": "2026-09-01T00:00:00Z"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error_code"] == 6001
