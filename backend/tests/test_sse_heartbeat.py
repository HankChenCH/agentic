"""SSE 心跳包装（_stream_and_close）的单元测试。

pyproject 无 pytest-asyncio：用 asyncio.run 在同步测试里驱动 async 生成器。
被测包装的契约：数据帧原样透传且保序；静默期（底层 next 迟迟不返回）插入
注释行心跳帧；任何退出路径（正常耗尽/提前 aclose/底层异常）都确定性关闭底层
同步生成器。
"""

import asyncio
import time

import pytest

from app.api.v1.endpoints.agentic import (
    _HEARTBEAT_FRAME,
    _stream_and_close,
)


async def _collect(agen):
    return [frame async for frame in agen]


def test_fast_stream_has_no_heartbeat():
    """帧间无静默：数据帧原序透传，心跳一帧不发。"""
    events = ["data: a\n\n", "data: b\n\n"]

    def gen():
        yield from events

    assert asyncio.run(_collect(_stream_and_close(gen()))) == events


def test_silent_gaps_produce_heartbeat_frames(monkeypatch):
    """底层 next 阻塞期间（模拟首 token 前/工具执行静默窗）插入注释行心跳，
    数据帧不丢不重、相对顺序不变。"""
    monkeypatch.setattr(
        "app.api.v1.endpoints.agentic._HEARTBEAT_INTERVAL_SECONDS", 0.02
    )

    def gen():
        yield "data: a\n\n"
        time.sleep(0.08)
        yield "data: b\n\n"
        time.sleep(0.08)
        yield "data: c\n\n"

    frames = asyncio.run(_collect(_stream_and_close(gen())))
    assert [f for f in frames if f != _HEARTBEAT_FRAME] == [
        "data: a\n\n", "data: b\n\n", "data: c\n\n",
    ]
    # 两段 0.08s 静默 × 0.02s 间隔，至少各触发一次心跳
    assert frames.count(_HEARTBEAT_FRAME) >= 2


def test_early_aclose_closes_underlying_generator():
    """客户端断连路径：收首帧后 aclose，底层同步生成器的收尾段被确定性执行
    （不依赖循环 GC——正是包装存在的理由）。"""
    closed = []

    def gen():
        try:
            yield "data: a\n\n"
            time.sleep(30)  # 若未被 close，收尾会被这段阻塞拖住
            yield "data: b\n\n"
        finally:
            closed.append(True)

    async def _run():
        agen = _stream_and_close(gen())
        first = await agen.__anext__()
        await agen.aclose()
        return first

    first = asyncio.run(_run())
    assert first == "data: a\n\n"
    assert closed == [True]


def test_underlying_error_propagates_and_closes():
    """底层生成器异常穿透包装上抛，同时 close 语义仍执行。"""
    closed = []

    def gen():
        try:
            yield "data: a\n\n"
            raise RuntimeError("boom")
        finally:
            closed.append(True)

    async def _run():
        agen = _stream_and_close(gen())
        with pytest.raises(RuntimeError, match="boom"):
            await _collect(agen)

    asyncio.run(_run())
    assert closed == [True]
