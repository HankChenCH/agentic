"""RedisClientFactory 实例管理：entry 解析 / 客户端缓存 / 错误路径。

纯单测：redis.Redis.from_url 惰性建连（构造不发网络请求），无需中间件
即可覆盖工厂的路由与缓存逻辑。
"""

from dataclasses import dataclass

import pytest
from redis import Redis

from app.core.config import RedisConfig, StandaloneRedisProviderEntry
from app.adapters.redis import RedisClientFactory


@dataclass
class _StubAppConfig:
    """最小 AppConfig 替身：工厂只消费 redis 分节，避免单测加载全量 yaml。"""

    redis: RedisConfig


def _factory(entries: dict[str, str], default: str = "main") -> RedisClientFactory:
    """entries：entry key → redis URL。"""
    config = RedisConfig(
        default=default,
        providers={key: StandaloneRedisProviderEntry(url=url) for key, url in entries.items()},
    )
    return RedisClientFactory(app_config=_StubAppConfig(redis=config))


def test_create_default_entry_builds_client():
    factory = _factory({"main": "redis://127.0.0.1:6379/0"})

    client = factory.create()

    assert isinstance(client, Redis)
    assert client.get_connection_kwargs()["decode_responses"] is False


def test_decode_responses_flag_propagates():
    """decode_responses 是消费方序列化偏好，经 create 参数传入构建器。"""
    factory = _factory({"main": "redis://127.0.0.1:6379/0"})

    client = factory.create(decode_responses=True)

    assert client.get_connection_kwargs()["decode_responses"] is True


def test_create_by_name_selects_matching_entry():
    factory = _factory({"main": "redis://127.0.0.1:6379/0", "aux": "redis://127.0.0.1:6380/1"})

    main = factory.create()
    aux = factory.create(name="aux")

    assert main.connection_pool.connection_kwargs["db"] == 0
    assert aux.connection_pool.connection_kwargs["db"] == 1
    assert aux.connection_pool.connection_kwargs["port"] == 6380


def test_client_cached_per_entry_and_flag():
    # 客户端持有连接池（重资源），同一 (entry, decode_responses) 组合复用同一实例
    factory = _factory({"main": "redis://127.0.0.1:6379/0", "aux": "redis://127.0.0.1:6380/1"})

    assert factory.create() is factory.create()
    assert factory.create(decode_responses=True) is factory.create(decode_responses=True)
    assert factory.create() is not factory.create(decode_responses=True)
    assert factory.create() is not factory.create(name="aux")


def test_unknown_key_raises():
    factory = _factory({"main": "redis://127.0.0.1:6379/0"})

    with pytest.raises(ValueError, match="unknown redis provider: nope"):
        factory.create(name="nope")


def test_unsupported_type_raises():
    # model_construct 绕过 pydantic 校验构造 type 未注册的脏 entry，
    # 走 _resolve 的 supported 报错分支（合法配置在校验期即被拒绝）
    dirty = StandaloneRedisProviderEntry.model_construct(type="bogus")
    config = RedisConfig.model_construct(default="main", providers={"main": dirty})
    factory = RedisClientFactory(app_config=_StubAppConfig(redis=config))

    with pytest.raises(ValueError, match="unsupported redis provider type: bogus"):
        factory.create()
