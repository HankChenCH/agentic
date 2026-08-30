"""图快照领域服务（P1 管理侧只读）：把记忆图谱投影为可视化友好的 JSON。

分层落位（AGENTS.md 两层制）：api ──► domain，管理端点直接消费本服务；
数据经 ``ports.MemoryGraphReader`` 端口获取——协议住领域层，实现由
components/memory 以 ``@injectable(as_type=...)`` 回填。

两种视图：
- 当前态（缺省）：ACTIVE 陈述为带谓词边，实体 + 情节（§E 事件节点）为节点；
- 时点回放（``at=``）：valid 区间覆盖该时刻的陈述全部入图（含已 SUPERSEDED
  的历史值——回放的意义恰在于看到"当时是什么"），情节取 occurred_at ≤ at。

输出为纯 JSON 安全结构（datetime→ISO、UUID→str、camelCase 键），前端
React Flow / Cytoscape 可直接布局；边 id 即拓扑行溯源键 ``#S{id}`` 中的
id，编辑/审计按号直达。
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from wireup import injectable

from app.exceptions.memory import MemoryInvalidTimeParamError
from app.models.domain.memory import MemoryEntity, MemoryEpisode, MemoryStatement
from app.services.domain.memory.ports import MemoryGraphReader

_DEFAULT_LIMIT = 300
_MAX_LIMIT = 2000


@injectable
@dataclass
class MemoryGraphService:
    """管理侧记忆图快照：只读、无状态、可任意次重放。

    快照作用域为当前用户（记忆是用户级数据）：经 ``reader.for_user`` 取
    绑定用户的只读视图后取数。
    """

    reader: MemoryGraphReader

    def graph_snapshot(self, user_id: UUID, at_iso: str | None = None, limit: int | None = None) -> dict:
        reader = self.reader.for_user(user_id)
        cap = min(max(limit or _DEFAULT_LIMIT, 1), _MAX_LIMIT)
        at = self._parse_at(at_iso)
        snapshot = self._slice(reader, at, cap) if at is not None else self._current(reader, cap)
        snapshot["at"] = _iso(at)
        snapshot["generatedAt"] = _iso(datetime.now(timezone.utc))
        snapshot["stats"] = {
            "entityNodes": sum(1 for n in snapshot["nodes"] if n["kind"] == "entity"),
            "episodeNodes": sum(1 for n in snapshot["nodes"] if n["kind"] == "episode"),
            "statementEdges": sum(1 for e in snapshot["edges"] if e["kind"] == "statement"),
            "episodeLinkEdges": sum(1 for e in snapshot["edges"] if e["kind"] == "episode_link"),
        }
        return snapshot

    # ---------------- 视图构建 ----------------

    def _current(self, reader: MemoryGraphReader, cap: int) -> dict:
        entities = {e.id: e for e in reader.list_entities(cap)}
        statements = reader.list_active_statements(cap)
        episodes = reader.list_episodes(cap)
        links = reader.links_for_episodes([ep.id for ep in episodes])

        # 补齐被 cap 截漏、但被边引用的实体节点（不截断图的连通性）
        missing = _referenced_entity_ids(statements) - set(entities)
        if missing:
            entities.update(reader.get_entities(sorted(missing)))

        return {
            "nodes": [_entity_node(e) for e in entities.values()]
            + [_episode_node(ep) for ep in episodes],
            "edges": [_statement_edge(s) for s in statements]
            + _link_edges(links),
        }

    def _slice(self, reader: MemoryGraphReader, at: datetime, cap: int) -> dict:
        statements = reader.statements_valid_at(at)[:cap]
        involved = _referenced_entity_ids(statements)
        entities = reader.get_entities(sorted(involved))
        # 情节只取已发生的（occurred_at ≤ at）；库值为 naive UTC，与锚比较前归一
        episodes = [
            ep for ep in reader.list_episodes(cap * 2)
            if _aware(ep.occurred_at) <= at
        ][:cap]
        links = reader.links_for_episodes([ep.id for ep in episodes])

        return {
            "nodes": [_entity_node(e) for e in entities.values()]
            + [_episode_node(ep) for ep in episodes],
            "edges": [_statement_edge(s) for s in statements]
            + _link_edges(links),
        }

    @staticmethod
    def _parse_at(raw: str | None) -> datetime | None:
        """ISO 日期/日期时间 → UTC 锚；纯日期按当天末尾（覆盖全天状态）。"""
        if raw is None or raw.strip() == "":
            return None
        text = raw.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                    "%Y/%m/%d %H:%M", "%Y/%m/%d"):
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
                    f"无法解析时间参数 at: {raw!r}，请使用 ISO 格式（YYYY-MM-DD[ HH:MM]）"
                ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if len(text) <= 10:  # 纯日期：取当天末尾，覆盖“那天全天的状态”
            parsed = parsed.replace(hour=23, minute=59, second=59)
        return parsed


# ---------------- JSON 组装小件（camelCase 对外契约） ----------------


def _referenced_entity_ids(statements: list[MemoryStatement]) -> set[int]:
    ids: set[int] = set()
    for s in statements:
        ids.add(s.subject_id)
        if s.object_entity_id is not None:
            ids.add(s.object_entity_id)
    return ids


def _entity_node(entity: MemoryEntity) -> dict:
    return {
        "id": f"e:{entity.id}",
        "kind": "entity",
        "entityType": entity.entity_type,
        "name": entity.name,
        "aliases": list(entity.aliases or []),
        "isUser": bool(entity.is_user),
        "importance": entity.importance,
        "accessCount": entity.access_count,
        "lastAccessedAt": _iso(getattr(entity, "last_accessed_at", None)),
    }


def _episode_node(episode: MemoryEpisode) -> dict:
    return {
        "id": f"ep:{episode.id}",
        "kind": "episode",
        "summary": episode.summary,
        "scene": episode.scene,
        "occurredAt": _iso(episode.occurred_at),
        "accessCount": episode.access_count,
    }


def _statement_edge(statement: MemoryStatement) -> dict:
    return {
        "id": f"s:{statement.id}",
        "kind": "statement",
        "source": f"e:{statement.subject_id}",
        "target": f"e:{statement.object_entity_id}" if statement.object_entity_id is not None else None,
        "objectText": statement.object_text,
        "predicate": statement.predicate,
        "summary": statement.summary,
        "state": statement.state,
        "validFrom": _iso(statement.valid_from),
        "validTo": _iso(statement.valid_to),
        "confidence": statement.confidence,
        "origin": statement.origin,
        "sourceThreadId": str(statement.source_thread_id) if statement.source_thread_id else None,
        "sourceTurnId": str(statement.source_turn_id) if statement.source_turn_id else None,
    }


def _link_edges(links: dict[int, list]) -> list[dict]:
    return [
        {
            "id": f"l:{link.id}",
            "kind": "episode_link",
            "source": f"ep:{link.episode_id}",
            "target": f"e:{link.entity_id}",
            "role": link.role,
        }
        for bundle in links.values()
        for link in bundle
    ]


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _iso(value: datetime | UUID | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value).isoformat()
    return str(value)
