"""ConversationRepository 事务边界：三表硬删除 / open_turn 单事务（含回滚）。"""

from uuid import uuid4

import pytest

from sqlmodel import Session, func, select

from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticMessageRole,
    AgenticMessageType,
)
from app.adapters.persistence.conversation_repository import ConversationRepository
from conftest import TEST_USER_ID


def make_conversation(engine, thread_id, title="标题") -> None:
    with Session(engine) as session:
        session.add(
            AgenticConversation(
                user_id=TEST_USER_ID,
                thread_id=thread_id,
                agentic_id="builtin:demo",
                conversation_title=title,
            )
        )
        session.commit()


def make_turn(engine, thread_id, turn_id, turn_num) -> None:
    with Session(engine) as session:
        session.add(
            AgenticConversationTurn(
                thread_id=thread_id, run_id=str(uuid4()), turn_id=turn_id, turn_num=turn_num
            )
        )
        session.commit()


def make_message(engine, thread_id, turn_id, sequence_num) -> None:
    with Session(engine) as session:
        session.add(
            AgenticConversationMessage(
                thread_id=thread_id,
                turn_id=turn_id,
                message_id=uuid4(),
                sequence_num=sequence_num,
                role=AgenticMessageRole.USER,
                message_type=AgenticMessageType.MESSAGE,
                content=[{"type": "text", "text": "hello"}],
                token_usage={},
                latency_ms=0,
            )
        )
        session.commit()


def seed_thread(engine, thread_id) -> None:
    """1 会话 + 2 轮次 + 每轮 2 条消息。"""
    make_conversation(engine, thread_id)
    for turn_num in range(2):
        turn_id = uuid4()
        make_turn(engine, thread_id, turn_id, turn_num)
        make_message(engine, thread_id, turn_id, turn_num * 2)
        make_message(engine, thread_id, turn_id, turn_num * 2 + 1)


def count_rows(engine, model, thread_id) -> int:
    with Session(engine) as session:
        return session.exec(
            select(func.count()).select_from(model).where(model.thread_id == thread_id)
        ).one()


def test_delete_removes_all_thread_rows_only(engine):
    repo = ConversationRepository(engine=engine)
    thread_a, thread_b = uuid4(), uuid4()
    seed_thread(engine, thread_a)
    seed_thread(engine, thread_b)

    deleted = repo.delete_conversation(thread_id=thread_a, user_id=TEST_USER_ID)

    assert deleted is not None
    assert deleted.thread_id == thread_a
    for model in (AgenticConversation, AgenticConversationTurn, AgenticConversationMessage):
        assert count_rows(engine, model, thread_a) == 0
    # 对照会话不受影响：1 会话 + 2 轮次 + 4 消息
    assert count_rows(engine, AgenticConversation, thread_b) == 1
    assert count_rows(engine, AgenticConversationTurn, thread_b) == 2
    assert count_rows(engine, AgenticConversationMessage, thread_b) == 4


def test_delete_missing_thread_returns_none(engine):
    repo = ConversationRepository(engine=engine)
    assert repo.delete_conversation(thread_id=uuid4(), user_id=TEST_USER_ID) is None


def test_recreate_after_delete_starts_clean(engine):
    repo = ConversationRepository(engine=engine)
    thread_id = uuid4()
    seed_thread(engine, thread_id)
    assert repo.delete_conversation(thread_id=thread_id, user_id=TEST_USER_ID) is not None

    # 同 thread_id 重新开轮（get-or-create）：得到全新空会话 + 仅本轮次/消息（无残留）
    conversation, turn = repo.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, agentic_id="builtin:demo",
        rebind=False, run_id="run-1", turn_id=uuid4(), parent_turn_id=None,
        attempt_no=1, content=[{"type": "text", "text": "hello"}],
    )
    assert conversation.conversation_title == ""
    assert turn.turn_num == 0
    assert count_rows(engine, AgenticConversationTurn, thread_id) == 1
    assert count_rows(engine, AgenticConversationMessage, thread_id) == 1


def test_open_turn_atomic_rollback_leaves_no_partial_state(engine):
    """单事务口径：轮次落库失败（turn_id 唯一约束冲突）→ 会话一并回滚。

    open_turn 是开轮全部写的原子边界：中途炸开时不允许残留「有会话无轮次 /
    有轮次无用户消息」的半截聚合。"""
    repo = ConversationRepository(engine=engine)
    thread_id = uuid4()
    turn_id = uuid4()
    make_turn(engine, thread_id, turn_id, turn_num=0)  # 预置同 turn_id 行

    with pytest.raises(Exception):  # turn 建行触发 UNIQUE 冲突，commit 永不发生
        repo.open_turn(
            user_id=TEST_USER_ID, thread_id=thread_id, agentic_id="builtin:demo",
            rebind=False, run_id="run-2", turn_id=turn_id, parent_turn_id=None,
            attempt_no=1, content=[{"type": "text", "text": "hello"}],
        )

    assert count_rows(engine, AgenticConversation, thread_id) == 0
    assert count_rows(engine, AgenticConversationMessage, thread_id) == 0
