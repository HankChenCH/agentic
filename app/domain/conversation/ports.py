"""领域侧端口（依赖倒置）：协议定义在领域层，实现由外层以
``@injectable(as_type=...)`` 回填，保证依赖箭头仍指向领域。

当前端口：
- ``CancelSignalStore``：聊天轮次显式取消的信号存储。断链（GeneratorExit）
  取消依赖传输层把断开及时传导到服务端，生产环境的代理/缓冲/keepalive
  可能吞掉或延迟该事件，故提供与传输层解耦的取消信号——REST cancel
  接口写标志，编排层流式循环（帧级节流）与工具入口协作式读，命中即收口。
  Redis 实现在 ``app/infrastructures/redis/``（RedisCancelSignalStore，连接
  来自独立的 redis.yaml 配置分节）；tests/conftest.py 的内存实现是第二实现。
"""

from typing import Protocol
from uuid import UUID


class CancelSignalStore(Protocol):
    """thread 作用域取消标志的读写契约。

    - 作用域：一个会话同一时刻只有一个活跃轮次，标志按 thread 覆盖；
    - 自过期：实现必须给标志设上限寿命，防孤儿标志误杀下一轮（正常
      生命周期内 open_turn 会防御性清理，上限寿命只作兜底）；
    - 错误语义：失败一律如实上抛——写失败要如实反馈取消调用方；读/清
      的宽容策略（降级不阻断聊天主链路）由消费方 ConversationService 决定，
      不属于实现。
    """

    def cancel(self, thread_id: UUID | str) -> None: ...

    def is_canceled(self, thread_id: UUID | str) -> bool: ...

    def clear(self, thread_id: UUID | str) -> None: ...
