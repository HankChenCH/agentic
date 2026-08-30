"""通用信号库：key 寻址的分布式 Event（触发—感知—复位—等待）。

契约与后端内聚一包：``SignalStore`` 为抽象契约（wait 为轮询默认实现），
``RedisSignalStore``（默认，经 wireup ``as_type`` 绑定）与
``InMemorySignalStore``（测试/单机）为内聚后端。消费方只注入契约类型；
新增后端 = 新增实现类换绑，消费方无感。扩展路径见 server/AGENTS.md。
"""

from app.packages.signal.memory_signal_store import InMemorySignalStore
from app.packages.signal.redis_signal_store import RedisSignalStore
from app.packages.signal.signal_store import SignalStore

__all__ = ["InMemorySignalStore", "RedisSignalStore", "SignalStore"]
