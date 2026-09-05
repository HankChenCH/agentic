from typing import Literal

from pydantic import BaseModel, Field, model_validator


class StandaloneRedisProviderEntry(BaseModel):
    """单机 Redis entry（``redis://`` URL 直连）。"""

    type: Literal["standalone"] = Field(default="standalone", description="Redis 连接类型标识")
    url: str = Field(
        default="redis://127.0.0.1:6379/0",
        description="Redis 连接地址，本地开发默认指向 docker compose 的 redis",
    )
    socket_timeout: float = Field(
        default=5.0,
        description="已建立连接上的命令读写超时（秒），防止单条命令无限挂起",
    )
    socket_connect_timeout: float = Field(
        default=3.0,
        description="建立连接的超时（秒）",
    )


# 未来新增连接形式（cluster / sentinel 等）时在此扩展 Union（按 type 判别）
RedisProviderEntry = StandaloneRedisProviderEntry


class RedisConfig(BaseModel):
    """Redis 连接配置：default 引用 providers 里的一个 entry key。

    直接使用 Redis 的能力（取消信号存储等）与 Celery 队列共用的连接事实
    归属本分节——task.yaml 的 broker/backend 经驱动+key引用指向这里的
    entry（默认同一 entry，即同实例 db0，key 前缀隔离）。
    """

    default: str = Field(default="redis", description="默认 entry key")
    providers: dict[str, RedisProviderEntry] = Field(description="具名 Redis 实例表")

    @model_validator(mode="after")
    def _default_must_exist(self):
        if self.default not in self.providers:
            raise ValueError(f"default provider '{self.default}' not in providers: {list(self.providers)}")
        return self
