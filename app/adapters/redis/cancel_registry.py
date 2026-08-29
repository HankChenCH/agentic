"""CancelSignalStore 端口的 Redis 实现：thread 作用域取消标志的存储机制。

只做机制（SET 带 TTL / EXISTS / DELETE，key 布局 `agentic:cancel:{thread_id}`）；
能力的语义契约——thread 作用域、自过期、失败如实上抛、读宽容归属领域——
见端口 ``app/services/domain/conversation/ports.py`` 与消费方
``ConversationService``。

连接经 ``RedisClientFactory`` 按 redis.yaml 分节（default + providers）解析；
Celery 的 task.yaml 亦以驱动+key引用指向本分节，默认共用同一 entry
（同实例 db0），key 前缀 `agentic:cancel:` 与 broker 自身的键空间隔离。
"""

from dataclasses import dataclass
from uuid import UUID

import redis
from wireup import injectable

from app.infrastructures.redis.redis_factory import RedisClientFactory
from app.services.domain.conversation.ports import CancelSignalStore

_KEY_PREFIX = "agentic:cancel:"
# 孤儿 key 的最长存活（端口契约要求的自过期上限）：正常生命周期内
# open_turn 清理 + TTL 兜底
_TTL_SECONDS = 3600


@injectable(as_type=CancelSignalStore)
@dataclass
class RedisCancelSignalStore:
    """thread 作用域取消标志的 Redis 读写（失败如实上抛，见端口契约）。"""

    client_factory: RedisClientFactory

    def __post_init__(self):
        # decode_responses：标志值就是 "1"，直接拿 str，免去 bytes 解码
        self._client = self.client_factory.create(decode_responses=True)

    def cancel(self, thread_id: UUID | str) -> None:
        self._client.set(_key(thread_id), "1", ex=_TTL_SECONDS)

    def is_canceled(self, thread_id: UUID | str) -> bool:
        return self._client.exists(_key(thread_id)) > 0

    def clear(self, thread_id: UUID | str) -> None:
        self._client.delete(_key(thread_id))


def _key(thread_id: UUID | str) -> str:
    return f"{_KEY_PREFIX}{thread_id}"
