"""通用业务信号契约：key 寻址的分布式 Event（触发—感知—复位—等待）。

行为协议对齐 ``threading.Event`` / ``asyncio.Event``（fire≈set、reset≈clear、
is_fired≈is_set、wait 同名），差异只在分布式属性：

- ``fire`` 幂等覆盖 value 并重置 TTL——同一方法兼作 keepalive 心跳续约；
- ``ttl_seconds`` 必选且须 > 0：跨进程信号必须短命（threading.Event 的
  "不朽"在分布式场景是危险的，孤儿信号靠 TTL 兜底自过期）；
- 错误语义：失败一律如实上抛——读/等的宽容策略（降级不阻断主链路）
  由消费方（领域服务）决定，不属于实现；
- ``wait`` 为轮询默认实现（is_fired + sleep 循环），后端可用原生通知覆写
  （redis pub/sub / etcd watch），消费方无感。

key 为调用方构造的相对键（如 ``conversation:{thread_id}:cancel``），实现
负责加全局命名空间前缀（redis 侧 ``agentic:signal:``），与同实例其他键
空间（Celery broker 等）隔离。后端在本包内聚提供：``RedisSignalStore``
（默认，wireup as_type 绑定）与 ``InMemorySignalStore``（测试/单机进程内）。
"""

from abc import ABC, abstractmethod
from time import monotonic, sleep


class SignalStore(ABC):
    """key 寻址的信号触发—感知—复位—等待契约。"""

    @abstractmethod
    def fire(self, key: str, *, value: str = "1", ttl_seconds: int) -> None:
        """触发信号（幂等）：覆盖 value 并重置 TTL。"""

    @abstractmethod
    def is_fired(self, key: str) -> bool:
        """非阻塞查询触发态。"""

    @abstractmethod
    def reset(self, key: str) -> None:
        """复位为未触发态（幂等，信号本不存在时为 no-op）。"""

    @abstractmethod
    def get(self, key: str) -> str | None:
        """读信号载荷；未触发（或已过期）返回 None。"""

    def wait(self, key: str, *, timeout: float | None = None, poll_interval: float = 0.5) -> bool:
        """阻塞直到信号触发或超时，返回是否触发（threading.Event.wait 语义）。

        轮询实现：以 poll_interval 秒为节奏查触发态，超过 timeout 秒
        （None 为不限时）返回 False。同步阻塞 API——异步消费方须经
        asyncio.to_thread 包装；后端可用原生通知覆写本方法。
        """
        deadline = None if timeout is None else monotonic() + timeout
        while True:
            if self.is_fired(key):
                return True
            if deadline is not None and monotonic() >= deadline:
                return False
            sleep(poll_interval)
