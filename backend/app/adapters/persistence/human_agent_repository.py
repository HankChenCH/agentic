"""人工客服坐席聚合数据访问：单表 CRUD（全局资源，无可见性谓词）。"""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select
from wireup import injectable

from app.domain.human_agent.ports import HumanAgentRepositoryPort
from app.domain.ports import RepositoryConflictError
from app.models.domain.human_agent import HumanAgent


@injectable(as_type=HumanAgentRepositoryPort)
@dataclass
class HumanAgentRepository:
    engine: Engine

    def create_agent(self, agent: HumanAgent) -> HumanAgent:
        # 姓名唯一冲突的并发窗口在此收口为契约级冲突信号（驱动异常不出适配器）
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(agent)
            try:
                session.commit()
            except IntegrityError:
                raise RepositoryConflictError("human agent name already exists") from None
            session.refresh(agent)
        return agent

    def get_agent(self, agent_id: UUID) -> HumanAgent | None:
        with Session(self.engine, expire_on_commit=False) as session:
            agent = session.get(HumanAgent, agent_id)
            session.commit()
        return agent

    def get_agent_by_name(self, name: str) -> HumanAgent | None:
        with Session(self.engine, expire_on_commit=False) as session:
            agent = session.exec(
                select(HumanAgent).where(col(HumanAgent.name) == name)
            ).first()
            session.commit()
        return agent

    def update_agent(self, agent: HumanAgent) -> HumanAgent:
        # 入参为携带修改的游离实例（expire_on_commit=False 产物），add 按主键重关联走 UPDATE
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(agent)
            try:
                session.commit()
            except IntegrityError:
                raise RepositoryConflictError("human agent name already exists") from None
            session.refresh(agent)
        return agent

    def delete_agent(self, agent_id: UUID) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            agent = session.get(HumanAgent, agent_id)
            if agent is None:
                session.commit()
                return False
            session.delete(agent)
            session.commit()
        return True

    def list_agents(self) -> List[HumanAgent]:
        # 排序只按创建时间倒序；「在线优先」的展示口径由 directory 服务按
        # 状态归组（枚举的库内字典序与接待优先级无关，不进 SQL）
        with Session(self.engine, expire_on_commit=False) as session:
            result = session.exec(
                select(HumanAgent).order_by(col(HumanAgent.created_at).desc())
            ).all()
            session.commit()
        return list(result)
