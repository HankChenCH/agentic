"""上下文压缩（SummarizationMiddleware 集成）：窗口声明驱动装配、摘要模型
回调隔离、摘要消息 id 盖章与消费侧过滤（零泄漏契约）。

泄漏背景（见 app/agents/middleware.py 模块注释）：摘要会触发一次内部模型
调用并把摘要消息写进图状态，两者都以 ChatModelStream 形态出现在
``interleave("messages")`` 投影里——防泄漏组合拳（回调隔离 + id 盖章 +
AgenticService 消费侧过滤）经真实 create_agent 图 + stream_events(v3) 全链
断言，回归的是 langchain 升级后私有实现变更导致的失效（失效模式 = 摘要
文案漏进用户流，可见故障）。
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.prompts import PromptTemplate
from pydantic import Field

from app.agents.base import BaseAgent
from app.agents.context import AgentRunContext
from app.agents.middleware import (
    CONTEXT_KEEP_MESSAGES,
    CONTEXT_TRIGGER_FRACTION,
    SUMMARY_MESSAGE_ID_PREFIX,
    CallbackIsolatedChatModel,
    ContextSummarizationMiddleware,
    is_internal_stream_item,
    model_max_input_tokens,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
_HISTORY_TEXT = "历史上下文内容。" * 125  # ~1000 chars ≈ 250 tokens（近似计数 4 chars/token）


class _ProfiledChatModel(BaseChatModel):
    """记录实收消息与 config 的桩模型：profile 可注入，回复可定长。

    profile 即 langchain 的模型画像字段——压缩中间件的 fraction 触发阈值与
    ``model_max_input_tokens`` 都从这里读。invoke 覆写只记录不干预，原样
    委托父类（摘要调用的 config 隔离断言依据）。
    """

    received: list = Field(default_factory=list)
    configs: list = Field(default_factory=list)
    reply: str = "最终回答。"

    @property
    def _llm_type(self) -> str:
        return "profiled-fake"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self  # 假模型从不产生 tool_calls，绑定与否不影响行为

    def invoke(self, input, config=None, **kwargs):
        self.configs.append(dict(config or {}))
        return super().invoke(input, config=config, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.received.append(list(messages))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.reply))])


class _SummAgent(BaseAgent):
    """最小桩智能体：无动态槽模板（build_middleware 只产出本需求装配的面）。"""

    agentic_id = "test:summ"

    def __init__(self, model):
        super().__init__(model=model, toolbox=SimpleNamespace(memory=SimpleNamespace(recall=None)))

    def build_system_prompt(self):
        return "人设：{tools}"


def _ctx(messages):
    return AgentRunContext(
        messages=messages,
        thread_id=str(uuid4()),
        run_id="run-1",
        user_id=str(uuid4()),
        now=NOW,
    )


def _template():
    return PromptTemplate.from_template("人设：{tools}")


# ==================== 装配判定（窗口声明驱动） ====================


def test_build_middleware_mounts_summarization_with_profile():
    agent = _SummAgent(model=_ProfiledChatModel(profile={"max_input_tokens": 1000}))
    mws = agent.build_middleware(_template(), {"tools": "- t: x"}, {})
    assert [type(m) for m in mws] == [ContextSummarizationMiddleware]
    # 装配即缺省口径：fraction 0.8 触发（中间件归一为 dict 子句）/ 尾部保留 20 条
    assert mws[0]._trigger_clauses == [{"fraction": CONTEXT_TRIGGER_FRACTION}]
    assert mws[0].keep == ("messages", CONTEXT_KEEP_MESSAGES)


def test_build_middleware_skips_without_profile():
    agent = _SummAgent(model=_ProfiledChatModel())  # 无 profile（未声明窗口）
    assert agent.build_middleware(_template(), {"tools": "- t: x"}, {}) == []


def test_build_middleware_none_model_safe():
    # 桩测试以 model=None 构建图（既有惯例）：读 profile 不得炸
    agent = _SummAgent(model=None)
    assert agent.build_middleware(_template(), {"tools": "- t: x"}, {}) == []


def test_model_max_input_tokens_defensive():
    assert model_max_input_tokens(None) is None
    assert model_max_input_tokens(object()) is None
    assert model_max_input_tokens(_ProfiledChatModel(profile={"name": "fake"})) is None  # 缺 max_input_tokens
    assert model_max_input_tokens(_ProfiledChatModel(profile={"max_input_tokens": 100})) == 100


# ==================== 回调隔离包装 ====================


def test_isolated_model_strips_callbacks_and_keeps_rest():
    inner = _ProfiledChatModel()
    iso = CallbackIsolatedChatModel(inner)

    iso.invoke("hi", config={"metadata": {"x": 1}})

    assert inner.configs[-1].get("callbacks") == []  # 回调置空 → 不进投影/用量
    assert inner.configs[-1]["metadata"] == {"x": 1}  # 既有 config 键原样保留
    assert iso._llm_type == inner._llm_type  # token counter 选择的读取面
    assert iso.profile == inner.profile  # 阈值读取面


def test_isolated_model_async_invoke_strips_callbacks():
    # ainvoke 与 invoke 是两条路径（同步覆写不被异步命中）：异步侧单独记录断言
    import asyncio

    class _AsyncRecording(_ProfiledChatModel):
        async def ainvoke(self, input, config=None, *, stop=None, **kwargs):
            self.configs.append(dict(config or {}))
            return await super().ainvoke(input, config=config, stop=stop, **kwargs)

    inner = _AsyncRecording()
    asyncio.run(CallbackIsolatedChatModel(inner).ainvoke("hi"))
    assert inner.configs[-1].get("callbacks") == []


# ==================== 消费侧判据 ====================


def test_is_internal_stream_item_defensive():
    assert is_internal_stream_item(None) is False
    assert is_internal_stream_item({"message_id": f"{SUMMARY_MESSAGE_ID_PREFIX}x"}) is False  # tools 条目
    stamped = SimpleNamespace(message_id=f"{SUMMARY_MESSAGE_ID_PREFIX}abc")
    assert is_internal_stream_item(stamped) is True
    assert is_internal_stream_item(SimpleNamespace(message_id="lc_run--xyz")) is False
    assert is_internal_stream_item(SimpleNamespace()) is False


# ==================== 零泄漏回归（真实 create_agent 图全链） ====================


def _long_history(turns: int) -> list:
    return [
        HumanMessage(content=_HISTORY_TEXT) if i % 2 == 0 else AIMessage(content=_HISTORY_TEXT)
        for i in range(turns)
    ]


def test_summarization_stream_no_leak():
    """全链契约：触发压缩的 run 中，摘要内部产物不进消费面。

    26 条历史（6500 tokens）越过阈值（0.8 × 1000），尾部 keep 20 条、
    前 6 条被折叠成摘要。可见文本只剩主调用回复；摘要消息进模型输入
    （压缩生效）但不进投影消费面；摘要模型调用的回调被隔离。
    """
    model = _ProfiledChatModel(profile={"max_input_tokens": 1000})
    agent = _SummAgent(model=model)
    history = _long_history(25)
    ctx = _ctx([*history, HumanMessage(content="现在回答我的问题")])

    run = agent.stream(ctx)
    visible, internal = [], []
    for name, item in run.interleave("messages", "tools"):
        (internal if is_internal_stream_item(item) else visible).append(item)

    # 消费面只看到主调用的回复文本（流已收口，投影非阻塞可读）
    texts = [str(item.text) for item in visible if getattr(item, "text", None)]
    assert texts == [model.reply]
    assert not any("Here is a summary" in t for t in texts)

    # 摘要确实发生：主调用输入 = 摘要消息 + 尾部保留，显著短于全量历史，
    # 且首条即带 langchain 摘要文案的摘要消息
    assert len(model.received) == 2  # 摘要调用 + 主调用
    summary_call, main_call = model.received
    assert len(summary_call) == 1  # 摘要调用：单条序列化 prompt
    assert len(main_call) < len(history) + 1
    # 首条是 create_agent 注入的 system，摘要消息紧随其后（折叠后回放给模型）
    assert any(
        str(m.content).startswith("Here is a summary of the conversation to date:")
        for m in main_call
    )

    # 内部产物被 id 盖章判据命中（state 摘要消息）；摘要模型调用经隔离包装
    # 根本不出现在投影——若 langchain 私有实现变更致盖章失效，本断言先红
    assert len(internal) >= 1
    assert all(str(i.message_id).startswith(SUMMARY_MESSAGE_ID_PREFIX) for i in internal)
    assert model.configs[0].get("callbacks") == []  # 摘要调用隔离
    assert model.configs[1].get("callbacks") != []  # 主调用正常携带回调
