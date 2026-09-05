"""SignalStore 契约的进程内实现（第二实现）：测试与单机部署用。

- 惰性过期：读取时按到期时刻判定，过期条目即取即清；
- ``now`` 时钟可注入（默认 ``time.monotonic``），TTL 与 wait 行为因此可测；
- 非 injectable：测试直接构造注入；单机部署如需切换，以 as_type 换绑即可。
"""

from dataclasses import dataclass
from time import monotonic
from typing import Callable

from app.packages.signal.signal_store import SignalStore


@dataclass
class InMemorySignalStore(SignalStore):
    """dict 承载的进程内信号（value, 到期时刻），无跨进程语义。"""

    now: Callable[[], float] = monotonic

    def __post_init__(self):
        self._entries: dict[str, tuple[str, float]] = {}

    def fire(self, key: str, *, value: str = "1", ttl_seconds: int) -> None:
        self._entries[key] = (value, self.now() + ttl_seconds)

    def is_fired(self, key: str) -> bool:
        return self.get(key) is not None

    def reset(self, key: str) -> None:
        self._entries.pop(key, None)

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self.now() >= expires_at:
            del self._entries[key]
            return None
        return value
