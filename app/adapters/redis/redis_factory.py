from dataclasses import dataclass

import redis
from wireup import injectable

from app.core.config import AppConfig, RedisConfig, RedisProviderEntry
from app.infrastructures.redis.redis_provider import REDIS_BUILDERS, RedisClientBuilder, RedisProvider

import app.infrastructures.redis.standalone_provider  # noqa: F401  触发 @register 连接形式注册


@injectable
@dataclass
class RedisClientFactory:
    """Redis 客户端工厂：按 provider entry key 创建 redis.Redis 客户端。

    契约类型即 redis-py 的 :class:`redis.Redis`，连接形式（standalone /
    未来的 cluster、sentinel）差异由构建器注册表消化，消费方不感知。
    客户端持有连接池，属重资源，按 (entry key, decode_responses) 缓存
    复用——同一组合多次 ``create`` 拿到同一实例。lazy：构造客户端不发起
    连接，真正的连接发生在首个命令。

    默认宽松、显式严格：未指定 name 时用 ``RedisConfig.default``；显式
    指定但 providers 里不存在的 key，或 entry 的连接形式未注册构建器，
    则直接抛 ValueError。
    """

    app_config: AppConfig

    def __post_init__(self):
        self._clients: dict[tuple[str, bool], redis.Redis] = {}

    def create(self, *, name: str | None = None, decode_responses: bool = False) -> redis.Redis:
        """按 provider entry key 创建 Redis 客户端。"""
        key = name if name is not None else self._config.default
        entry, builder = self._resolve(key)
        cache_key = (key, decode_responses)
        if cache_key not in self._clients:
            self._clients[cache_key] = builder.build(entry, decode_responses=decode_responses)
        return self._clients[cache_key]

    @property
    def _config(self) -> RedisConfig:
        return self.app_config.redis

    def _resolve(self, key: str) -> tuple[RedisProviderEntry, RedisClientBuilder]:
        entry = self._config.providers.get(key)
        if entry is None:
            raise ValueError(f"unknown redis provider: {key}, configured: {list(self._config.providers)}")

        try:
            provider = RedisProvider(entry.type)
        except ValueError:
            supported = [p.value for p in RedisProvider]
            raise ValueError(
                f"unsupported redis provider type: {entry.type} (entry: {key}), supported: {supported}"
            )
        return entry, REDIS_BUILDERS[provider]()


@injectable(lifetime="singleton")
def create_default_redis(factory: RedisClientFactory) -> redis.Redis:
    """默认 Redis 客户端（``RedisConfig.default``）的注入入口。

    仿 ``create_default_db`` 的惯例：消费方（就绪探针等）直接注入
    ``redis.Redis`` 契约类型使用，无需感知工厂（api 层禁入
    infrastructures 的分层禁边由此绕开——只依赖第三方类型）；需要指定
    其他 entry 时注入 :class:`RedisClientFactory` 自行 ``create(name=...)``。
    lazy——wireup 首次解析本函数才触发构建，连接发生在首个命令。
    """
    return factory.create()
