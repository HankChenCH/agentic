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
from app.repositories.conversation_repository import ConversationRepository
from app.services.domain.conversation.branching import (
    ancestor_chain,
    detect_retry_of_latest,
    next_attempt_no,
    snapshot_of,
)
from app.services.domain.conversation.conversation_service import ConversationService

from conftest import TEST_USER_ID, StubLoggerFactory


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
    service.conversation_repo.store_conversation_message(AgenticConversationMessage(
        thread_id=turn.thread_id,
        turn_id=turn.turn_id,
        message_id=uuid4(),
        sequence_num=1,
        role=AgenticMessageRole.ASSISTANT,
        message_type=AgenticMessageType.MESSAGE,
        content=[{"type": "text", "text": text}],
        token_usage={},
        latency_ms=0,
    ))
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

    # 重试最新一轮：payload 截断到最后一个提问 [q1, a1, q2]
    assert detect_retry_of_latest(_user_payload("问题一", "回答一", "问题二"), chain) == t2
    # 单轮重试
    assert detect_retry_of_latest(_user_payload("问题一"), chain[:1]) == t1
    # 非重试：新问题内容不等值
    assert detect_retry_of_latest(_user_payload("问题一", "回答一", "新问题"), chain) is None
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


def test_explicit_branch_signal_locates_base_by_message_id(service, engine):
    """显式信号：baseMessageId（目标问题前一条 assistant 行 id）精确定位兄弟基点。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "A1")
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2", content=_text("Q2"))
    _answer(service, t2, "A2")

    # 会话中段重试 Q1：baseMessageId 指向 t1 的 assistant 行（简化：这里即唯一 assistant 行）
    with Session(engine) as session:
        assistant_row = session.exec(
            select(AgenticConversationMessage)
            .where(AgenticConversationMessage.turn_id == t1.turn_id)
            .where(AgenticConversationMessage.role == AgenticMessageRole.ASSISTANT)
        ).first()
    _, t3 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3",
        content=_text("Q1-重试"), branch={"baseMessageId": str(assistant_row.message_id)},
    )
    assert t3.parent_turn_id == t1.parent_turn_id
    assert t3.attempt_no == 2

    # 中段重试完成后，活跃路径经 t3（叶子推进），Q2 子树出局
    _answer(service, t3, "A1-重试")
    conversation = service.describe_conversation(user_id=TEST_USER_ID, thread_id=thread_id)
    assert conversation.current_turn_id == t3.turn_id

    # 非法/跨会话 id 降级为普通续聊
    _, t4 = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r4",
        content=_text("Q4"), branch={"baseMessageId": str(uuid4())},
    )
    assert t4.parent_turn_id == t3.turn_id
    assert t4.attempt_no == 1


def test_history_returns_active_path_plus_incomplete_turns(service, engine):
    """历史集合：被替换的旧 COMPLETED 轮次隐藏，失败/取消轮次保留（标注状态）。"""
    thread_id = uuid4()
    _, t1 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r1", content=_text("Q1"))
    _answer(service, t1, "A1")

    # 失败的第二个提问（悬空 RUNNING 亦同口径，这里用 FAILED）
    _, t2 = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="r2", content=_text("Q2"))
    service.fail_turn(t2)

    # Q1 重试成功 → 旧 t1 出局（payload 截断到目标提问，单轮链即 [Q1]）
    _, retry = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="r3",
        content=_text("Q1"), payload=_user_payload("Q1"),
    )
    _answer(service, retry, "A1-新")

    result = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id)
    items = result["items"]
    ids = [t.turn_id for t in items]
    assert result["total"] == len(items) == 2
    assert retry.turn_id in ids          # 活跃叶子
    assert t1.turn_id not in ids         # 被重试替换：隐藏
    assert t2.turn_id in ids             # 失败轮次：保留并标注
    by_id = {t.turn_id: t for t in items}
    assert AgenticTurnStatus(by_id[t2.turn_id].status) == AgenticTurnStatus.FAILED
    assert AgenticTurnStatus(by_id[retry.turn_id].status) == AgenticTurnStatus.COMPLETED
    # 分支元数据随 model_dump 下发（前端后续可重建分支）
    assert by_id[retry.turn_id].parent_turn_id is None
    assert by_id[retry.turn_id].attempt_no == 2


def test_history_paging_keeps_latest_page_semantics(service, engine):
    """offset/limit 沿用"最新一页"语义：offset=0 取最新 limit 条，items 旧→新。"""
    thread_id = uuid4()
    for i in range(5):
        _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id=f"r{i}", content=_text(f"Q{i}"))
        _answer(service, turn, f"A{i}")

    result = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id, offset=0, limit=2)
    assert result["total"] == 5
    nums = [t.turn_num for t in result["items"]]
    assert nums == [3, 4]  # 最新的 2 条，旧→新
