"""实体档案切片：MemoryEntity 行的查找/合并收敛/直改/批量取。"""

from sqlmodel import Session, select

from app.adapters.persistence.memory_graph_repository._scope import GraphScopeMixin
from app.models.domain.memory import MemoryEntity


class EntityMixin(GraphScopeMixin):
    """实体（entity 表）的读写；合并语义与直改语义的分工见端口注解。"""

    def find_entity_by_name(self, name: str) -> MemoryEntity | None:
        with Session(self.engine, expire_on_commit=False) as session:
            return session.exec(
                select(MemoryEntity)
                .where(MemoryEntity.name == name, *self._where_user(MemoryEntity))
            ).first()

    def find_entity_by_alias(self, alias: str) -> MemoryEntity | None:
        # JSON 数组成员查询不可移植：当前量级直接全量扫内存过滤，命中即止
        with Session(self.engine, expire_on_commit=False) as session:
            query = select(MemoryEntity).where(*self._where_user(MemoryEntity))
            for entity in session.exec(query).all():
                if alias in (entity.aliases or []):
                    return entity
        return None

    def upsert_entity(self, entity: MemoryEntity) -> MemoryEntity:
        with Session(self.engine, expire_on_commit=False) as session:
            merged: MemoryEntity
            if entity.id is not None:
                existing = session.get(MemoryEntity, entity.id)
                if existing is None or not self._in_scope(existing):
                    session.add(self._stamp_user(entity))
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
                session.add(self._stamp_user(entity))
                merged = entity
            session.commit()
            session.refresh(merged)
            return merged

    def get_entity(self, entity_id: int) -> MemoryEntity | None:
        with Session(self.engine, expire_on_commit=False) as session:
            entity = session.get(MemoryEntity, entity_id)
            return entity if entity is not None and self._in_scope(entity) else None

    def save_entity(self, entity: MemoryEntity) -> MemoryEntity:
        with Session(self.engine, expire_on_commit=False) as session:
            if entity.id is not None:
                existing = session.get(MemoryEntity, entity.id)
                if existing is not None and not self._in_scope(existing):
                    # 作用域内不可改写他人实体行（编辑端口的调用方已先过作用域 get）
                    raise PermissionError(f"entity #{entity.id} 不在当前作用域内")
            merged = session.merge(entity)
            session.commit()
            session.refresh(merged)
            return merged

    def get_entities(self, entity_ids: list[int]) -> dict[int, MemoryEntity]:
        if not entity_ids:
            return {}
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(MemoryEntity).where(MemoryEntity.id.in_(entity_ids), *self._where_user(MemoryEntity))
            ).all()
            return {row.id: row for row in rows}

    def list_entities(self, limit: int | None = None) -> list[MemoryEntity]:
        query = select(MemoryEntity).where(*self._where_user(MemoryEntity)).order_by(MemoryEntity.id)
        if limit is not None:
            query = query.limit(limit)
        with Session(self.engine, expire_on_commit=False) as session:
            return list(session.exec(query).all())
