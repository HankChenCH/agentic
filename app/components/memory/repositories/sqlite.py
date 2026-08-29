"""SQLite 图谱存取策略：复用共享 db 基建注入的 Engine。

逐方法短会话（``expire_on_commit=False``）沿用既有惯例；当前量级下个别
全表扫描（别名消歧）可接受，策略升级时新增实现类换绑即可。
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, delete, func, update
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
from app.components.memory.vocab import fact_summary


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

    def get_entity(self, entity_id: int) -> MemoryEntity | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.get(MemoryEntity, entity_id)

    def save_entity(self, entity: MemoryEntity) -> MemoryEntity:
        with Session(self.engine, expire_on_commit=False) as session:
            merged = session.merge(entity)
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

    def get_statement(self, statement_id: int) -> MemoryStatement | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.get(MemoryStatement, statement_id)

    def replace_statement(
        self, old_statement_id: int, new_statement: MemoryStatement,
        valid_to: datetime, invalidated_at: datetime,
    ) -> tuple[bool, MemoryStatement | None]:
        # 取代与落库同一事务：中断只会整体回滚，不会出现「旧行已废、新行未立」的断链态
        with Session(self.engine, expire_on_commit=False) as session:
            old = session.get(MemoryStatement, old_statement_id)
            if old is None or old.state != StatementState.ACTIVE.value:
                return False, None
            old.state = StatementState.SUPERSEDED.value
            old.valid_to = valid_to
            old.invalidated_at = invalidated_at
            session.add(old)
            session.add(new_statement)
            session.commit()
            session.refresh(new_statement)
            return True, new_statement

    def archive_statement(self, statement_id: int, valid_to: datetime, invalidated_at: datetime) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            statement = session.get(MemoryStatement, statement_id)
            if statement is None or statement.state != StatementState.ACTIVE.value:
                return False
            statement.state = StatementState.ARCHIVED.value
            statement.valid_to = valid_to
            statement.invalidated_at = invalidated_at
            session.add(statement)
            session.commit()
            return True

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

    def list_statements_created_between(self, from_dt: datetime, to_dt: datetime) -> list[MemoryStatement]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryStatement).where(
                    MemoryStatement.created_at >= from_dt,
                    MemoryStatement.created_at <= to_dt,
                )
            ).all())

    def list_statements_by_thread(self, thread_id: UUID) -> list[MemoryStatement]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryStatement).where(MemoryStatement.source_thread_id == thread_id)
            ).all())

    def list_episodes_created_between(self, from_dt: datetime, to_dt: datetime) -> list[MemoryEpisode]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryEpisode).where(
                    MemoryEpisode.created_at >= from_dt,
                    MemoryEpisode.created_at <= to_dt,
                )
            ).all())

    def list_episodes_by_thread(self, thread_id: UUID) -> list[MemoryEpisode]:
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryEpisode).where(MemoryEpisode.thread_id == thread_id)
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
            for row in rows:
                row.state = StatementState.ARCHIVED.value
                row.valid_to = valid_to
                row.invalidated_at = invalidated_at
                session.add(row)
            session.commit()
            return len(rows)

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
        )
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def list_all_statements(self, limit: int | None = None) -> list[MemoryStatement]:
        query = select(MemoryStatement).order_by(MemoryStatement.id)
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())

    def reset_all(self) -> dict[str, int]:
        # 先数后删同事务：返回值即本次重置的删除量
        counts: dict[str, int] = {}
        with Session(self.engine, expire_on_commit=False) as session:
            for model in (MemoryEpisodeLink, MemoryStatement, MemoryEpisode, MemoryEntity):
                counts[model.__tablename__] = len(
                    session.exec(select(model.id)).all()  # type: ignore[attr-defined]
                )
                session.exec(delete(model))
            session.commit()
        return counts

    def episode_links_by_ids(self, link_ids: list[int]) -> list[MemoryEpisodeLink]:
        if not link_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(
                select(MemoryEpisodeLink).where(MemoryEpisodeLink.id.in_(link_ids))
            ).all())

    def get_episode(self, episode_id: int) -> MemoryEpisode | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.get(MemoryEpisode, episode_id)

    def save_episode(self, episode: MemoryEpisode) -> MemoryEpisode:
        with Session(self.engine, expire_on_commit=False) as session:
            merged = session.merge(episode)
            session.commit()
            session.refresh(merged)
            return merged

    def delete_episode(self, episode_id: int) -> MemoryEpisode | None:
        with Session(self.engine, expire_on_commit=False) as session:
            episode = session.get(MemoryEpisode, episode_id)
            if episode is None:
                return None
            # 参与边先行：事件删除是物理删（无状态机），参与边随行清空防悬挂
            for link in session.exec(
                select(MemoryEpisodeLink).where(MemoryEpisodeLink.episode_id == episode_id)
            ).all():
                session.delete(link)
            session.delete(episode)
            session.commit()
            return episode

    def get_episode_link(self, link_id: int) -> MemoryEpisodeLink | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.get(MemoryEpisodeLink, link_id)

    def save_episode_link(self, link: MemoryEpisodeLink) -> MemoryEpisodeLink | None:
        with Session(self.engine, expire_on_commit=False) as session:
            try:
                merged = session.merge(link)
                session.commit()
            except IntegrityError:
                # (episode, entity, role) 组合唯一：改挂撞已有参与 → 3007 由端口翻译
                session.rollback()
                return None
            session.refresh(merged)
            return merged

    def absorb_entity(self, source_id: int, target_id: int) -> dict:
        with Session(self.engine, expire_on_commit=False) as session:
            source = session.get(MemoryEntity, source_id)
            target = session.get(MemoryEntity, target_id)
            if source is None or target is None:
                return {"merged": False, "statements": 0, "links": 0, "moved": [], "target": None}

            moved: list[MemoryStatement] = []
            rows = session.exec(
                select(MemoryStatement).where(
                    (MemoryStatement.subject_id == source_id)
                    | (MemoryStatement.object_entity_id == source_id)
                )
            ).all()
            for row in rows:
                if row.subject_id == source_id:
                    row.subject_id = target_id
                if row.object_entity_id == source_id:
                    row.object_entity_id = target_id
                row.summary = self._recomposed_summary(row, session)
                moved.append(row)

            links = session.exec(
                select(MemoryEpisodeLink).where(MemoryEpisodeLink.entity_id == source_id)
            ).all()
            for link in links:
                link.entity_id = target_id

            # 名字/别名并入（target 规范名保留），attributes 合并后清除指向
            # source 的拆分禁令——人工此刻的合并意图覆盖旧拆分禁令
            target.aliases = sorted(
                (set(target.aliases or []) | {source.name} | set(source.aliases or []))
                - {target.name}
            )
            merged_attrs = {**(target.attributes or {}), **(source.attributes or {})}
            source_names = {source.name, *(source.aliases or [])}
            merged_attrs["merge_blocklist"] = sorted(
                set(merged_attrs.get("merge_blocklist", [])) - source_names
            )
            target.attributes = merged_attrs
            session.add(target)
            session.delete(source)
            session.commit()
            return {
                "merged": True,
                "statements": len(moved),
                "links": len(links),
                "moved": moved,
                "target": target,
            }

    def split_entity(
        self, source_id: int, new_entity: MemoryEntity,
        statement_ids: list[int], link_ids: list[int], alias_names: list[str],
    ) -> dict:
        with Session(self.engine, expire_on_commit=False) as session:
            source = session.get(MemoryEntity, source_id)
            if source is None:
                return {
                    "new": None, "statements": 0, "links": 0,
                    "moved": [], "source_deleted": False,
                }
            # 拆分禁令素材要在别名迁移前取：source 的现存名字与别名
            source_names = {source.name, *(source.aliases or [])}

            session.add(new_entity)
            session.flush()  # 取自增 id 供改挂引用
            moved: list[MemoryStatement] = []
            for row in session.exec(
                select(MemoryStatement).where(MemoryStatement.id.in_(statement_ids))
            ).all():
                touched = False
                if row.subject_id == source_id:
                    row.subject_id = new_entity.id
                    touched = True
                if row.object_entity_id == source_id:
                    row.object_entity_id = new_entity.id
                    touched = True
                if touched:
                    row.summary = self._recomposed_summary(row, session)
                    moved.append(row)

            moved_links = 0
            for link in session.exec(
                select(MemoryEpisodeLink).where(MemoryEpisodeLink.id.in_(link_ids))
            ).all():
                if link.entity_id == source_id:
                    link.entity_id = new_entity.id
                    moved_links += 1

            moving_aliases = {a for a in alias_names if a}
            new_names = {new_entity.name, *moving_aliases}
            source.aliases = [a for a in (source.aliases or []) if a not in moving_aliases]
            new_entity.aliases = sorted(moving_aliases)
            # 双方互写拆分禁令（attributes.merge_blocklist）：消歧遇到禁令对
            # 同时达标时，禁令优先于余弦排序
            source_attrs = {**(source.attributes or {})}
            source_attrs["merge_blocklist"] = sorted(
                set(source_attrs.get("merge_blocklist", [])) | new_names
            )
            source.attributes = source_attrs
            session.add(source)
            new_entity.attributes = {"merge_blocklist": sorted(source_names)}

            # 拆空自动删除：source 只剩历史引用且无参与、无别名 → 身份纠错
            # 已把全部现存内容迁走，空壳无保留价值（时点回放经改挂行依然成立）
            left_statement = session.exec(
                select(MemoryStatement.id).where(
                    (MemoryStatement.subject_id == source_id)
                    | (MemoryStatement.object_entity_id == source_id)
                )
            ).first()
            left_link = session.exec(
                select(MemoryEpisodeLink.id).where(MemoryEpisodeLink.entity_id == source_id)
            ).first()
            source_deleted = False
            if left_statement is None and left_link is None and not source.aliases:
                session.delete(source)
                source_deleted = True

            session.commit()
            session.refresh(new_entity)
            return {
                "new": new_entity,
                "statements": len(moved),
                "links": moved_links,
                "moved": moved,
                "source_deleted": source_deleted,
            }

    def delete_fully_orphan_entity(self, entity_id: int) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            entity = session.get(MemoryEntity, entity_id)
            if entity is None:
                return False
            ref_statement = session.exec(
                select(MemoryStatement.id).where(
                    (MemoryStatement.subject_id == entity_id)
                    | (MemoryStatement.object_entity_id == entity_id)
                )
            ).first()
            ref_link = session.exec(
                select(MemoryEpisodeLink.id).where(MemoryEpisodeLink.entity_id == entity_id)
            ).first()
            if ref_statement is not None or ref_link is not None:
                return False
            session.delete(entity)
            session.commit()
            return True

    @staticmethod
    def _recomposed_summary(row: MemoryStatement, session: Session) -> str:
        """身份改挂后的规范句式重组（subject/object 任一侧换挂都要重写）。"""
        subject = session.get(MemoryEntity, row.subject_id)
        if row.object_entity_id is None:
            object_label = row.object_text or ""
        else:
            obj = session.get(MemoryEntity, row.object_entity_id)
            object_label = obj.name if obj is not None else (row.object_text or "")
        return fact_summary(subject.name if subject else "?", row.predicate, object_label)
