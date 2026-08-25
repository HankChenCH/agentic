from dataclasses import dataclass
from typing import Any

from wireup import injectable
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.config import AppConfig, LLMConfig, LLMProviderEntry, ModelTaskType
from app.infrastructures.llm.model_provider import MODEL_BUILDERS, ModelBuilder, ModelProvider

import app.infrastructures.llm.deepseek_provider  # noqa: F401  触发 @register 供应商注册
import app.infrastructures.llm.openai_provider  # noqa: F401
import app.infrastructures.llm.ollama_provider  # noqa: F401


@injectable
@dataclass
class ModelFactory:
    """模型工厂：按 provider entry key 创建模型实例。

    默认宽松、显式严格：未指定时用 ``LLMConfig.default``；
    显式指定但 providers 里不存在的 key，或 entry 的供应商 type 未注册构建器，
    则直接抛 ValueError。

    entry 的用途由其 ``task_type`` 声明：create 只接受 chat entry，
    create_embeddings 只接受 embedding entry，错配即抛 ValueError，
    避免拿错 entry 静默构建出用途错误的模型。
    """

    app_config: AppConfig

    def create(self, name: str | None = None, **overrides: Any) -> BaseChatModel:
        entry, builder = self._resolve(name)
        self._check_task_type(entry, name, ModelTaskType.CHAT)
        return builder.build_chat(entry, **overrides)

    def create_embeddings(self, name: str, **overrides: Any) -> Embeddings:
        """按 provider entry key 创建 embedding 模型。

        name 必填：``LLMConfig.default`` 指向的是 chat entry，不能隐式当作
        embedding 默认值，调用方需显式指定（如 RAG 组件的 embedding entry）。
        """
        entry, builder = self._resolve(name)
        self._check_task_type(entry, name, ModelTaskType.EMBEDDING)
        return builder.build_embedding(entry, **overrides)

    def _resolve(self, name: str | None) -> tuple[LLMProviderEntry, ModelBuilder]:
        config: LLMConfig = self.app_config.llm
        key = name if name is not None else config.default

        entry = config.providers.get(key)
        if entry is None:
            raise ValueError(f"unknown llm provider: {key}, configured: {list(config.providers)}")

        try:
            provider = ModelProvider(entry.type)
        except ValueError:
            supported = [p.value for p in ModelProvider]
            raise ValueError(f"unsupported model provider type: {entry.type} (entry: {key}), supported: {supported}")
        return entry, MODEL_BUILDERS[provider]()

    @staticmethod
    def _check_task_type(entry: LLMProviderEntry, key: str | None, expected: ModelTaskType):
        if entry.task_type is not expected:
            raise ValueError(
                f"llm entry '{key}' task_type is '{entry.task_type.value}', expected '{expected.value}'"
                f" (chat entry → create(), embedding entry → create_embeddings())"
            )
