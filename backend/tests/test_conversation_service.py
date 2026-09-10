"""ConversationService 领域契约：存在性异常、开轮写路径、用量聚合。

纯单测：真实 ConversationRepository + 临时 SQLite（conftest 的 engine fixture）。
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.exceptions import ConversationNotFoundError
from app.models.domain.agentic import (
    AgenticConversation,
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticMessageRole,
    AgenticMessageType,
    AgenticTurnStatus,
)
from app.packages.signal.memory_signal_store import InMemorySignalStore
from app.adapters.persistence.conversation_repository import ConversationRepository
from app.domain.conversation.conversation_service import ConversationService


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


def _text(text):
    """纯文本请求的存储形态 content 数组（RunMessage.storage_content 的产物）。"""
    return [{"type": "text", "text": text}]


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
    conversation, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-1", content=_text("你好"))

    assert conversation.agentic_id == "builtin:demo"
    # 活跃叶子语义：开轮不推进指针，完成才推进（重试失败保留旧叶子）
    assert conversation.current_turn_id is None
    assert AgenticTurnStatus(turn.status) == AgenticTurnStatus.RUNNING
    assert turn.parent_turn_id is None
    assert turn.attempt_no == 1

    service.complete_turn(turn)
    with Session(engine) as session:
        conversation_row = session.exec(
            select(AgenticConversation).where(AgenticConversation.thread_id == thread_id)
        ).first()
    assert conversation_row.current_turn_id == turn.turn_id

    with Session(engine) as session:
        messages = session.exec(
            select(AgenticConversationMessage).where(AgenticConversationMessage.thread_id == thread_id)
        ).all()
    assert len(messages) == 1
    assert messages[0].sequence_num == 0
    assert messages[0].content == [{"type": "text", "text": "你好"}]


def test_open_turn_agent_binding_switch(service, engine):
    """显式 agentId：新建绑定、缺省沿用、显式不同切换（前端选择智能体的落库口径）。"""
    thread_id = uuid4()

    # 新建会话：显式指定即绑定
    conversation, _ = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-1", content=_text("你好"), agent_id="builtin:rag",
    )
    assert conversation.agentic_id == "builtin:rag"

    # 缺省：沿用现有绑定，不改写
    conversation, _ = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-2", content=_text("继续"))
    assert conversation.agentic_id == "builtin:rag"

    # 显式切换：本轮即生效并落库
    conversation, _ = service.open_turn(
        user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-3", content=_text("换人"), agent_id="builtin:demo",
    )
    assert conversation.agentic_id == "builtin:demo"
    with Session(engine) as session:
        row = session.exec(
            select(AgenticConversation).where(AgenticConversation.thread_id == thread_id)
        ).first()
        assert row.agentic_id == "builtin:demo"


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

    service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-3", content=_text("你好"))
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
    _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=uuid4(), run_id="run-3", content=_text("你好"))

    service.cancel_turn(turn)

    assert AgenticTurnStatus(turn.status) == AgenticTurnStatus.CANCELED
    with Session(engine) as session:
        row = session.exec(
            select(AgenticConversationTurn).where(AgenticConversationTurn.turn_id == turn.turn_id)
        ).one()
    assert AgenticTurnStatus(row.status) == AgenticTurnStatus.CANCELED


def test_record_turn_usage_accumulates_with_whitelist(service, engine):
    _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=uuid4(), run_id="run-2", content=_text("你好"))
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


# ---------------------------------------------------------------- 历史出口双内容
def _tool_rows(thread_id, turn_id, seq_start):
    """一行带 display_content 的 TOOL_RESULT（双内容契约）+ 一行不带（存量形态）。"""
    def row(message_id, tool_call_id, content_part, seq):
        return AgenticConversationMessage(
            thread_id=thread_id,
            turn_id=turn_id,
            message_id=message_id,
            parent_message_id=None,
            sequence_num=seq,
            role=AgenticMessageRole.TOOL,
            message_type=AgenticMessageType.TOOL_RESULT,
            content=[content_part],
            token_usage={},
            latency_ms=0,
        )

    return [
        row(uuid4(), "call-1", {
            "type": "tool_result", "tool_call_id": "call-1",
            "content": "真实检索JSON（含出处）", "display_content": "1. 展示摘要",
        }, seq_start),
        row(uuid4(), "call-2", {
            "type": "tool_result", "tool_call_id": "call-2",
            "content": "普通工具结果",
        }, seq_start + 1),
    ]


def test_history_replaces_tool_result_with_display_content(service):
    """历史出口：带 display_content 的 TOOL_RESULT 行换成展示版、键不外泄，
    真实结果不经前端接口出去；无 display_content 的行原样。"""
    thread_id = uuid4()
    _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-h", content=_text("问"))
    service.conversation_repo.store_conversation_messages(_tool_rows(thread_id, turn.turn_id, 1))
    # 历史集合只含 COMPLETED 轮次（作废/进行中不下发），先收口再读
    service.complete_turn(turn)

    items = service.list_history_messages(user_id=TEST_USER_ID, thread_id=thread_id)["items"]
    parts = [
        m["content"][0]
        for t in items for m in t["messages"]
        if m["message_type"] == AgenticMessageType.TOOL_RESULT
    ]
    assert parts[0]["content"] == "1. 展示摘要"
    assert "真实检索JSON" not in parts[0]["content"] and "display_content" not in parts[0]
    assert parts[1] == {"type": "tool_result", "tool_call_id": "call-2", "content": "普通工具结果"}


def test_replay_history_keeps_real_tool_result_content(service):
    """LLM 回放不受展示视图影响：ToolMessage 仍是真实 content（与模型当时所见一致）。"""
    from langchain.messages import AIMessage, ToolMessage

    thread_id = uuid4()
    _, turn = service.open_turn(user_id=TEST_USER_ID, thread_id=thread_id, run_id="run-r", content=_text("问"))
    rows = _tool_rows(thread_id, turn.turn_id, 2)
    rows.insert(0, AgenticConversationMessage(
        thread_id=thread_id,
        turn_id=turn.turn_id,
        message_id=uuid4(),
        parent_message_id=None,
        sequence_num=1,
        role=AgenticMessageRole.ASSISTANT,
        message_type=AgenticMessageType.TOOL_CALL,
        content=[{"type": "tool_call", "tool_call_id": "call-1", "name": "knowledge_search", "args": {}}],
        token_usage={},
        latency_ms=0,
    ))
    service.conversation_repo.store_conversation_messages(rows)
    service.complete_turn(turn)

    history = service.replay_history(thread_id=thread_id, base_turn_id=turn.turn_id)
    tool_messages = [m for m in history if isinstance(m, ToolMessage)]
    # call-2 无配对 TOOL_CALL，按契约跳过孤儿结果；call-1 用真实 content（非展示版）
    assert [m.content for m in tool_messages] == ["真实检索JSON（含出处）"]
    ai = next(m for m in history if isinstance(m, AIMessage))
    assert ai.tool_calls[0]["id"] == "call-1"
