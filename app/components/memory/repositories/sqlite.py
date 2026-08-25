from typing import List
from dataclasses import dataclass

from wireup import injectable
from sqlmodel import Session, select
from sqlalchemy import Engine

from app.models.domain.agentic import AgenticMemory
from app.components.memory.repositories.base import MemoryRepository


@injectable(as_type=MemoryRepository)
@dataclass
class SqliteMemoryRepository(MemoryRepository):
    """SQLite 存取策略：复用共享 db 基建注入的 Engine。

    v1 全量召回：按主键（入库先后）升序返回，忽略 query / limit；
    条目量大了以后新增语义检索实现类（向量库等）替换绑定即可。
    """

    engine: Engine

    def recall_memories(self, query: str | None = None, limit: int | None = None) -> List[AgenticMemory]:
        with Session(self.engine, expire_on_commit=False) as session:
            memories = session.exec(select(AgenticMemory)).all()
            session.commit()

        return list(memories)

    def store_memories(self, memories: List[AgenticMemory]) -> List[AgenticMemory]:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add_all(memories)
            session.commit()
            for memory in memories:
                session.refresh(memory)

        return memories
