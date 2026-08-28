"""SQLite 图谱存取策略：复用共享 db 基建注入的 Engine。

逐方法短会话（``expire_on_commit=False``）沿用既有惯例；当前量级下个别
全表扫描（别名消歧）可接受，策略升级时新增实现类换绑即可。
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from wireup import injectable

from app.models.domain.memory import (
    StatementState,
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryStatement,
)
from app.components.memory.repositories.base import MemoryRepository


@injectable(as_type=MemoryRepository)
@dataclass
class SqliteGraphMemoryRepository(MemoryRepository):
    """关系库承载图谱事实源；引用完整性（FK/唯一约束）由库层兜底。"""

    engine: Engine

    def find_entity_by_name(self, name: str) -> MemoryEntity | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.exec(
                select(MemoryEntity).where(MemoryEntity.name == name)
            ).first()

    def find_entity_by_alias(self, alias: str) -> MemoryEntity | None:
        # JSON 数组成员查询不可移植：当前量级直接全量扫内存过滤，命中即止
        with Session(self.engine, expire_on_commit=False) as session:
            for entity in session.exec(select(MemoryEntity)).all():
                if alias in (entity.aliases or []):
                    return entity
        return None

    def upsert_entity(self, entity: MemoryEntity) -> MemoryEntity:
        with Session(self.engine, expire_on_commit=False) as session:
            merged: MemoryEntity
            if entity.id is not None:
                existing = session.get(MemoryEntity, entity.id)
                if existing is None:
                    session.add(entity)
                    merged = entity
                else:
                    # 合并语义只做增量收敛：别名并集、属性覆盖更新、
                    # 重要度取高、is_user 单向升（特殊节点永不降级）
                    existing.aliases = sorted(set(existing.aliases or []) | set(entity.aliases or []))
                    existing.attributes = {**(existing.attributes or {}), **(entity.attributes or {})}
                    existing.importance = max(existing.importance, entity.importance)
                    existing.is_user = existing.is_user or entity.is_user
                    merged = existing
            else:
                session.add(entity)
                merged = entity
            session.commit()
            session.refresh(merged)
            return merged

    def get_entities(self, entity_ids: list[int]) -> dict[int, MemoryEntity]:
        if not entity_ids:
            return {}
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(MemoryEntity).where(MemoryEntity.id.in_(entity_ids))
            ).all()
            return {row.id: row for row in rows}

    def list_entities(self, limit: int | None = None) -> list[MemoryEntity]:
        query = select(MemoryEntity).order_by(MemoryEntity.id)
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def list_episodes(self, limit: int | None = None) -> list[MemoryEpisode]:
        query = select(MemoryEpisode).order_by(MemoryEpisode.occurred_at.desc())
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def list_active_statements(self, limit: int | None = None) -> list[MemoryStatement]:
        with Session(self.engine, expire_on_commit=False) as session:
            query = select(MemoryStatement).where(MemoryStatement.state == StatementState.ACTIVE.value)
            if limit is not None:
                query = query.limit(limit)
            return list(session.exec(query).all())

    def find_active_statements(self, subject_ids: list[int]) -> list[MemoryStatement]:
        if not subject_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryStatement).where(
                    MemoryStatement.subject_id.in_(subject_ids),
                    MemoryStatement.state == StatementState.ACTIVE.value,
                )
            ).all())

    def statements_by_ids(self, statement_ids: list[int]) -> list[MemoryStatement]:
        if not statement_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryStatement).where(MemoryStatement.id.in_(statement_ids))
            ).all())

    def statements_valid_at(
        self, moment: datetime, subject_ids: list[int] | None = None
    ) -> list[MemoryStatement]:
        query = select(MemoryStatement).where(
            MemoryStatement.valid_from.is_not(None),
            MemoryStatement.valid_from <= moment,
        )
        query = query.where(
            (MemoryStatement.valid_to.is_(None)) | (MemoryStatement.valid_to > moment)
        )
        if subject_ids is not None:
            if not subject_ids:
                return []
            query = query.where(MemoryStatement.subject_id.in_(subject_ids))
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def insert_statement(self, statement: MemoryStatement) -> MemoryStatement:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(statement)
            session.commit()
            session.refresh(statement)
            return statement

    def supersede_statement(self, statement_id: int, valid_to: datetime, invalidated_at: datetime) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            statement = session.get(MemoryStatement, statement_id)
            if statement is None or statement.state != StatementState.ACTIVE.value:
                return False
            statement.state = StatementState.SUPERSEDED.value
            statement.valid_to = valid_to
            statement.invalidated_at = invalidated_at
            session.add(statement)
            session.commit()
            return True

    def insert_episode(self, episode: MemoryEpisode) -> MemoryEpisode:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(episode)
            session.commit()
            session.refresh(episode)
            return episode

    def episodes_by_ids(self, episode_ids: list[int]) -> list[MemoryEpisode]:
        if not episode_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryEpisode).where(MemoryEpisode.id.in_(episode_ids))
            ).all())

    def link_episode_entities(self, links: list[MemoryEpisodeLink]) -> None:
        if not links:
            return
        with Session(self.engine, expire_on_commit=False) as session:
            session.add_all(links)
            try:
                session.commit()
            except IntegrityError:
                # 重复挂接幂等跳过：整体失败后逐条补插、冲突项丢弃
                session.rollback()
                for link in links:
                    try:
                        session.add(link)
                        session.commit()
                    except IntegrityError:
                        session.rollback()

    def episodes_for_entities(self, entity_ids: list[int], limit: int | None = None) -> list[MemoryEpisode]:
        if not entity_ids:
            return []
        query = (
            select(MemoryEpisode)
            .join(MemoryEpisodeLink, MemoryEpisode.id == MemoryEpisodeLink.episode_id)
            .where(MemoryEpisodeLink.entity_id.in_(entity_ids))
            .distinct()
            .order_by(MemoryEpisode.occurred_at.desc())
        )
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def links_for_episodes(self, episode_ids: list[int]) -> dict[int, list[MemoryEpisodeLink]]:
        if not episode_ids:
            return {}
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(MemoryEpisodeLink).where(MemoryEpisodeLink.episode_id.in_(episode_ids))
            ).all()
            grouped: dict[int, list[MemoryEpisodeLink]] = {}
            for link in rows:
                grouped.setdefault(link.episode_id, []).append(link)
            return grouped

    def bump_access(
        self, statements: list[int], episodes: list[int], entities: list[int], now: datetime
    ) -> None:
        pairs = [
            (MemoryStatement, statements),
            (MemoryEpisode, episodes),
            (MemoryEntity, entities),
        ]
        with Session(self.engine, expire_on_commit=False) as session:
            for model, ids in pairs:
                if not ids:
                    continue
                session.exec(
                    update(model)
                    .where(model.id.in_(ids))
                    .values(access_count=model.access_count + 1, last_accessed_at=now)
                )
            session.commit()
