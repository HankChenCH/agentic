from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar

from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore

from app.core.config import VectorDBProviderEntry


class VectorDBProvider(str, Enum):
    """向量库供应商标识，与 ``VectorDBProviderEntry.type`` 对应。"""

    WEAVIATE = "weaviate"


class VectorDBBuilder(ABC):
    """向量库构建器：每个供应商一个子类，把 provider entry 翻译成具体实例。

    分两层构建：``build_client`` 产出供应商原生客户端（持有 gRPC 连接等
    重资源，由工厂按 entry key 缓存复用）；``build`` 基于已有客户端构建
    LangChain ``VectorStore``（面向 collection 的轻量封装，按需创建）。
    契约返回类型为 ``langchain_core.vectorstores.VectorStore``，消费方只
    依赖 LangChain 抽象；entry 已携带全部直连参数（host / port 等），
    构建器自身无需再读全局配置。工厂按 entry 的 ``type`` 路由到注册的
    构建器，保证类型与构建器一一对应。
    """

    provider: ClassVar[VectorDBProvider]

    @abstractmethod
    def build_client(self, entry: VectorDBProviderEntry) -> Any:
        """按 entry 创建供应商原生客户端（重资源，由工厂缓存复用）。"""

    @abstractmethod
    def build(
        self,
        client: Any,
        *,
        embeddings: Embeddings,
        index_name: str,
        text_key: str,
        **overrides: Any,
    ) -> VectorStore:
        """基于已有客户端构建 LangChain VectorStore（契约类型）。"""

    def drop(self, client: Any, index_name: str) -> None:
        """删除整个 collection（LangChain VectorStore 无此能力，按需实现）。"""
        raise NotImplementedError(f"{type(self).__name__} does not support drop")


# 供应商注册表：@register 自动登记，新增供应商不改工厂
VECTOR_DB_BUILDERS: dict[VectorDBProvider, type[VectorDBBuilder]] = {}


def register(builder_cls: type[VectorDBBuilder]) -> type[VectorDBBuilder]:
    VECTOR_DB_BUILDERS[builder_cls.provider] = builder_cls
    return builder_cls
