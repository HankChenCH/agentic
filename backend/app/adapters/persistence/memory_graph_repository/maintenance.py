"""跨聚合维护切片：召回强化记账 + 危险操作区 L4（当日清除/会话遗忘/导出清单/整体重置）。

范围清除的编排（影响面预览、范围内归档/物理删的顺序语义）归
components/memory 的编辑端口；本切片只提供行级原语。
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, update
from sqlmodel import Session, select

from app.adapters.persistence.memory_graph_repository._scope import GraphScopeMixin
from app.models.domain.memory import (
    StatementState,
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryStatement,
)


class MaintenanceMixin(GraphScopeMixin):
    """召回记账与 L4 危险操作的原语；作用域内只清本人行。"""

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
                    .where(model.id.in_(ids), *self._where_user(model))
                    .values(access_count=model.access_count + 1, last_accessed_at=now)
                )
            session.commit()

    def list_statements_created_between(self, from_dt: datetime, to_dt: datetime) -> list[MemoryStatement]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryStatement).where(
                    MemoryStatement.created_at >= from_dt,
                    MemoryStatement.created_at <= to_dt,
                    *self._where_user(MemoryStatement),
                )
            ).all())

    def list_statements_by_thread(self, thread_id: UUID) -> list[MemoryStatement]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryStatement).where(
                    MemoryStatement.source_thread_id == thread_id,
                    *self._where_user(MemoryStatement),
                )
            ).all())

    def list_episodes_created_between(self, from_dt: datetime, to_dt: datetime) -> list[MemoryEpisode]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryEpisode).where(
                    MemoryEpisode.created_at >= from_dt,
                    MemoryEpisode.created_at <= to_dt,
                    *self._where_user(MemoryEpisode),
                )
            ).all())

    def list_episodes_by_thread(self, thread_id: UUID) -> list[MemoryEpisode]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryEpisode).where(
                    MemoryEpisode.thread_id == thread_id,
                    *self._where_user(MemoryEpisode),
                )
            ).all())

    def archive_statements(self, statement_ids: list[int], valid_to: datetime, invalidated_at: datetime) -> int:
        if not statement_ids:
            return 0
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(MemoryStatement).where(
                    MemoryStatement.id.in_(statement_ids),
                    MemoryStatement.state == StatementState.ACTIVE.value,
                )
            ).all()
            archived = 0
            for row in rows:
                if not self._in_scope(row):
                    continue
                row.state = StatementState.ARCHIVED.value
                row.valid_to = valid_to
                row.invalidated_at = invalidated_at
                session.add(row)
                archived += 1
            session.commit()
            return archived

    def list_orphan_entities_created_between(self, from_dt: datetime, to_dt: datetime) -> list[MemoryEntity]:
        statement_ref = select(MemoryStatement.id).where(
            (MemoryStatement.subject_id == MemoryEntity.id)
            | (MemoryStatement.object_entity_id == MemoryEntity.id)
        ).exists()
        link_ref = select(MemoryEpisodeLink.id).where(
            MemoryEpisodeLink.entity_id == MemoryEntity.id
        ).exists()
        query = select(MemoryEntity).where(
            MemoryEntity.created_at >= from_dt,
            MemoryEntity.created_at <= to_dt,
            MemoryEntity.is_user == False,  # noqa: E712 用户节点永不清理
            ~statement_ref,
            ~link_ref,
            *self._where_user(MemoryEntity),
        )
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def list_all_statements(self, limit: int | None = None) -> list[MemoryStatement]:
        query = select(MemoryStatement).where(*self._where_user(MemoryStatement)).order_by(MemoryStatement.id)
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def reset_all(self) -> dict[str, int]:
        # 先数后删同事务：返回值即本次重置的删除量；作用域内只清本人行
        counts: dict[str, int] = {}
        with Session(self.engine, expire_on_commit=False) as session:
            for model in (MemoryEpisodeLink, MemoryStatement, MemoryEpisode, MemoryEntity):
                counts[model.__tablename__] = len(
                    session.exec(select(model.id).where(*self._where_user(model))).all()  # type: ignore[attr-defined]
                )
                session.exec(delete(model).where(*self._where_user(model)))
            session.commit()
        return counts
