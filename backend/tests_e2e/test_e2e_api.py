"""端到端测试：针对**运行中的** backend 服务（真实 LLM / Redis / Weaviate / Ollama 链路）。

前置：
- 服务已启动：``uv run uvicorn app.cmd.http.main:server --port 8000``（CWD=backend/）
- 中间件栈健康（docker compose）、Ollama 嵌入模型在线、.env 携带真实 DEEPSEEK_API_KEY

运行：``E2E_BASE_URL=http://127.0.0.1:8000 uv run pytest tests_e2e/ -v``
（与单元套件分离：``pytest tests/`` 仍是纯单元级。）

口径：走公开 HTTP 面 + ag-ui SSE 协议，断言用户可感知行为（信封、事件序列、
状态机、归属隔离），不窥探内部。每个用例独立注册用户，互不依赖。
"""

import json
import os
import time
import uuid

import httpx

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
PASSWORD = "e2e-Passw0rd!123"
RUN_TIMEOUT = 180.0


# ---------------- 基础设施 ----------------


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


def _register(c: httpx.Client, tag: str) -> dict:
    username = f"e2e_{tag}_{uuid.uuid4().hex[:8]}"
    resp = c.post("/auth/register", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error_code"] == 0
    return {"username": username, "token": body["response"]["token"], "user": body["response"]["user"]}


def _run_sse(
    c: httpx.Client, token: str, thread_id: str, text: str, *, run_id: str | None = None
) -> list[dict]:
    """发起一次 agentic run 并收集全部 ag-ui 事件（SSE data 帧解析）。"""
    events: list[dict] = []
    payload = {
        "threadId": thread_id,
        "runId": run_id or uuid.uuid4().hex,
        "messages": [{"id": uuid.uuid4().hex, "role": "user", "content": text}],
    }
    with c.stream(
        "POST",
        "/agentic/run",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=RUN_TIMEOUT,
    ) as resp:
        assert resp.status_code == 200, resp.read().decode()
        assert resp.headers["content-type"].startswith("text/event-stream")
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def _run_sse_history(
    c: httpx.Client, token: str, thread_id: str, turns: list[tuple[str, str]]
) -> list[dict]:
    """按完整本地历史投影发起 run：(文本, role) 序列，末位为当前提问。"""
    payload = {
        "threadId": thread_id,
        "runId": uuid.uuid4().hex,
        "messages": [
            {"id": uuid.uuid4().hex, "role": role, "content": text}
            for text, role in turns
        ],
    }
    events: list[dict] = []
    with c.stream(
        "POST",
        "/agentic/run",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=RUN_TIMEOUT,
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: "):]))
    return events


def _types(events: list[dict]) -> list[str]:
    return [e.get("type", "") for e in events]


def _text_of(events: list[dict]) -> str:
    return "".join(e.get("delta", "") for e in events if e.get("type") == "TEXT_MESSAGE_CONTENT")


def _history(c: httpx.Client, token: str, thread_id: str) -> dict:
    """历史 = ``{"items": [turn...]}``：turn 携带 status 与嵌套 messages。"""
    body = c.get(f"/agentic/conversation/{thread_id}/history", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["error_code"] == 0, body
    return body["response"]


def _turns(hist: dict) -> list[dict]:
    return hist.get("items") or []


def _all_messages(hist: dict) -> list[dict]:
    return [m for turn in _turns(hist) for m in (turn.get("messages") or [])]


def _wait_for(pred, timeout: float = 60.0, interval: float = 2.5) -> bool:
    """轮询等待后台收尾（标题/记忆在流闭后的 daemon 线程里最终一致）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def _find_thread_in_list(c: httpx.Client, token: str, thread_id: str) -> dict | None:
    body = c.get("/agentic/conversation", headers={"Authorization": f"Bearer {token}"}).json()
    assert body["error_code"] == 0
    page = body["response"]
    items = page.get("items") or page.get("conversations") or []
    want = str(uuid.UUID(thread_id))  # hex 与带连字符形态归一
    for item in items:
        got = item.get("thread_id") or item.get("threadId")
        if got is not None and str(uuid.UUID(str(got))) == want:
            return item
    return None


# ---------------- 用例 ----------------


def test_01_health():
    with _client() as c:
        resp = c.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["error_code"] == 0
        components = body["response"]["components"]
        assert components["db"] == "up"
        assert components["redis"] == "up"


def test_02_auth_lifecycle():
    with _client() as c:
        user = _register(c, "auth")
        token = user["token"]
        auth = {"Authorization": f"Bearer {token}"}

        # me：注册即登录，身份可读
        me = c.get("/auth/me", headers=auth).json()
        assert me["error_code"] == 0
        assert me["response"]["username"] == user["username"]

        # 重复登录：同密码可再取 token
        login = c.post("/auth/login", json={"username": user["username"], "password": PASSWORD})
        assert login.status_code == 200 and login.json()["error_code"] == 0
        assert login.json()["response"]["token"]

        # 错密码：401 + 5002（不泄露存在性口径）
        bad = c.post("/auth/login", json={"username": user["username"], "password": "wrong-pass-123"})
        assert bad.status_code == 401
        assert bad.json()["error_code"] == 5002

        # 改昵称
        nick = c.patch("/auth/me", headers=auth, json={"nickname": "端到端机器人"})
        assert nick.status_code == 200 and nick.json()["error_code"] == 0
        assert c.get("/auth/me", headers=auth).json()["response"]["nickname"] == "端到端机器人"

        # 改密后：旧密码失效（登录 401/5002）、新密码可登录；
        # JWT 为无状态令牌（不含密码版本），旧 token 到期前仍有效——语义如此
        change = c.post(
            "/auth/change-password",
            headers=auth,
            json={"old_password": PASSWORD, "new_password": "n3w-Secret!999"},
        )
        assert change.status_code == 200 and change.json()["error_code"] == 0
        still_valid = c.get("/auth/me", headers=auth)
        assert still_valid.status_code == 200 and still_valid.json()["error_code"] == 0
        old_password_login = c.post(
            "/auth/login", json={"username": user["username"], "password": PASSWORD}
        )
        assert old_password_login.status_code == 401
        relogin = c.post(
            "/auth/login", json={"username": user["username"], "password": "n3w-Secret!999"}
        )
        assert relogin.status_code == 200 and relogin.json()["error_code"] == 0


def test_03_catalogs():
    with _client() as c:
        user = _register(c, "cat")
        auth = {"Authorization": f"Bearer {user['token']}"}
        agents = c.get("/agentic/agents", headers=auth).json()
        assert agents["error_code"] == 0
        agent_ids = json.dumps(agents["response"])
        assert "builtin:demo" in agent_ids
        assert "builtin:rag" in agent_ids
        tools = c.get("/agentic/tool-catalog", headers=auth).json()
        assert tools["error_code"] == 0


def test_04_run_multiturn_and_lifecycle():
    """单轮文本流 → 服务端权威多轮 → 列表/详情/历史 → 删除 → 404。"""
    with _client() as c:
        user = _register(c, "run")
        token = user["token"]
        auth = {"Authorization": f"Bearer {token}"}
        thread_id = uuid.uuid4().hex

        # 第一轮：文本增量 + 正常收口
        q1 = "你好，请用一句话介绍你自己，不要使用任何工具。"
        events = _run_sse(c, token, thread_id, q1)
        types = _types(events)
        assert types[0] == "RUN_STARTED", types
        assert "RUN_FINISHED" in types, types
        assert "RUN_ERROR" not in types, events
        answer1 = _text_of(events)
        assert len(answer1) > 0, events

        # 会话出现于列表；详情与历史可读
        assert _find_thread_in_list(c, token, thread_id) is not None
        detail = c.get(f"/agentic/conversation/{thread_id}", headers=auth).json()
        assert detail["error_code"] == 0
        hist = _history(c, token, thread_id)
        assert len(_all_messages(hist)) >= 2, hist
        assert {m.get("role") for m in _all_messages(hist)} == {"user", "assistant"}

        # 第二轮（多轮）：服务端从 DB 重建上下文，能引用上一轮。
        # ag-ui 客户端契约：请求携带完整本地历史投影（服务端只取末条作提问，
        # 载荷长度用于区分普通续聊与重试/编辑——只发末条会被判为编辑）
        q2 = "我上一句话问了你什么？请原样复述我的问题，不要使用任何工具。"
        events2 = _run_sse_history(
            c, token, thread_id,
            [(q1, "user"), (answer1, "assistant"), (q2, "user")],
        )
        types2 = _types(events2)
        assert "RUN_FINISHED" in types2, events2
        assert "RUN_ERROR" not in types2, events2
        answer2 = _text_of(events2)
        assert "介绍" in answer2 or "自己" in answer2, answer2

        # 历史增长（两个轮次）
        hist2 = _history(c, token, thread_id)
        assert len(_turns(hist2)) == 2, hist2

        # 删除后详情 404、列表不再包含
        deleted = c.delete(f"/agentic/conversation/{thread_id}", headers=auth)
        assert deleted.status_code == 200 and deleted.json()["error_code"] == 0
        assert c.get(f"/agentic/conversation/{thread_id}", headers=auth).status_code == 404
        assert _find_thread_in_list(c, token, thread_id) is None


def test_05_run_cancel():
    """长生成中途显式取消：流即断、轮次落 CANCELED、半截消息不落历史。"""
    with _client() as c:
        user = _register(c, "cancel")
        token = user["token"]
        thread_id = uuid.uuid4().hex
        payload = {
            "threadId": thread_id,
            "runId": uuid.uuid4().hex,
            "messages": [
                {
                    "id": uuid.uuid4().hex,
                    "role": "user",
                    "content": "请写一篇约1500字的科幻短篇小说，从正文开始，不要停顿。",
                }
            ],
        }

        collected: list[bytes] = []
        with c.stream(
            "POST",
            "/agentic/run",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=RUN_TIMEOUT,
        ) as resp:
            assert resp.status_code == 200
            canceled = False
            for chunk in resp.iter_bytes():
                collected.append(chunk)
                # 取消时机：RUN_STARTED 先于会话落库（过早 404）；推理帧也是
                # 帧级检查的覆盖对象，帧≥5（思考早期）即取消——生成窗口最大
                if not canceled and b"".join(collected).count(b"data: ") >= 5:
                    cancel = c.post(
                        "/agentic/run/cancel",
                        json={"threadId": thread_id},
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    canceled = True
                    assert cancel.status_code == 200 and cancel.json()["error_code"] == 0
        # 流应很快收口（帧级取消检查 ≤0.5s + 生成中的下一帧），不待超时
        stream_text = b"".join(collected).decode()
        assert "RUN_STARTED" in stream_text
        assert "RUN_FINISHED" not in stream_text, stream_text[-500:]

        # 轮次状态机落 canceled（历史 items 的 turn.status；半截消息不落库）
        def _canceled() -> bool:
            hist = _history(c, token, thread_id)
            return any(turn.get("status") == "canceled" for turn in _turns(hist)) and (
                {m.get("role") for m in _all_messages(hist)} == {"user"}
            )

        assert _wait_for(_canceled, timeout=20.0, interval=1.0), "轮次未在时限内落 canceled"
        # 取消后同会话可继续（服务端权威回放跳过 canceled 轮）
        events_after = _run_sse(c, token, thread_id, "回复\"继续\"两个字即可。")
        assert "RUN_FINISHED" in _types(events_after), events_after


def test_06_conversation_isolation():
    """归属隔离：B 访问/删除 A 的会话一律 404，不泄露存在性。"""
    with _client() as c:
        user_a = _register(c, "iso_a")
        user_b = _register(c, "iso_b")
        thread_id = uuid.uuid4().hex
        events = _run_sse(c, user_a["token"], thread_id, "就说\"好\"一个字即可。")
        assert "RUN_FINISHED" in _types(events), events

        auth_b = {"Authorization": f"Bearer {user_b['token']}"}
        assert c.get(f"/agentic/conversation/{thread_id}", headers=auth_b).status_code == 404
        assert c.get(f"/agentic/conversation/{thread_id}/history", headers=auth_b).status_code == 404
        assert c.delete(f"/agentic/conversation/{thread_id}", headers=auth_b).status_code == 404
        # A 自己仍可读
        auth_a = {"Authorization": f"Bearer {user_a['token']}"}
        assert c.get(f"/agentic/conversation/{thread_id}", headers=auth_a).json()["error_code"] == 0


def test_07_memory_consolidation_and_graph():
    """记忆巩固全链路：轮末抽取→落库→向量（Weaviate+Ollama）→图快照可查。"""
    with _client() as c:
        user = _register(c, "mem")
        token = user["token"]
        thread_id = uuid.uuid4().hex
        marker = f"李雷{uuid.uuid4().hex[:4]}"
        events = _run_sse(
            c, token, thread_id, f"请记住：我叫{marker}，我最喜欢的颜色是蓝色。回复\"已记住\"即可。"
        )
        assert "RUN_FINISHED" in _types(events), events

        # 收尾加工在流闭后的后台线程执行（标题/记忆最终一致），轮询等待
        def _remembered() -> bool:
            graph = c.get("/memory/graph", headers={"Authorization": f"Bearer {token}"}).json()
            assert graph["error_code"] == 0, graph
            dumped = json.dumps(graph["response"], ensure_ascii=False)
            return marker in dumped or "蓝色" in dumped

        assert _wait_for(_remembered, timeout=90.0), "记忆巩固未在时限内落库"

        # 标题也由后台收尾生成
        def _titled() -> bool:
            detail = c.get(f"/agentic/conversation/{thread_id}", headers={"Authorization": f"Bearer {token}"}).json()
            return bool(detail["response"].get("conversation_title"))

        assert _wait_for(_titled, timeout=90.0), "会话标题未被后台收尾生成"


def test_08_knowledge_kb_crud():
    """知识库管理侧 CRUD（不上传文档，不依赖 MinerU）。"""
    with _client() as c:
        user = _register(c, "kb")
        auth = {"Authorization": f"Bearer {user['token']}"}
        kb_name = f"e2e知识库{uuid.uuid4().hex[:6]}"

        created = c.post(
            "/knowledge",
            headers=auth,
            json={"name": kb_name, "description": "e2e 临时库", "isPublic": False},
        )
        assert created.status_code == 200, created.text
        assert created.json()["error_code"] == 0
        kb = created.json()["response"]
        kb_id = kb["id"] if isinstance(kb, dict) and "id" in kb else kb.get("kbId") or kb.get("kb_id")
        assert kb_id

        listed = c.get("/knowledge", headers=auth).json()
        assert listed["error_code"] == 0
        assert kb_name in json.dumps(listed["response"], ensure_ascii=False)

        detail = c.get(f"/knowledge/{kb_id}", headers=auth).json()
        assert detail["error_code"] == 0

        # 他用户不可见（私有库归属）
        stranger = _register(c, "kb_s")
        assert (
            c.get(f"/knowledge/{kb_id}", headers={"Authorization": f"Bearer {stranger['token']}"}).status_code
            == 404
        )

        deleted = c.delete(f"/knowledge/{kb_id}", headers=auth)
        assert deleted.status_code == 200 and deleted.json()["error_code"] == 0
        assert c.get(f"/knowledge/{kb_id}", headers=auth).status_code == 404


def test_09_rag_agent_grounded_answer():
    """builtin:rag 图智能体：无命中时如实兜底（不编造），流正常收口。"""
    with _client() as c:
        user = _register(c, "rag")
        thread_id = uuid.uuid4().hex
        payload = {
            "threadId": thread_id,
            "runId": uuid.uuid4().hex,
            "messages": [
                {
                    "id": uuid.uuid4().hex,
                    "role": "user",
                    "content": "量子纠缠在超导体中的应用是什么？（知识库中没有相关资料时应如实说明）",
                }
            ],
            "forwardedProps": {"agentId": "builtin:rag"},
        }
        events: list[dict] = []
        with c.stream(
            "POST",
            "/agentic/run",
            json=payload,
            headers={"Authorization": f"Bearer {user['token']}"},
            timeout=RUN_TIMEOUT,
        ) as resp:
            assert resp.status_code == 200
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
        types = _types(events)
        assert "RUN_FINISHED" in types, events
        assert "RUN_ERROR" not in types, events


def test_10_unauthorized_rejected():
    """未认证访问受保护面：401。"""
    with _client() as c:
        assert c.get("/agentic/conversation").status_code == 401
        assert c.get("/memory/graph").status_code == 401
        assert c.get("/knowledge").status_code == 401
