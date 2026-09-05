"""用量追踪包装：非流式 LLM 调用的 usage 捕获（内部任务的模型面）。

背景：记忆巩固/消歧裁决等内部 LLM 调用点都是
``model.with_structured_output(...).invoke(...)`` 形态——``include_raw=False``
只返回解析后的 pydantic 对象，usage 根本不可达。本模块用 duck-typed 薄包装
把用量旁路出来，调用点零改动：

- 裸 ``invoke``：读返回消息的 ``usage_metadata``（经 normalize_usage 归一）；
- ``with_structured_output``：委托内部模型构建链，给返回的 Runnable 注入
  ``on_llm_end`` 回调——回调沿 RunnableConfig 冒泡到链内模型调用，
  ``LLMResult`` 的 llm_output["token_usage"]（OpenAI 兼容族）或末条
  generation 消息的 usage_metadata 都能取到用量（与 LangSmith 追踪同机制）。

只承诺 ``invoke`` / ``with_structured_output`` 两个消费面（内部任务的全部
用法），不继承 BaseChatModel——避免为非流式内部任务承担流式/绑定等完整
模型协议面。产出经 sink 回调（``UsageService.usage_sink``，调用方按
user/thread/turn/scene 组装）落库，容错在 sink 内。

归属说明：本模块是计量域的纯逻辑（零供应商依赖，langchain-core 回调协议
属领域侧框架白名单）——调用方（组件/application）从模型获取端口拿裸模型
后自行包装，「是否计量、何场景、何上下文」的策略留在编排侧。
"""

from typing import Any, Callable

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from app.domain.usage.extract import llm_model_name, normalize_usage

# sink(model_name, usage_dict)：usage_dict 为归一化后的 {prompt/completion/total}_tokens
UsageSink = Callable[[str, dict], None]


class UsageTrackingChatModel:
    """裸模型的用量追踪薄包装：非流式调用后把用量交给 sink。"""

    def __init__(self, inner: Any, sink: UsageSink):
        self._inner = inner
        self._sink = sink
        self._model_name = llm_model_name(inner)

    @property
    def model_name(self) -> str:
        return self._model_name

    def invoke(self, messages, **kwargs):
        response = self._inner.invoke(messages, **kwargs)
        usage = normalize_usage(getattr(response, "usage_metadata", None))
        if usage:
            self._sink(self._model_name, usage)
        return response

    def with_structured_output(self, schema, **kwargs):
        chain = self._inner.with_structured_output(schema, **kwargs)
        return _TrackedChain(chain, self)


class _TrackedChain:
    """with_structured_output 返回链的薄包装：invoke 时注入 on_llm_end 回调。"""

    def __init__(self, chain: Any, tracker: UsageTrackingChatModel):
        self._chain = chain
        self._tracker = tracker

    def invoke(self, user_input: Any, config: dict | None = None, **kwargs):
        handler = _SinkHandler(self._tracker)
        merged = dict(config or {})
        merged["callbacks"] = [*list(merged.get("callbacks") or []), handler]
        return self._chain.invoke(user_input, config=merged, **kwargs)


class _SinkHandler(BaseCallbackHandler):
    """on_llm_end 回调：从 LLMResult 提取用量交给 sink（单次 invoke 只报一次）。"""

    def __init__(self, tracker: UsageTrackingChatModel):
        self._tracker = tracker
        self._reported = False

    def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        if self._reported:
            return
        self._reported = True
        usage = _usage_from_llm_result(response)
        if usage:
            self._tracker._sink(self._tracker._model_name, usage)


def _usage_from_llm_result(response: LLMResult) -> dict:
    """优先末条 generation 消息的 usage_metadata，回退 llm_output["token_usage"]。"""
    try:
        message = getattr(response.generations[-1][-1], "message", None)
        usage = normalize_usage(getattr(message, "usage_metadata", None))
        if usage:
            return usage
    except (IndexError, TypeError):
        pass
    llm_output = getattr(response, "llm_output", None)
    return normalize_usage((llm_output or {}).get("token_usage"))
