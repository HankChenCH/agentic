from typing import Any, ClassVar

from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from app.core.config import LLMProviderEntry
from app.infrastructures.llm.model_provider import ModelBuilder, ModelProvider, register


@register
class DeepSeekModelBuilder(ModelBuilder):
    """DeepSeek 供应商构建器。"""

    provider: ClassVar[ModelProvider] = ModelProvider.DEEPSEEK

    def build_chat(self, entry: LLMProviderEntry, **overrides: Any) -> BaseChatModel:
        return ChatDeepSeek(
            api_key=entry.api_key,
            model=entry.model,
            base_url=entry.api_url,
            **overrides,
        )
