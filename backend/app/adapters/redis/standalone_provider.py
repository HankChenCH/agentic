from typing import ClassVar

import redis

from app.core.config import StandaloneRedisProviderEntry
from app.adapters.redis.redis_provider import RedisClientBuilder, RedisProvider, register


@register
class StandaloneRedisBuilder(RedisClientBuilder):
    """单机 Redis 构建器：``redis://`` URL 直连。"""

    provider: ClassVar[RedisProvider] = RedisProvider.STANDALONE

    def build(self, entry: StandaloneRedisProviderEntry, *, decode_responses: bool = False) -> redis.Redis:
        # lazy：from_url 不发起连接，真正的连接发生在首个命令；
        # socket 超时避免 Redis 抖动时取消检查/收尾等路径无限挂起
        return redis.Redis.from_url(
            entry.url,
            decode_responses=decode_responses,
            socket_timeout=entry.socket_timeout,
            socket_connect_timeout=entry.socket_connect_timeout,
        )
