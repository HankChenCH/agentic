"""召回块：两级记忆召回——build_fast_context 快注 + 深度三件套。

组件自内聚惯例：存取经注入的抽象 MemoryRepository（当前绑定 SQLite 图谱
实现）；向量适配器自 services/domain/memory 注入（components→domain 合法
边）。快注纯 SQL 直读+评分截断，零 LLM/embedding，由 BaseAgent._input 每轮
组装进 system prompt（常驻摘要，非工具）；timeline/expand/state_at 深度
工具由 manifest.py 薄封装成 agent 可调用的闭包。会话内已投喂的记忆经
SessionInjectRegistry 登记，深度工具只返回增量。expand 的锚点解析复用
resolution 的三层消歧漏斗（与收尾写路同规：精确/强余弦直并 + 灰度带
LLM 语义裁决——model_factory 未注入时灰度带自动退化为不启用）。
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from wireup import injectable

from app.core.config import AppConfig
from app.adapters.llm import ModelFactory
from app.components.memory.internal import renderer, resolution
from app.components.memory.internal.extraction import (
    adjudicate_entity_merge,
    resolve_time_hint,
)
from app.components.memory.repositories import MemoryRepository
from app.components.memory.internal.scoring import ScoreWeights, ScorableItem, score_item
from app.domain.memory import KIND_EPISODE, MemoryVectorIndexPort

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
class MemoryRecallService:
    """召回块：build_fast_context system prompt 快注 + timeline/expand/state_at 深度三件套。

    零 LLM：快注纯 SQL 直读，三件套只做向量检索/时间切片 + 渲染。
    """

    memory_repo: MemoryRepository
    vector_index: MemoryVectorIndexPort
    app_config: AppConfig
    # 灰度带锚点裁决用的裸模型工厂（惯例同 consolidation；单测传 None，
    # 灰度带随之退化为不启用——快注路径零 LLM 的语义不变，模型按需惰性创建）
    model_factory: ModelFactory

    def __post_init__(self):
        self.inject_registry = SessionInjectRegistry()
        self._model = None

    # ==================== 快速注入 ====================

    def build_fast_context(self, query: str, user_id: UUID, thread_id: UUID) -> str:
        """快速记忆块：纯 SQL 直读评分截断，零 LLM/embedding。寒暄不注入。

        语义是**用户级常驻摘要**（装配进 system prompt），不是按问题检索——
        快路径无相关度输入，评分为「时近 × 重要」排序，与 query 无关（query
        仅作寒暄短路判别）。块计算不跳过会话内已投喂 id（块每轮重算且不落库，
        历史回放取原始 query，跳过会造成事实在会话内只可见一轮的滚动丢失），
        但仍登记 id 供深度三件套只返回增量。不触发 bump_access——稳定 top-10
        若每轮自我强化访问计数会挤占新记忆（深度工具保留强化，它们才是真正的
        提取练习）。

        user_id 决定记忆作用域（用户级隔离），thread_id 仅用于投喂登记。
        """
        if not self.app_config.memory.enabled or _is_smalltalk(query):
            return ""
        now = datetime.now(timezone.utc)
        weights = self._weights()
        half_life = self.app_config.memory.score.recency_half_life_days
        repo = self.memory_repo.for_user(user_id)

        scored = []
        for row in repo.list_active_statements():
            value = score_item(_item_of(row, None), weights, half_life, now)
            scored.append((value, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        top = scored[: self.app_config.memory.recall.fast_limit]
        if not top:
            return ""

        rows = [row for _score, row in top]
        ent_ids = {r.subject_id for r in rows} | {r.object_entity_id for r in rows if r.object_entity_id}
        ents = repo.get_entities(sorted(ent_ids))
        lines = [renderer.statement_topology_line(r, ents, now=now) for r in rows]

        self.inject_registry.mark(thread_id, [f"s:{r.id}" for r in rows])
        return renderer.brief_context(now, lines)

    # ==================== 深度三件套 ====================

    def timeline(self, query: str, user_id: UUID, thread_id: UUID) -> str:
        """深度·情节检索：episode 向量命中 → 分片渲染（仅增量）。"""
        now = datetime.now(timezone.utc)
        cfg = self.app_config.memory
        repo = self.memory_repo.for_user(user_id)
        index = self.vector_index.for_user(user_id)
        hits = index.search(query, kinds=(KIND_EPISODE,), top_k=cfg.recall.top_k, alpha=cfg.recall.alpha)
        fresh = [(h.ref_id, h.score) for h in hits if f"e:{h.ref_id}" not in self.inject_registry.seen(thread_id)]
        episodes = repo.episodes_by_ids([ref for ref, _score in fresh])
        by_id = {ep.id: ep for ep in episodes}
        eps_scored = [(by_id[ref], score) for ref, score in fresh if ref in by_id]
        if not eps_scored:
            return renderer.fragments_output(now, [])

        links = repo.links_for_episodes([ep.id for ep, _s in eps_scored])
        ent_ids = sorted({lk.entity_id for bundle in links.values() for lk in bundle})
        entities = repo.get_entities(ent_ids)
        fragments = renderer.assemble_fragments([], eps_scored, links, entities, now=now)[
            : cfg.render.max_fragments
        ]

        self.inject_registry.mark(thread_id, [f"e:{ep.id}" for ep, _s in eps_scored])
        repo.bump_access(
            [], [ep.id for ep, _s in eps_scored], sorted(entities.keys()), now
        )
        return renderer.fragments_output(now, fragments)

    def expand(self, entity_ref: str, user_id: UUID, thread_id: UUID) -> str:
        """深度·图扩散：命中实体的邻域陈述 + 关联事件（1-hop）。"""
        now = datetime.now(timezone.utc)
        cfg = self.app_config.memory
        repo = self.memory_repo.for_user(user_id)
        index = self.vector_index.for_user(user_id)
        anchor = resolution.find_entity_by_ref(
            entity_ref, repo, index,
            cfg.resolution.similarity_threshold,
            grey_zone_lower=cfg.resolution.grey_zone_lower,
            merge_adjudicator=self._anchor_adjudicator(repo),
        )
        if anchor is None:
            return f"（未找到与“{entity_ref}”相关的记忆对象）"
        seen = self.inject_registry.seen(thread_id)

        statements = [
            r for r in repo.find_active_statements([anchor.id])
            if f"s:{r.id}" not in seen
        ]
        episodes = [
            e for e in repo.episodes_for_entities([anchor.id], cfg.recall.expansion_limit)
            if f"e:{e.id}" not in seen
        ]
        if not statements and not episodes:
            return f"（关于“{anchor.name}”暂无未读关联记忆）"

        weights, half_life = self._weights(), cfg.score.recency_half_life_days
        s_scored = [(r, score_item(_item_of(r, None), weights, half_life, now)) for r in statements]
        e_scored = [(e, score_item(_item_of(e, None), weights, half_life, now)) for e in episodes]
        links = repo.links_for_episodes([e.id for e, _s in e_scored])

        ent_ids = set()
        for r in statements:
            ent_ids.update({r.subject_id, r.object_entity_id} - {None})
        for bundle in links.values():
            ent_ids.update(lk.entity_id for lk in bundle)
        entities = repo.get_entities(sorted(ent_ids))

        fragments = renderer.assemble_fragments(
            s_scored, e_scored, links, entities, now=now
        )[: cfg.render.max_fragments]

        self.inject_registry.mark(
            thread_id, [f"s:{r.id}" for r in statements] + [f"e:{e.id}" for e, _s in e_scored]
        )
        repo.bump_access(
            [r.id for r in statements], [e.id for e, _s in e_scored],
            sorted(set(entities) | {anchor.id}), now,
        )
        return renderer.fragments_output(now, fragments)

    def state_at(self, moment_hint: str, user_id: UUID, thread_id: UUID) -> str:
        """深度·时点回放：某时刻的世界状态切片（含 SUPERSEDED 历史值）。"""
        now = datetime.now(timezone.utc)
        parsed = resolve_time_hint(moment_hint, now)
        if parsed is None:
            return "时间无法解析，请用 YYYY-MM-DD（或 YYYY-MM-DD HH:MM）格式重试。"
        # 纯日期按当天末尾解释：覆盖“那天全天的状态”
        moment = parsed if (parsed.hour or parsed.minute or len(moment_hint.strip()) > 10) \
            else parsed.replace(hour=23, minute=59, second=59)
        limit = self.app_config.memory.recall.expansion_limit * 2
        repo = self.memory_repo.for_user(user_id)
        rows = sorted(
            repo.statements_valid_at(moment)[:limit],
            key=lambda r: (r.valid_from or moment, r.id),
        )
        if not rows:
            return f"（{parsed.strftime('%Y-%m-%d')} 时点暂无任何在效记录）"
        ent_ids = {r.subject_id for r in rows} | {r.object_entity_id for r in rows if r.object_entity_id}
        entities = repo.get_entities(sorted(ent_ids))
        lines = [renderer.statement_topology_line(r, entities, now=now) for r in rows]
        self.inject_registry.mark(thread_id, [f"s:{r.id}" for r in rows])
        repo.bump_access([r.id for r in rows], [], sorted(entities), now)
        head = f"## 时点回放 · {moment.strftime('%Y-%m-%d %H:%M')} 在效事实"
        numbered = [f"{i}. {line}" for i, line in enumerate(lines, start=1)]
        return "\n".join([head, *numbered])

    def _weights(self) -> ScoreWeights:
        score_cfg = self.app_config.memory.score
        return ScoreWeights(
            relevance=score_cfg.relevance_weight,
            recency=score_cfg.recency_weight,
            importance=score_cfg.importance_weight,
        )

    def _anchor_adjudicator(self, repo: MemoryRepository):
        """expand 锚点的灰度带语义裁决回调；灰度带关闭或无模型工厂时返回 None。"""
        if self.model_factory is None or self.app_config.memory.resolution.grey_zone_lower <= 0:
            return None

        def adjudicate(ref: str, expected_type: str | None, candidates: list) -> int | None:
            grouped: dict[int, list[str]] = {}
            for row in repo.find_active_statements([c.id for c in candidates]):
                grouped.setdefault(row.subject_id, []).append(row.summary)
            return adjudicate_entity_merge(
                self._extraction_model(), ref, expected_type, candidates, grouped,
            )

        return adjudicate

    def _extraction_model(self):
        if self._model is None:
            self._model = self.model_factory.create(self.app_config.memory.extraction_provider)
        return self._model


def _is_smalltalk(query: str) -> bool:
    """最省成本的小话判别：超短且是问候/致谢类词面才跳过注入（宁可多注入）。"""
    text = query.strip()
    if len(text) > 12:
        return False
    tokens = ("你好", "您好", "嗨", "哈喽", "hello", "hi", "在吗", "早上好", "下午好",
              "晚上好", "谢谢", "多谢", "再见", "拜拜", "晚安")
    lowered = text.lower()
    return any(token in lowered for token in tokens)


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
