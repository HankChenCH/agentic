from dataclasses import dataclass, field
import logging
import time
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from wireup import injectable

from app.core.config import AppConfig
from app.infrastructures.llm import ModelFactory
from app.components.memory.repositories import MemoryRepository
from app.components.memory.extraction import (
    ExtractedEntity,
    FactDecision,
    adjudicate_facts,
    extract_structure,
    resolve_time_hint,
)
from app.components.memory import renderer
from app.components.memory.scoring import ScoreWeights, ScorableItem, score_item
from app.components.memory.vocab import cardinality
from app.services.domain.memory import (
    KIND_ENTITY,
    KIND_EPISODE,
    KIND_STATEMENT,
    MemoryVectorIndex,
    VectorEntry,
)

from app.models.domain.memory import (
    EntityType,
    MemoryOrigin,
    StatementState,
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryStatement,
)

from app.models.domain.agentic import (
    AgenticMessageType,
    AgenticConversationMessage,
)

# “用户”节点的规范名：抽取出的 user 键恒定映射到该实体
USER_ENTITY_NAME = "用户"

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（惯例同 api/exception_handlers）
logger = logging.getLogger(__name__)

# 会话内已投喂记忆登记的默认存活时长
_REGISTRY_TTL_SECONDS = 12 * 3600


class SessionInjectRegistry:
    """thread → 已投喂记忆 id 的进程内 TTL 登记（设计 §6.3 去重）。

    id 编码 ``s:{sid}`` / ``e:{eid}`` 区分陈述与事件；TTL 过期整键清理。
    仅服务单进程语义——uvicorn 多 worker 各自维护，作为去重的尽力而为。
    """

    def __init__(self, ttl_seconds: int = _REGISTRY_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._store: dict[str, tuple[float, set[str]]] = {}

    def mark(self, thread_id: UUID | str, ref_ids: list[str], now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        key = str(thread_id)
        expires, bucket = self._store.get(key, (now + self._ttl, set()))
        if expires <= now:
            expires = now + self._ttl
            bucket = set()
        bucket.update(ref_ids)
        self._store[key] = (expires, bucket)

    def seen(self, thread_id: UUID | str) -> set[str]:
        now = time.monotonic()
        entry = self._store.get(str(thread_id))
        if entry is None:
            return set()
        expires, bucket = entry
        if expires <= now:
            self._store.pop(str(thread_id), None)
            return set()
        return set(bucket)


@injectable
@dataclass
class MemoryService:
    """记忆组件门面：remember 巩固管线（写）+ 两级召回（读）。

    组件自内聚惯例不变：存取策略收敛在自有 repositories（注入抽象
    MemoryRepository，当前绑定 SQLite 图谱实现）；向量适配器按「归位
    领域层」新约自 services/domain/memory 注入（components→domain 合法边）；
    抽取走 ModelFactory 裸模型而非 AgentFactory——memory 组件不得依赖
    agents（智能体工具装配会反向依赖本组件），否则形成包级环。

    两级召回：build_fast_context 会话前自动注入（纯 SQL 直读+评分截断，
    零 LLM/embedding）；timeline/expand/state_at 深度工具的实现在此，
    由 tools.py 薄封装成 agent 可调用的闭包。会话内已投喂的记忆经
    SessionInjectRegistry 登记，深度工具只返回增量。
    """

    memory_repo: MemoryRepository
    vector_index: MemoryVectorIndex
    model_factory: ModelFactory
    app_config: AppConfig

    def __post_init__(self):
        self.inject_registry = SessionInjectRegistry()

    # ==================== 写路径：巩固管线 ====================

    def remember(
        self, query: str, turn_messages: List[AgenticConversationMessage],
        thread_id: UUID, turn_id: UUID,
    ) -> list:
        """轮次结束后巩固记忆：❶结构化抽取→❷实体消歧→❸陈述裁决→❹落库。

        任何一环失败都不抛出（调用方 TurnFinalizer 另有兜底），只返回已落库条目。
        """
        if not self.app_config.memory.enabled:
            return []

        transcript = self._build_transcript(query, turn_messages)
        if not transcript:
            return []

        now = datetime.now(timezone.utc)
        model = self.model_factory.create(self.app_config.memory.extraction_provider)
        extracted = extract_structure(model=model, transcript=transcript, now=now)
        if extracted.is_empty():
            return []

        vector_entries: list[VectorEntry] = []
        created: list = []

        # ---- ❷ 实体消歧/建户节点 ----
        entities_by_key = self._resolve_entities(extracted.entities)
        user_node = self._ensure_user_entity()
        touched_entities = {row.id: row for row in entities_by_key.values()}
        vector_entries.extend(
            VectorEntry(KIND_ENTITY, row.id, _entity_content(row), None, None)
            for row in touched_entities.values()
        )

        # ---- 事件梗概 + 挂接 ----
        for episode in extracted.episodes:
            occurred = resolve_time_hint(episode.time_hint, now) or now
            row = self.memory_repo.insert_episode(MemoryEpisode(
                thread_id=thread_id, turn_id=turn_id,
                occurred_at=occurred, scene=episode.scene, summary=episode.summary,
            ))
            links = [
                MemoryEpisodeLink(episode_id=row.id, entity_id=target.id, role=role or "参与者")
                for key, role in episode.links
                for target in [user_node if key == "user" else entities_by_key.get(key)]
                if target is not None
            ]
            self.memory_repo.link_episode_entities(links)
            vector_entries.append(VectorEntry(KIND_EPISODE, row.id, episode.summary, str(thread_id), occurred))
            created.append(row)

        # ---- 陈述配对（key → 实体行 / 字面量）----
        paired = self._pair_facts(extracted.facts, entities_by_key, user_node, thread_id, turn_id, now)
        active_rows = self.memory_repo.find_active_statements(sorted({p.subject.id for p in paired}))
        names = {**{row.id: row.name for row in entities_by_key.values()}, user_node.id: USER_ENTITY_NAME}

        # ---- ❸ 裁决（LLM；失败程序化降级）----
        decisions = adjudicate_facts(model, [p.fact for p in paired], self._statement_digest(active_rows, names))
        if decisions is None:
            decisions = self._programmatic_decisions(paired, active_rows)

        # ---- ❹ 按 ADD/REPLACE/SKIP 应用 + 向量同步 ----
        by_id = {row.id: row for row in active_rows}
        live_subject = {p.subject.id for p in paired}
        for fact_pair, decision in zip(paired, decisions):
            statement_row = self._apply_decision(
                fact_pair, decision, by_id, live_subject, now
            )
            if decision.replace_id is not None and statement_row is not None:
                target = by_id.pop(decision.replace_id, None)
                if target is not None and target.subject_id in live_subject:
                    self.memory_repo.supersede_statement(target.id, fact_pair.valid_from, now)
            if statement_row is None:
                continue
            created.append(statement_row)
            vector_entries.append(VectorEntry(
                KIND_STATEMENT, statement_row.id, statement_row.summary,
                str(thread_id), statement_row.valid_from or now,
            ))

        if vector_entries:
            self.vector_index.upsert_many(vector_entries)
        return created

    def _resolve_entities(self, candidates: tuple[ExtractedEntity, ...]) -> dict[str, MemoryEntity]:
        """精确名 → 别名 → 向量候选发现+余弦判定（阈值内合并别名）→ 新建。

        向量分支两段式：search 只做候选发现（融合分仅排序，不可与绝对
        阈值比较——「学校并入公司」事故见 docs/memory-v2-design.md §8），
        合并与否由 text_cosine 的真实余弦 ≥ resolution.similarity_threshold
        决定；类型冲突一律拒绝。合并/拒绝均记日志，杜绝静默错合并。
        """
        resolved: dict[str, MemoryEntity] = {}
        threshold = self.app_config.memory.resolution.similarity_threshold
        for candidate in candidates:
            if renderer.is_internal_ref(candidate.name):
                logger.warning("跳过内部溯源引用形态的候选实体：%r", candidate.name)
                continue
            hit = (
                self.memory_repo.find_entity_by_name(candidate.name)
                or self.memory_repo.find_entity_by_alias(candidate.name)
                or next(
                    (self.memory_repo.find_entity_by_alias(a) for a in candidate.aliases if a),
                    None,
                )
            )
            if hit is None:
                hit = self._vector_resolve(candidate, threshold)
            clean_aliases = [a for a in candidate.aliases if not renderer.is_internal_ref(a)]
            if hit is None:
                hit = self.memory_repo.upsert_entity(MemoryEntity(
                    entity_type=candidate.entity_type,
                    name=candidate.name,
                    aliases=clean_aliases,
                    importance=candidate.importance,
                    origin=MemoryOrigin.EXTRACTED.value,
                ))
            else:
                hit.aliases = sorted(set(hit.aliases or []) | {candidate.name} | set(clean_aliases))
                hit.importance = max(hit.importance, candidate.importance)
                hit = self.memory_repo.upsert_entity(hit)
                logger.info("实体合并：%r 并入 #%s「%s」", candidate.name, hit.id, hit.name)
            resolved[candidate.key] = hit
        return resolved

    def _vector_resolve(self, candidate: ExtractedEntity, threshold: float) -> MemoryEntity | None:
        """向量候选发现 + 客户端余弦判定；低分/类型冲突/失败一律不合并。

        search 的融合分只用于圈定 top-3 候选；合并判定用 text_cosine 对
        候选档案文本（name+aliases，与向量写入同内容）现算的绝对余弦。
        """
        similar = self.vector_index.search(
            candidate.name, kinds=(KIND_ENTITY,), top_k=3, alpha=1.0
        )
        rows = self.memory_repo.get_entities([h.ref_id for h in similar])
        if not rows:
            return None
        cosines = self.vector_index.text_cosine(
            candidate.name, [_entity_content(row) for row in rows.values()]
        )
        best_row, best_score = max(zip(rows.values(), cosines), key=lambda pair: pair[1])
        if best_score < threshold:
            logger.info(
                "实体 %r 最近候选 #%s「%s」余弦 %.2f 低于阈值 %.2f，按新建处理",
                candidate.name, best_row.id, best_row.name, best_score, threshold,
            )
            return None
        if (
            candidate.entity_type != best_row.entity_type
            and EntityType.OTHER.value not in (candidate.entity_type, best_row.entity_type)
        ):
            logger.warning(
                "实体 %r(%s) 与候选 #%s「%s」(%s) 余弦 %.2f 达标但类型冲突，拒绝合并",
                candidate.name, candidate.entity_type,
                best_row.id, best_row.name, best_row.entity_type, best_score,
            )
            return None
        return best_row

    def _ensure_user_entity(self) -> MemoryEntity:
        node = self.memory_repo.find_entity_by_name(USER_ENTITY_NAME)
        if node is None:
            node = self.memory_repo.upsert_entity(MemoryEntity(
                entity_type=EntityType.PERSON.value,
                name=USER_ENTITY_NAME,
                is_user=True,
                importance=0.9,
                origin=MemoryOrigin.EXTRACTED.value,
            ))
        return node

    def _pair_facts(self, facts, entities_by_key, user_node, thread_id, turn_id, now) -> list["_FactPair"]:
        paired = []
        for fact in facts:
            subject = user_node if fact.subject_key == "user" else entities_by_key.get(fact.subject_key)
            if subject is None:
                continue
            object_entity = None
            literal = fact.object_text
            if fact.object_key:
                if fact.object_key == "user":
                    object_entity = user_node
                else:
                    object_entity = entities_by_key.get(fact.object_key)
            valid_from = resolve_time_hint(fact.time_hint, now) or now
            evidence = (
                [{"quote": fact.evidence, "source_turn_id": str(turn_id)}]
                if fact.evidence else []
            )
            paired.append(_FactPair(
                fact=fact, subject=subject, object_entity=object_entity,
                object_text=(literal if object_entity is None else None),
                evidence=evidence, valid_from=valid_from,
                source_thread_id=thread_id, source_turn_id=turn_id,
            ))
        return paired

    def _apply_decision(self, pair: "_FactPair", decision: FactDecision, by_id, live_subject, now) -> MemoryStatement | None:
        # 身份重复保险：无论裁决结果如何，同主体同谓词同客体的 ACTIVE 行不重复插入
        if any(
            row.predicate == pair.fact.predicate
            and row.subject_id == pair.subject.id
            and (
                (pair.object_entity is not None and row.object_entity_id == pair.object_entity.id)
                or (pair.object_text is not None and row.object_text == pair.object_text)
            )
            for row in by_id.values()
            if row.state == StatementState.ACTIVE.value
        ):
            return None
        if decision.action == "SKIP":
            return None
        if decision.action == "REPLACE" and decision.replace_id is not None:
            target = by_id.get(decision.replace_id)
            if target is None or target.state != StatementState.ACTIVE.value:
                pass  # 失效目标：降级为普通新增继续落库
            elif target.origin == MemoryOrigin.MANUAL.value:
                # MANUAL 保护（设计 §4）：人工事实不可被自动取代，冲突事实整体放弃
                return None
            else:
                self.memory_repo.supersede_statement(target.id, pair.valid_from, now)
                by_id.pop(target.id, None)
        summary = _fact_summary(pair)
        return self.memory_repo.insert_statement(MemoryStatement(
            subject_id=pair.subject.id,
            predicate=pair.fact.predicate,
            object_entity_id=pair.object_entity.id if pair.object_entity else None,
            object_text=pair.object_text,
            summary=summary,
            evidence=pair.evidence,
            state=StatementState.ACTIVE.value,
            valid_from=pair.valid_from,
            time_remark=pair.fact.time_remark,
            origin=MemoryOrigin.EXTRACTED.value,
            confidence=pair.fact.confidence,
            source_thread_id=pair.source_thread_id,
            source_turn_id=pair.source_turn_id,
        ))

    def _programmatic_decisions(self, paired, active_rows) -> list[FactDecision]:
        group: dict[int, list] = {}
        for row in active_rows:
            group.setdefault(row.subject_id, []).append(row)
        decisions = []
        for pair in paired:
            same_pred = [
                r for r in group.get(pair.subject.id, [])
                if r.predicate == pair.fact.predicate
            ]
            duplicate = any(
                (pair.object_entity is not None and r.object_entity_id == pair.object_entity.id)
                or (pair.object_text is not None and r.object_text == pair.object_text)
                for r in same_pred
            )
            if duplicate:
                decisions.append(FactDecision("SKIP"))
            elif cardinality(pair.fact.predicate) == "single" and same_pred:
                newest = max(same_pred, key=lambda r: r.updated_at or r.created_at)
                decisions.append(FactDecision("REPLACE", replace_id=newest.id))
            else:
                decisions.append(FactDecision("ADD"))
        return decisions

    @staticmethod
    def _statement_digest(rows, names: dict[int, str]) -> list[dict]:
        return [
            {
                "id": row.id,
                "subject_name": names.get(row.subject_id, "?"),
                "predicate": row.predicate,
                "object_label": f"{names.get(row.object_entity_id, '')}"
                if row.object_entity_id is not None
                else (row.object_text or ""),
                "origin": row.origin,
            }
            for row in rows
        ]

    @staticmethod
    def _build_transcript(query: str, turn_messages: List[AgenticConversationMessage]) -> str:
        # 与标题生成同款拼接：用户 query + 助手 MESSAGE 文本。助手侧剥离
        # 内部溯源引用（#S13/§E3 等）——防止被复读的编号经抽取吸回实体档案
        assistant_text = "\n".join(
            renderer.strip_internal_refs(part.get("text", ""))
            for msg in turn_messages
            if msg.message_type == AgenticMessageType.MESSAGE
            for part in msg.content
            if part.get("type") == "text"
        )
        return f"用户：{query}\n助手：{assistant_text}".strip()

    # ==================== 读路径：两级召回 ====================

    def build_fast_context(self, query: str, thread_id: UUID) -> str:
        """快速回忆块：纯 SQL 直读评分截断，零 LLM/embedding。寒暄不注入。"""
        if not self.app_config.memory.enabled or _is_smalltalk(query):
            return ""
        now = datetime.now(timezone.utc)
        weights = self._weights()
        half_life = self.app_config.memory.score.recency_half_life_days
        seen = self.inject_registry.seen(thread_id)

        scored = []
        for row in self.memory_repo.list_active_statements():
            if f"s:{row.id}" in seen:
                continue
            value = score_item(_item_of(row, None), weights, half_life, now)
            scored.append((value, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = scored[: self.app_config.memory.recall.fast_limit]
        if not top:
            return ""

        rows = [row for _score, row in top]
        ent_ids = {r.subject_id for r in rows} | {r.object_entity_id for r in rows if r.object_entity_id}
        ents = self.memory_repo.get_entities(sorted(ent_ids))
        lines = [renderer.statement_topology_line(r, ents, now=now) for r in rows]

        self.inject_registry.mark(thread_id, [f"s:{r.id}" for r in rows])
        self.memory_repo.bump_access([r.id for r in rows], [], sorted(ents), now)
        return renderer.brief_context(now, lines)

    def timeline(self, query: str, thread_id: UUID) -> str:
        """深度·情节检索：episode 向量命中 → 分片渲染（仅增量）。"""
        now = datetime.now(timezone.utc)
        cfg = self.app_config.memory
        hits = self.vector_index.search(query, kinds=(KIND_EPISODE,), top_k=cfg.recall.top_k, alpha=cfg.recall.alpha)
        fresh = [(h.ref_id, h.score) for h in hits if f"e:{h.ref_id}" not in self.inject_registry.seen(thread_id)]
        episodes = self.memory_repo.episodes_by_ids([ref for ref, _score in fresh])
        by_id = {ep.id: ep for ep in episodes}
        eps_scored = [(by_id[ref], score) for ref, score in fresh if ref in by_id]
        if not eps_scored:
            return renderer.fragments_output(now, [])

        links = self.memory_repo.links_for_episodes([ep.id for ep, _s in eps_scored])
        ent_ids = sorted({lk.entity_id for bundle in links.values() for lk in bundle})
        entities = self.memory_repo.get_entities(ent_ids)
        fragments = renderer.assemble_fragments([], eps_scored, links, entities, now=now)[
            : cfg.render.max_fragments
        ]

        self.inject_registry.mark(thread_id, [f"e:{ep.id}" for ep, _s in eps_scored])
        self.memory_repo.bump_access(
            [], [ep.id for ep, _s in eps_scored], sorted(entities.keys()), now
        )
        return renderer.fragments_output(now, fragments)

    def expand(self, entity_ref: str, thread_id: UUID) -> str:
        """深度·图扩散：命中实体的邻域陈述 + 关联事件（1-hop）。"""
        now = datetime.now(timezone.utc)
        cfg = self.app_config.memory
        anchor = self._resolve_anchor(entity_ref)
        if anchor is None:
            return f"（未找到与“{entity_ref}”相关的记忆对象）"
        seen = self.inject_registry.seen(thread_id)

        statements = [
            r for r in self.memory_repo.find_active_statements([anchor.id])
            if f"s:{r.id}" not in seen
        ]
        episodes = [
            e for e in self.memory_repo.episodes_for_entities([anchor.id], cfg.recall.expansion_limit)
            if f"e:{e.id}" not in seen
        ]
        if not statements and not episodes:
            return f"（关于“{anchor.name}”暂无未读关联记忆）"

        weights, half_life = self._weights(), cfg.score.recency_half_life_days
        s_scored = [(r, score_item(_item_of(r, None), weights, half_life, now)) for r in statements]
        e_scored = [(e, score_item(_item_of(e, None), weights, half_life, now)) for e in episodes]
        links = self.memory_repo.links_for_episodes([e.id for e, _s in e_scored])

        ent_ids = set()
        for r in statements:
            ent_ids.update({r.subject_id, r.object_entity_id} - {None})
        for bundle in links.values():
            ent_ids.update(lk.entity_id for lk in bundle)
        entities = self.memory_repo.get_entities(sorted(ent_ids))

        fragments = renderer.assemble_fragments(
            s_scored, e_scored, links, entities, now=now
        )[: cfg.render.max_fragments]

        self.inject_registry.mark(
            thread_id, [f"s:{r.id}" for r in statements] + [f"e:{e.id}" for e, _s in e_scored]
        )
        self.memory_repo.bump_access(
            [r.id for r in statements], [e.id for e, _s in e_scored],
            sorted(set(entities) | {anchor.id}), now,
        )
        return renderer.fragments_output(now, fragments)

    def state_at(self, moment_hint: str, thread_id: UUID) -> str:
        """深度·时点回放：某时刻的世界状态切片（含 SUPERSEDED 历史值）。"""
        now = datetime.now(timezone.utc)
        parsed = resolve_time_hint(moment_hint, now)
        if parsed is None:
            return "时间无法解析，请用 YYYY-MM-DD（或 YYYY-MM-DD HH:MM）格式重试。"
        # 纯日期按当天末尾解释：覆盖“那天全天的状态”
        moment = parsed if (parsed.hour or parsed.minute or len(moment_hint.strip()) > 10) \
            else parsed.replace(hour=23, minute=59, second=59)
        limit = self.app_config.memory.recall.expansion_limit * 2
        rows = sorted(
            self.memory_repo.statements_valid_at(moment)[:limit],
            key=lambda r: (r.valid_from or moment, r.id),
        )
        if not rows:
            return f"（{parsed.strftime('%Y-%m-%d')} 时点暂无任何在效记录）"
        ent_ids = {r.subject_id for r in rows} | {r.object_entity_id for r in rows if r.object_entity_id}
        entities = self.memory_repo.get_entities(sorted(ent_ids))
        lines = [renderer.statement_topology_line(r, entities, now=now) for r in rows]
        self.inject_registry.mark(thread_id, [f"s:{r.id}" for r in rows])
        self.memory_repo.bump_access([r.id for r in rows], [], sorted(entities), now)
        head = f"## 时点回放 · {moment.strftime('%Y-%m-%d %H:%M')} 在效事实"
        numbered = [f"{i}. {line}" for i, line in enumerate(lines, start=1)]
        return "\n".join([head, *numbered])

    def _resolve_anchor(self, entity_ref: str) -> MemoryEntity | None:
        anchor = (
            self.memory_repo.find_entity_by_name(entity_ref.strip())
            or self.memory_repo.find_entity_by_alias(entity_ref.strip())
        )
        if anchor is not None:
            return anchor
        # 向量兜底与实体消歧同规：融合分仅圈候选，判定用真实余弦（§8 教训）
        threshold = self.app_config.memory.resolution.similarity_threshold
        hits = self.vector_index.search(entity_ref, kinds=(KIND_ENTITY,), top_k=3, alpha=1.0)
        rows = self.memory_repo.get_entities([h.ref_id for h in hits])
        if not rows:
            return None
        cosines = self.vector_index.text_cosine(
            entity_ref, [_entity_content(row) for row in rows.values()]
        )
        best_row, best_score = max(zip(rows.values(), cosines), key=lambda pair: pair[1])
        return best_row if best_score >= threshold else None

    def _weights(self) -> ScoreWeights:
        score_cfg = self.app_config.memory.score
        return ScoreWeights(
            relevance=score_cfg.relevance_weight,
            recency=score_cfg.recency_weight,
            importance=score_cfg.importance_weight,
        )


@dataclass(frozen=True)
class _FactPair:
    """裁决前的事实-实体配对投影。"""

    fact: object
    subject: MemoryEntity
    object_entity: MemoryEntity | None
    object_text: str | None
    evidence: list
    valid_from: datetime
    source_thread_id: UUID | None = None
    source_turn_id: UUID | None = None


def _is_smalltalk(query: str) -> bool:
    """最省成本的小话判别：超短且是问候/致谢类词面才跳过注入（宁可多注入）。"""
    text = query.strip()
    if len(text) > 12:
        return False
    tokens = ("你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "早上好", "下午好",
              "晚上好", "谢谢", "多谢", "再见", "拜拜", "晚安")
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _entity_content(row: MemoryEntity) -> str:
    alias_bit = f"（{'、'.join(row.aliases)}）" if row.aliases else ""
    return f"{row.name}{alias_bit}"


def _fact_summary(pair: _FactPair) -> str:
    obj = pair.object_entity.name if pair.object_entity else (pair.object_text or "")
    single_wording = f"{pair.subject.name}的{pair.fact.predicate}是{obj}"
    generic_wording = f"{pair.subject.name}{pair.fact.predicate}{obj}"
    return single_wording if cardinality(pair.fact.predicate) == "single" else generic_wording


def _item_of(row, relevance: float | None) -> ScorableItem:
    last_active_candidates = (
        getattr(row, "last_accessed_at", None),
        getattr(row, "occurred_at", None),
        getattr(row, "valid_from", None),
    )
    last_active = max((dt for dt in last_active_candidates if dt), default=None)
    return ScorableItem(
        relevance=relevance,
        last_active=last_active,
        importance=getattr(row, "importance", 0.5) or 0.5,
        access_count=getattr(row, "access_count", 0) or 0,
    )
