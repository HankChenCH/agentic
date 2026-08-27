"""TurnFinalizer 收尾加工的行为契约：三步各自的容错语义。

纯单测：真实 ConversationRepository + 临时 SQLite（conftest 的 engine fixture），
标题生成 / 记忆 / 日志全替身，直接同步调用 run()。
"""

from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.models.domain.agentic import (
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticMessageRole,
    AgenticMessageType,
)
from app.repositories.conversation_repository import ConversationRepository
from app.services.turn_finalizer import TurnFinalizer


class FakeTitleGenerator:
    def __init__(self, title="新标题", error=None):
        self.title = title
        self.error = error
        self.calls = 0

    def generate(self, query, turn_messages):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.title


class FakeMemory:
    def __init__(self, error=None):
        self.remembered = []
        self._error = error

    def remember(self, **kwargs):
        if self._error is not None:
            raise self._error
        self.remembered.append(kwargs)


class RecordingLogger:
    def __init__(self):
        self.events = []

    def _record(self, level, msg):
        self.events.append((level, msg))

    def debug(self, msg, *args, **kwargs):
        self._record("debug", msg)

    def info(self, msg, *args, **kwargs):
        self._record("info", msg)

    def warning(self, msg, *args, **kwargs):
        self._record("warning", msg)

    def error(self, msg, *args, **kwargs):
        self._record("error", msg)

    def critical(self, msg, *args, **kwargs):
        self._record("critical", msg)

    def exception(self, msg, *args, **kwargs):
        self._record("error", msg)

    def isEnabledFor(self, level):
        return True


class RecordingLoggerFactory:
    def __init__(self):
        self.logger = RecordingLogger()

    def get_logger(self, name):
        return self.logger


class ExplodingStoreTurnRepo:
    """store_conversation_turn 直接抛错（token 落库这一环失败），其余透传真实仓库。"""

    def __init__(self, inner):
        self._inner = inner

    def store_conversation_turn(self, turn):
        raise RuntimeError("db down")

    def __getattr__(self, name):
        return getattr(self._inner, name)


def make_finalizer(repo, title_generator=None, memory=None):
    factory = RecordingLoggerFactory()
    finalizer = TurnFinalizer(
        title_generator=title_generator or FakeTitleGenerator(),
        conversation_repo=repo,
        memory=memory or FakeMemory(),
        logger_factory=factory,
    )
    return finalizer, factory.logger


def make_message(thread_id, turn_id, sequence_num, token_usage):
    return AgenticConversationMessage(
        thread_id=thread_id,
        turn_id=turn_id,
        message_id=uuid4(),
        sequence_num=sequence_num,
        role=AgenticMessageRole.ASSISTANT,
        message_type=AgenticMessageType.MESSAGE,
        content=[{"type": "text", "text": f"回复{sequence_num}"}],
        token_usage=token_usage,
        latency_ms=0,
    )


def stored_turn(engine, thread_id) -> AgenticConversationTurn:
    with Session(engine) as session:
        return session.exec(
            select(AgenticConversationTurn).where(AgenticConversationTurn.thread_id == thread_id)
        ).first()


@pytest.fixture()
def seeded(engine):
    """一条空标题会话 + 一个 RUNNING 轮次。"""
    repo = ConversationRepository(engine=engine)
    conversation = repo.init_conversation(thread_id=uuid4(), agentic_id="builtin:demo")
    turn = repo.create_conversation_turn(conversation=conversation, run_id="run-1", turn_id=uuid4())
    return repo, conversation, turn


def test_first_turn_fills_title_then_never_again(seeded):
    repo, conversation, turn = seeded
    generator = FakeTitleGenerator(title="智能体设计")
    finalizer, logger = make_finalizer(repo, title_generator=generator)
    messages = [
        make_message(conversation.thread_id, turn.turn_id, 1, {"total_tokens": 3}),
        make_message(conversation.thread_id, turn.turn_id, 2, {"total_tokens": 4}),
    ]

    finalizer.run(conversation, turn, messages, "你好")
    assert repo.get_conversation(conversation.thread_id).conversation_title == "智能体设计"
    assert generator.calls == 1

    # 标题已有：不再调生成器
    finalizer.run(conversation, turn, messages, "再来一句")
    assert generator.calls == 1
    assert not any(level == "error" for level, _ in logger.events)


def test_existing_title_not_overwritten(seeded):
    repo, conversation, turn = seeded
    conversation.conversation_title = "已有"
    repo.store_conversation(conversation)
    generator = FakeTitleGenerator(title="新标题")
    finalizer, _ = make_finalizer(repo, title_generator=generator)

    finalizer.run(conversation, turn, [], "你好")

    assert generator.calls == 0
    assert repo.get_conversation(conversation.thread_id).conversation_title == "已有"


def test_title_failure_swallowed_and_retriable(seeded):
    repo, conversation, turn = seeded
    generator = FakeTitleGenerator(error=RuntimeError("llm down"))
    finalizer, logger = make_finalizer(repo, title_generator=generator)

    # 不向调用方抛异常（daemon 线程里不允许裸抛）
    finalizer.run(conversation, turn, [], "你好")

    assert generator.calls == 1
    assert repo.get_conversation(conversation.thread_id).conversation_title == ""
    assert any(level == "error" for level, _ in logger.events)


def test_token_usage_aggregation_ignores_unknown_keys(engine, seeded):
    repo, conversation, turn = seeded
    finalizer, _ = make_finalizer(repo)
    messages = [
        make_message(conversation.thread_id, turn.turn_id, 1, {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}),
        make_message(conversation.thread_id, turn.turn_id, 2, {"prompt_tokens": 5, "total_tokens": 7, "cached_tokens": 99}),
    ]

    finalizer.run(conversation, turn, messages, "你好")

    assert stored_turn(engine, conversation.thread_id).token_usage == {"prompt_tokens": 15, "completion_tokens": 20, "total_tokens": 37}


def test_memory_failure_does_not_break_token_write(engine, seeded):
    repo, conversation, turn = seeded
    memory = FakeMemory(error=RuntimeError("memory down"))
    finalizer, logger = make_finalizer(repo, memory=memory)

    finalizer.run(conversation, turn, [make_message(conversation.thread_id, turn.turn_id, 1, {"total_tokens": 8})], "你好")

    assert stored_turn(engine, conversation.thread_id).token_usage == {"total_tokens": 8}
    assert any(level == "error" for level, _ in logger.events)


def test_outer_catchall_swallows_repo_failure(seeded):
    repo, conversation, turn = seeded
    exploding = ExplodingStoreTurnRepo(repo)
    generator = FakeTitleGenerator(title="智能体设计")
    finalizer, logger = make_finalizer(exploding, title_generator=generator)

    # token 落库这一环失败：外层兜底吞掉，已写成的标题保留，不向上抛
    finalizer.run(conversation, turn, [], "你好")

    assert generator.calls == 1
    assert repo.get_conversation(conversation.thread_id).conversation_title == "智能体设计"
    assert any(level == "error" and "after chat post-processing failed" in msg for level, msg in logger.events)
