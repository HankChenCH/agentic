from dataclasses import dataclass
from typing import Any

from wireup import injectable
from langchain_core.vectorstores import VectorStore

from app.core.config import AppConfig, VectorDBConfig, VectorDBProviderEntry
from app.infrastructures.llm import ModelFactory
from app.infrastructures.vector.vector_db_provider import VECTOR_DB_BUILDERS, VectorDBBuilder, VectorDBProvider

import app.infrastructures.vector.weaviate_provider  # noqa: F401  触发 @register 供应商注册


@injectable
@dataclass
class VectorStoreFactory:
    """向量库工厂：按 provider entry key 创建 LangChain VectorStore 实例。

    契约类型为 ``langchain_core.vectorstores.VectorStore``，消费方（如
    RAG/检索组件）只依赖该抽象。embedding 模型由 ``vector_db.yaml`` 的
    ``embedding`` 字段指定（指向 llm 的 embedding task_type entry），经
    ModelFactory 解析——entry 不存在或用途不匹配时 fail-fast 抛 ValueError。

    供应商客户端（gRPC 连接等重资源）按 entry key 缓存复用；VectorStore
    是面向 collection 的轻量封装，每次 create 新建，由调用方持有。

    默认宽松、显式严格：未指定 name 时用 ``VectorDBConfig.default``；
    显式指定但 providers 里不存在的 key，或 entry 的供应商 type 未注册
    构建器，则直接抛 ValueError。
    """

    app_config: AppConfig
    model_factory: ModelFactory

    def __post_init__(self):
        self._clients: dict[str, Any] = {}

    def create(
        self,
        *,
        index_name: str,
        name: str | None = None,
        text_key: str = "text",
        **overrides: Any,
    ) -> VectorStore:
        """按 provider entry key 创建绑定到指定 collection 的 VectorStore。

        ``index_name`` 必填：VectorStore 面向单个 collection，没有合理的
        全局默认值；collection 不存在时由供应商实现自动创建。
        ``overrides`` 透传给供应商的 VectorStore 构造参数。
        """
        key = name if name is not None else self._config.default
        entry, builder = self._resolve(key)
        client = self._client_for(key, entry, builder)
        embeddings = self.model_factory.create_embeddings(self._config.embedding)
        return builder.build(
            client,
            embeddings=embeddings,
            index_name=index_name,
            text_key=text_key,
            **overrides,
        )

    def drop_index(self, *, index_name: str, name: str | None = None) -> None:
        """删除整个 collection（如删知识库时清理向量库）。"""
        key = name if name is not None else self._config.default
        entry, builder = self._resolve(key)
        client = self._client_for(key, entry, builder)
        builder.drop(client, index_name)

    def close(self) -> None:
        """关闭并清空缓存的供应商客户端（进程优雅关闭时调用）。

        逐客户端经注册构建器释放（如 weaviate 的 gRPC 通道）；close 是
        快速失败语义，best-effort 容错由调用方（HTTP lifespan）负责。
        """
        for key, client in self._clients.items():
            self._resolve(key)[1].close(client)
        self._clients.clear()

    @property
    def _config(self) -> VectorDBConfig:
        return self.app_config.vector_db

    def _resolve(self, key: str) -> tuple[VectorDBProviderEntry, VectorDBBuilder]:
        entry = self._config.providers.get(key)
        if entry is None:
            raise ValueError(f"unknown vector db provider: {key}, configured: {list(self._config.providers)}")

        try:
            provider = VectorDBProvider(entry.type)
        except ValueError:
            supported = [p.value for p in VectorDBProvider]
            raise ValueError(
                f"unsupported vector db provider type: {entry.type} (entry: {key}), supported: {supported}"
            )
        return entry, VECTOR_DB_BUILDERS[provider]()

    def _client_for(self, key: str, entry: VectorDBProviderEntry, builder: VectorDBBuilder) -> Any:
        """按 entry key 取共享客户端，首次调用时构建并缓存。"""
        if key not in self._clients:
            self._clients[key] = builder.build_client(entry)
        return self._clients[key]
