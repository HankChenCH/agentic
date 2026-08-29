from abc import ABC, abstractmethod
from enum import Enum
from typing import ClassVar

import redis

from app.core.config import RedisProviderEntry


class RedisProvider(str, Enum):
    """Redis 连接形式标识，与 ``RedisProviderEntry.type`` 对应。"""

    STANDALONE = "standalone"


class RedisClientBuilder(ABC):
    """Redis 构建器：每种连接形式一个子类，把 provider entry 翻译成客户端。

    契约类型即 redis-py 的 :class:`redis.Redis`（单一客户端库，无需
    自定契约 ABC），连接形式差异全部收敛在构建器里。客户端持有连接池，
    属重资源，由工厂按 key 缓存复用；``decode_responses`` 是消费方序列化
    偏好（取 str 而非 bytes），不属于部署拓扑，故作为构建参数传入而非
    entry 字段。
    """

    provider: ClassVar[RedisProvider]

    @abstractmethod
    def build(self, entry: RedisProviderEntry, *, decode_responses: bool = False) -> redis.Redis:
        """按 entry 创建 Redis 客户端（连接池重资源，由工厂缓存复用）。"""


# 构建器注册表：@register 自动登记，新增连接形式不改工厂
REDIS_BUILDERS: dict[RedisProvider, type[RedisClientBuilder]] = {}


def register(builder_cls: type[RedisClientBuilder]) -> type[RedisClientBuilder]:
    REDIS_BUILDERS[builder_cls.provider] = builder_cls
    return builder_cls
