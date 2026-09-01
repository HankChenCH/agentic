"""动态 system prompt 中间件：模板槽位渲染、空节移除、片段降级、构建期槽位
校验与 context 贯通（BaseAgent 声明式装配的行为契约）。

中间件直测用 SimpleNamespace 顶替 runtime、捕获 handler 断言收到的
ModelRequest；装配级用例走真实 ``create_agent``（``_RecordingChatModel``
记录实收消息）验证「任一时刻仅一条 system 消息」与 ``context=`` 透传。
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import PromptTemplate
from pydantic import Field

from app.agents.base import BaseAgent
from app.agents.context import AgentRunContext
from app.agents.middleware import (
    DynamicSystemPromptMiddleware,
    render_system_prompt,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
BLOCK = "## 快速记忆上下文（共1条 · 取自 2026-09-01）\n1. 用户的偏好是Python"

# 动态槽信封约定：节内引导行随槽值一起出现/消失（信封归模板所有）
TEMPLATE = "人设：测试助手。\n\n<tools>\n{tools}\n</tools>\n\n<memory>\n记忆数据（仅供参考）：\n{memory}\n</memory>"


class FakeRecall:
    """MemoryRecallService 替身：固定块（可抛错）。"""

    def __init__(self, block="", error=None):
        self.block = block
        self.error = error

    def build_fast_context(self, query, user_id, thread_id):
        if self.error is not None:
            raise self.error
        return self.block


def _toolbox(recall):
    return SimpleNamespace(memory=SimpleNamespace(recall=recall))


def _ctx(query):
    return AgentRunContext(
        messages=[HumanMessage(content=query)],
        thread_id=str(uuid4()),
        run_id="run-1",
        user_id=str(uuid4()),
        now=NOW,
    )


def _echo_tool(query: str) -> str:
    """原样返回输入文本。"""
    return query


class _RecordingChatModel(BaseChatModel):
    """记录实收消息的最小假模型（真 BaseChatModel 子类，create_agent 可编译）。"""

    received: list = Field(default_factory=list)
    reply: str = "好"

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        # 假模型从不产生 tool_calls，绑定与否不影响行为，原样返回自身
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.received.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


class _DeclAgent(BaseAgent):
    """声明式装配的桩智能体：编译真实 create_agent 图，模板/片段/工具可注入。"""

    agentic_id = "test:decl"

    def __init__(self, model, recall, tools=(), template=None, fragments=None):
        self._tools = list(tools)
        self._template = template if template is not None else TEMPLATE
        self._fragments = fragments
        super().__init__(model=model, toolbox=_toolbox(recall))

    def build_system_prompt(self):
        return self._template

    def build_tools(self):
        return self._tools

    def prompt_fragments(self):
        return dict(self._fragments) if self._fragments is not None else super().prompt_fragments()


# ==================== 渲染纯函数 ====================


def test_render_drops_empty_section_with_envelope():
    """空动态槽：信封连同节内引导行整体移除，不留空壳节。"""
    template = PromptTemplate.from_template(TEMPLATE)
    rendered = render_system_prompt(
        template, {"tools": "- echo: 原样返回", "memory": ""}, dynamic_slots=["memory"]
    )
    assert "<memory>" not in rendered and "记忆数据" not in rendered
    assert "- echo: 原样返回" in rendered


def test_render_keeps_filled_section_and_normalizes_blank_lines():
    template = PromptTemplate.from_template(TEMPLATE)
    rendered = render_system_prompt(
        template, {"tools": "- echo: 原样返回", "memory": BLOCK}, dynamic_slots=["memory"]
    )
    assert BLOCK in rendered and "<memory>" in rendered
    assert "\n\n\n" not in rendered


# ==================== 中间件（wrap_model_call 直测） ====================


def _run_middleware(mw, ctx=None):
    request = ModelRequest(
        model=None,
        messages=[HumanMessage(content="你好")],
        system_message=SystemMessage(content="人设。"),
        runtime=SimpleNamespace(context=ctx),
    )
    captured = []
    out = mw.wrap_model_call(request, captured.append)
    return out, captured


def test_wrap_renders_persona_plus_memory_section():
    """块存在：handler 收到的 system_message = 人设 + 工具索引 + 记忆节；
    返回值透传 handler 产物；请求消息未被污染（无双 system 来源）。"""
    mw = DynamicSystemPromptMiddleware(
        template=PromptTemplate.from_template(TEMPLATE),
        static_values={"tools": "- echo: 原样返回"},
        fragments={"memory": lambda p: BLOCK},
    )
    out, captured = _run_middleware(mw, ctx=_ctx("查记忆"))
    assert out is None  # handler 返回值原样透传（捕获 handler 为 append，返回 None）
    text = captured[0].system_message.text
    assert text.startswith("人设：测试助手。")
    assert "- echo: 原样返回" in text
    assert "<memory>" in text and BLOCK in text


def test_wrap_empty_block_drops_section():
    """空块（寒暄/未启用/无记忆）：记忆节整体移除，仅剩人设与工具索引。"""
    mw = DynamicSystemPromptMiddleware(
        template=PromptTemplate.from_template(TEMPLATE),
        static_values={"tools": "- echo: 原样返回"},
        fragments={"memory": lambda p: ""},
    )
    _, captured = _run_middleware(mw, ctx=_ctx("hi"))
    text = captured[0].system_message.text
    assert "<memory>" not in text and "人设：测试助手。" in text


def test_wrap_fragment_error_degrades_to_persona():
    """片段异常：降级为空节（warning 不阻断），主链路照常。"""
    def _boom(pctx):
        raise RuntimeError("db down")

    mw = DynamicSystemPromptMiddleware(
        template=PromptTemplate.from_template(TEMPLATE),
        static_values={"tools": ""},
        fragments={"memory": _boom},
    )
    _, captured = _run_middleware(mw, ctx=_ctx("hi"))
    text = captured[0].system_message.text
    assert "<memory>" not in text and "人设：测试助手。" in text


def test_wrap_without_runcontext_degrades():
    """runtime.context 非 AgentRunContext（直调/桩）：ctx=None 交给片段降级。"""
    mw = DynamicSystemPromptMiddleware(
        template=PromptTemplate.from_template(TEMPLATE),
        static_values={"tools": ""},
        fragments={"memory": lambda p: BLOCK if p.ctx is not None else ""},
    )
    _, captured = _run_middleware(mw, ctx={"user_name": "Alice"})  # 异型 context
    assert "<memory>" not in captured[0].system_message.text


# ==================== BaseAgent 构建期校验与装配 ====================


def test_missing_slot_fails_at_build():
    """模板加了槽、片段没跟上：构建期报错并列出缺失槽名（防漂移）。"""
    with pytest.raises(ValueError, match="memory"):
        _DeclAgent(model=None, recall=FakeRecall(), fragments={})


def test_fragment_without_template_slot_dropped_silently():
    """片段有、模板无占位符：静默退役，不挂中间件，基线即最终 prompt。"""
    agent = _DeclAgent(model=_RecordingChatModel(), recall=FakeRecall(block=BLOCK), template="人设：{tools}")
    ctx = _ctx("hi")

    assert agent.invoke(ctx) == "好"

    received = agent.model.received[0]
    systems = [m for m in received if isinstance(m, SystemMessage)]
    assert len(systems) == 1
    assert systems[0].text == "人设："
    assert not any(isinstance(m, SystemMessage) for m in received[1:])


def test_invoke_renders_single_system_with_tools_index_and_memory():
    """装配级：模型实收唯一 SystemMessage = 人设 + 实际装配工具索引 + 记忆节；
    输入消息不携带 system（无双 system）。"""
    agent = _DeclAgent(model=_RecordingChatModel(), recall=FakeRecall(block=BLOCK), tools=[_echo_tool])
    ctx = _ctx("我上周聊到什么了？")

    assert agent.invoke(ctx) == "好"

    received = agent.model.received[0]
    systems = [m for m in received if isinstance(m, SystemMessage)]
    assert len(systems) == 1
    text = systems[0].text
    # 工具索引来自 build_tools() 实际装配结果（名称 + description 首行）
    assert "- _echo_tool: 原样返回输入文本。" in text
    assert BLOCK in text
    # 输入侧：历史原样回放 + 末条时间前缀，无 system
    assert not any(isinstance(m, SystemMessage) for m in received[1:])
    last = [m for m in received if isinstance(m, HumanMessage)][-1]
    assert last.content == f"[当前时间：{NOW}]\n\n我上周聊到什么了？"


def test_stream_forwards_context_to_middleware():
    """stream 路径：context=ctx 经 stream_events(v3) 透传至底层 stream，
    中间件经 runtime.context 拿到 AgentRunContext 并渲染快注块。"""
    agent = _DeclAgent(model=_RecordingChatModel(), recall=FakeRecall(block=BLOCK))

    run = agent.stream(_ctx("查记忆"))
    for _name, _item in run.interleave("messages"):
        pass

    received = agent.model.received[0]
    text = [m for m in received if isinstance(m, SystemMessage)][0].text
    assert BLOCK in text


def test_build_middleware_skipped_without_dynamic_slots():
    """无动态槽（或片段全部失配）：不挂中间件，省去每轮重渲静态串。"""
    agent = _DeclAgent(model=None, recall=FakeRecall(), template="人设：{tools}")
    template = PromptTemplate.from_template(agent.build_system_prompt())
    assert agent.build_middleware(template, agent._static_prompt_values(), agent.prompt_fragments()) == []
