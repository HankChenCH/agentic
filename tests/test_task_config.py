"""TaskConfig 连接引用：解析 / 失效 fail-fast / 随仓 yaml 守护。

纯单测：只构造配置模型，不触碰 Redis；resolve_url 位于 core.config
（纯数据包），无导入副作用。
"""

import pytest

from app.core.config import (
    RedisConfig,
    StandaloneRedisProviderEntry,
    TaskConfig,
    TaskConnectionRef,
    resolve_url,
)
from app.core.config.loader import load_section


def _redis_config(default: str = "redis", **entries: str) -> RedisConfig:
    """entries：entry key → redis URL。"""
    return RedisConfig(
        default=default,
        providers={key: StandaloneRedisProviderEntry(url=url) for key, url in entries.items()},
    )


def test_default_refs_resolve_to_redis_entry_url():
    config = TaskConfig()
    redis = _redis_config(redis="redis://127.0.0.1:6379/0")

    assert resolve_url(config.broker, redis=redis) == "redis://127.0.0.1:6379/0"
    assert resolve_url(config.backend, redis=redis) == "redis://127.0.0.1:6379/0"


def test_refs_can_point_at_different_entries():
    config = TaskConfig(
        broker=TaskConnectionRef(provider="main"),
        backend=TaskConnectionRef(provider="aux"),
    )
    redis = _redis_config(default="main", main="redis://127.0.0.1:6379/0", aux="redis://192.168.1.10:6379/1")

    assert resolve_url(config.broker, redis=redis) == "redis://127.0.0.1:6379/0"
    assert resolve_url(config.backend, redis=redis) == "redis://192.168.1.10:6379/1"


def test_unknown_provider_key_raises():
    config = TaskConfig(broker=TaskConnectionRef(provider="nope"))

    with pytest.raises(ValueError, match="unknown redis provider 'nope' referenced by task config"):
        resolve_url(config.broker, redis=_redis_config(redis="redis://127.0.0.1:6379/0"))


def test_unsupported_driver_raises():
    # model_construct 绕过 pydantic 校验构造非法 driver，走 resolve_url 的
    # supported 报错分支（合法取值在校验期即被 Literal 拒绝）
    config = TaskConfig.model_construct(broker=TaskConnectionRef.model_construct(driver="amqp"))

    with pytest.raises(ValueError, match="unsupported task driver: amqp"):
        resolve_url(config.broker, redis=_redis_config(redis="redis://127.0.0.1:6379/0"))


def test_shipped_task_yaml_loads_and_resolves():
    # 随仓 yaml 守护：真实 task.yaml 必须能加载并解析出可用 URL（防 yaml/模型漂移）
    config = load_section("task.yaml", TaskConfig)
    redis = load_section("redis.yaml", RedisConfig)

    assert resolve_url(config.broker, redis=redis)
    assert resolve_url(config.backend, redis=redis)
