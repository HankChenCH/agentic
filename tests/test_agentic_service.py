"""AgenticService.run 的 SSE 错误路径：RunError 消息脱敏、准备段异常兜底、
轮次 FAILED 收口；客户端断连（生成器被 close 注入 GeneratorExit）时轮次 CANCELED 收口。

纯单测：真实 ConversationRepository + 临时 SQLite（conftest 的 engine fixture），
agent 工厂 / 记忆 / 日志全替身；threading.Thread 换成同步 InlineThread，
保证断言时收尾加工（TurnFinalizer.run）已确定性完成。
"""

import json
from time import sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from conftest import TEST_USER_ID
from app.exceptions import ConversationNotFoundError
from app.models.domain.agentic import (
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticTurnStatus,
)
from app.packages.signal.memory_signal_store import InMemorySignalStore
from app.repositories.conversation_repository import ConversationRepository
from app.services.domain.conversation.conversation_service import ConversationService
from app.services.domain.conversation.signals import CANCEL_FLAG_TTL_SECONDS, cancel_flag_key
from app.services.orchestration.agentic_service import AgenticService
from app.services.orchestration.turn_finalizer import TurnFinalizer

# 模拟不该泄漏给客户端的内部细节（连接串）
SECRET = "connect timeout postgres://agentic:secret@10.0.0.1:5432/agentic"


def decode(frames):
    """SSE 帧（data: {...}\\n\\n）→ ag-ui 事件 dict 列表。"""
    return [json.loads(frame.removeprefix("data: ").strip()) for frame in frames]


def set_environment(monkeypatch, environment):
    monkeypatch.setattr("app.services.orchestration.agentic_service.get_environment", lambda: environment)


def turn_status(engine, thread_id):
    with Session(engine) as session:
        turn = session.exec(
            select(AgenticConversationTurn).where(AgenticConversationTurn.thread_id == thread_id)
        ).first()
        return None if turn is None else AgenticTurnStatus(turn.status)


def stored_messages(engine, thread_id):
    with Session(engine) as session:
        return session.exec(
            select(AgenticConversationMessage).where(AgenticConversationMessage.thread_id == thread_id)
        ).all()


class Deltas:
    """假消息投影：可逐 delta 迭代（ag-ui 流式侧），也可整体转 str（落库侧）。"""

    def __init__(self, *chunks):
        self._chunks = list(chunks)

    def __iter__(self):
        return iter(self._chunks)

    def __str__(self):
        return "".join(self._chunks)

    def __bool__(self):
        return bool(self._chunks)


class FakeChatModelStream:
    """messages 投影 item 的最小替身（两个 translator 只读这些公开面）。"""

    def __init__(self, text=""):
        self.reasoning = Deltas()
        self.text = Deltas(text)
        self.tool_calls = type("ToolCalls", (), {"get": lambda self: []})()
        self.output_message = None


class FakeRun:
    """agent.stream() 返回值替身：interleave 或产出既定 items、或直接抛错。"""

    def __init__(self, items=(), error=None):
        self._items = list(items)
        self._error = error

    def interleave(self, *names):
        if self._error is not None:
            raise self._error
        yield from self._items


class FakeAgent:
    captured_context = None

    def __init__(self, run):
        self._run = run

    def stream(self, context):
        type(self).captured_context = context
        return self._run


class FakeTitleGenerator:
    """标题生成替身：固定输出（或抛错），记录调用次数供断言。"""

    def __init__(self, title="测试标题", error=None):
        self.title = title
        self.error = error
        self.calls = 0

    def generate(self, query, turn_messages):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.title


class FakeAgentFactory:
    default_agentic_id = "builtin:demo"

    def __init__(self, agent=None, error=None):
        self._agent = agent
        self._error = error

    def create(self, agentic_id):
        if self._error is not None:
            raise self._error
        return self._agent


class ExplodingRepository:
    """init_conversation 直接抛错（准备段第一环失败，轮次行尚未建立）。"""

    def __init__(self, inner):
        self._inner = inner

    def init_conversation(self, **kwargs):
        raise RuntimeError("db down: " + SECRET)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class FakeMemory:
    """记忆巩固替身：remember 静默并记录调用（消费方仅 TurnFinalizer）。"""

    def __init__(self):
        self.remembered = []

    def remember(self, **kwargs):
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


class InlineThread:
    """threading.Thread 替身：start() 同步执行 target，消除后台线程竞态。"""

    def __init__(self, target, args=(), daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


class FakeUserService:
    """UserService 替身：get_user 回放固定用户（收尾同步用户节点用）。"""

    def __init__(self, user):
        self._user = user

    def get_user(self, user_id):
        return self._user


class FakeUserNodeSync:
    """UserNodeSyncPort 替身：记录同步调用。"""

    def __init__(self):
        self.calls = []

    def sync_user_node(self, user_id, username, nickname):
        self.calls.append((user_id, username, nickname))


@pytest.fixture()
def make_service(engine, monkeypatch):
    monkeypatch.setattr("app.services.orchestration.agentic_service.threading.Thread", InlineThread)

    def _make(agent_factory, conversation_repo=None, memory=None, signal_store=None):
        repo = conversation_repo or ConversationRepository(engine=engine)
        # 只用到 default_agentic_id 一项配置，替身避免加载全量 YAML
        conversations = ConversationService(
            conversation_repo=repo,
            app_config=SimpleNamespace(default_agentic_id="builtin:demo"),
            signal_store=signal_store or InMemorySignalStore(),
            logger_factory=RecordingLoggerFactory(),
        )
        finalizer = TurnFinalizer(
            title_generator=FakeTitleGenerator(),
            conversations=conversations,
            memory=memory or FakeMemory(),
            users=FakeUserService(user=SimpleNamespace(id=uuid4(), username="tester", nickname="")),
            memory_user_node=FakeUserNodeSync(),
            logger_factory=RecordingLoggerFactory(),
        )
        return AgenticService(
            agent_factory=agent_factory,
            conversations=conversations,
            turn_finalizer=finalizer,
            logger_factory=RecordingLoggerFactory(),
        )

    return _make


def test_stream_error_prod_sanitized_to_generic_message(engine, make_service, monkeypatch):
    """流式段未知异常：prod 下 RunError 消息为通用文案，内部细节不外泄，轮次收口 FAILED。"""
    set_environment(monkeypatch, "prod")
    service = make_service(FakeAgentFactory(agent=FakeAgent(FakeRun(error=RuntimeError(SECRET)))))
    thread_id = uuid4()

    frames = list(service.run(TEST_USER_ID, thread_id, "run-1", "你好"))

    events = decode(frames)
    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[-1]["message"] == "服务内部错误"
    assert all(SECRET not in frame for frame in frames)
    assert turn_status(engine, thread_id) == AgenticTurnStatus.FAILED
    # 非业务异常按 error 落日志（完整堆栈，不脱敏）
    assert any(level == "error" for level, _ in service.logger.events)


def test_stream_error_dev_keeps_detail(engine, make_service, monkeypatch):
    """流式段未知异常：dev 下 RunError 如实携带异常信息，便于排查。"""
    set_environment(monkeypatch, "dev")
    service = make_service(FakeAgentFactory(agent=FakeAgent(FakeRun(error=RuntimeError(SECRET)))))
    thread_id = uuid4()

    events = decode(list(service.run(TEST_USER_ID, thread_id, "run-2", "你好")))

    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[-1]["message"] == SECRET
    assert turn_status(engine, thread_id) == AgenticTurnStatus.FAILED


def test_stream_business_error_verbatim_in_prod(engine, make_service, monkeypatch):
    """流式段业务异常：message 属预期内错误，prod 也如实透出，且按 warning 记日志。"""
    set_environment(monkeypatch, "prod")
    service = make_service(
        FakeAgentFactory(agent=FakeAgent(FakeRun(error=ConversationNotFoundError("conversation not found"))))
    )
    thread_id = uuid4()

    events = decode(list(service.run(TEST_USER_ID, thread_id, "run-3", "你好")))

    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[-1]["message"] == "conversation not found"
    assert turn_status(engine, thread_id) == AgenticTurnStatus.FAILED
    assert any(level == "warning" for level, _ in service.logger.events)


def test_setup_error_marks_turn_failed(engine, make_service, monkeypatch):
    """准备段异常（轮次已建、agent 构建失败）：不再裸断流，发 RunError 且轮次置 FAILED。"""
    set_environment(monkeypatch, "dev")
    service = make_service(FakeAgentFactory(error=RuntimeError("agent build failed: " + SECRET)))
    thread_id = uuid4()

    events = decode(list(service.run(TEST_USER_ID, thread_id, "run-4", "你好")))

    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[-1]["message"] == "agent build failed: " + SECRET
    assert turn_status(engine, thread_id) == AgenticTurnStatus.FAILED


def test_setup_error_before_turn_row_still_emits_error_frame(engine, make_service, monkeypatch):
    """准备段第一环（建会话）失败：轮次行未建，仍发 RunStarted/RunError，生成器不向调用方抛异常。"""
    set_environment(monkeypatch, "dev")
    service = make_service(
        FakeAgentFactory(agent=FakeAgent(FakeRun())),
        conversation_repo=ExplodingRepository(ConversationRepository(engine=engine)),
    )
    thread_id = uuid4()

    events = decode(list(service.run(TEST_USER_ID, thread_id, "run-5", "你好")))

    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert turn_status(engine, thread_id) is None


def test_happy_path_lifecycle(engine, make_service, monkeypatch):
    """正常路径回归：事件序列与落库不受重构影响。"""
    set_environment(monkeypatch, "dev")
    run = FakeRun(items=[("messages", FakeChatModelStream("你好，世界"))])
    service = make_service(FakeAgentFactory(agent=FakeAgent(run)))
    thread_id = uuid4()

    events = decode(list(service.run(TEST_USER_ID, thread_id, "run-6", "hi")))

    assert [e["type"] for e in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[2]["delta"] == "你好，世界"
    assert turn_status(engine, thread_id) == AgenticTurnStatus.COMPLETED
    # 用户消息 + assistant MESSAGE 各一行（InlineThread 已同步跑完收尾加工）
    assert len(stored_messages(engine, thread_id)) == 2


def test_orchestration_passes_raw_query_to_agent(engine, make_service, monkeypatch):
    """编排层只透传原始 query，不做任何记忆拼接——快注装配归 agent 层
    （BaseAgent._input 组装进 system prompt）。"""
    set_environment(monkeypatch, "dev")

    memory = FakeMemory()
    run = FakeRun(items=[("messages", FakeChatModelStream("好"))])
    service = make_service(FakeAgentFactory(agent=FakeAgent(run)), memory=memory)
    thread_id = uuid4()

    frames = list(service.run(TEST_USER_ID, thread_id, "run-7", "我上周聊到什么了？"))

    assert [e["type"] for e in decode(frames)][-1] == "RUN_FINISHED"
    # FakeMemory 仅被 TurnFinalizer.remember 消费：编排层不触发快注
    assert memory.remembered and memory.remembered[0]["query"] == "我上周聊到什么了？"
    assert FakeAgent.captured_context.messages[-1].content == "我上周聊到什么了？"


def test_client_disconnect_marks_turn_canceled(engine, make_service, monkeypatch):
    """流式中途客户端断连（生成器被 close）：轮次收口 CANCELED，半截 assistant
    消息不落库、收尾加工不执行，且不发 RUN_ERROR 帧。"""
    set_environment(monkeypatch, "dev")

    memory = FakeMemory()
    run = FakeRun(items=[("messages", FakeChatModelStream("你好，世界"))])
    service = make_service(FakeAgentFactory(agent=FakeAgent(run)), memory=memory)
    thread_id = uuid4()

    gen = service.run(TEST_USER_ID, thread_id, "run-8", "hi")
    frames = [next(gen), next(gen), next(gen)]  # RunStarted + 首条消息流式中途挂起
    gen.close()

    assert [e["type"] for e in decode(frames)] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
    ]
    assert turn_status(engine, thread_id) == AgenticTurnStatus.CANCELED
    # 用户消息已落库，半截 assistant 消息不落库（与异常路径同口径）
    assert len(stored_messages(engine, thread_id)) == 1
    # remember 只由 TurnFinalizer 调用：未被调用即收尾加工未执行
    assert memory.remembered == []


def test_disconnect_before_turn_open_is_noop(engine, make_service, monkeypatch):
    """轮次行未建就断连（挂在首个 RunStarted 帧上）：close 不抛异常、无轮次行。"""
    set_environment(monkeypatch, "dev")
    run = FakeRun(items=[("messages", FakeChatModelStream("hi"))])
    service = make_service(FakeAgentFactory(agent=FakeAgent(run)))
    thread_id = uuid4()

    gen = service.run(TEST_USER_ID, thread_id, "run-9", "hi")
    assert next(gen) is not None
    gen.close()

    assert turn_status(engine, thread_id) is None


def test_close_at_error_frame_keeps_failed(engine, make_service, monkeypatch):
    """错误帧挂起期间断连：轮次已置 FAILED，不被断连收口改写成 CANCELED。"""
    set_environment(monkeypatch, "dev")
    service = make_service(FakeAgentFactory(agent=FakeAgent(FakeRun(error=RuntimeError(SECRET)))))
    thread_id = uuid4()

    gen = service.run(TEST_USER_ID, thread_id, "run-10", "hi")
    frames = [next(gen), next(gen)]  # RunStarted + RunError（挂在错误帧 yield 上）
    gen.close()

    assert [e["type"] for e in decode(frames)] == ["RUN_STARTED", "RUN_ERROR"]
    assert turn_status(engine, thread_id) == AgenticTurnStatus.FAILED


class CancelMidStreamRun:
    """在第二个 item 被拉取前置位取消标志（sleep 跨过 0.5s 节流窗）。"""

    def __init__(self, store, thread_id):
        self._store = store
        self._thread_id = thread_id

    def interleave(self, *names):
        yield ("messages", FakeChatModelStream("第一段"))
        sleep(0.6)
        self._store.fire(cancel_flag_key(self._thread_id), ttl_seconds=CANCEL_FLAG_TTL_SECONDS)
        yield ("messages", FakeChatModelStream("第二段"))
        yield ("messages", FakeChatModelStream("第三段"))


def test_cancel_flag_stops_stream_silently(engine, make_service, monkeypatch):
    """显式取消（流中置位）：下一节流边界静默断流，轮次收口 CANCELED，
    不发 RUN_ERROR、后续 item 不产出、半截消息不落库、收尾加工不执行。"""
    set_environment(monkeypatch, "dev")

    store = InMemorySignalStore()
    memory = FakeMemory()
    run = CancelMidStreamRun(store, None)  # thread_id 由测试启动时回填
    service = make_service(FakeAgentFactory(agent=FakeAgent(run)), memory=memory, signal_store=store)
    thread_id = uuid4()
    run._thread_id = thread_id

    frames = list(service.run(TEST_USER_ID, thread_id, "run-11", "hi"))

    types = [e["type"] for e in decode(frames)]
    assert types[0] == "RUN_STARTED"
    assert "第二段" not in json.dumps(frames, ensure_ascii=False)
    assert "RUN_ERROR" not in types and "RUN_FINISHED" not in types
    assert turn_status(engine, thread_id) == AgenticTurnStatus.CANCELED
    # remember 只由 TurnFinalizer 调用：未被调用即收尾加工未执行
    assert memory.remembered == []


class CancelBeforeFirstItemRun:
    """在首个 item 产出前置位取消标志。"""

    def __init__(self, store, thread_id):
        self._store = store
        self._thread_id = thread_id

    def interleave(self, *names):
        self._store.fire(cancel_flag_key(self._thread_id), ttl_seconds=CANCEL_FLAG_TTL_SECONDS)
        yield ("messages", FakeChatModelStream("不该出现"))


def test_cancel_flag_before_first_item_stops_stream(engine, make_service, monkeypatch):
    """取消标志在首个 item 产出前置位：首个边界即静默断流，零内容帧。"""
    set_environment(monkeypatch, "dev")

    store = InMemorySignalStore()
    run = CancelBeforeFirstItemRun(store, None)
    service = make_service(FakeAgentFactory(agent=FakeAgent(run)), signal_store=store)
    thread_id = uuid4()
    run._thread_id = thread_id

    events = [e["type"] for e in decode(list(service.run(TEST_USER_ID, thread_id, "run-12", "hi")))]

    assert events == ["RUN_STARTED"]
    assert turn_status(engine, thread_id) == AgenticTurnStatus.CANCELED


def test_stale_cancel_flag_does_not_kill_new_turn(engine, make_service, monkeypatch):
    """上轮取消标志的残留被 open_turn 防御性清理：新一轮正常完成，不被误杀。"""
    set_environment(monkeypatch, "dev")

    store = InMemorySignalStore()
    run = FakeRun(items=[("messages", FakeChatModelStream("你好，世界"))])
    service = make_service(FakeAgentFactory(agent=FakeAgent(run)), signal_store=store)
    thread_id = uuid4()

    # 模拟 TTL 前的残留标志（绕开 cancel_run 的归属校验，属仓储级残留）
    store.fire(cancel_flag_key(thread_id), ttl_seconds=CANCEL_FLAG_TTL_SECONDS)
    events = [e["type"] for e in decode(list(service.run(TEST_USER_ID, thread_id, "run-13", "hi")))]

    assert events[-1] == "RUN_FINISHED"
    assert turn_status(engine, thread_id) == AgenticTurnStatus.COMPLETED
