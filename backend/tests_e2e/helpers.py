"""e2e 套件共享辅助：客户端、注册、SSE 收集、轮询。

两种入口形态共用同一套用例（见 deploy/e2e-fullstack.sh）：
- 直连：``E2E_BASE_URL=http://127.0.0.1:8000``（API 挂顶级路径）；
- 经 nginx 全栈入口：``E2E_BASE_URL=http://127.0.0.1:8081`` +
  ``E2E_API_PREFIX=/api``（前端 nginx 同源反代，/api 前缀剥离后转发后端）。

nginx 对 /api/auth/ 限流 1r/s + burst 5（deployment-spec §12.3），多用户
注册/登录的套件会被误伤——客户端对 429 做退避重试（网关 429 = 请求未达
后端，重放无副作用）；需要观察原始 429 行为的用例（网关变体断言）用
raw 客户端。
"""

import json
import time
import uuid

import httpx

BASE_URL = __import__("os").environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
API_PREFIX = __import__("os").environ.get("E2E_API_PREFIX", "")
PASSWORD = "e2e-Passw0rd!123"
RUN_TIMEOUT = 180.0

_429_RETRIES = 3
_429_BACKOFF_SECONDS = 1.2


class _Client(httpx.Client):
    """路径前缀改写 + 429 退避的 e2e 客户端。

    ``API_PREFIX`` 非空时所有请求路径前置该前缀（nginx 变体）；429 时按
    退避间隔重试（nginx 429 表示请求被网关拒绝，未达后端，重放安全）。
    """

    def send(self, request, **kwargs):
        if API_PREFIX:
            request.url = request.url.copy_with(path=API_PREFIX + request.url.path)
        for attempt in range(_429_RETRIES + 1):
            resp = super().send(request, **kwargs)
            if resp.status_code != 429 or attempt == _429_RETRIES:
                return resp
            time.sleep(_429_BACKOFF_SECONDS * (attempt + 1))
        return resp


def _client() -> httpx.Client:
    """常规客户端：前缀改写 + 429 退避。"""
    return _Client(base_url=BASE_URL, timeout=30.0)


def raw_client() -> httpx.Client:
    """无改写无重试的裸客户端：网关变体断言原始 429 / 头透传用。"""
    return httpx.Client(base_url=BASE_URL, timeout=30.0)


def api_prefix() -> str:
    return API_PREFIX


def _register(c: httpx.Client, tag: str) -> dict:
    username = f"e2e_{tag}_{uuid.uuid4().hex[:8]}"
    resp = c.post("/auth/register", json={"username": username, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error_code"] == 0
    return {
        "username": username,
        "token": body["response"]["token"],
        "refresh_token": body["response"]["refresh_token"],
        "user": body["response"]["user"],
    }


def _run_sse(
    c: httpx.Client, token: str, thread_id: str, text: str, *, run_id: str | None = None,
    agent_id: str | None = None,
) -> list[dict]:
    """发起一次 agentic run 并收集全部 ag-ui 事件（SSE data 帧解析）。"""
    events: list[dict] = []
    payload = {
        "threadId": thread_id,
        "runId": run_id or uuid.uuid4().hex,
        "messages": [{"id": uuid.uuid4().hex, "role": "user", "content": text}],
    }
    if agent_id:
        payload["forwardedProps"] = {"agentId": agent_id}
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
