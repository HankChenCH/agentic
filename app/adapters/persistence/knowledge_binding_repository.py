"""agent ↔ 知识库绑定数据访问（绑定是独立小聚合，无跨聚合计数维护）。"""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from sqlalchemy import Engine, delete
from sqlmodel import Session, col, select
from wireup import injectable

from app.models.domain.knowledge import KnowledgeAgentBinding


@injectable
@dataclass
class KnowledgeBindingRepository:
    engine: Engine

    def list_by_agent(self, agent_id: str) -> List[KnowledgeAgentBinding]:
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(KnowledgeAgentBinding).where(col(KnowledgeAgentBinding.agent_id) == agent_id)
            ).all()
            session.commit()
        return list(rows)

    def replace_by_agent(self, agent_id: str, kb_ids: List[UUID]) -> List[KnowledgeAgentBinding]:
        """全量替换该 agent 的绑定列表（delete + bulk insert 同事务，幂等）。"""
        bindings = [KnowledgeAgentBinding(agent_id=agent_id, kb_id=kb_id) for kb_id in kb_ids]
        with Session(self.engine, expire_on_commit=False) as session:
            session.exec(
                delete(KnowledgeAgentBinding).where(col(KnowledgeAgentBinding.agent_id) == agent_id)
            )
            for binding in bindings:
                session.add(binding)
            session.commit()
        return bindings

    def delete_by_kb(self, kb_id: UUID) -> None:
        # 知识库三段式删除的配套清理：绑定行随库删除，避免悬空引用
        with Session(self.engine, expire_on_commit=False) as session:
            session.exec(
                delete(KnowledgeAgentBinding).where(col(KnowledgeAgentBinding.kb_id) == kb_id)
            )
            session.commit()
