from typing import Literal
from pydantic import BaseModel, Field, model_validator


class WeaviateDBProviderEntry(BaseModel):
    """Weaviate 向量库 entry。

    ``grpc_port`` 供 weaviate-client v4 的 gRPC 通道使用（与 HTTP ``port``
    分开暴露）；服务端未开鉴权时无需凭证字段，接入鉴权部署时在此扩展。
    """
    type: Literal["weaviate"] = Field(default="weaviate", description="向量库类型标识")
    host: str = Field(
        default="127.0.0.1",
        description="Weaviate HTTP 地址",
    )
    port: int = Field(
        default=8080,
        description="Weaviate HTTP 端口",
    )
    grpc_port: int = Field(
        default=50051,
        description="Weaviate gRPC 端口（weaviate-client v4 需要）",
    )

# 未来新增向量库供应商时在此扩展 Union（按 type 判别）
VectorDBProviderEntry = WeaviateDBProviderEntry


class VectorDBConfig(BaseModel):
    """向量库配置：default 引用 providers 里的一个 entry key。

    ``embedding`` 指向 ``llm.yaml`` 里 task_type 为 embedding 的 entry key
    （向量由应用侧生成，向量库端不配置 vectorizer）；entry 不存在或用途
    不匹配时，由 VectorStoreFactory 创建期经 ModelFactory fail-fast。
    """
    default: str = Field(default="weaviate", description="默认 entry key")
    embedding: str = Field(description="embedding 模型的 llm provider entry key")
    providers: dict[str, VectorDBProviderEntry] = Field(description="具名向量库实例表")

    @model_validator(mode="after")
    def _default_must_exist(self):
        if self.default not in self.providers:
            raise ValueError(f"default provider '{self.default}' not in providers: {list(self.providers)}")
        return self
