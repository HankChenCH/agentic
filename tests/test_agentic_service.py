"""AgenticService.chat 的 SSE 错误路径：RunError 消息脱敏、准备段异常兜底、轮次 FAILED 收口。

纯单测：真实 ConversationRepository + 临时 SQLite（conftest 的 engine fixture），
agent 工厂 / 记忆 / 日志全替身；threading.Thread 换成同步 InlineThread，
保证断言时后置处理（_after_chat）已确定性完成。
"""

import json
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from app.exceptions import ConversationNotFoundError
from app.models.domain.agentic import (
    AgenticConversationMessage,
    AgenticConversationTurn,
    AgenticTurnStatus,
)
from app.repositories.conversation_repository import ConversationRepository
from app.services.agentic_service import AgenticService

# 模拟不该泄漏给客户端的内部细节（连接串）
SECRET = "connect timeout postgres://agentic:secret@10.0.0.1:5432/agentic"


def decode(frames):
    """SSE 帧（data: {...}\\n\\n）→ ag-ui 事件 dict 列表。"""
    return [json.loads(frame.removeprefix("data: ").strip()) for frame in frames]


def set_environment(monkeypatch, environment):
    monkeypatch.setattr("app.services.agentic_service.get_environment", lambda: environment)


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
    def __init__(self, run):
        self._run = run

    def stream(self, context):
        return self._run

    def invoke(self, context):
        return "测试标题"


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
    def remember(self, **kwargs):
        pass


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


@pytest.fixture()
def make_service(engine, monkeypatch):
    monkeypatch.setattr("app.services.agentic_service.threading.Thread", InlineThread)

    def _make(agent_factory, conversation_repo=None):
        return AgenticService(
            agent_factory=agent_factory,
            conversation_repo=conversation_repo or ConversationRepository(engine=engine),
            memory=FakeMemory(),
            logger_factory=RecordingLoggerFactory(),
        )

    return _make


def test_stream_error_prod_sanitized_to_generic_message(engine, make_service, monkeypatch):
    """流式段未知异常：prod 下 RunError 消息为通用文案，内部细节不外泄，轮次收口 FAILED。"""
    set_environment(monkeypatch, "prod")
    service = make_service(FakeAgentFactory(agent=FakeAgent(FakeRun(error=RuntimeError(SECRET)))))
    thread_id = uuid4()

    frames = list(service.chat(thread_id, "run-1", "你好"))

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

    events = decode(list(service.chat(thread_id, "run-2", "你好")))

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

    events = decode(list(service.chat(thread_id, "run-3", "你好")))

    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert events[-1]["message"] == "conversation not found"
    assert turn_status(engine, thread_id) == AgenticTurnStatus.FAILED
    assert any(level == "warning" for level, _ in service.logger.events)


def test_setup_error_marks_turn_failed(engine, make_service, monkeypatch):
    """准备段异常（轮次已建、agent 构建失败）：不再裸断流，发 RunError 且轮次置 FAILED。"""
    set_environment(monkeypatch, "dev")
    service = make_service(FakeAgentFactory(error=RuntimeError("agent build failed: " + SECRET)))
    thread_id = uuid4()

    events = decode(list(service.chat(thread_id, "run-4", "你好")))

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

    events = decode(list(service.chat(thread_id, "run-5", "你好")))

    assert [e["type"] for e in events] == ["RUN_STARTED", "RUN_ERROR"]
    assert turn_status(engine, thread_id) is None


def test_happy_path_lifecycle(engine, make_service, monkeypatch):
    """正常路径回归：事件序列与落库不受重构影响。"""
    set_environment(monkeypatch, "dev")
    run = FakeRun(items=[("messages", FakeChatModelStream("你好，世界"))])
    service = make_service(FakeAgentFactory(agent=FakeAgent(run)))
    thread_id = uuid4()

    events = decode(list(service.chat(thread_id, "run-6", "hi")))

    assert [e["type"] for e in events] == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[2]["delta"] == "你好，世界"
    assert turn_status(engine, thread_id) == AgenticTurnStatus.COMPLETED
    # 用户消息 + assistant MESSAGE 各一行（InlineThread 已同步跑完后置处理）
    assert len(stored_messages(engine, thread_id)) == 2
