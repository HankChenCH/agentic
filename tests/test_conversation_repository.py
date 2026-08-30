"""ConversationRepository.delete_conversation：三表硬删除与 None 语义。"""

from uuid import uuid4

from sqlmodel import Session, func, select

from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticMessageRole,
    AgenticMessageType,
)
from app.repositories.conversation_repository import ConversationRepository
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

    # 同 thread_id 重新 get-or-create：得到全新空会话（无残留轮次/消息）
    recreated = repo.init_conversation(user_id=TEST_USER_ID, thread_id=thread_id, agentic_id="builtin:demo")
    assert recreated.conversation_title == ""
    assert count_rows(engine, AgenticConversationTurn, thread_id) == 0
    assert count_rows(engine, AgenticConversationMessage, thread_id) == 0
