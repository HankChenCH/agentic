from typing import Any, ClassVar

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import LLMProviderEntry
from app.infrastructures.llm.model_provider import ModelBuilder, ModelProvider, register


@register
class OpenAIModelBuilder(ModelBuilder):
    """OpenAI 供应商构建器（兼容 OpenAI 协议的中转/三方网关）。

    chat 与 embedding 两种 task_type 均支持，按 entry 声明分别构建。
    """

    provider: ClassVar[ModelProvider] = ModelProvider.OPENAI

    def build_chat(self, entry: LLMProviderEntry, **overrides: Any) -> BaseChatModel:
        return ChatOpenAI(
            api_key=entry.api_key,
            model=entry.model,
            base_url=entry.api_url,
            **overrides,
        )

    def build_embedding(self, entry: LLMProviderEntry, **overrides: Any) -> Embeddings:
        return OpenAIEmbeddings(
            api_key=entry.api_key,
            model=entry.model,
            base_url=entry.api_url,
            **overrides,
        )
