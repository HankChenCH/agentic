"""MemoryEditor 端口的组件侧回填：事实纠错与实体身份纠错的用例级实现。

绑定 ``@injectable(as_type=MemoryEditor)``（协议定义在
``services/domain/memory/ports.py``，依赖箭头 components ──► domain 合法）。
与 graph_reader.py 的只读薄委托不同，本实现承载编辑用例的机械语义：

- 取代式纠正 = ``replace_statement`` 单事务复合写（旧行 SUPERSEDED + 新行
  MANUAL ACTIVE 同成同败），新行 summary 按抽取侧同一规范句式重组——
  summary 是向量嵌入源，客体改名不重写会留下语义错位的向量；
- 实体身份纠错（合并/拆分/孤立清理）= 仓储复合事务（身份追改允许追溯
  改写归属，含历史行防悬挂 FK）+ 定向向量同步（改挂行 summary 重组后
  覆盖嵌入、消失实体删除档案向量）；
- 向量同步是方法内聚动作：SQL 提交后 best-effort 执行，失败不上抛
  （SQL 是事实源，``rebuild-index`` CLI 可兜底对账）；
- MANUAL 来源在此产生：人工事实享受裁决层全链路保护（LLM 恒 SKIP）。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from wireup import injectable

from app.components.memory import resolution
from app.components.memory.repositories import MemoryRepository
from app.components.memory.vocab import fact_summary
from app.exceptions.memory import (
    MemoryConstraintConflictError,
    MemoryInvalidParamError,
    MemoryObjectNotFoundError,
    MemoryProtectedObjectError,
)
from app.models.domain.memory import (
    EntityType,
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryOrigin,
    MemoryStatement,
    StatementState,
)
from app.services.domain.memory import (
    KIND_ENTITY,
    KIND_EPISODE,
    KIND_STATEMENT,
    MemoryVectorIndex,
    VectorEntry,
)
from app.services.domain.memory.graph_snapshot import (
    _entity_node,
    _episode_node,
    _statement_edge,
)
from app.services.domain.memory.ports import (
    EntitySplitSpec,
    FactWrite,
    MemoryEditor,
    StatementMutation,
)


@injectable(as_type=MemoryEditor)
@dataclass
class MemoryRepositoryEditor:
    """编辑用例实现：仓储复合写 + 定向向量同步。"""

    memory_repo: MemoryRepository
    vector_index: MemoryVectorIndex

    # ---------------- 查询面（领域服务校验用） ----------------

    def get_statement(self, statement_id: int) -> MemoryStatement | None:
        return self.memory_repo.get_statement(statement_id)

    def get_entity(self, entity_id: int) -> MemoryEntity | None:
        return self.memory_repo.get_entity(entity_id)

    def find_entity_by_name(self, name: str) -> MemoryEntity | None:
        return self.memory_repo.find_entity_by_name(name)

    def find_entity_by_alias(self, alias: str) -> MemoryEntity | None:
        return self.memory_repo.find_entity_by_alias(alias)

    def statements_by_ids(self, statement_ids: list[int]) -> list[MemoryStatement]:
        return self.memory_repo.statements_by_ids(statement_ids)

    def episode_links_by_ids(self, link_ids: list[int]) -> list[MemoryEpisodeLink]:
        return self.memory_repo.episode_links_by_ids(link_ids)

    # ---------------- 事实写面 ----------------

    def add_statement(self, spec: FactWrite, now: datetime) -> MemoryStatement:
        subject = self._ensure_subject(spec)
        object_entity, object_text = self._require_object(spec)
        predicate = (spec.predicate or "").strip()
        if not predicate:
            raise MemoryInvalidParamError("谓词不可为空")
        object_label = object_entity.name if object_entity is not None else object_text
        summary = spec.summary or self._compose_summary(subject.name, predicate, object_label)
        row = self.memory_repo.insert_statement(MemoryStatement(
            subject_id=subject.id,
            predicate=predicate,
            object_entity_id=object_entity.id if object_entity is not None else None,
            object_text=object_text,
            summary=summary,
            evidence=self._manual_evidence(spec.note),
            state=StatementState.ACTIVE.value,
            valid_from=spec.valid_from or now,
            origin=MemoryOrigin.MANUAL.value,
            confidence=1.0,
        ))
        self.vector_index.upsert_many([self._statement_entry(row, now)])
        return row

    def correct_statement(self, statement_id: int, spec: FactWrite, now: datetime) -> StatementMutation:
        old = self.memory_repo.get_statement(statement_id)
        if old is None:
            raise MemoryObjectNotFoundError(f"陈述不存在: {statement_id}")
        if old.state != StatementState.ACTIVE.value:
            raise MemoryInvalidParamError(f"陈述 #{statement_id} 已非在效状态（{old.state}），不可纠正")

        predicate = (spec.predicate if spec.predicate is not None else old.predicate).strip()
        if not predicate:
            raise MemoryInvalidParamError("谓词不可为空")
        subject_id = old.subject_id
        subject_name = self._entity_name(old.subject_id)
        if spec.subject_entity_id is not None or spec.subject_name is not None:
            subject = self._ensure_subject(spec)
            subject_id, subject_name = subject.id, subject.name

        if spec.object_entity_id is not None:
            target = self.memory_repo.get_entity(spec.object_entity_id)
            if target is None:
                raise MemoryObjectNotFoundError(f"客体实体不存在: {spec.object_entity_id}")
            object_entity_id, object_text, object_label = target.id, None, target.name
        elif spec.object_text is not None:
            object_entity_id, object_text, object_label = None, spec.object_text, spec.object_text
        else:
            object_entity_id = old.object_entity_id
            object_text = old.object_text
            object_label = object_text if object_entity_id is None else self._entity_name(object_entity_id)

        summary = spec.summary or self._compose_summary(subject_name, predicate, object_label)
        new_row = MemoryStatement(
            subject_id=subject_id,
            predicate=predicate,
            object_entity_id=object_entity_id,
            object_text=object_text,
            summary=summary,
            evidence=self._manual_evidence(spec.note),
            state=StatementState.ACTIVE.value,
            valid_from=spec.valid_from or old.valid_from,
            time_remark=old.time_remark,
            origin=MemoryOrigin.MANUAL.value,
            confidence=1.0,
            importance=old.importance,
        )
        replaced, row = self.memory_repo.replace_statement(
            old.id, new_row, valid_to=now, invalidated_at=now,
        )
        if not replaced:
            raise MemoryInvalidParamError(f"陈述 #{statement_id} 已不是在效状态，请刷新后重试")
        # 旧值的嵌入同步退场（孤儿向量由 rebuild 对账，这里即时删除保召回干净）
        self.vector_index.delete([(KIND_STATEMENT, old.id)])
        self.vector_index.upsert_many([self._statement_entry(row, now)])
        # old 取落库后状态（SUPERSEDED + 双时间轴终点），调用方展示「何时被取代」
        return StatementMutation(old=self.memory_repo.get_statement(old.id), new=row)

    def archive_statement(self, statement_id: int, now: datetime) -> MemoryStatement:
        old = self.memory_repo.get_statement(statement_id)
        if old is None:
            raise MemoryObjectNotFoundError(f"陈述不存在: {statement_id}")
        if old.state != StatementState.ACTIVE.value:
            raise MemoryInvalidParamError(f"陈述 #{statement_id} 已非在效状态（{old.state}），无需归档")
        if not self.memory_repo.archive_statement(statement_id, valid_to=now, invalidated_at=now):
            raise MemoryInvalidParamError(f"陈述 #{statement_id} 已不是在效状态，请刷新后重试")
        self.vector_index.delete([(KIND_STATEMENT, statement_id)])
        return self.memory_repo.get_statement(statement_id)

    # ---------------- 实体写面 ----------------

    def update_entity(
        self, entity_id: int,
        name: str | None = None, aliases: list[str] | None = None,
        entity_type: str | None = None,
    ) -> MemoryEntity:
        entity = self.memory_repo.get_entity(entity_id)
        if entity is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {entity_id}")
        if name is not None:
            entity.name = name
        if aliases is not None:
            entity.aliases = sorted({a for a in aliases if a})
        if entity_type is not None:
            entity.entity_type = entity_type
        row = self.memory_repo.save_entity(entity)
        # 名称/别名是实体向量档案文本（消歧候选发现依据），变更即重写
        self.vector_index.upsert_many([self._entity_entry(row)])
        return row

    # ---------------- 实体身份纠错（L2：合并 / 拆分 / 孤立清理）----------------

    def merge_entity(self, source_id: int, target_id: int) -> dict:
        source = self.memory_repo.get_entity(source_id)
        if source is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {source_id}")
        if target_id == source_id:
            raise MemoryInvalidParamError("合并双方相同：请选择另一个实体作为存留方")
        if self.memory_repo.get_entity(target_id) is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {target_id}")
        if source.is_user:
            raise MemoryProtectedObjectError("用户节点只能作为合并的存留方，不可被并入")
        result = self.memory_repo.absorb_entity(source_id, target_id)
        if not result["merged"]:
            raise MemoryInvalidParamError("合并失败：目标实体不存在，请刷新后重试")
        # 向量同步：source 档案退场、target 档案（别名扩了）重写、
        # 被改挂陈述按重组后的 summary 覆盖嵌入
        self.vector_index.delete([(KIND_ENTITY, source_id)])
        self.vector_index.upsert_many([self._entity_entry(result["target"])])
        now = datetime.now(timezone.utc)
        self.vector_index.upsert_many(
            [self._statement_entry(row, now) for row in result["moved"]]
        )
        return result

    def split_entity(self, source_id: int, spec: EntitySplitSpec) -> dict:
        source = self.memory_repo.get_entity(source_id)
        if source is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {source_id}")
        new_row = MemoryEntity(
            entity_type=spec.entity_type,
            name=spec.name,
            aliases=[],
            origin=MemoryOrigin.MANUAL.value,
        )
        result = self.memory_repo.split_entity(
            source_id, new_row,
            list(spec.statement_ids), list(spec.episode_link_ids), list(spec.aliases),
        )
        # 向量同步：新实体档案入索引；source 别名变了重写（拆空被删则退场）；
        # 被改挂陈述按重组后的 summary 覆盖嵌入
        now = datetime.now(timezone.utc)
        entries: list[VectorEntry] = [self._entity_entry(result["new"])]
        if result["source_deleted"]:
            self.vector_index.delete([(KIND_ENTITY, source_id)])
        else:
            refreshed = self.memory_repo.get_entity(source_id)
            if refreshed is not None:
                entries.append(self._entity_entry(refreshed))
        entries.extend(self._statement_entry(row, now) for row in result["moved"])
        self.vector_index.upsert_many(entries)
        return result

    def delete_orphan_entity(self, entity_id: int) -> MemoryEntity:
        entity = self.memory_repo.get_entity(entity_id)
        if entity is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {entity_id}")
        if entity.is_user:
            raise MemoryProtectedObjectError("用户节点受保护，不可删除")
        if not self.memory_repo.delete_fully_orphan_entity(entity_id):
            raise MemoryInvalidParamError(
                "该实体仍被事实或事件引用，先纠正/归档关联内容（或走合并/拆分）后再删除"
            )
        self.vector_index.delete([(KIND_ENTITY, entity_id)])
        return entity

    # ---------------- 事件编辑（L3）----------------

    def update_episode(
        self, episode_id: int,
        summary: str | None = None, scene: str | None = None,
        occurred_at: datetime | None = None,
    ) -> MemoryEpisode:
        episode = self.memory_repo.get_episode(episode_id)
        if episode is None:
            raise MemoryObjectNotFoundError(f"事件不存在: {episode_id}")
        if summary is not None:
            episode.summary = summary
        if scene is not None:
            # 空串 = 清除场景；None = 保持原值（部分更新语义）
            episode.scene = scene or None
        if occurred_at is not None:
            episode.occurred_at = occurred_at
        row = self.memory_repo.save_episode(episode)
        # summary 是事件向量的嵌入源，变更即重写
        self.vector_index.upsert_many([VectorEntry(
            KIND_EPISODE, row.id, row.summary,
            str(row.thread_id) if row.thread_id else None, row.occurred_at,
        )])
        return row

    def delete_episode(self, episode_id: int) -> MemoryEpisode:
        deleted = self.memory_repo.delete_episode(episode_id)
        if deleted is None:
            raise MemoryObjectNotFoundError(f"事件不存在: {episode_id}")
        self.vector_index.delete([(KIND_EPISODE, episode_id)])
        return deleted

    def update_episode_link(
        self, link_id: int, entity_id: int | None = None, role: str | None = None,
    ) -> MemoryEpisodeLink:
        link = self.memory_repo.get_episode_link(link_id)
        if link is None:
            raise MemoryObjectNotFoundError(f"事件参与不存在: {link_id}")
        if entity_id is not None:
            if self.memory_repo.get_entity(entity_id) is None:
                raise MemoryObjectNotFoundError(f"实体不存在: {entity_id}")
            link.entity_id = entity_id
        if role is not None:
            link.role = role or None  # 空串 = 清除角色
        saved = self.memory_repo.save_episode_link(link)
        if saved is None:
            raise MemoryConstraintConflictError(
                "该事件已有完全相同的参与记录（实体+角色），请换角色或先删除原参与"
            )
        return saved

    # ---------------- 危险操作区（L4）----------------

    def purge_preview(
        self, *,
        from_dt: datetime | None = None, to_dt: datetime | None = None,
        thread_id: UUID | None = None,
    ) -> dict:
        statements, episodes = self._scope_rows(from_dt, to_dt, thread_id)
        return {
            "statements": len(statements),
            "activeStatements": sum(
                1 for s in statements if s.state == StatementState.ACTIVE.value
            ),
            "episodes": len(episodes),
            "entities": 0,  # 预览不真跑孤立扫描，由 purge 后置清理兜底（见 purge_memory）
        }

    def purge_memory(
        self, *,
        from_dt: datetime | None = None, to_dt: datetime | None = None,
        thread_id: UUID | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        statements, episodes = self._scope_rows(from_dt, to_dt, thread_id)
        # ❶ 在效事实批量归档（软删：时点回放仍可溯）
        archived = self.memory_repo.archive_statements(
            [s.id for s in statements if s.state == StatementState.ACTIVE.value],
            valid_to=now, invalidated_at=now,
        )
        # ❷ 事件物理删除（含参与边）
        deleted_episodes = sum(1 for ep in episodes if self.memory_repo.delete_episode(ep.id))
        # ❸ 孤立实体后置清理：范围内创建、此后零引用（归档行引用者按既知
        #    边界保留，防悬挂 FK）；仅时间范围语义提供，会话遗忘不动实体
        deleted_entities: list[int] = []
        if thread_id is None and from_dt is not None and to_dt is not None:
            for e in self.memory_repo.list_orphan_entities_created_between(from_dt, to_dt):
                if self.memory_repo.delete_fully_orphan_entity(e.id):
                    deleted_entities.append(e.id)
        # ❹ 向量同步：归档行/删除行/清理实体全部退场（best-effort）
        self.vector_index.delete(
            [(KIND_STATEMENT, s.id) for s in statements if s.state == StatementState.ACTIVE.value]
        )
        self.vector_index.delete([(KIND_EPISODE, ep.id) for ep in episodes])
        self.vector_index.delete([(KIND_ENTITY, i) for i in deleted_entities])
        return {
            "archivedStatements": archived,
            "deletedEpisodes": deleted_episodes,
            "deletedEntities": len(deleted_entities),
        }

    def _scope_rows(self, from_dt, to_dt, thread_id):
        """范围取行；时间入参归一为 naive UTC（库值约定）。"""
        if thread_id is not None:
            return (
                self.memory_repo.list_statements_by_thread(thread_id),
                self.memory_repo.list_episodes_by_thread(thread_id),
            )
        naive_from = _naive_utc(from_dt)
        naive_to = _naive_utc(to_dt)
        return (
            self.memory_repo.list_statements_created_between(naive_from, naive_to),
            self.memory_repo.list_episodes_created_between(naive_from, naive_to),
        )

    def export_memory(self) -> dict:
        entities = self.memory_repo.list_entities()
        episodes = self.memory_repo.list_episodes()
        statements = self.memory_repo.list_all_statements()
        links = self.memory_repo.links_for_episodes([ep.id for ep in episodes])
        return {
            "exportedAt": datetime.now(timezone.utc).isoformat(),
            "entities": [_entity_node(e) for e in entities],
            "statements": [_statement_edge(s) for s in statements],
            "episodes": [_episode_node(ep) for ep in episodes],
            "episodeLinks": [
                {
                    "id": f"l:{link.id}",
                    "kind": "episode_link",
                    "source": f"ep:{link.episode_id}",
                    "target": f"e:{link.entity_id}",
                    "role": link.role,
                }
                for bundle in links.values()
                for link in bundle
            ],
        }

    def reset_all_memory(self) -> dict[str, int]:
        counts = self.memory_repo.reset_all()
        # drop + 空 collection：下一次写入/检索会按需重建
        self.vector_index.rebuild([])
        return counts

    # ---------------- 内部小件 ----------------

    def _ensure_subject(self, spec: FactWrite) -> MemoryEntity:
        """主体解析：id 直引必须存在；name 引用不存在则建（MANUAL）。"""
        if spec.subject_entity_id is not None:
            subject = self.memory_repo.get_entity(spec.subject_entity_id)
            if subject is None:
                raise MemoryObjectNotFoundError(f"主体实体不存在: {spec.subject_entity_id}")
            return subject
        if spec.subject_name:
            name = spec.subject_name.strip()
            hit = self.memory_repo.find_entity_by_name(name) or self.memory_repo.find_entity_by_alias(name)
            if hit is not None:
                return hit
            created = self.memory_repo.upsert_entity(MemoryEntity(
                entity_type=spec.subject_entity_type or EntityType.OTHER.value,
                name=name,
                aliases=[],
                origin=MemoryOrigin.MANUAL.value,
            ))
            self.vector_index.upsert_many([self._entity_entry(created)])
            return created
        raise MemoryInvalidParamError("主体缺失：需提供 subjectEntityId 或 subjectName")

    def _require_object(self, spec: FactWrite) -> tuple[MemoryEntity | None, str | None]:
        """客体二选一校验（新增路径：两者必居其一）。"""
        if (spec.object_entity_id is None) == (spec.object_text is None):
            raise MemoryInvalidParamError("客体必须二选一：objectEntityId 或 objectText")
        if spec.object_entity_id is not None:
            row = self.memory_repo.get_entity(spec.object_entity_id)
            if row is None:
                raise MemoryObjectNotFoundError(f"客体实体不存在: {spec.object_entity_id}")
            return row, None
        return None, spec.object_text

    def _entity_name(self, entity_id: int | None) -> str:
        if entity_id is None:
            return ""
        row = self.memory_repo.get_entity(entity_id)
        return row.name if row is not None else ""

    def _compose_summary(self, subject_name: str, predicate: str, object_label: str) -> str:
        # 唯一规范句式在 vocab.fact_summary：抽取落库与人工编辑共用
        return fact_summary(subject_name, predicate, object_label)

    @staticmethod
    def _manual_evidence(note: str | None) -> list:
        return [{"quote": note}] if note else []

    @staticmethod
    def _statement_entry(row: MemoryStatement, now: datetime) -> VectorEntry:
        return VectorEntry(
            KIND_STATEMENT, row.id, row.summary,
            str(row.source_thread_id) if row.source_thread_id else None,
            row.valid_from or now,
        )

    @staticmethod
    def _entity_entry(row: MemoryEntity) -> VectorEntry:
        # 实体向量写入的档案文本单源在 resolution.entity_content（与消歧判定同内容）
        return VectorEntry(KIND_ENTITY, row.id, resolution.entity_content(row), None, None)


def _naive_utc(value: datetime) -> datetime:
    """aware → naive UTC（库值约定）；naive 原样返回。"""
    return value.replace(tzinfo=None) if value.tzinfo is not None else value
