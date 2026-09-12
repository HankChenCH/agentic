"""实体身份纠错切片：合并（absorb）/拆分（split）/孤立清理的复合事务写。

唯一的追溯改写区：身份纠错例外地改写历史陈述的归属（防悬挂 FK），被改挂
行的 summary 按规范句式（domain/memory/vocab.fact_summary）重组。拆分双方
互写 attributes.merge_blocklist，消歧遇禁令对优先于余弦排序。
"""

from sqlmodel import Session, select

from app.adapters.persistence.memory_graph_repository._scope import GraphScopeMixin
from app.domain.memory.vocab import fact_summary
from app.models.domain.memory import MemoryEntity, MemoryEpisodeLink, MemoryStatement


class IdentityMixin(GraphScopeMixin):
    """实体身份纠错（L2）；归属改写与 source 删除同一事务同成同败。"""

    def absorb_entity(self, source_id: int, target_id: int) -> dict:
        with Session(self.engine, expire_on_commit=False) as session:
            source = session.get(MemoryEntity, source_id)
            target = session.get(MemoryEntity, target_id)
            if (
                source is None or target is None
                or not self._in_scope(source) or not self._in_scope(target)
            ):
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
            if source is None or not self._in_scope(source):
                return {
                    "new": None, "statements": 0, "links": 0,
                    "moved": [], "source_deleted": False,
                }
            self._stamp_user(new_entity)
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
            if entity is None or not self._in_scope(entity):
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
