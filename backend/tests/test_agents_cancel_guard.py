"""CancelGuardMiddleware 工具入口守卫的行为契约：取消命中跳过工具体、
未命中透传、装配级经 ``context=`` 贯通（真实 create_agent 图验证——取消
检查闭包由 ``AgentRunContext.cancel_check`` 经 runtime.context 可达）。

单元层直构 ``ToolCallRequest``（哨兵 handler 断言调用次数）；装配层用
脚本化假模型（首轮发 tool_call，其后回固定文本）跑完整 ReAct 循环：取消
命中时工具体零调用、``RunCanceledError`` 原样冲出图运行——langgraph 的
默认工具错误处理只消化参数校验类异常，普通异常照常上抛，与重构前
``_cancel_guard`` 在工具函数内抛出的路径逐字一致（经旧实现实证），
由编排层流式循环的异常兜底收口。
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain.agents.middleware import ToolCallRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from app.agents import RunCanceledError
from app.agents.base import BaseAgent
from app.agents.cancel import CancelGuardMiddleware
from app.agents.context import AgentRunContext

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

TOOL_CALL = {"name": "echo", "args": {"query": "hi"}, "id": "call-1", "type": "tool_call"}


def _ctx(cancel_check=None):
    return AgentRunContext(
        messages=[HumanMessage(content="hi")],
        thread_id=str(uuid4()),
        run_id="run-1",
        user_id=str(uuid4()),
        now=NOW,
        cancel_check=cancel_check,
    )


def _request(ctx):
    """直构 ToolCallRequest：守卫只读 runtime.context，其余字段可置空。"""
    return ToolCallRequest(
        tool_call=dict(TOOL_CALL),
        tool=None,
        state={},
        runtime=SimpleNamespace(context=ctx),
    )


def _handler(calls, result="ok"):
    def handler(request):
        calls.append(request)
        return ToolMessage(content=result, tool_call_id=request.tool_call["id"])

    return handler


# ==================== 单元层（wrap_tool_call 直测） ====================


def test_wrap_canceled_raises_without_executing():
    """取消命中：抛 RunCanceledError，handler（工具体）一次都不执行。"""
    calls = []
    with pytest.raises(RunCanceledError):
        CancelGuardMiddleware().wrap_tool_call(_request(_ctx(lambda: True)), _handler(calls))
    assert calls == []


def test_wrap_not_canceled_calls_handler_once():
    """未取消（闭包返回 False）：handler 恰调一次，结果原样透传。"""
    calls = []
    result = CancelGuardMiddleware().wrap_tool_call(_request(_ctx(lambda: False)), _handler(calls))
    assert len(calls) == 1
    assert isinstance(result, ToolMessage)
    assert result.content == "ok"


def test_wrap_passes_through_when_check_missing():
    """未接取消通道（cancel_check=None）：放行，同重构前无闭包的口径。"""
    calls = []
    result = CancelGuardMiddleware().wrap_tool_call(_request(_ctx(None)), _handler(calls))
    assert len(calls) == 1 and result.content == "ok"


def test_wrap_passes_through_with_foreign_context():
    """runtime.context 非 AgentRunContext（直调/测试桩）：防御性放行。"""
    calls = []
    for foreign in ({"user": "x"}, SimpleNamespace(cancel_check=lambda: True), SimpleNamespace()):
        calls.clear()
        CancelGuardMiddleware().wrap_tool_call(_request(foreign), _handler(calls))
        assert len(calls) == 1


# ==================== 装配层（真实 create_agent 图） ====================


class _ScriptedToolCallModel(BaseChatModel):
    """脚本化假模型：消息流中无 ToolMessage 时发 echo 的 tool_call，其后回固定文本。"""

    received: list = Field(default_factory=list)
    reply: str = "好"

    @property
    def _llm_type(self) -> str:
        return "scripted-fake"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.received.append(list(messages))
        if any(isinstance(m, ToolMessage) for m in messages):
            message = AIMessage(content=self.reply)
        else:
            message = AIMessage(content="", tool_calls=[dict(TOOL_CALL)])
        return ChatResult(generations=[ChatGeneration(message=message)])


class _ToolAgent(BaseAgent):
    """带一个裸函数工具的桩智能体（无动态槽，模板零占位符）。"""

    agentic_id = "test:cancel"

    def __init__(self, model, tool):
        self._tool = tool
        super().__init__(model=model, toolbox=SimpleNamespace(memory=SimpleNamespace(recall=None)))

    def build_system_prompt(self):
        return "人设：测试助手。"

    def build_tools(self):
        return [self._tool]


def _make_echo_tool(calls):
    def echo(query: str) -> str:
        """原样返回输入文本。"""
        calls.append(query)
        return f"result:{query}"

    return echo


def test_invoke_canceled_skips_tool_body_and_propagates():
    """取消命中：工具体零调用，``RunCanceledError`` 原样冲出图运行——
    与重构前守卫在工具函数内抛出同路（langgraph 默认工具错误处理不消化
    普通异常），由编排层流式循环的异常兜底收口。"""
    calls = []
    model = _ScriptedToolCallModel()
    agent = _ToolAgent(model=model, tool=_make_echo_tool(calls))

    with pytest.raises(RunCanceledError):
        agent.invoke(_ctx(cancel_check=lambda: True))
    assert calls == []


def test_invoke_uncanceled_executes_tool():
    """未启用取消：工具体执行一次，结果以 ToolMessage 进第二轮模型调用。"""
    calls = []
    model = _ScriptedToolCallModel()
    agent = _ToolAgent(model=model, tool=_make_echo_tool(calls))

    assert agent.invoke(_ctx()) == "好"
    assert calls == ["hi"]
    tool_results = [m for m in model.received[1] if isinstance(m, ToolMessage)]
    assert len(tool_results) == 1
    assert tool_results[0].content == "result:hi"
