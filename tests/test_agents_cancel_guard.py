"""_cancel_guard 工具入口守卫的行为契约：取消命中跳过工具体、未命中透传、
config 注入参数的签名合成（对未声明 config 的工具补 config: RunnableConfig）。

纯单测：直接函数级调用守卫，不经 langgraph。
"""

import inspect

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents import RunCanceledError
from app.agents.base import _cancel_guard


def _tool_without_config(query: str) -> str:
    """按关键词检索资料。"""
    return f"result:{query}"


def _tool_with_config(query: str, config: RunnableConfig) -> str:
    return f"result:{query}:{'armed' if (config or {}).get('configurable', {}).get('cancel_check') else 'bare'}"


def test_guard_passes_through_without_cancel_channel():
    guarded = _cancel_guard(_tool_without_config)

    # 未接取消通道（config 缺省 / configurable 里没有闭包）：原样执行
    assert guarded(query="hi") == "result:hi"
    assert guarded(query="hi", config={"configurable": {}}) == "result:hi"


def test_guard_skips_tool_body_when_canceled():
    guarded = _cancel_guard(_tool_without_config)

    with pytest.raises(RunCanceledError):
        guarded(query="hi", config={"configurable": {"cancel_check": lambda: True}})


def test_guard_synthesizes_config_param_for_tools_without_one():
    """未声明 config 的工具被补上 config: RunnableConfig——langchain 的注入与
    schema 排除都按这个注解判定（同 memory 工具的既有模式）。"""
    sig = inspect.signature(_cancel_guard(_tool_without_config))
    assert sig.parameters["config"].annotation is RunnableConfig
    # 注入的 config 不回传给不接收它的原函数（原函数无 config 形参，透传会 TypeError）
    guarded = _cancel_guard(_tool_without_config)
    assert guarded(query="hi", config={"configurable": {"cancel_check": lambda: False}}) == "result:hi"


def test_guard_keeps_declared_config_tool_working():
    """已声明 config 的工具（memory 系模式）：守卫不改签名，闭包可达。"""
    guarded = _cancel_guard(_tool_with_config)

    assert guarded(query="hi", config={"configurable": {"cancel_check": lambda: False}}) == "result:hi:armed"
    with pytest.raises(RunCanceledError):
        guarded(query="hi", config={"configurable": {"cancel_check": lambda: True}})
