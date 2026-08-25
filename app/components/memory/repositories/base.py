from abc import ABC, abstractmethod
from typing import List

from app.models.domain.agentic import AgenticMemory


class MemoryRepository(ABC):
    """记忆存取策略抽象：memory 组件自有的数据访问入口。

    存储与召回按策略各自演化（实现类声明 ``@injectable(as_type=MemoryRepository)``
    注册到容器）：当前仅有 SQLite 全量实现；召回策略升级为语义检索时，
    ``recall_memories`` 的 query / limit 参数即为接缝，新增实现类即可切换，
    门面（MemoryService）与调用方无感。
    """

    @abstractmethod
    def store_memories(self, memories: List[AgenticMemory]) -> List[AgenticMemory]:
        """批量写入记忆条目，返回带主键与时间戳的持久化结果。"""

    @abstractmethod
    def recall_memories(self, query: str | None = None, limit: int | None = None) -> List[AgenticMemory]:
        """按召回策略取记忆：query 为检索意图（语义策略使用），limit 为条数上限。"""
