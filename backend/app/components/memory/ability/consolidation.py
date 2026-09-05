"""收尾块：轮次结束后的记忆巩固管线（remember 四步）。

❶结构化抽取（build_transcript + extract_structure，LLM×1；输入附用户身份卡
与既有实体名册——指代归一 Tier1：自报姓名归保留键、指代既有对象回填
ref_id）→ ❷实体消歧（身份归并 Tier3：候选命中用户称呼并入「用户」节点；
resolution 三层漏斗 Tier2：精确/别名 → 强余弦直并 → 灰度带 LLM 语义裁决，
带外新建）→ ❸陈述裁决（adjudicate_facts，LLM×1；失败 programmatic_decisions
降级）→ ❹按 ADD/REPLACE/SKIP 落库 + 向量同步（SQL 为事实源，向量
best-effort）。由 TurnFinalizer 第三步在后台 daemon 线程调用；任何一环
失败都不抛出（调用方另有兜底），只返回已落库条目。

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
from app.adapters.llm import ModelFactory
from app.adapters.llm.usage_tracking import UsageTrackingChatModel
from app.components.memory.repositories import MemoryRepository
from app.components.memory.internal.extraction import (
    ExtractionResult,
    ExtractedEntity,
    FactDecision,
    PairedFact,
    adjudicate_entity_merge,
    adjudicate_facts,
    build_transcript,
    extract_structure,
    programmatic_decisions,
    resolve_time_hint,
    statement_digest,
)
from app.components.memory.internal import renderer, resolution
from app.components.memory.internal.vocab import fact_summary
from app.domain.memory import (
    KIND_ENTITY,
    KIND_EPISODE,
    KIND_STATEMENT,
    MemoryVectorIndexPort,
    VectorEntry,
)
from app.domain.usage import UsageService
from app.models.domain.usage import UsageScene

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

# 用户自报身份的谓词：客体字面量化并回填用户别名（身份归一 Tier3）
SELF_NAME_PREDICATES = frozenset({"姓名", "称呼"})

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（惯例同 api/exception_handlers）
logger = logging.getLogger(__name__)


@injectable
@dataclass
class MemoryConsolidationService:
    """收尾块：remember 巩固管线——抽取→消歧→裁决→落库+向量同步。"""

    memory_repo: MemoryRepository
    vector_index: MemoryVectorIndexPort
    model_factory: ModelFactory
    app_config: AppConfig
    # 用量落库门面（components→domain 合法边）：巩固管线的 LLM 调用此前完全
    # 无计量，模型经 UsageTrackingChatModel 包装后由 sink 以 memory 场景落库
    usage: UsageService

    # ==================== 写路径：巩固管线 ====================

    def remember(
        self, query: str, turn_messages: List[AgenticConversationMessage],
        user_id: UUID, thread_id: UUID, turn_id: UUID,
    ) -> list:
        """轮次结束后巩固记忆：❶结构化抽取→❷实体消歧→❸陈述裁决→❹落库。

        记忆作用域为 user_id（用户级隔离）：实体消歧、用户节点、向量读写
        全部限定在该用户的图谱与 collection 内。任何一环失败都不抛出
        （调用方 TurnFinalizer 另有兜底），只返回已落库条目。

        指代归一前置（抽取上游预防）：抽取前装载用户身份卡与既有实体名册
        （向量按 transcript 提名），让模型把「我是xx/我们公司」这类指称
        归到保留键与既有实体，而不是另起新名。
        """
        if not self.app_config.memory.enabled:
            return []

        transcript = build_transcript(query, turn_messages)
        if not transcript:
            return []

        now = datetime.now(timezone.utc)
        # 模型经用量追踪包装：抽取/消歧裁决/陈述裁决的每次调用（含
        # with_structured_output 链内）都以 memory 场景落库，调用点零改动
        model = UsageTrackingChatModel(
            inner=self.model_factory.create(self.app_config.memory.extraction_provider),
            sink=self.usage.usage_sink(
                user_id=user_id, thread_id=thread_id, turn_id=turn_id,
                scene=UsageScene.MEMORY,
            ),
        )
        repo = self.memory_repo.for_user(user_id)
        index = self.vector_index.for_user(user_id)

        # ---- Tier1 前置：用户节点 + 身份卡 + 既有实体名册 ----
        user_node = self._ensure_user_entity(repo)
        user_aliases_before = set(user_node.aliases or [])
        identity_names = self._user_identity_names(repo, user_node)
        # 自愈：身份卡里的称呼缺别名登记的补齐（含存量数据），读路径
        # expand 按别名才能锚定用户节点；向量随 touched 检测同步重写
        missing_aliases = identity_names - {user_node.name} - set(user_node.aliases or [])
        if missing_aliases:
            user_node.aliases = sorted(set(user_node.aliases or []) | missing_aliases)
            user_node = repo.upsert_entity(user_node)
        roster = self._load_roster(repo, index, transcript)

        extracted = extract_structure(
            model=model, transcript=transcript, now=now,
            identity_names=sorted(identity_names), roster=roster,
        )
        if extracted.is_empty():
            return []

        vector_entries: list[VectorEntry] = []
        created: list = []

        # ---- ❷ 实体消歧（身份归并/名册复用/三层漏斗）----
        self_literals = self._self_name_literals(extracted)
        if self_literals:
            fresh_names = set(self_literals.values()) - set(user_node.aliases or [])
            if fresh_names:
                user_node.aliases = sorted(set(user_node.aliases or []) | fresh_names)
                user_node = repo.upsert_entity(user_node)
        entities_by_key = self._resolve_entities(
            extracted.entities, repo, index,
            user_node=user_node, identity_names=identity_names,
            skip_keys=frozenset(self_literals), model=model,
        )
        touched_entities = {row.id: row for row in entities_by_key.values()}
        if set(user_node.aliases or []) != user_aliases_before:
            # 身份归并回填了用户别名：档案文本变了，向量随写
            touched_entities[user_node.id] = user_node
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

        # ---- 陈述配对（key → 实体行 / 字面量；用户自报姓名字面量化）----
        paired = self._pair_facts(extracted.facts, entities_by_key, user_node,
                                  thread_id, turn_id, now, self_literals)
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
        repo: MemoryRepository, index: MemoryVectorIndexPort,
        *, user_node: MemoryEntity | None = None,
        identity_names: frozenset[str] = frozenset(),
        skip_keys: frozenset[str] = frozenset(),
        model=None,
    ) -> dict[str, MemoryEntity]:
        """逐候选消歧：身份归并 → 名册 ref_id 复用 → 精确/别名 → 三层向量漏斗 → 新建。

        - 身份归并（Tier3）：候选名/别名命中用户已知称呼 → 直接归到「用户」
          节点并回填别名（用户是保留指称，不为它建独立实体）；
        - 名册复用（Tier1）：抽取回填的 ref_id 直取既有行（跨过表面形式），
          失效（他人行/已删）则落回正常消歧；
        - 向量漏斗（Tier2）：余弦 ≥ 阈值直并，灰度带交 LLM 语义裁决
          （同一现实事物才算同一），带外/不确定一律新建——「学校并入公司」
          事故见 docs/memory-v2-design.md §8。
        仓储/索引均为调用方传入的用户作用域视图——实体身份天然用户内唯一。
        """
        resolved: dict[str, MemoryEntity] = {}
        threshold = self.app_config.memory.resolution.similarity_threshold
        grey_lower = self.app_config.memory.resolution.grey_zone_lower
        adjudicator = self._merge_adjudicator(repo, model) if (model is not None and grey_lower > 0) else None
        for candidate in candidates:
            if renderer.is_internal_ref(candidate.name):
                logger.warning("跳过内部溯源引用形态的候选实体：%r", candidate.name)
                continue
            if candidate.key in skip_keys:
                continue  # 用户自报姓名：字面量化，不建实体（_pair_facts 消费）
            # Tier3：命中用户本人称呼 → 归并到用户节点
            if user_node is not None and identity_names:
                hits = {candidate.name, *candidate.aliases} & identity_names
                if hits:
                    user_node.aliases = sorted(set(user_node.aliases or []) | hits)
                    user_node.importance = max(user_node.importance, candidate.importance)
                    user_node = repo.upsert_entity(user_node)
                    resolved[candidate.key] = user_node
                    logger.info("候选 %r 命中用户本人称呼，归并到用户节点 #%s",
                                candidate.name, user_node.id)
                    continue
            # Tier1：名册 ref_id 直取既有实体
            hit = None
            if candidate.ref_id is not None:
                hit = repo.get_entity(candidate.ref_id)
                if hit is None:
                    logger.warning("名册 ref_id=%s 实体不存在（他人行或已删），%r 落回正常消歧",
                                   candidate.ref_id, candidate.name)
            if hit is None:
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
                    grey_zone_lower=grey_lower, merge_adjudicator=adjudicator,
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

    def _load_roster(self, repo: MemoryRepository, index: MemoryVectorIndexPort, transcript: str) -> list:
        """既有实体名册（Tier1）：向量按本轮 transcript 提名，供抽取 prompt 归指。

        提名失败/关闭返回空（prompt 省名册节，抽取退回无状态行为）；用户
        节点不入册（身份走身份卡与保留键，名册出现「用户」只会诱导建环）。
        """
        limit = self.app_config.memory.roster_limit
        if limit <= 0:
            return []
        hits = index.search(
            transcript, kinds=(KIND_ENTITY,), top_k=limit,
            alpha=self.app_config.memory.recall.alpha,
        )
        rows = repo.get_entities([h.ref_id for h in hits])
        return [row for row in rows.values() if not row.is_user]

    def _user_identity_names(self, repo: MemoryRepository, user_node: MemoryEntity) -> frozenset[str]:
        """用户本人已知称呼全集：节点名/别名 + 账号 username/nickname + 姓名/称呼陈述。

        账号字段键与 user_node.py 的同步口径一致（字面量，避免环导入）。
        """
        names = {user_node.name} | {a for a in (user_node.aliases or []) if a}
        attrs = user_node.attributes or {}
        for key in ("username", "nickname"):
            value = attrs.get(key)
            if value:
                names.add(str(value))
        for row in repo.find_active_statements([user_node.id]):
            if row.predicate not in SELF_NAME_PREDICATES:
                continue
            if row.object_text:
                names.add(row.object_text)
            elif row.object_entity_id is not None:
                target = repo.get_entity(row.object_entity_id)
                if target is not None:
                    names.add(target.name)
        return frozenset(
            n for n in names if n and not renderer.is_internal_ref(n)
        )

    def _self_name_literals(self, extracted: ExtractionResult) -> dict[str, str]:
        """用户自报姓名/称呼的候选 key → 字面量名（不建实体，陈述用 object_text）。"""
        literals: dict[str, str] = {}
        names_by_key = {e.key: e.name for e in extracted.entities}
        for fact in extracted.facts:
            if fact.subject_key != "user" or fact.predicate not in SELF_NAME_PREDICATES:
                continue
            if not fact.object_key or fact.object_key == "user":
                continue
            name = _clip_text(names_by_key.get(fact.object_key, fact.object_key), 60)
            if name and not renderer.is_internal_ref(name):
                literals.setdefault(fact.object_key, name)
        return literals

    def _merge_adjudicator(self, repo: MemoryRepository, model) -> resolution.MergeAdjudicator | None:
        """灰度带语义裁决回调（Tier2）：带候选既有事实档案调 LLM 判同一性。"""
        if model is None:
            return None

        def adjudicate(ref: str, expected_type: str | None, candidates: list) -> int | None:
            grouped: dict[int, list[str]] = {}
            for row in repo.find_active_statements([c.id for c in candidates]):
                grouped.setdefault(row.subject_id, []).append(row.summary)
            return adjudicate_entity_merge(model, ref, expected_type, candidates, grouped)

        return adjudicate

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

    def _pair_facts(self, facts, entities_by_key, user_node, thread_id, turn_id, now,
                    self_literals: dict[str, str] | None = None) -> list[PairedFact]:
        self_literals = self_literals or {}
        paired = []
        for fact in facts:
            subject = user_node if fact.subject_key == "user" else entities_by_key.get(fact.subject_key)
            if subject is None and fact.subject_key in self_literals:
                subject = user_node  # 自报姓名的键已字面量化：指称仍归用户本人
            if subject is None:
                logger.warning("事实引用了无法解析的主体，已丢弃：subject_key=%r predicate=%r",
                               fact.subject_key, fact.predicate)
                continue
            object_entity = None
            literal = fact.object_text
            if fact.object_key:
                if fact.object_key == "user":
                    object_entity = user_node
                elif fact.object_key in self_literals:
                    # 用户自报姓名/称呼：客体退化为字面量，不为它建实体挂边
                    literal = self_literals[fact.object_key]
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


def _clip_text(value: str | None, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None
