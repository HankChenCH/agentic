"""SignalStore 契约的 Redis 实现：默认后端（wireup as_type 绑定）。

只做机制（SET 带 TTL / GET / EXISTS / DELETE，wait 继承契约的轮询默认）；
相对 key 加全局前缀 ``agentic:signal:``，与同实例其他键空间隔离。连接经
``RedisClientFactory`` 按 redis.yaml 分节（default + providers）解析——
Celery 的 task.yaml 亦以驱动+key 引用指向本分节，默认共用同一 entry
（同实例 db0）。
"""

from dataclasses import dataclass

from wireup import injectable

from app.adapters.redis.redis_factory import RedisClientFactory
from app.packages.signal.signal_store import SignalStore

_KEY_NAMESPACE = "agentic:signal"


@injectable(as_type=SignalStore)
@dataclass
class RedisSignalStore(SignalStore):
    """Redis 信号存储（失败如实上抛，见契约 ``SignalStore``）。"""

    client_factory: RedisClientFactory

    def __post_init__(self):
        # decode_responses：信号 value 即 str，免去 bytes 解码
        self._client = self.client_factory.create(decode_responses=True)

    def fire(self, key: str, *, value: str = "1", ttl_seconds: int) -> None:
        self._client.set(_key(key), value, ex=ttl_seconds)

    def is_fired(self, key: str) -> bool:
        return self._client.exists(_key(key)) > 0

    def reset(self, key: str) -> None:
        self._client.delete(_key(key))

    def get(self, key: str) -> str | None:
        return self._client.get(_key(key))


def _key(key: str) -> str:
    return f"{_KEY_NAMESPACE}:{key}"
