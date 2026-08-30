"""收尾块：轮次结束后的记忆巩固管线（remember 四步）。

❶结构化抽取（build_transcript + extract_structure，LLM×1）→ ❷实体消歧
（resolution 两段式：精确名/别名 → 向量候选发现+真余弦判定）→ ❸陈述裁决
（adjudicate_facts，LLM×1；失败 programmatic_decisions 降级）→ ❹按
ADD/REPLACE/SKIP 落库 + 向量同步（SQL 为事实源，向量 best-effort）。
由 TurnFinalizer 第三步在后台 daemon 线程调用；任何一环失败都不抛出
（调用方另有兜底），只返回已落库条目。

组件自内聚惯例：存取经注入的抽象 MemoryRepository（当前绑定 SQLite 图谱
实现）；向量适配器自 services/domain/memory 注入（components→domain 合法
边）；抽取走 ModelFactory 裸模型而非 AgentFactory——memory 组件不得依赖
agents（智能体工具装配会反向依赖本组件），否则形成包级环。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List
from uuid import UUID

from wireup import injectable

from app.core.config import AppConfig
from app.infrastructures.llm import ModelFactory
from app.components.memory.repositories import MemoryRepository
from app.components.memory.internal.extraction import (
    ExtractedEntity,
    FactDecision,
    PairedFact,
    adjudicate_facts,
    build_transcript,
    extract_structure,
    programmatic_decisions,
    resolve_time_hint,
    statement_digest,
)
from app.components.memory.internal import renderer, resolution
from app.components.memory.internal.vocab import fact_summary
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
    AgenticConversationMessage,
)

# “用户”节点的规范名：抽取出的 user 键恒定映射到该实体
USER_ENTITY_NAME = "用户"

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（惯例同 api/exception_handlers）
logger = logging.getLogger(__name__)


@injectable
@dataclass
class MemoryConsolidationService:
    """收尾块：remember 巩固管线——抽取→消歧→裁决→落库+向量同步。"""

    memory_repo: MemoryRepository
    vector_index: MemoryVectorIndex
    model_factory: ModelFactory
    app_config: AppConfig

    # ==================== 写路径：巩固管线 ====================

    def remember(
        self, query: str, turn_messages: List[AgenticConversationMessage],
        user_id: UUID, thread_id: UUID, turn_id: UUID,
    ) -> list:
        """轮次结束后巩固记忆：❶结构化抽取→❷实体消歧→❸陈述裁决→❹落库。

        记忆作用域为 user_id（用户级隔离）：实体消歧、用户节点、向量读写
        全部限定在该用户的图谱与 collection 内。任何一环失败都不抛出
        （调用方 TurnFinalizer 另有兜底），只返回已落库条目。
        """
        if not self.app_config.memory.enabled:
            return []

        transcript = build_transcript(query, turn_messages)
        if not transcript:
            return []

        now = datetime.now(timezone.utc)
        model = self.model_factory.create(self.app_config.memory.extraction_provider)
        extracted = extract_structure(model=model, transcript=transcript, now=now)
        if extracted.is_empty():
            return []

        repo = self.memory_repo.for_user(user_id)
        index = self.vector_index.for_user(user_id)

        vector_entries: list[VectorEntry] = []
        created: list = []

        # ---- ❷ 实体消歧/建户节点 ----
        entities_by_key = self._resolve_entities(extracted.entities, repo, index)
        user_node = self._ensure_user_entity(repo)
        touched_entities = {row.id: row for row in entities_by_key.values()}
        vector_entries.extend(
            VectorEntry(KIND_ENTITY, row.id, resolution.entity_content(row), None, None)
            for row in touched_entities.values()
        )

        # ---- 事件梗概 + 挂接 ----
        for episode in extracted.episodes:
            occurred = resolve_time_hint(episode.time_hint, now) or now
            row = repo.insert_episode(MemoryEpisode(
                user_id=user_id,
                thread_id=thread_id, turn_id=turn_id,
                occurred_at=occurred, scene=episode.scene, summary=episode.summary,
            ))
            links = [
                MemoryEpisodeLink(episode_id=row.id, entity_id=target.id, role=role or "参与者")
                for key, role in episode.links
                for target in [user_node if key == "user" else entities_by_key.get(key)]
                if target is not None
            ]
            repo.link_episode_entities(links)
            vector_entries.append(VectorEntry(KIND_EPISODE, row.id, episode.summary, str(thread_id), occurred))
            created.append(row)

        # ---- 陈述配对（key → 实体行 / 字面量）----
        paired = self._pair_facts(extracted.facts, entities_by_key, user_node, thread_id, turn_id, now)
        active_rows = repo.find_active_statements(sorted({p.subject.id for p in paired}))
        names = {**{row.id: row.name for row in entities_by_key.values()}, user_node.id: USER_ENTITY_NAME}

        # ---- ❸ 裁决（LLM；失败程序化降级）----
        decisions = adjudicate_facts(model, [p.fact for p in paired], statement_digest(active_rows, names))
        if decisions is None:
            logger.warning("记忆裁决不可用，降级为程序化规则（facts=%d，active=%d）",
                           len(paired), len(active_rows))
            decisions = programmatic_decisions(paired, active_rows)

        # ---- ❹ 按 ADD/REPLACE/SKIP 应用 + 向量同步 ----
        by_id = {row.id: row for row in active_rows}
        live_subject = {p.subject.id for p in paired}
        for fact_pair, decision in zip(paired, decisions):
            statement_row = self._apply_decision(
                fact_pair, decision, by_id, live_subject, now, repo,
            )
            if decision.replace_id is not None and statement_row is not None:
                target = by_id.pop(decision.replace_id, None)
                if target is not None and target.subject_id in live_subject:
                    repo.supersede_statement(target.id, fact_pair.valid_from, now)
            if statement_row is None:
                continue
            created.append(statement_row)
            vector_entries.append(VectorEntry(
                KIND_STATEMENT, statement_row.id, statement_row.summary,
                str(thread_id), statement_row.valid_from or now,
            ))

        if vector_entries:
            index.upsert_many(vector_entries)
        return created

    def _resolve_entities(
        self, candidates: tuple[ExtractedEntity, ...],
        repo: MemoryRepository, index: MemoryVectorIndex,
    ) -> dict[str, MemoryEntity]:
        """精确名 → 别名 → 向量候选发现+余弦判定（阈值内合并别名）→ 新建。

        向量分支两段式：search 只做候选发现（融合分仅排序，不可与绝对
        阈值比较——「学校并入公司」事故见 docs/memory-v2-design.md §8），
        合并与否由 text_cosine 的真实余弦 ≥ resolution.similarity_threshold
        决定；类型冲突一律拒绝。合并/拒绝均记日志，杜绝静默错合并。
        仓储/索引均为调用方传入的用户作用域视图——实体身份天然用户内唯一。
        """
        resolved: dict[str, MemoryEntity] = {}
        threshold = self.app_config.memory.resolution.similarity_threshold
        for candidate in candidates:
            if renderer.is_internal_ref(candidate.name):
                logger.warning("跳过内部溯源引用形态的候选实体：%r", candidate.name)
                continue
            hit = (
                resolution.exact_entity_match(candidate.name, repo)
                or next(
                    (repo.find_entity_by_alias(a) for a in candidate.aliases if a),
                    None,
                )
            )
            if hit is None:
                hit = resolution.vector_entity_match(
                    candidate.name, candidate.entity_type, repo, index, threshold,
                )
            clean_aliases = [a for a in candidate.aliases if not renderer.is_internal_ref(a)]
            if hit is None:
                hit = repo.upsert_entity(MemoryEntity(
                    entity_type=candidate.entity_type,
                    name=candidate.name,
                    aliases=clean_aliases,
                    importance=candidate.importance,
                    origin=MemoryOrigin.EXTRACTED.value,
                ))
            else:
                hit.aliases = sorted(set(hit.aliases or []) | {candidate.name} | set(clean_aliases))
                hit.importance = max(hit.importance, candidate.importance)
                hit = repo.upsert_entity(hit)
                logger.info("实体合并：%r 并入 #%s「%s」", candidate.name, hit.id, hit.name)
            resolved[candidate.key] = hit
        return resolved

    def _ensure_user_entity(self, repo: MemoryRepository) -> MemoryEntity:
        """每用户一个「用户」节点（is_user=True）：作用域内按规范名查找/创建，
        消歧合并永不参与被吞并的保护语义由 admin 面沿用（不变）。"""
        node = repo.find_entity_by_name(USER_ENTITY_NAME)
        if node is None:
            node = repo.upsert_entity(MemoryEntity(
                entity_type=EntityType.PERSON.value,
                name=USER_ENTITY_NAME,
                is_user=True,
                importance=0.9,
                origin=MemoryOrigin.EXTRACTED.value,
            ))
        return node

    def _pair_facts(self, facts, entities_by_key, user_node, thread_id, turn_id, now) -> list[PairedFact]:
        paired = []
        for fact in facts:
            subject = user_node if fact.subject_key == "user" else entities_by_key.get(fact.subject_key)
            if subject is None:
                logger.warning("事实引用了无法解析的主体，已丢弃：subject_key=%r predicate=%r",
                               fact.subject_key, fact.predicate)
                continue
            object_entity = None
            literal = fact.object_text
            if fact.object_key:
                if fact.object_key == "user":
                    object_entity = user_node
                else:
                    object_entity = entities_by_key.get(fact.object_key)
                    if object_entity is None:
                        logger.warning("事实客体的实体键无法解析：object_key=%r predicate=%r",
                                       fact.object_key, fact.predicate)
            valid_from = resolve_time_hint(fact.time_hint, now) or now
            evidence = (
                [{"quote": fact.evidence, "source_turn_id": str(turn_id)}]
                if fact.evidence else []
            )
            paired.append(PairedFact(
                fact=fact, subject=subject, object_entity=object_entity,
                object_text=(literal if object_entity is None else None),
                evidence=evidence, valid_from=valid_from,
                source_thread_id=thread_id, source_turn_id=turn_id,
            ))
        return paired

    def _apply_decision(self, pair: PairedFact, decision: FactDecision, by_id, live_subject, now, repo: MemoryRepository) -> MemoryStatement | None:
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
                logger.warning("裁决 REPLACE 目标无效，降级为普通新增：replace_id=%s predicate=%r",
                               decision.replace_id, pair.fact.predicate)
            elif target.origin == MemoryOrigin.MANUAL.value:
                # MANUAL 保护（设计 §4）：人工事实不可被自动取代，冲突事实整体放弃
                logger.warning("裁决试图取代人工事实，该事实整体放弃：replace_id=%s predicate=%r",
                               decision.replace_id, pair.fact.predicate)
                return None
            else:
                repo.supersede_statement(target.id, pair.valid_from, now)
                by_id.pop(target.id, None)
        summary = _fact_summary(pair)
        return repo.insert_statement(MemoryStatement(
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


def _fact_summary(pair: PairedFact) -> str:
    obj = pair.object_entity.name if pair.object_entity else (pair.object_text or "")
    return fact_summary(pair.subject.name, pair.fact.predicate, obj)
