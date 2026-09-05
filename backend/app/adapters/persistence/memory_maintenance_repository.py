"""维护面仓储：``MemoryMaintenancePort`` 的持久化实现（全局算子视角）。

维护链路（application 的 repair / rebuild-index 编排）专用：与图谱仓储
同 Engine，但不做用户作用域——维护天然跨用户操作（定位与改写按行归属
分组回写各用户向量 collection）。只提供与具体事故无关的通用原语；定位
条件与修复顺序的事故级编排归 application 用例。
"""

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlmodel import Session, select
from wireup import injectable

from app.models.domain.memory import MemoryEpisodeLink, MemoryStatement
from app.domain.memory.ports import MemoryMaintenancePort


@injectable(as_type=MemoryMaintenancePort)
@dataclass
class MemoryMaintenanceRepository(MemoryMaintenancePort):
    """维护面原语实现：逐方法短会话，惯例同图谱仓储。"""

    engine: Engine

    def list_statements_by_object_predicate(
        self, predicate: str, object_entity_id: int,
    ) -> list[MemoryStatement]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryStatement).where(
                    MemoryStatement.predicate == predicate,
                    MemoryStatement.object_entity_id == object_entity_id,
                )
            ).all())

    def list_episode_links_by_entity(
        self, entity_id: int, role: str | None = None,
    ) -> list[MemoryEpisodeLink]:
        with Session(self.engine, expire_on_commit=False) as session:
            query = select(MemoryEpisodeLink).where(MemoryEpisodeLink.entity_id == entity_id)
            if role is not None:
                query = query.where(MemoryEpisodeLink.role == role)
            return list(session.exec(query).all())

    def save_statement(self, statement: MemoryStatement) -> MemoryStatement:
        with Session(self.engine, expire_on_commit=False) as session:
            merged = session.merge(statement)
            session.commit()
            session.refresh(merged)
            return merged
