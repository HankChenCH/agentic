from typing import Any, ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from app.core.config import LLMProviderEntry
from app.infrastructures.llm.model_provider import ModelBuilder, ModelProvider, register


class ThinkingAwareChatDeepSeek(ChatDeepSeek):
    """能力感知的 DeepSeek 聊天模型：结构化输出 method 自发现 + 思考模式对齐。

    langchain 的 ``with_structured_output(method=...)`` 各 method 在 DeepSeek
    通道上的真实请求形态不同（已对本栈离线断言并经线上验证）：

    - ``function_calling`` / ``json_schema``（langchain-deepseek 把后者重映射
      为前者）：绑定工具并**强制 tool_choice**——DeepSeek 思考模式拒绝该请求
      （400 "Thinking mode does not support this tool_choice"），与思考互斥；
    - ``json_mode``：``response_format=json_object``，无 tool_choice——思考
      兼容（线上验证可同时返回 reasoning_content 与合法 JSON）。

    entry 的 ``capabilities`` 声明与上述 method 对齐：``thinkable`` 声明可
    思考；``features`` 声明支持的 method 白名单（声明顺序即自发现优先级）。
    本类据此自发现与对齐，规则只此一处，调用方零改动：

    - 未指定 method → 按声明顺序自发现：thinkable 时跳过思考互斥项，全部
      互斥则回退首个并自动以关思考副本执行；
    - 显式 method → 先按 langchain-deepseek 别名归一（json_schema →
      function_calling），未声明即 ValueError fail-fast（声明与调用不一致属
      配置错误，不静默降级）；
    - features 未声明 → 不发现不校验，沿用父类默认（function_calling）。
    """

    thinkable: bool = False
    features: tuple[str, ...] = ()
    # capabilities.multimodal 声明（text/vision）；"vision" in multimodal 由
    # BaseAgent.supports_vision 读取，决定图片输入透传还是降级
    multimodal: tuple[str, ...] = ()

    # langchain-deepseek 的 method 别名：json_schema 实际重映射为 function_calling
    _METHOD_ALIASES: ClassVar[dict] = {"json_schema": "function_calling"}
    # 强制 tool_choice 的 method（与思考互斥）；json_schema 已被别名归一进来
    _FORCED_TOOL_CHOICE: ClassVar[set] = {"function_calling"}
    # features 未声明时交回父类的默认 method
    _DEFAULT_METHOD: ClassVar[str] = "function_calling"

    def with_structured_output(
        self, schema=None, *, method=None, include_raw=False,
        strict=None, **kwargs: Any,
    ):
        if not self.features:
            return super().with_structured_output(
                schema, method=method or self._DEFAULT_METHOD,
                include_raw=include_raw, strict=strict, **kwargs,
            )
        # 归一先于一切判定：声明的 json_schema 实际就是 function_calling
        # （强制 tool_choice），不归一会误判为思考兼容
        normalized = [self._METHOD_ALIASES.get(f, f) for f in self.features]
        resolved = self._METHOD_ALIASES.get(method, method) if method else self._discover(normalized)
        if resolved not in normalized:
            raise ValueError(
                f"structured output method {method!r} is not declared in this llm entry's "
                f"capabilities.features {list(self.features)}; declare it or use a declared method"
            )
        if self.thinkable and resolved in self._FORCED_TOOL_CHOICE:
            # extra_body 由 OpenAI SDK 合并进请求 JSON body（供应商私有参数的
            # 正规通道）；model_copy 浅拷贝共享 client，与 langchain 自身 strict
            # 路径的 model_copy(beta endpoint) 同款先例
            disabled = self.model_copy(update={
                "extra_body": {**(self.extra_body or {}), "thinking": {"type": "disabled"}},
            })
            # 未绑定直调父类实现：副本同为本子类，经副本调用会无限递归
            return ChatDeepSeek.with_structured_output(
                disabled, schema, method=resolved,
                include_raw=include_raw, strict=strict, **kwargs,
            )
        return super().with_structured_output(
            schema, method=resolved, include_raw=include_raw, strict=strict, **kwargs
        )

    def _discover(self, normalized: list[str]) -> str:
        """按声明顺序自发现 method：thinkable 优先思考兼容项，全部互斥回退首个。"""
        if self.thinkable:
            for feature in normalized:
                if feature not in self._FORCED_TOOL_CHOICE:
                    return feature
        return normalized[0]


@register
class DeepSeekModelBuilder(ModelBuilder):
    """DeepSeek 供应商构建器。"""

    provider: ClassVar[ModelProvider] = ModelProvider.DEEPSEEK

    def build_chat(self, entry: LLMProviderEntry, **overrides: Any) -> BaseChatModel:
        return ThinkingAwareChatDeepSeek(
            api_key=entry.api_key,
            model=entry.model,
            base_url=entry.api_url,
            request_timeout=entry.timeout,
            thinkable=entry.capabilities.thinkable,
            features=tuple(entry.capabilities.features),
            multimodal=tuple(entry.capabilities.multimodal),
            **overrides,
        )
