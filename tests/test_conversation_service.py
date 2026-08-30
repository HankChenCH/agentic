"""ConversationService 领域契约：存在性异常、开轮写路径、用量聚合。

纯单测：真实 ConversationRepository + 临时 SQLite（conftest 的 engine fixture）。
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.exceptions import ConversationNotFoundError
from app.models.domain.agentic import (
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticTurnStatus,
)
from app.packages.signal.memory_signal_store import InMemorySignalStore
from app.repositories.conversation_repository import ConversationRepository
from app.services.domain.conversation.conversation_service import ConversationService


from conftest import TEST_USER_ID, StubLoggerFactory


@pytest.fixture()
def service(engine):
    # 只用到 default_agentic_id 一项配置，替身避免加载全量 YAML
    return ConversationService(
        conversation_repo=ConversationRepository(engine=engine),
        app_config=SimpleNamespace(default_agentic_id="builtin:demo"),
        signal_store=InMemorySignalStore(),
        logger_factory=StubLoggerFactory(),
    )


def usage_msg(turn_id, **usage):
    return SimpleNamespace(token_usage=dict(usage))


def test_describe_and_delete_raise_when_missing(service):
    """对齐知识域惯例：存在性校验在领域服务内抛业务异常（端点不再判 None）。"""
    missing = uuid4()
    with pytest.raises(ConversationNotFoundError):
        service.describe_conversation(user_id=TEST_USER_ID, thread_id=missing)
    with pytest.raises(ConversationNotFoundError):
        service.delete_conversation(user_id=TEST_USER_ID, thread_id=missing)


def test_open_turn_persists_conversation_turn_and_user_message(service, engine):
    thread_id = uuid4()
    conversation, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-1", query="你好")

    assert conversation.agentic_id == "builtin:demo"
    assert conversation.current_turn_id == turn.turn_id
    assert AgenticTurnStatus(turn.status) == AgenticTurnStatus.RUNNING

    with Session(engine) as session:
        messages = session.exec(
            select(AgenticConversationMessage).where(AgenticConversationMessage.thread_id == thread_id)
        ).all()
    assert len(messages) == 1
    assert messages[0].sequence_num == 0
    assert messages[0].content == [{"type": "text", "text": "你好"}]


def test_cancel_flag_roundtrip_and_open_turn_clears_it(engine):
    """取消标志 roundtrip；open_turn 防御性清理上轮残留（TTL 前的标志不误杀新一轮）。"""
    store = InMemorySignalStore()
    service = ConversationService(
        conversation_repo=ConversationRepository(engine=engine),
        app_config=SimpleNamespace(default_agentic_id="builtin:demo"),
        signal_store=store,
        logger_factory=StubLoggerFactory(),
    )
    thread_id = uuid4()

    service.cancel_run_flag(thread_id)
    assert service.is_run_canceled(thread_id)

    service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-3", query="你好")
    assert not service.is_run_canceled(thread_id)


def test_is_run_canceled_degrades_when_store_unavailable(engine):
    """宽容策略归领域：信号存储读失败绝不阻断聊天主链路，视为未取消（节流告警）。"""

    class FailingSignalStore(InMemorySignalStore):
        def is_fired(self, key):
            raise RuntimeError("store down")

    service = ConversationService(
        conversation_repo=ConversationRepository(engine=engine),
        app_config=SimpleNamespace(default_agentic_id="builtin:demo"),
        signal_store=FailingSignalStore(),
        logger_factory=StubLoggerFactory(),
    )

    assert service.is_run_canceled(uuid4()) is False


def test_cancel_turn_persists_canceled_status(service, engine):
    """取消轮次：内存对象与库中行都收口为 CANCELED（断连路径的持久化契约）。"""
    _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=uuid4(), run_id="run-3", query="你好")

    service.cancel_turn(turn)

    assert AgenticTurnStatus(turn.status) == AgenticTurnStatus.CANCELED
    with Session(engine) as session:
        row = session.exec(
            select(AgenticConversationTurn).where(AgenticConversationTurn.turn_id == turn.turn_id)
        ).one()
    assert AgenticTurnStatus(row.status) == AgenticTurnStatus.CANCELED


def test_record_turn_usage_accumulates_with_whitelist(service, engine):
    _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=uuid4(), run_id="run-2", query="你好")
    turn.token_usage = {"total_tokens": 100}

    service.record_turn_usage(turn, [
        usage_msg(turn.turn_id, prompt_tokens=10, completion_tokens=20, total_tokens=30),
        usage_msg(turn.turn_id, prompt_tokens=5, total_tokens=7, cached_tokens=99),
    ])

    with Session(engine) as session:
        row = session.exec(
            select(AgenticConversationTurn).where(AgenticConversationTurn.turn_id == turn.turn_id)
        ).one()
    assert row.token_usage == {"prompt_tokens": 15, "completion_tokens": 20, "total_tokens": 137}


def test_management_envelopes_empty_state(service):
    assert service.list_conversations(user_id=TEST_USER_ID, page=1, page_size=10) == {"items": [], "total": 0, "page": 1, "pageSize": 10}
    # 归属校验前置：不存在的会话按 404 处理（与 describe/delete 同口径），而非空列表
    with pytest.raises(ConversationNotFoundError):
        service.list_history_messages(user_id=TEST_USER_ID, thread_id=uuid4())
