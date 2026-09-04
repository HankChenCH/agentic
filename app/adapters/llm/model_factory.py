from dataclasses import dataclass
from typing import Any

from wireup import injectable
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel

from app.core.config import AppConfig, LLMConfig, LLMProviderEntry, ModelTaskType
from app.adapters.llm.model_provider import MODEL_BUILDERS, ModelBuilder, ModelProvider

import app.adapters.llm.deepseek_provider  # noqa: F401  触发 @register 供应商注册
import app.adapters.llm.openai_provider  # noqa: F401
import app.adapters.llm.ollama_provider  # noqa: F401


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

    模型实例内含 HTTP 连接池（httpx 客户端），属重资源，按 entry key
    缓存复用——同一 entry 多次 ``create`` 拿到同一实例（entry 的
    ``task_type`` 唯一，同一 key 不会同时产出 chat / embedding 两种模型）。
    带 ``overrides`` 的调用绕过缓存：定制参数不定型，不污染后续调用。
    LangChain 模型是无状态 Runnable、httpx.Client 线程安全，跨请求 /
    跨线程复用安全（与 db / redis / vector / filesystem 工厂同一惯例）。
    """

    app_config: AppConfig

    def __post_init__(self):
        self._models: dict[str, Any] = {}

    def create(self, name: str | None = None, **overrides: Any) -> BaseChatModel:
        key = name if name is not None else self._config.default
        entry, builder = self._resolve(key)
        self._check_task_type(entry, key, ModelTaskType.CHAT)
        if not overrides and key in self._models:
            return self._models[key]
        model = builder.build_chat(entry, **overrides)
        if not overrides:
            self._models[key] = model
        return model

    def create_embeddings(self, name: str, **overrides: Any) -> Embeddings:
        """按 provider entry key 创建 embedding 模型。

        name 必填：``LLMConfig.default`` 指向的是 chat entry，不能隐式当作
        embedding 默认值，调用方需显式指定（如 RAG 组件的 embedding entry）。
        """
        entry, builder = self._resolve(name)
        self._check_task_type(entry, name, ModelTaskType.EMBEDDING)
        if not overrides and name in self._models:
            return self._models[name]
        model = builder.build_embedding(entry, **overrides)
        if not overrides:
            self._models[name] = model
        return model

    @property
    def _config(self) -> LLMConfig:
        return self.app_config.llm

    def _resolve(self, key: str) -> tuple[LLMProviderEntry, ModelBuilder]:
        entry = self._config.providers.get(key)
        if entry is None:
            raise ValueError(f"unknown llm provider: {key}, configured: {list(self._config.providers)}")

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
