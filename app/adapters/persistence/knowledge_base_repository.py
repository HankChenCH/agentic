"""知识库聚合数据访问：仅关系表，向量/对象存储不进仓库层。"""

from dataclasses import dataclass
from typing import List, Tuple
from uuid import UUID

from sqlalchemy import Engine, func
from sqlmodel import Session, col, select
from wireup import injectable

from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus


@injectable
@dataclass
class KnowledgeBaseRepository:
    engine: Engine

    def create_kb(self, kb: KnowledgeBase) -> KnowledgeBase:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(kb)
            session.commit()
            session.refresh(kb)
        return kb

    def get_kb(self, kb_id: UUID) -> KnowledgeBase | None:
        with Session(self.engine, expire_on_commit=False) as session:
            kb = session.get(KnowledgeBase, kb_id)
            session.commit()
        return kb

    def get_kb_by_name(self, name: str) -> KnowledgeBase | None:
        with Session(self.engine, expire_on_commit=False) as session:
            kb = session.exec(
                select(KnowledgeBase).where(col(KnowledgeBase.name) == name)
            ).first()
            session.commit()
        return kb

    def update_kb(self, kb: KnowledgeBase) -> KnowledgeBase:
        # 入参为携带修改的游离实例（expire_on_commit=False 产物），add 按主键重关联走 UPDATE
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(kb)
            session.commit()
            session.refresh(kb)
        return kb

    def delete_kb(self, kb_id: UUID) -> bool:
        # documents/segments 由 Relationship 的 delete-orphan 在库内级联删除
        with Session(self.engine, expire_on_commit=False) as session:
            kb = session.get(KnowledgeBase, kb_id)
            if kb is None:
                session.commit()
                return False
            session.delete(kb)
            session.commit()
        return True

    def list_kbs(self, page: int, page_size: int) -> Tuple[List[KnowledgeBase], int]:
        # 分页 offset 必须按页大小计算：(page-1) * page_size
        # deleting 资源视同已删除：列表与计数都不返回（详情接口仍可查到）
        offset = (page - 1) * page_size
        with Session(self.engine, expire_on_commit=False) as session:
            total = session.exec(
                select(func.count())
                .select_from(KnowledgeBase)
                .where(col(KnowledgeBase.status) != KnowledgeStatus.DELETING)
            ).one()
            result = session.exec(
                select(KnowledgeBase)
                .where(col(KnowledgeBase.status) != KnowledgeStatus.DELETING)
                .order_by(col(KnowledgeBase.weight).desc(), col(KnowledgeBase.created_at).desc())
                .offset(offset)
                .limit(page_size)
            ).all()
            session.commit()
        return list(result), int(total)
