"""AgenticService 优雅关闭：begin_shutdown（SIGTERM 信号处理器入口）与
drain_finalizers（lifespan 关闭段）。

纯单测：复用 test_agentic_service 的替身家族（真实 ConversationRepository +
临时 SQLite，agent/记忆/日志全替身）；信号存储用 InMemorySignalStore 直接
断言取消标志的点亮，不依赖流式循环的感知时序（后者已由 cancel 系列测试锁定）。
"""

from concurrent.futures import Future
from types import SimpleNamespace
from uuid import uuid4

import pytest

from conftest import TEST_USER_ID, StubUsageService
from test_agentic_service import (
    FakeAgent,
    FakeAgentFactory,
    FakeChatModelStream,
    FakeMemory,
    FakeRun,
    FakeTitleGenerator,
    FakeUserNodeSync,
    FakeUserService,
    InlineFinalizerExecutor,
    RecordingLoggerFactory,
    set_environment,
    turn_status,
)
from app.adapters.filesystem.local_provider import LocalFilesystem
from app.adapters.persistence.conversation_repository import ConversationRepository
from app.application.agentic_service import AgenticService
from app.application.turn_finalizer import TurnFinalizer
from app.domain.conversation.attachments import ConversationAttachmentStore
from app.domain.conversation.conversation_service import ConversationService
from app.domain.conversation.signals import cancel_flag_key
from app.models.domain.agentic import AgenticTurnStatus
from app.packages.signal.memory_signal_store import InMemorySignalStore


class ExplodingSignalStore(InMemorySignalStore):
    """fire 即抛的信号存储替身：模拟 Redis 不可用（标志写入失败如实上抛）。"""

    def fire(self, key, value=None, ttl_seconds=None):
        raise RuntimeError("redis down")


class DeferredFinalizerExecutor(InlineFinalizerExecutor):
    """submit 只登记不执行：收尾任务挂成在途 Future，供 drain 测试手动释放。"""

    def __init__(self, *args, **kwargs):
        self.pending = []

    def submit(self, fn, *args, **kwargs):
        future = Future()
        self.pending.append((future, fn, args, kwargs))
        return future

    def release_all(self):
        for future, fn, args, kwargs in self.pending:
            if not future.done():
                future.set_result(fn(*args, **kwargs))
        self.pending.clear()

    def shutdown(self, *args, **kwargs):
        pass


def _make_service(engine, tmp_path, agent_factory, signal_store=None):
    """最小化组装 AgenticService（口径同 test_agentic_service.make_service）。
    收尾执行器不在此替换——需要确定性/挂起语义的测试用 monkeypatch 自行注入。"""
    conversations = ConversationService(
        conversation_repo=ConversationRepository(engine=engine),
        app_config=SimpleNamespace(default_agentic_id="builtin:demo"),
        signal_store=signal_store or InMemorySignalStore(),
        logger_factory=RecordingLoggerFactory(),
    )
    attachments = ConversationAttachmentStore(
        filesystem=LocalFilesystem(root=tmp_path / "attachments"),
        logger_factory=RecordingLoggerFactory(),
    )
    finalizer = TurnFinalizer(
        title_generator=FakeTitleGenerator(),
        conversations=conversations,
        memory=FakeMemory(),
        users=FakeUserService(user=SimpleNamespace(id=uuid4(), username="tester", nickname="")),
        memory_user_node=FakeUserNodeSync(),
        logger_factory=RecordingLoggerFactory(),
    )
    return AgenticService(
        agent_factory=agent_factory,
        conversations=conversations,
        attachments=attachments,
        turn_finalizer=finalizer,
        usage=StubUsageService(),
        logger_factory=RecordingLoggerFactory(),
    )


def _start_run(service, thread_id):
    """推进 run 生成器到首帧之后：在途登记（_active_runs）已发生、生成器挂起。"""
    gen = service.run(TEST_USER_ID, thread_id, f"run-{thread_id.hex[:8]}", [{"type": "text", "text": "hi"}])
    first = next(gen)
    assert "RUN_STARTED" in first
    return gen


def test_begin_shutdown_fires_cancel_for_active_runs(engine, tmp_path, monkeypatch):
    """在途 run 逐一点亮 thread 作用域取消标志，返回在途数量。"""
    set_environment(monkeypatch, "dev")
    store = InMemorySignalStore()
    service = _make_service(engine, tmp_path, FakeAgentFactory(agent=FakeAgent(FakeRun(error=RuntimeError("unreachable")))), signal_store=store)
    thread_a, thread_b = uuid4(), uuid4()
    gen_a = _start_run(service, thread_a)
    gen_b = _start_run(service, thread_b)

    assert service.begin_shutdown() == 2
    assert store.is_fired(cancel_flag_key(thread_a))
    assert store.is_fired(cancel_flag_key(thread_b))

    gen_a.close()
    gen_b.close()


def test_begin_shutdown_without_active_runs_returns_zero(engine, tmp_path, monkeypatch):
    set_environment(monkeypatch, "dev")
    service = _make_service(engine, tmp_path, FakeAgentFactory(agent=FakeAgent(FakeRun())))
    assert service.begin_shutdown() == 0


def test_begin_shutdown_swallows_flag_write_failure(engine, tmp_path, monkeypatch):
    """信号存储不可用：单流标志写入失败只记日志，不阻断对其余流的取消。"""
    set_environment(monkeypatch, "dev")
    store = ExplodingSignalStore()
    service = _make_service(engine, tmp_path, FakeAgentFactory(agent=FakeAgent(FakeRun())), signal_store=store)
    thread_id = uuid4()
    gen = _start_run(service, thread_id)

    assert service.begin_shutdown() == 1  # 不上抛，计数仍如实返回

    gen.close()


def test_shutdown_cancels_in_flight_turn_to_canceled(engine, tmp_path, monkeypatch):
    """端到端语义：流式过程中点亮标志（生产时序：生成器挂在帧间 yield 上），
    跨过 0.5s 节流窗后的下一检查点静默收口——轮次 CANCELED、无 RUN_ERROR 帧，
    与客户端断连/Stop 同口径。"""
    from time import sleep

    set_environment(monkeypatch, "dev")
    store = InMemorySignalStore()
    run = FakeRun(items=[("messages", FakeChatModelStream("一段足够长的文本输出"))])
    service = _make_service(engine, tmp_path, FakeAgentFactory(agent=FakeAgent(run)), signal_store=store)
    thread_id = uuid4()
    gen = service.run(TEST_USER_ID, thread_id, "run-cancel", [{"type": "text", "text": "hi"}])
    next(gen)  # RUN_STARTED
    next(gen)  # TEXT_MESSAGE_START——生成器挂在 translate 的帧间 yield 上

    assert service.begin_shutdown() == 1
    sleep(0.6)  # 跨过帧级检查的 0.5s 节流窗
    # 下一帧拉取命中取消检查 → 静默收口（无 RUN_ERROR / RUN_FINISHED 帧）
    with pytest.raises(StopIteration):
        next(gen)

    assert turn_status(engine, thread_id) == AgenticTurnStatus.CANCELED


def test_drain_finalizers_idle_returns_zero(engine, tmp_path, monkeypatch):
    set_environment(monkeypatch, "dev")
    service = _make_service(engine, tmp_path, FakeAgentFactory(agent=FakeAgent(FakeRun())))
    assert service.drain_finalizers(timeout=0.1) == 0


def test_drain_finalizers_waits_for_pending_and_reports_timeouts(engine, tmp_path, monkeypatch):
    """在途收尾任务：释放后排空返回 0；永不完成的任务超时后按剩余数上报，
    且 shutdown(cancel_futures) 已委派给 executor（不阻断退出）。"""
    set_environment(monkeypatch, "dev")
    executor = DeferredFinalizerExecutor()
    monkeypatch.setattr("app.application.agentic_service.ThreadPoolExecutor", lambda **kwargs: executor)
    service = _make_service(engine, tmp_path, FakeAgentFactory(agent=FakeAgent(FakeRun(items=[("messages", FakeChatModelStream("hi"))]))))
    thread_id = uuid4()

    list(service.run(TEST_USER_ID, thread_id, "run-drain", [{"type": "text", "text": "hi"}]))
    # 收尾任务已挂起未执行（Deferred executor 不同步跑）
    assert len(executor.pending) == 1

    # 超时路径：任务未释放 → 按剩余数上报 1
    assert service.drain_finalizers(timeout=0.05) == 1

    # 释放后再次排空：在途集合已清（上一次 drain 触发过 shutdown），新任务为空
    executor.release_all()
    assert service.drain_finalizers(timeout=0.1) == 0
