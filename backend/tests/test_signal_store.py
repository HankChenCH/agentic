"""SignalStore 契约行为：InMemory 后端全行为 + Redis 后端机制映射（stub 客户端）。

纯单测：不连 Redis；wait 的轮询以 monkeypatch sleep / 极小间隔覆盖。
"""

import pytest

from app.packages.signal.memory_signal_store import InMemorySignalStore
from app.packages.signal.redis_signal_store import RedisSignalStore
from app.packages.signal.signal_store import SignalStore


class FakeClock:
    """可推进的单调时钟（注入 InMemorySignalStore.now，TTL 行为可测）。"""

    def __init__(self):
        self._now = 1000.0

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def fail_if_called(_seconds):
    raise AssertionError("不应轮询：信号已触发应立即返回")


# ---- InMemorySignalStore：触发—感知—复位—等待全行为 ----


def test_fire_is_fired_get_reset_roundtrip():
    store = InMemorySignalStore()
    assert not store.is_fired("k")
    assert store.get("k") is None

    store.fire("k", ttl_seconds=60)
    assert store.is_fired("k")
    assert store.get("k") == "1"  # 默认载荷

    store.reset("k")
    assert not store.is_fired("k")
    assert store.get("k") is None

    store.reset("k")  # 幂等：复位不存在的信号为 no-op


def test_fire_carries_payload_value():
    store = InMemorySignalStore()
    store.fire("k", value="running", ttl_seconds=60)
    assert store.get("k") == "running"


def test_ttl_expiry_auto_expires():
    """信号必短命：过期后触发态消失，条目即取即清。"""
    clock = FakeClock()
    store = InMemorySignalStore(now=clock)
    store.fire("k", ttl_seconds=10)

    clock.advance(9)
    assert store.is_fired("k")

    clock.advance(1)  # 恰到到期
    assert not store.is_fired("k")
    assert store.get("k") is None


def test_fire_overwrites_value_and_refreshes_ttl():
    """心跳续约语义：周期 fire 覆盖载荷并重置 TTL（keepalive 即此用法）。"""
    clock = FakeClock()
    store = InMemorySignalStore(now=clock)
    store.fire("k", value="a", ttl_seconds=10)

    clock.advance(8)
    store.fire("k", value="b", ttl_seconds=10)

    clock.advance(8)  # 已越过首次到期点，仍在续约窗口内
    assert store.is_fired("k")
    assert store.get("k") == "b"


def test_wait_returns_true_when_already_fired(monkeypatch):
    store = InMemorySignalStore()
    store.fire("k", ttl_seconds=60)
    monkeypatch.setattr("app.packages.signal.signal_store.sleep", fail_if_called)
    assert store.wait("k", timeout=1) is True


def test_wait_blocks_until_fired(monkeypatch):
    store = InMemorySignalStore()
    state = {"polls": 0}

    def fire_on_second_poll(_seconds):
        state["polls"] += 1
        if state["polls"] == 2:
            store.fire("k", ttl_seconds=60)

    monkeypatch.setattr("app.packages.signal.signal_store.sleep", fire_on_second_poll)
    assert store.wait("k", timeout=10, poll_interval=0.01) is True
    assert state["polls"] == 2


def test_wait_times_out_returning_false():
    store = InMemorySignalStore()
    assert store.wait("k", timeout=0.05, poll_interval=0.01) is False


def test_signal_store_is_abstract():
    with pytest.raises(TypeError):
        SignalStore()  # type: ignore[abstract]


# ---- RedisSignalStore：机制映射（key 布局 / 命令参数），stub 客户端不连网 ----


class StubRedis:
    """承载最小行为的 redis 替身（验证机制映射，不验证 Redis 本身）。"""

    def __init__(self):
        self.data = {}

    def set(self, key, value, ex=None):
        self.data[key] = (value, ex)

    def get(self, key):
        entry = self.data.get(key)
        return entry[0] if entry else None

    def exists(self, key):
        return 1 if key in self.data else 0

    def delete(self, key):
        self.data.pop(key, None)


class StubRedisClientFactory:
    """捕获 create 参数的工厂替身。"""

    def __init__(self):
        self.client = StubRedis()
        self.create_kwargs = None

    def create(self, **kwargs):
        self.create_kwargs = kwargs
        return self.client


def make_redis_store():
    factory = StubRedisClientFactory()
    return RedisSignalStore(client_factory=factory), factory


def test_redis_fire_uses_namespaced_key_and_ttl():
    store, factory = make_redis_store()
    client = factory.client

    store.fire("conversation:t1:cancel", ttl_seconds=3600)

    assert "agentic:signal:conversation:t1:cancel" in client.data
    assert client.data["agentic:signal:conversation:t1:cancel"] == ("1", 3600)


def test_redis_client_created_with_decoded_responses():
    store, factory = make_redis_store()

    store.fire("k", ttl_seconds=1)

    assert factory.create_kwargs == {"decode_responses": True}


def test_redis_is_fired_get_reset_map_to_commands():
    store, _ = make_redis_store()

    store.fire("k", value="running", ttl_seconds=30)
    assert store.is_fired("k") is True
    assert store.get("k") == "running"

    store.reset("k")
    assert store.is_fired("k") is False
    assert store.get("k") is None

    store.reset("k")  # 幂等
