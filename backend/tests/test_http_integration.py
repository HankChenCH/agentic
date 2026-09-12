"""首个真实 HTTP 集成测试：真实 ``create_app()`` + TestClient 全链路走通。

覆盖两条线：
1. 主流程：注册 → 登录 → run（SSE）→ 重试出兄弟分支 → activate 切换活跃叶子
   → replay 只含活跃链（history 派生链 / 服务层 replay_history / 后续 run 的
   真实 LLM 上下文三重断言）；
2. 鉴权 sweep：除公开路由外，全部业务路由无 token 一律 401 + error_code=5002；
   另有 refresh 旋转/登出生命周期与 stats 冒烟。

装配套路（env 就位 / 迁移建表 / 容器 override / signal 替身）已提取到
http_test_kit.py 供多个集成测试模块共用；本模块 ``import`` 顺序保持
kit → app 的约束。
"""

import asyncio
import re
from uuid import UUID, uuid4


# 必须先于任何 app.* 导入（env 就位 + 配置缓存清理，见 kit 模块 docstring）
import http_test_kit  # noqa: E402
from http_test_kit import (  # noqa: E402
    _agent,
    _PUBLIC_PATHS,
    active_chain_of,
    api_routes,
    history_of,
    new_username,
    post_run,
    register_and_login,
    sse_events,
    text_of_message,
)

from app.domain.conversation.conversation_service import (  # noqa: E402
    ConversationService,
)

# module 级 TestClient fixture：模块属性重导出供 pytest 按名发现（不能用
# from-import——函数参数 `client` 会被 ruff F811 判成重定义）
client = http_test_kit.client


def replay_text_of(messages):
    return "\n".join(str(m.content) for m in messages)


def test_run_retry_activate_replay_keeps_only_active_chain(client):
    """主流程：run → 重试出兄弟分支 → activate 切回旧轮 → replay 只含活跃链。"""
    headers, token = register_and_login(client, new_username())
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
    headers, token = register_and_login(client, new_username())
    thread_id = uuid4()

    _agent.answer = "第一次回答"
    post_run(client, headers, thread_id, "run-dup", "问题")
    assert history_of(client, token, thread_id)["total"] == 1

    _agent.answer = "不应出现的重复回答"
    resp = client.post(
        "/agentic/run",
        headers=headers,
        json=http_test_kit.run_request(thread_id, "run-dup", "问题"),
    )
    assert resp.status_code == 200
    events = sse_events(resp.text)
    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[1]["message"] == "请勿重复提交：相同请求已在本会话中提交过"
    # 轮次与回答都不变：重复请求既没开新轮次，也没触发 agent 二次执行
    history = history_of(client, token, thread_id)
    assert history["total"] == 1
    assert _agent.contexts[-1].messages[-1].content == "问题"


def test_all_business_routes_require_auth(client):
    """鉴权 sweep：除公开路由外，全部业务路由无 token 一律 401 + error_code=5002。

    路由级 Depends(require_user) 在路径/请求体解析之前短路，路径参数与空 JSON
    体不会改变响应（401 先于 422）。"""
    checked = []
    for route in api_routes(client.app.routes):
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


def test_refresh_rotation_and_logout_lifecycle(client):
    """refresh 旋转/复用检测/登出走真实 HTTP + 登记表（临时 SQLite 全迁移）。

    旋转 → 宽限期内重放旧票 401 且族存活 → 登出吊销族 → 族内票全失效 →
    重新登录恢复（新族）。未鉴权 logout 的 401 口径由鉴权 sweep 覆盖。"""
    headers, _ = register_and_login(client, "refresh_user")
    login = client.post(
        "/auth/login", json={"username": "refresh_user", "password": "password123"}
    )
    refresh_token = login.json()["response"]["refresh_token"]

    # 旋转：旧票换新对
    rotated = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert rotated.status_code == 200, rotated.text
    new_pair = rotated.json()["response"]
    assert new_pair["refresh_token"] != refresh_token

    # 宽限期内立即重放旧票：401 同口径，不连坐（多标签页良性竞态）
    replay = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert replay.status_code == 401 and replay.json()["error_code"] == 5002
    again = client.post("/auth/refresh", json={"refresh_token": new_pair["refresh_token"]})
    assert again.status_code == 200
    live_token = again.json()["response"]["refresh_token"]

    # 登出：吊销该票所在族 → 族内票全部失效
    out = client.post(
        "/auth/logout", headers=headers, json={"refresh_token": live_token}
    )
    assert out.status_code == 200 and out.json()["error_code"] == 0
    assert out.json()["response"] == {"logged_out": True}
    after = client.post("/auth/refresh", json={"refresh_token": live_token})
    assert after.status_code == 401

    # 重新登录恢复（新族，与其他设备的独立语义不冲突）
    relogin = client.post(
        "/auth/login", json={"username": "refresh_user", "password": "password123"}
    )
    assert relogin.status_code == 200 and relogin.json()["error_code"] == 0


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
