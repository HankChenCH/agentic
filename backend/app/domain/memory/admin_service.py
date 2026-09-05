"""记忆编辑领域服务（L1：事实补充/取代式纠正/归档 + 实体改名）。

分层落位（AGENTS.md 两层制）：api ──► domain，管理端点直接消费本服务；
写操作经 ``ports.MemoryEditor`` 端口下沉组件侧（用例级事务 + 向量同步），
本层只做请求语义判断：引用解析、取值合法性、名字冲突预检与「是否真的
有变化」。响应形状复用图快照的 camelCase 组装件，前后端契约零漂移。

用户作用域：每个用例先 ``editor.for_user(user_id)`` 取绑定用户的视图——
记忆是用户级数据，他人对象在作用域内一律「不存在」（3xxx 业务异常，
不泄露存在性）。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from wireup import injectable

from app.exceptions.memory import (
    MemoryInvalidParamError,
    MemoryInvalidTimeParamError,
    MemoryNameConflictError,
    MemoryNoChangeError,
    MemoryObjectNotFoundError,
)
from app.models.domain.memory import EntityType
from app.models.schema.request.memory import (
    EntityMergeRequest,
    EntitySplitRequest,
    EntityUpdateRequest,
    EpisodeLinkUpdateRequest,
    EpisodeUpdateRequest,
    MaintenancePurgeRequest,
    MaintenanceResetRequest,
    StatementCorrectRequest,
    StatementCreateRequest,
)
from app.domain.memory.ports import EntitySplitSpec, FactWrite, MemoryEditor
# 同包复用图快照的对外 JSON 组装件（下划线私有仅包内引用），保证编辑
# 返回的实体/陈述形状与快照逐字段一致，前端可按同一类型渲染
from .graph_snapshot import _entity_node, _episode_node, _statement_edge

# 可纠正/可归档的编辑对象引用前缀（与图快照边/节点 id 同形：#S13 / e:5）
_STATEMENT_PREFIX = "s:"
_ENTITY_PREFIX = "e:"
_LINK_PREFIX = "l:"
_EPISODE_PREFIX = "ep:"

# 与图快照 _parse_at 同格式族
_TIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M", "%Y/%m/%d",
)


@injectable
@dataclass
class MemoryAdminService:
    """管理侧记忆编辑入口：校验在领域、事务在端口实现。"""

    editor: MemoryEditor

    # ---------------- 事实：补充 / 纠正 / 归档 ----------------

    def add_statement(self, user_id: UUID, request: StatementCreateRequest) -> dict:
        editor = self.editor.for_user(user_id)
        if request.subjectEntityId is not None and request.subjectName:
            raise MemoryInvalidParamError("主体二选一：subjectEntityId 或 subjectName")
        self._require_known_entity(editor, request.objectEntityId)
        row = editor.add_statement(
            FactWrite(
                subject_entity_id=request.subjectEntityId,
                subject_name=request.subjectName,
                subject_entity_type=request.subjectEntityType,
                predicate=request.predicate,
                object_entity_id=request.objectEntityId,
                object_text=request.objectText,
                summary=request.summary,
                valid_from=self._parse_moment(request.validFrom) if request.validFrom else None,
                note=request.note,
            ),
            now=datetime.now(timezone.utc),
        )
        return _statement_edge(row)

    def correct_statement(self, user_id: UUID, statement_ref: str, request: StatementCorrectRequest) -> dict:
        editor = self.editor.for_user(user_id)
        statement_id = self._parse_ref(statement_ref, _STATEMENT_PREFIX)
        old = editor.get_statement(statement_id)
        if old is None:
            raise MemoryObjectNotFoundError(f"陈述不存在: {statement_ref}")
        self._require_known_entity(editor, request.objectEntityId)
        self._ensure_values_changed(old, request)

        mutation = editor.correct_statement(
            statement_id,
            FactWrite(
                predicate=request.predicate,
                object_entity_id=request.objectEntityId,
                object_text=request.objectText,
                summary=request.summary,
                valid_from=self._parse_moment(request.validFrom) if request.validFrom else None,
                note=request.note,
            ),
            now=datetime.now(timezone.utc),
        )
        return {"old": _statement_edge(mutation.old), "new": _statement_edge(mutation.new)}

    def archive_statement(self, user_id: UUID, statement_ref: str) -> dict:
        editor = self.editor.for_user(user_id)
        row = editor.archive_statement(
            self._parse_ref(statement_ref, _STATEMENT_PREFIX),
            now=datetime.now(timezone.utc),
        )
        return _statement_edge(row)

    # ---------------- 实体：改名 / 别名 / 类型 ----------------

    def update_entity(self, user_id: UUID, entity_ref: str, request: EntityUpdateRequest) -> dict:
        editor = self.editor.for_user(user_id)
        entity_id = self._parse_ref(entity_ref, _ENTITY_PREFIX)
        entity = editor.get_entity(entity_id)
        if entity is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {entity_ref}")
        if request.entityType is not None and request.entityType not in {e.value for e in EntityType}:
            raise MemoryInvalidParamError(
                f"非法实体类型: {request.entityType!r}，可选值: {', '.join(e.value for e in EntityType)}"
            )
        if request.name is not None and request.name != entity.name:
            self._ensure_name_free(editor, request.name, entity_id)
        for alias in request.aliases or []:
            if alias != entity.name and alias != request.name:
                self._ensure_name_free(editor, alias, entity_id)

        row = editor.update_entity(
            entity_id,
            name=request.name,
            aliases=request.aliases,
            entity_type=request.entityType,
        )
        return _entity_node(row)

    # ---------------- 实体身份纠错：合并 / 拆分 / 孤立清理 ----------------

    def merge_entity(self, user_id: UUID, source_ref: str, request: EntityMergeRequest) -> dict:
        """错分离合并：source 全量并入 target（含历史行）后删除 source。"""
        editor = self.editor.for_user(user_id)
        result = editor.merge_entity(
            self._parse_ref(source_ref, _ENTITY_PREFIX),
            self._parse_ref(request.targetRef, _ENTITY_PREFIX),
        )
        return {
            "movedStatements": result["statements"],
            "movedLinks": result["links"],
            "target": _entity_node(result["target"]),
        }

    def split_entity(self, user_id: UUID, source_ref: str, request: EntitySplitRequest) -> dict:
        """错合并拆分：所选内容迁往新实体，双方互写拆分禁令。"""
        editor = self.editor.for_user(user_id)
        source_id = self._parse_ref(source_ref, _ENTITY_PREFIX)
        source = editor.get_entity(source_id)
        if source is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {source_ref}")

        statement_ids = [self._parse_ref(r, _STATEMENT_PREFIX) for r in request.statementIds]
        link_ids = [self._parse_ref(r, _LINK_PREFIX) for r in request.episodeLinkIds]
        moving_aliases = [a for a in request.aliases if a]
        if not statement_ids and not link_ids and not moving_aliases:
            raise MemoryNoChangeError("未选择任何要分离的内容：事实 / 事件参与 / 别名至少一项")

        if request.name == source.name:
            raise MemoryInvalidParamError("新实体名称与原实体相同：请改用其他名字")
        if request.entityType not in {e.value for e in EntityType}:
            raise MemoryInvalidParamError(
                f"非法实体类型: {request.entityType!r}，可选值: {', '.join(e.value for e in EntityType)}"
            )
        # 新名字/别名不得撞其他实体——拆成已存在的名字等于变相合并
        self._ensure_name_free(editor, request.name, source_id)
        for alias in moving_aliases:
            if alias != source.name:
                self._ensure_name_free(editor, alias, source_id)

        # 归属校验：所选陈述/参与必须真的挂在 source 上（防跨实体误迁移）
        for row in editor.statements_by_ids(statement_ids):
            if source_id not in (row.subject_id, row.object_entity_id):
                raise MemoryInvalidParamError(
                    f"陈述 s:{row.id} 不属于「{source.name}」，无法分离"
                )
        for link in editor.episode_links_by_ids(link_ids):
            if link.entity_id != source_id:
                raise MemoryInvalidParamError(
                    f"事件参与 l:{link.id} 不属于「{source.name}」，无法分离"
                )

        result = editor.split_entity(source_id, EntitySplitSpec(
            name=request.name,
            entity_type=request.entityType,
            aliases=tuple(moving_aliases),
            statement_ids=tuple(statement_ids),
            episode_link_ids=tuple(link_ids),
        ))
        return {
            "entity": _entity_node(result["new"]),
            "movedStatements": result["statements"],
            "movedLinks": result["links"],
            "sourceDeleted": result["source_deleted"],
        }

    def delete_entity(self, user_id: UUID, entity_ref: str) -> dict:
        """孤立实体清理；返回被删实体的档案快照。"""
        editor = self.editor.for_user(user_id)
        entity_id = self._parse_ref(entity_ref, _ENTITY_PREFIX)
        entity = editor.get_entity(entity_id)
        if entity is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {entity_ref}")
        node = _entity_node(entity)
        editor.delete_orphan_entity(entity_id)
        return node

    # ---------------- 事件编辑（L3）：档案直改 / 删除 / 参与改挂 ----------------

    def update_episode(self, user_id: UUID, episode_ref: str, request: EpisodeUpdateRequest) -> dict:
        if request.summary is None and request.scene is None and request.occurredAt is None:
            raise MemoryInvalidParamError("未提供任何要修改的字段")
        editor = self.editor.for_user(user_id)
        row = editor.update_episode(
            self._parse_ref(episode_ref, _EPISODE_PREFIX),
            summary=request.summary,
            scene=request.scene,
            occurred_at=self._parse_moment(request.occurredAt) if request.occurredAt else None,
        )
        return _episode_node(row)

    def delete_episode(self, user_id: UUID, episode_ref: str) -> dict:
        """物理删除事件及其参与边（不可恢复）；返回被删档案快照。"""
        editor = self.editor.for_user(user_id)
        row = editor.delete_episode(self._parse_ref(episode_ref, _EPISODE_PREFIX))
        return _episode_node(row)

    def update_episode_link(self, user_id: UUID, link_ref: str, request: EpisodeLinkUpdateRequest) -> dict:
        if request.entityId is None and request.role is None:
            raise MemoryNoChangeError("未提供任何要修改的字段")
        editor = self.editor.for_user(user_id)
        self._require_known_entity(editor, request.entityId)
        link = editor.update_episode_link(
            self._parse_ref(link_ref, _LINK_PREFIX),
            entity_id=request.entityId,
            role=request.role,
        )
        return {
            "id": f"l:{link.id}",
            "kind": "episode_link",
            "source": f"ep:{link.episode_id}",
            "target": f"e:{link.entity_id}",
            "role": link.role,
        }

    # ---------------- 请求语义判断 ----------------

    def _ensure_values_changed(self, old, request: StatementCorrectRequest) -> None:
        """纠正值与现值全部等价（且无备注）时拒绝：空转的取代链只会污染历史。"""
        if request.predicate is not None and request.predicate.strip() != old.predicate:
            return
        if request.objectEntityId is not None and request.objectEntityId != old.object_entity_id:
            return
        if request.objectText is not None and (
            old.object_entity_id is not None or request.objectText != old.object_text
        ):
            return
        if request.summary is not None and request.summary != old.summary:
            return
        if request.validFrom is not None and self._parse_moment(request.validFrom) != _aware(old.valid_from):
            return
        raise MemoryNoChangeError(
            "纠正值与现值相同：该事实无需纠正（如需补充备注请直接新增关联事实）"
        )

    @staticmethod
    def _ensure_name_free(editor: MemoryEditor, candidate: str, self_entity_id: int) -> None:
        """名字/别名不得撞其他实体（撞自己无碍）：拆成已存在的名字等于变相合并。"""
        for hit in (
            editor.find_entity_by_name(candidate),
            editor.find_entity_by_alias(candidate),
        ):
            if hit is not None and hit.id != self_entity_id:
                raise MemoryNameConflictError(
                    f"「{candidate}」已被实体 #{hit.id}「{hit.name}」占用，改名会与其实体身份冲突"
                )

    @staticmethod
    def _require_known_entity(editor: MemoryEditor, entity_id: int | None) -> None:
        if entity_id is not None and editor.get_entity(entity_id) is None:
            raise MemoryObjectNotFoundError(f"实体不存在: {entity_id}")

    # ---------------- 解析小件 ----------------

    # ---------------- 危险操作区（L4）：当日清除 / 会话遗忘 / 整体重置 ----------------

    def purge_preview(self, user_id: UUID, request: MaintenancePurgeRequest) -> dict:
        from_dt, to_dt, thread_id = self._resolve_scope(request)
        return self.editor.for_user(user_id).purge_preview(from_dt=from_dt, to_dt=to_dt, thread_id=thread_id)

    def purge_memory(self, user_id: UUID, request: MaintenancePurgeRequest) -> dict:
        from_dt, to_dt, thread_id = self._resolve_scope(request)
        return self.editor.for_user(user_id).purge_memory(from_dt=from_dt, to_dt=to_dt, thread_id=thread_id)

    def export_memory(self, user_id: UUID) -> dict:
        return self.editor.for_user(user_id).export_memory()

    def reset_all_memory(self, user_id: UUID, request: MaintenanceResetRequest) -> dict[str, int]:
        if request.confirmation.strip() != "重置":
            raise MemoryInvalidParamError("确认文字不匹配：请逐字输入「重置」以执行该操作")
        return self.editor.for_user(user_id).reset_all_memory()

    @staticmethod
    def _resolve_scope(request: MaintenancePurgeRequest) -> tuple[datetime | None, datetime | None, UUID | None]:
        if request.scope == "thread":
            if not request.threadId:
                raise MemoryInvalidParamError("会话遗忘需要提供 threadId")
            try:
                return None, None, UUID(request.threadId)
            except ValueError as exc:
                raise MemoryInvalidParamError(f"非法 threadId: {request.threadId!r}") from exc
        if not request.from_dt or not request.to:
            raise MemoryInvalidParamError("范围清除需要提供 from/to（ISO 时刻）")
        return (
            MemoryAdminService._parse_moment(request.from_dt),
            MemoryAdminService._parse_moment(request.to),
            None,
        )

    @staticmethod
    def _parse_ref(raw: str, prefix: str) -> int:
        """``s:13`` / ``e:5``（图快照 id 形态）或裸数字 → 数据库主键。"""
        text = raw.strip()
        if text.startswith(prefix):
            text = text[len(prefix):]
        try:
            return int(text)
        except ValueError as exc:
            raise MemoryInvalidParamError(f"非法对象引用: {raw!r}") from exc

    @staticmethod
    def _parse_moment(raw: str) -> datetime:
        """ISO 日期/日期时间 → UTC 时刻；纯日期取当天 00:00（事实自当天起生效）。"""
        text = raw.strip()
        for fmt in _TIME_FORMATS:
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        else:
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError as exc:
                raise MemoryInvalidTimeParamError(
                    f"无法解析时间参数: {raw!r}，请使用 ISO 格式（YYYY-MM-DD[ HH:MM]）"
                ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
