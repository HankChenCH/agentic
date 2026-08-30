from typing import Any, ClassVar

from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_ollama import ChatOllama, OllamaEmbeddings

from app.core.config import LLMProviderEntry
from app.infrastructures.llm.model_provider import ModelBuilder, ModelProvider, register


@register
class OllamaModelBuilder(ModelBuilder):
    """Ollama 供应商构建器：面向本地部署的模型服务。

    本地 Ollama 默认无需密钥（entry 可省略 api_key）；如网关启用了鉴权，
    将凭证写进 api_url 的 userinfo 即可，如 ``http://user:pass@127.0.0.1:11434``。
    """

    provider: ClassVar[ModelProvider] = ModelProvider.OLLAMA

    @staticmethod
    def _client_kwargs(entry: LLMProviderEntry) -> dict[str, Any]:
        # ollama 客户端超时不在模型字段上，经 client_kwargs 传给底层 httpx 客户端
        return {"timeout": entry.timeout} if entry.timeout is not None else {}

    def build_chat(self, entry: LLMProviderEntry, **overrides: Any) -> BaseChatModel:
        return ChatOllama(
            model=entry.model,
            base_url=entry.api_url,
            client_kwargs=self._client_kwargs(entry),
            **overrides,
        )

    def build_embedding(self, entry: LLMProviderEntry, **overrides: Any) -> Embeddings:
        return OllamaEmbeddings(
            model=entry.model,
            base_url=entry.api_url,
            client_kwargs=self._client_kwargs(entry),
            **overrides,
        )
