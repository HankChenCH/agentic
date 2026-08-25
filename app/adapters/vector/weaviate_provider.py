from typing import Any, ClassVar

import weaviate
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from langchain_weaviate import WeaviateVectorStore

from app.core.config import VectorDBProviderEntry, WeaviateDBProviderEntry
from app.infrastructures.vector.vector_db_provider import VectorDBBuilder, VectorDBProvider, register


@register
class WeaviateVectorDBBuilder(VectorDBBuilder):
    """Weaviate 供应商构建器（weaviate-client v4，gRPC 通道）。

    ``connect_to_local`` 默认执行启动检查（连通性 / 服务端版本），服务
    不可用时 fail-fast；collection 由 ``WeaviateVectorStore`` 构造时自动
    创建（默认 schema 不带 vectorizer，向量由应用侧 embedding 生成）。
    """

    provider: ClassVar[VectorDBProvider] = VectorDBProvider.WEAVIATE

    def build_client(self, entry: WeaviateDBProviderEntry) -> weaviate.WeaviateClient:
        return weaviate.connect_to_local(
            host=entry.host,
            port=entry.port,
            grpc_port=entry.grpc_port,
        )

    def build(
        self,
        client: Any,
        *,
        embeddings: Embeddings,
        index_name: str,
        text_key: str,
        **overrides: Any,
    ) -> VectorStore:
        return WeaviateVectorStore(
            client=client,
            index_name=index_name,
            text_key=text_key,
            embedding=embeddings,
            **overrides,
        )

    def drop(self, client: Any, index_name: str) -> None:
        # collection 不存在时 Weaviate 幂等返回，无需前置 exists 检查
        client.collections.delete(index_name)
