"""TurnFinalizer 收尾加工的行为契约：三步各自的容错语义。

纯单测：真实 ConversationRepository + 临时 SQLite（conftest 的 engine fixture），
标题生成 / 记忆 / 日志全替身，直接同步调用 run()。
"""

from uuid import uuid4

import pytest
from sqlmodel import Session, select
from types import SimpleNamespace

from app.models.domain.agentic import (
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticMessageRole,
    AgenticMessageType,
)
from app.packages.signal.memory_signal_store import InMemorySignalStore
from conftest import TEST_USER_ID
from app.adapters.persistence.conversation_repository import ConversationRepository
from app.domain.conversation.conversation_service import ConversationService
from app.application.turn_finalizer import TurnFinalizer


class FakeTitleGenerator:
    def __init__(self, title="新标题", error=None):
        self.title = title
        self.error = error
        self.calls = 0
        self.calls_kwargs = []

    def generate(self, query, turn_messages, *, user_id=None, thread_id=None):
        self.calls += 1
        self.calls_kwargs.append({"user_id": user_id, "thread_id": thread_id})
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


class FakeUserNodeSync:
    """UserNodeSyncPort 替身：记录同步调用。"""

    def __init__(self):
        self.calls = []

    def sync_user_node(self, user_id, username, nickname):
        self.calls.append((user_id, username, nickname))


class FakeUserService:
    """UserService 替身：get_user 回放预置用户，可注入失败。"""

    def __init__(self, user=None, error=None):
        self._user = user
        self._error = error

    def get_user(self, user_id):
        if self._error is not None:
            raise self._error
        return self._user


def make_finalizer(repo, title_generator=None, memory=None, user_service=None, user_node_sync=None):
    factory = RecordingLoggerFactory()
    # 只用到 default_agentic_id 一项配置，替身避免加载全量 YAML（open_turn 才会读到）
    conversations = ConversationService(
        conversation_repo=repo,
        app_config=SimpleNamespace(default_agentic_id="builtin:demo"),
        signal_store=InMemorySignalStore(),
        logger_factory=factory,
    )
    finalizer = TurnFinalizer(
        title_generator=title_generator or FakeTitleGenerator(),
        conversations=conversations,
        memory=memory or FakeMemory(),
        users=user_service or FakeUserService(user=SimpleNamespace(
            id=TEST_USER_ID, username="tester", nickname="",
        )),
        memory_user_node=user_node_sync or FakeUserNodeSync(),
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
    """一条空标题会话 + 一个 RUNNING 轮次（open_turn 单事务种子，含 0 号用户行）。"""
    repo = ConversationRepository(engine=engine)
    conversation, turn = repo.open_turn(
        user_id=TEST_USER_ID, thread_id=uuid4(), agentic_id="builtin:demo",
        rebind=False, run_id="run-1", turn_id=uuid4(), parent_turn_id=None,
        attempt_no=1, content=[{"type": "text", "text": "你好"}],
    )
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
    assert repo.get_conversation(conversation.thread_id, TEST_USER_ID).conversation_title == "智能体设计"
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
    assert repo.get_conversation(conversation.thread_id, TEST_USER_ID).conversation_title == "已有"


def test_title_failure_swallowed_and_retriable(seeded):
    repo, conversation, turn = seeded
    generator = FakeTitleGenerator(error=RuntimeError("llm down"))
    finalizer, logger = make_finalizer(repo, title_generator=generator)

    # 不向调用方抛异常（daemon 线程里不允许裸抛）
    finalizer.run(conversation, turn, [], "你好")

    assert generator.calls == 1
    assert repo.get_conversation(conversation.thread_id, TEST_USER_ID).conversation_title == ""
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


def test_token_usage_aggregation_accepts_native_usage_metadata_keys(engine, seeded):
    """存量消息行的原生键族（LangChain usage_metadata：input/output_tokens）
    同样计入轮次聚合——键名归一化改造前落库的历史数据不丢输入/输出维度。"""
    repo, conversation, turn = seeded
    finalizer, _ = make_finalizer(repo)
    messages = [
        make_message(conversation.thread_id, turn.turn_id, 1, {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130}),
        make_message(conversation.thread_id, turn.turn_id, 2, {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16}),
    ]

    finalizer.run(conversation, turn, messages, "你好")

    assert stored_turn(engine, conversation.thread_id).token_usage == {"input_tokens": 111, "output_tokens": 35, "total_tokens": 146}


def test_memory_failure_does_not_break_token_write(engine, seeded):
    repo, conversation, turn = seeded
    memory = FakeMemory(error=RuntimeError("memory down"))
    finalizer, logger = make_finalizer(repo, memory=memory)

    finalizer.run(conversation, turn, [make_message(conversation.thread_id, turn.turn_id, 1, {"total_tokens": 8})], "你好")

    assert stored_turn(engine, conversation.thread_id).token_usage == {"total_tokens": 8}
    assert any(level == "error" for level, _ in logger.events)


def test_user_node_synced_before_remember(engine, seeded):
    repo, conversation, turn = seeded
    sync = FakeUserNodeSync()
    memory = FakeMemory()
    finalizer, _ = make_finalizer(repo, memory=memory, user_node_sync=sync)

    finalizer.run(conversation, turn, [], "你好")

    # 每轮收尾都会用最新账号 profile 自愈刷新用户节点，且先于 remember
    assert sync.calls == [(TEST_USER_ID, "tester", "")]


def test_user_node_sync_failure_does_not_break_remember(engine, seeded):
    repo, conversation, turn = seeded

    class BoomSync:
        def sync_user_node(self, *args, **kwargs):
            raise RuntimeError("node down")

    memory = FakeMemory()
    finalizer, logger = make_finalizer(repo, memory=memory, user_node_sync=BoomSync())

    finalizer.run(conversation, turn, [], "你好")

    assert len(memory.remembered) == 1                     # 节点同步失败不影响 remember
    assert any(level == "error" and "sync memory user node failed" in msg for level, msg in logger.events)


def test_outer_catchall_swallows_repo_failure(seeded):
    repo, conversation, turn = seeded
    exploding = ExplodingStoreTurnRepo(repo)
    generator = FakeTitleGenerator(title="智能体设计")
    finalizer, logger = make_finalizer(exploding, title_generator=generator)

    # token 落库这一环失败：外层兜底吞掉，已写成的标题保留，不向上抛
    finalizer.run(conversation, turn, [], "你好")

    assert generator.calls == 1
    assert repo.get_conversation(conversation.thread_id, TEST_USER_ID).conversation_title == "智能体设计"
    assert any(level == "error" and "after run post-processing failed" in msg for level, msg in logger.events)
