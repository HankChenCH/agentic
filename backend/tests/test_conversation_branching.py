"""轮次分支建模：基点定位（显式信号/自动检测）、兄弟序号、活跃路径回放与历史过滤。

纯单测：真实 ConversationRepository/ConversationService + 临时 SQLite
（conftest 的 engine fixture），覆盖重试场景的端到端领域契约。
"""

import pytest
from uuid import uuid4

from sqlmodel import Session, select

from app.models.domain.agentic import (
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticMessageRole,
    AgenticMessageType,
    AgenticTurnStatus,
)
from app.exceptions import ConversationNotFoundError, TurnNotAtTipError
from app.adapters.persistence.conversation_repository import ConversationRepository
from app.domain.conversation.branching import (
    ancestor_chain,
    detect_retry_of_latest,
    next_attempt_no,
    snapshot_of,
)
from app.domain.conversation.conversation_service import ConversationService

from conftest import TEST_USER_ID, OTHER_USER_ID, StubLoggerFactory


def _text(text):
    """纯文本请求的存储形态 content 数组（RunMessage.storage_content 的产物）。"""
    return [{"type": "text", "text": text}]


def _user_payload(*texts):
    """run 请求的 (role, content) 投影：user/assistant 交替的简化构造。"""
    out = []
    for i, t in enumerate(texts):
        role = "user" if i % 2 == 0 else "assistant"
        out.append((role, _text(t)))
    return out


@pytest.fixture()
def service(engine):
    from types import SimpleNamespace

    from app.packages.signal.memory_signal_store import InMemorySignalStore

    return ConversationService(
        conversation_repo=ConversationRepository(engine=engine),
        app_config=SimpleNamespace(default_agentic_id="builtin:demo"),
        signal_store=InMemorySignalStore(),
        logger_factory=StubLoggerFactory(),
    )


def _answer(service, turn, text):
    """给轮次补一条 assistant MESSAGE 行并完成收口（模拟一次成功的 run）。"""
    service.conversation_repo.store_conversation_messages([AgenticConversationMessage(
        thread_id=turn.thread_id,
        turn_id=turn.turn_id,
        message_id=uuid4(),
        sequence_num=1,
        role=AgenticMessageRole.ASSISTANT,
        message_type=AgenticMessageType.MESSAGE,
        content=[{"type": "text", "text": text}],
        token_usage={},
        latency_ms=0,
    )])
    service.complete_turn(turn)


def _question_of(engine, turn):
    with Session(engine) as session:
        row = session.exec(
            select(AgenticConversationMessage)
            .where(AgenticConversationMessage.turn_id == turn.turn_id)
            .where(AgenticConversationMessage.sequence_num == 0)
        ).first()
    return row.content if row else None


def _assistant_text_rows(engine, turn):
    with Session(engine) as session:
        rows = session.exec(
            select(AgenticConversationMessage)
            .where(AgenticConversationMessage.turn_id == turn.turn_id)
            .where(AgenticConversationMessage.message_type == AgenticMessageType.MESSAGE)
            .where(AgenticConversationMessage.role == AgenticMessageRole.ASSISTANT)
        ).all()
    return [r.content[0]["text"] for r in sorted(rows, key=lambda m: m.sequence_num)]


# ---------------------------------------------------------------------------
# branching 纯函数
# ---------------------------------------------------------------------------


def _snap(turn_id, parent=None, attempt=1, status=AgenticTurnStatus.COMPLETED, content=None):
    return snapshot_of(
        AgenticConversationTurn(
            thread_id=uuid4(), run_id="r", turn_id=turn_id, turn_num=0,
            parent_turn_id=parent, attempt_no=attempt, status=status,
        ),
        content,
    )


def test_ancestor_chain_walks_to_root_and_reverses():
    t1, t2, t3 = uuid4(), uuid4(), uuid4()
    by_id = {
        t1: _snap(t1, parent=None),
        t2: _snap(t2, parent=t1),
        t3: _snap(t3, parent=t2),
    }
    chain = ancestor_chain(t3, by_id)
    assert [s.turn_id for s in chain] == [t1, t2, t3]


def test_ancestor_chain_tolerates_dangling_parent_and_missing_root():
    t1 = uuid4()
    by_id = {t1: _snap(t1, parent=uuid4())}  # parent 指向不存在的轮次：按根截断
    assert [s.turn_id for s in ancestor_chain(t1, by_id)] == [t1]
    assert ancestor_chain(None, by_id) == []


def test_next_attempt_no_counts_siblings_only():
    parent = uuid4()
    other = uuid4()
    snaps = [
        _snap(uuid4(), parent=parent, attempt=1),
        _snap(uuid4(), parent=parent, attempt=3),
        _snap(uuid4(), parent=other, attempt=9),
    ]
    assert next_attempt_no(parent, snaps) == 4
    assert next_attempt_no(other, snaps) == 10
    assert next_attempt_no(uuid4(), snaps) == 1


def test_detect_retry_of_latest_hits_and_misses():
    q1, q2 = _text("问题一"), _text("问题二")
    t1, t2 = uuid4(), uuid4()
    chain = [_snap(t1, content=q1), _snap(t2, content=q2)]

    # 重新生成：payload 截断到最后一个提问 [q1, a1, q2]，末位等值
    assert detect_retry_of_latest(_user_payload("问题一", "回答一", "问题二"), chain) == t2
    # 编辑最新问题：前缀等值、末位不同（q2 的编辑版）→ 同样命中，兄弟变体
    assert detect_retry_of_latest(_user_payload("问题一", "回答一", "新问题二"), chain) == t2
    # 单轮重试/编辑
    assert detect_retry_of_latest(_user_payload("问题一"), chain[:1]) == t1
    assert detect_retry_of_latest(_user_payload("问题一的编辑版"), chain[:1]) == t1
    # 前缀错位：首问就不等值（普通续聊的新提问不在此形态——长度已不同）
    assert detect_retry_of_latest(_user_payload("别的", "回答", "问题二"), chain) is None
    # 长度/结构不符：偶数条、角色不交替、user 数与轮次数不一致
    assert detect_retry_of_latest(_user_payload("问题一", "回答一"), chain) is None
    assert detect_retry_of_latest([("user", q1), ("user", q2)], chain) is None
    assert detect_retry_of_latest(_user_payload("问题一"), chain) is None
    assert detect_retry_of_latest([], chain) is None
    assert detect_retry_of_latest(_user_payload("问题一"), []) is None


# ---------------------------------------------------------------------------
# 领域服务：开轮分支定位 / 指针生命周期 / 回放 / 历史
# ---------------------------------------------------------------------------


def test_linear_turns_chain_via_active_leaf(service, engine):
    """普通续聊：parent = 活跃叶子，attempt=1，turn_num 插入序递增。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "A1")
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2", content=_text("Q2"))

    assert t1.parent_turn_id is None and t1.attempt_no == 1 and t1.turn_num == 0
    assert t2.parent_turn_id == t1.turn_id and t2.attempt_no == 1 and t2.turn_num == 1


def test_retry_auto_detected_creates_sibling_attempt(service, engine):
    """前端零改动的自动检测：payload 截断到最后提问 → 新轮次为被重试轮次的兄弟。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "A1")

    _, retry = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2",
        content=_text("Q1"), payload=_user_payload("Q1"),
    )
    assert retry.parent_turn_id == t1.parent_turn_id  # 兄弟：共享同一分支基点（None）
    assert retry.attempt_no == 2
    assert retry.turn_num == 1


def test_retry_failure_keeps_previous_answer_active(service, engine):
    """重试失败（fail/cancel 不推进叶子）：活跃路径仍是原轮次。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "A1")
    _, retry = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2",
        content=_text("Q1"), payload=_user_payload("Q1"),
    )
    service.fail_turn(retry)

    conversation = service.describe_conversation(user_id=TEST_USER_ID, thread_id=thread_id)
    assert conversation.current_turn_id == t1.turn_id
    # 再重试：兄弟间 attempt 继续累加
    _, retry2 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3",
        content=_text("Q1"), payload=_user_payload("Q1"),
    )
    assert retry2.attempt_no == 3


def test_retry_completion_replaces_answer_in_llm_replay(service, engine):
    """核心契约：重试完成后，被替换的旧答案不再进 LLM 上下文。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "旧答案")

    # 重试进行中（base=RUNNING）：上下文只有分支点之前的路径，旧答案已排除
    _, retry = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2",
        content=_text("Q1"), payload=_user_payload("Q1"),
    )
    mid = service.replay_history(thread_id=thread_id, base_turn_id=retry.turn_id)
    assert mid == []

    _answer(service, retry, "新答案")
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3", content=_text("Q2"))
    history = service.replay_history(thread_id=thread_id, base_turn_id=t2.turn_id)
    texts = [m.content for m in history if m.type == "ai" and isinstance(m.content, str)]
    assert "旧答案" not in texts
    assert "新答案" in texts
    # 用户问题序列只出现一次 Q1
    user_texts = [m.content for m in history if m.type == "human"]
    assert user_texts == ["Q1"]


def test_replay_pairs_tool_chain_and_skips_a2ui_custom_rows(service, engine):
    """回放还原工具调用链（TOOL_CALL→AIMessage(tool_calls) + TOOL_RESULT→ToolMessage
    成对），CUSTOM（A2UI 卡片载荷）不回灌——UI 是表现层，不进模型上下文。"""
    from langchain_core.messages import ToolMessage

    thread_id = uuid4()
    _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("天气如何"))
    service.conversation_repo.store_conversation_messages([
        AgenticConversationMessage(
            thread_id=thread_id, turn_id=turn.turn_id, message_id=uuid4(),
            sequence_num=1, role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.TOOL_CALL,
            content=[{"type": "tool_call", "tool_call_id": "c1", "name": "get_weather", "args": {"city": "中山"}}],
            token_usage={}, latency_ms=0,
        ),
        AgenticConversationMessage(
            thread_id=thread_id, turn_id=turn.turn_id, message_id=uuid4(),
            sequence_num=2, role=AgenticMessageRole.TOOL,
            message_type=AgenticMessageType.TOOL_RESULT,
            content=[{"type": "tool_result", "tool_call_id": "c1", "content": '{"city": "中山"}'}],
            token_usage={}, latency_ms=0,
        ),
        AgenticConversationMessage(
            thread_id=thread_id, turn_id=turn.turn_id, message_id=uuid4(),
            sequence_num=3, role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.CUSTOM,
            content=[{"type": "custom", "name": "a2ui", "value": [{"version": "v0.9", "createSurface": {}}]}],
            token_usage={}, latency_ms=0,
        ),
        AgenticConversationMessage(
            thread_id=thread_id, turn_id=turn.turn_id, message_id=uuid4(),
            sequence_num=4, role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.MESSAGE,
            content=[{"type": "text", "text": "中山今天晴朗。"}],
            token_usage={}, latency_ms=0,
        ),
    ])
    service.complete_turn(turn)
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2", content=_text("Q2"))

    history = service.replay_history(thread_id=thread_id, base_turn_id=t2.turn_id)
    tool_messages = [m for m in history if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1 and tool_messages[0].tool_call_id == "c1"
    assert [m for m in history if getattr(m, "tool_calls", None)]  # AIMessage(tool_calls) 成对回放
    assert "中山今天晴朗。" in [m.content for m in history if m.type == "ai" and isinstance(m.content, str)]
    # a2ui 消息数组不回灌上下文
    assert not any("createSurface" in str(m.content) for m in history)


def test_replay_skips_thought_rows(service, engine):
    """THOUGHT（reasoning）行不回放：思考文本不进 LLM 上下文（docstring 契约，
    厂商 API 不接受历史 reasoning 注入），仅 MESSAGE 行照常回放。"""
    thread_id = uuid4()
    _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    service.conversation_repo.store_conversation_messages([
        AgenticConversationMessage(
            thread_id=thread_id, turn_id=turn.turn_id, message_id=uuid4(),
            sequence_num=1, role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.THOUGHT,
            content=[{"type": "text", "text": "让我想想这个问题的思路…"}],
            token_usage={}, latency_ms=0,
        ),
        AgenticConversationMessage(
            thread_id=thread_id, turn_id=turn.turn_id, message_id=uuid4(),
            sequence_num=2, role=AgenticMessageRole.ASSISTANT,
            message_type=AgenticMessageType.MESSAGE,
            content=[{"type": "text", "text": "答案是 42。"}],
            token_usage={}, latency_ms=0,
        ),
    ])
    service.complete_turn(turn)
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2", content=_text("Q2"))

    history = service.replay_history(thread_id=thread_id, base_turn_id=t2.turn_id)
    ai_texts = [m.content for m in history if m.type == "ai" and isinstance(m.content, str)]
    assert "答案是 42。" in ai_texts
    assert not any("让我想想" in t for t in ai_texts)


def test_explicit_branch_signal_locates_base_by_message_id(service, engine):
    """显式信号：baseMessageId 定位兄弟基点；仅末梢可分支（中段已定型拒绝）。"""
    thread_id = uuid4()
    _, t0 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q0"))
    _answer(service, t0, "A0")
    with Session(engine) as session:
        t0_assistant = session.exec(
            select(AgenticConversationMessage)
            .where(AgenticConversationMessage.turn_id == t0.turn_id)
            .where(AgenticConversationMessage.role == AgenticMessageRole.ASSISTANT)
        ).first()

    # 末梢重新生成：baseMessageId = 叶子轮次自己的 assistant 行 → 兄弟变体
    _, t2 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2",
        content=_text("Q0"), branch={"baseMessageId": str(t0_assistant.message_id)},
    )
    assert t2.parent_turn_id == t0.parent_turn_id
    assert t2.attempt_no == 2

    # 中段节点已定型：沿 t2 续聊后再对 t0（此时已是祖先）重试 → 末梢守卫拒绝
    _answer(service, t2, "A2")
    _, t3 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3", content=_text("Q3"))
    _answer(service, t3, "A3")
    with pytest.raises(TurnNotAtTipError):
        service.open_turn(
            user_id=TEST_USER_ID, thread_id=thread_id, run_id="r4",
            content=_text("Q0-重试"), branch={"baseMessageId": str(t0_assistant.message_id)},
        )

    # 非法/跨会话 id 降级为普通续聊（不拦截）
    _, t4 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r5",
        content=_text("Q4"), branch={"baseMessageId": str(uuid4())},
    )
    assert t4.parent_turn_id == t3.turn_id
    assert t4.attempt_no == 1


def test_activate_turn_moves_leaf_within_tip_fan(service, engine):
    """变体切换：末梢扇形内移动活跃叶子；续聊沿所选分支，LLM 回放随之切换。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "旧答案")
    _, t2 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2",
        content=_text("Q1"), payload=_user_payload("Q1"),
    )
    _answer(service, t2, "新答案")
    assert service.describe_conversation(user_id=TEST_USER_ID, thread_id=thread_id).current_turn_id == t2.turn_id

    # 用户对比后选定旧变体：切换叶子 → 续聊挂在 t1 下、上下文走旧答案分支
    service.activate_turn(user_id=TEST_USER_ID, thread_id=thread_id, turn_id=t1.turn_id)
    assert service.describe_conversation(user_id=TEST_USER_ID, thread_id=thread_id).current_turn_id == t1.turn_id

    _, t3 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3", content=_text("Q2"))
    assert t3.parent_turn_id == t1.turn_id
    _answer(service, t3, "A2-沿旧分支")
    history = service.replay_history(thread_id=thread_id, base_turn_id=t3.turn_id)
    texts = [m.content for m in history if m.type == "ai" and isinstance(m.content, str)]
    assert texts == ["旧答案", "A2-沿旧分支"]

    # 幂等：激活当前叶子无副作用；续聊定型后，旧节点不可再激活（末梢规则）
    service.activate_turn(user_id=TEST_USER_ID, thread_id=thread_id, turn_id=t3.turn_id)
    assert service.describe_conversation(user_id=TEST_USER_ID, thread_id=thread_id).current_turn_id == t3.turn_id
    with pytest.raises(TurnNotAtTipError):
        service.activate_turn(user_id=TEST_USER_ID, thread_id=thread_id, turn_id=t1.turn_id)


def test_activate_turn_rejects_off_tip_and_incomplete(service, engine):
    """末梢守卫：非末梢（祖先）与未完成轮次不可切换；跨会话按不存在处理。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "A1")
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2", content=_text("Q2"))
    _answer(service, t2, "A2")
    _, failed = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3", content=_text("Q3"))
    service.fail_turn(failed)

    # 祖先节点（已定型）拒绝
    with pytest.raises(TurnNotAtTipError):
        service.activate_turn(user_id=TEST_USER_ID, thread_id=thread_id, turn_id=t1.turn_id)
    # 未完成（失败）轮次拒绝
    with pytest.raises(TurnNotAtTipError):
        service.activate_turn(user_id=TEST_USER_ID, thread_id=thread_id, turn_id=failed.turn_id)
    # 跨会话/不存在的轮次按不存在处理
    with pytest.raises(ConversationNotFoundError):
        service.activate_turn(user_id=TEST_USER_ID, thread_id=thread_id, turn_id=uuid4())
    # 他人会话 404（归属校验前置）
    with pytest.raises(ConversationNotFoundError):
        service.activate_turn(user_id=OTHER_USER_ID, thread_id=thread_id, turn_id=t1.turn_id)


def test_edit_latest_question_creates_sibling_attempt(service, engine):
    """复刻线上场景：问 q1 → 问 q2 → 编辑 q2 重跑 → t3 与 t2 并列、父为 t1。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "A1")
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2", content=_text("Q2"))
    _answer(service, t2, "A2")

    # 编辑 t2 的问题重跑：payload 截断到 [q1, a1, q2编辑版]
    _, t3 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3",
        content=_text("Q2-编辑"), payload=_user_payload("Q1", "A1", "Q2-编辑"),
    )
    assert t3.parent_turn_id == t1.turn_id
    assert t3.attempt_no == 2

    _answer(service, t3, "A2-编辑")
    # 编辑完成后活跃叶子推进到 t3，t2 出局
    conversation = service.describe_conversation(user_id=TEST_USER_ID, thread_id=thread_id)
    assert conversation.current_turn_id == t3.turn_id

    # 后续普通提问：上下文走 t1 → t3 活跃路径，被替换的 A2 不再进 LLM
    _, t4 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r4",
        content=_text("Q3"), payload=_user_payload("Q1", "A1", "Q2-编辑", "A2-编辑", "Q3"),
    )
    assert t4.parent_turn_id == t3.turn_id
    history = service.replay_history(thread_id=thread_id, base_turn_id=t4.turn_id)
    texts = [m.content for m in history if m.type == "ai" and isinstance(m.content, str)]
    assert "A2" not in texts and "A2-编辑" in texts

    _answer(service, t4, "A3")
    # 历史展示：t2（被编辑替换的旧 COMPLETED 轮次）隐藏，活跃问答保留
    # （进行中/作废轮次同样不下发，见 test_history_excludes_voided_turns）
    items = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id)["items"]
    ids = {t["turn_id"] for t in items}
    assert t2.turn_id not in ids
    assert {t1.turn_id, t3.turn_id, t4.turn_id} <= ids


def test_history_includes_tip_fan_for_offline_comparison(service, engine):
    """刷新后仍可对比：末梢扇形随历史下发（带 active_turn_id）；定型后收回。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "旧答案")
    _, t2 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2",
        content=_text("Q1"), payload=_user_payload("Q1"),
    )
    _answer(service, t2, "新答案")

    result = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id)
    ids = {t["turn_id"] for t in result["items"]}
    assert result["active_turn_id"] == t2.turn_id
    assert {t1.turn_id, t2.turn_id} <= ids  # 末梢扇形可见，可切换

    # 沿 t2 续聊定型后：扇形槽位退居路径后方，旧变体不再下发
    _, t3 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3", content=_text("Q2"))
    _answer(service, t3, "A2")
    result2 = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id)
    ids2 = {t["turn_id"] for t in result2["items"]}
    assert result2["active_turn_id"] == t3.turn_id
    assert t1.turn_id not in ids2
    assert {t2.turn_id, t3.turn_id} <= ids2


def test_history_excludes_voided_turns(service, engine):
    """历史集合：作废轮次（失败/取消/悬挂 RUNNING）不是活跃节点，一律不下发；
    末梢扇形只保留已完成变体。轮次行本身仍在库中（审计/分支定位不受影响）。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "A1")

    # 失败的第二个提问（悬空 RUNNING 亦同口径，这里用 FAILED）
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2", content=_text("Q2"))
    service.fail_turn(t2)

    # Q1 重试成功 → 末梢变体扇形 = {t1, retry}；t2 失败轮不下发
    _, retry = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3",
        content=_text("Q1"), payload=_user_payload("Q1"),
    )
    _answer(service, retry, "A1-新")

    result = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id)
    items = result["items"]
    ids = {t["turn_id"] for t in items}
    assert result["total"] == len(items) == 2
    assert result["active_turn_id"] == retry.turn_id
    assert retry.turn_id in ids          # 活跃叶子（新变体）
    assert t1.turn_id in ids             # 末梢扇形旧变体：已完成，保留可对比
    assert t2.turn_id not in ids         # 失败轮次：作废，不进历史
    # 库里仍在：审计与分支定位不受展示过滤影响
    with Session(engine) as session:
        row = session.exec(
            select(AgenticConversationTurn).where(AgenticConversationTurn.turn_id == t2.turn_id)
        ).one()
    assert AgenticTurnStatus(row.status) == AgenticTurnStatus.FAILED


def test_history_canceled_root_turn_yields_empty_history(service):
    """首个提问即被取消（无任何 COMPLETED 轮次）：历史为空——作废轮次不充当
    活跃节点，避免"悬空提问 + 已停止占位"被当作正常对话回放。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    service.cancel_turn(t1)

    result = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id)
    assert result["items"] == []
    assert result["total"] == 0
    assert result["active_turn_id"] is None


def test_history_paging_keeps_latest_page_semantics(service, engine):
    """offset/limit 沿用"最新一页"语义：offset=0 取最新 limit 条，items 旧→新。"""
    thread_id = uuid4()
    for i in range(5):
        _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id=f"r{i}", content=_text(f"Q{i}"))
        _answer(service, turn, f"A{i}")

    result = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id, offset=0, limit=2)
    assert result["total"] == 5
    nums = [t["turn_num"] for t in result["items"]]
    assert nums == [3, 4]  # 最新的 2 条，旧→新
