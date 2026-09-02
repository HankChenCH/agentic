"""实体消歧单源：三层漏斗「精确/强余弦直并 + 灰度带语义裁决」的唯一实现点。

漏斗分层（docs/memory-v2-design.md §8 事故教训 + §12 指代归一）：
- 精确名/别名命中 → 复用（``exact_entity_match``，确定性）；
- ``MemoryVectorIndex.search`` 只做候选发现（hybrid 融合分是相对排序，
  绝不与绝对阈值比较），合并判定用 ``text_cosine`` 客户端真余弦：
  - 余弦 ≥ similarity_threshold → 强表面信号，直接复用；
  - 余弦落 [grey_zone_lower, 阈值) 灰度带 → 交给 ``merge_adjudicator``
    （LLM 语义裁决：同一现实事物才算同一，简称/代称归一在此收口）；
  - 带外或裁决不可用 → 新建（保守默认，8-28「学校并入公司」取向）。
拆分禁令（attributes.merge_blocklist，人工拆分时互写）优先于一切自动
归并——阈值抖动与 LLM 判断都不应推翻人工拆分意图。类型护栏只在「存在
期望类型」时生效（写路的抽取候选自带 entity_type；读路锚点解析没有期望
类型，结构性跳过）。本模块无状态：仓储与向量索引一律以参数传入。
"""

import logging
from typing import Callable

from app.components.memory.repositories import MemoryRepository
from app.models.domain.memory import EntityType, MemoryEntity
from app.services.domain.memory import KIND_ENTITY, MemoryVectorIndex

logger = logging.getLogger(__name__)

# 向量候选圈定数量：融合分只排序，取最近几个进余弦复判
_VECTOR_CANDIDATES = 3

# 灰度带语义裁决回调：（指称, 期望类型, 灰度带候选行）→ 命中实体 id | None
MergeAdjudicator = Callable[[str, str | None, list[MemoryEntity]], int | None]


def entity_content(row: MemoryEntity) -> str:
    """实体档案文本：向量写入与余弦判定共用同一内容（name+aliases）。"""
    alias_bit = f"（{'、'.join(row.aliases)}）" if row.aliases else ""
    return f"{row.name}{alias_bit}"


def merge_blocklist(row: MemoryEntity) -> set[str]:
    """实体档案上的拆分禁令名单（人工拆分时互写，见 repo.split_entity）。"""
    return set((row.attributes or {}).get("merge_blocklist", []))


def block_paired(a: MemoryEntity, b: MemoryEntity) -> bool:
    """两实体是否被人工拆分禁令配对（任一方名单命中对方名字/别名）。"""
    names_b = {b.name, *(b.aliases or [])}
    names_a = {a.name, *(a.aliases or [])}
    return bool(merge_blocklist(a) & names_b) or bool(merge_blocklist(b) & names_a)


def exact_entity_match(ref: str, memory_repo: MemoryRepository) -> MemoryEntity | None:
    """精确匹配：规范名 → 别名（入参先去空白）。"""
    text = ref.strip()
    if not text:
        return None
    return memory_repo.find_entity_by_name(text) or memory_repo.find_entity_by_alias(text)


def vector_entity_match(
    ref: str,
    expected_type: str | None,
    memory_repo: MemoryRepository,
    vector_index: MemoryVectorIndex,
    threshold: float,
    grey_zone_lower: float = 0.0,
    merge_adjudicator: MergeAdjudicator | None = None,
) -> MemoryEntity | None:
    """三层漏斗向量消歧：search 圈候选，真余弦分档判定。

    - 余弦 ≥ threshold：强表面信号，直接复用；
    - 余弦落 [grey_zone_lower, threshold) 灰度带且有裁决器：LLM 语义判定
      （同一现实事物才算同一），裁决结果同样过拆分禁令与类型护栏；
    - 带外 / 裁决不可用 / 裁决判非同一：返回 None，由调用方按新建（写路）
      或未命中（读路）处理。低分/冲突/失败一律记日志，杜绝静默错合并。
    """
    similar = vector_index.search(
        ref, kinds=(KIND_ENTITY,), top_k=_VECTOR_CANDIDATES, alpha=1.0
    )
    rows = memory_repo.get_entities([h.ref_id for h in similar])
    if not rows:
        return None
    cosines = vector_index.text_cosine(
        ref, [entity_content(row) for row in rows.values()]
    )
    scored = sorted(zip(rows.values(), cosines), key=lambda pair: pair[1], reverse=True)

    passing = [(row, score) for row, score in scored if score >= threshold]
    if not passing:
        return _grey_zone_resolve(
            ref, expected_type, scored, grey_zone_lower, merge_adjudicator,
        )
    best_row, best_score = _switch_on_blocklist(
        max(passing, key=lambda pair: pair[1]), passing, ref,
    )
    return _guard_type(best_row, best_score, expected_type, ref)


def _grey_zone_resolve(
    ref: str,
    expected_type: str | None,
    scored: list[tuple],
    grey_zone_lower: float,
    merge_adjudicator: MergeAdjudicator | None,
) -> MemoryEntity | None:
    """灰度带语义裁决层：余弦说不清的（简称/代称/变体）交给裁决器。

    裁决器缺失、关闭（下限 ≤0）、判为不匹配或给了候选外 id → None（新建）。
    """
    if grey_zone_lower <= 0 or merge_adjudicator is None:
        logger.info(
            "实体 %r 最近候选余弦均低于阈值，按未命中处理（灰度带未启用）", ref,
        )
        return None
    band = [(row, score) for row, score in scored if score >= grey_zone_lower]
    if not band:
        logger.info("实体 %r 余弦低于灰度带下限 %.2f，按新建处理", ref, grey_zone_lower)
        return None
    score_by_id = {row.id: score for row, score in band}
    picked_id = merge_adjudicator(ref, expected_type, [row for row, _score in band])
    picked = next((row for row, _score in band if row.id == picked_id), None)
    if picked is None:
        return None
    picked, picked_score = _switch_on_blocklist((picked, score_by_id[picked.id]), band, ref)
    return _guard_type(picked, picked_score, expected_type, ref, adjudicated=True)


def _switch_on_blocklist(picked: tuple, pool: list[tuple], ref: str) -> tuple:
    """拆分禁令优先于余弦排序与语义裁决：命中禁令对时改选对方。"""
    best_row, best_score = picked
    for row, score in pool:
        if row.id != best_row.id and block_paired(best_row, row):
            logger.info(
                "实体 %r 命中人工拆分禁令对 #%s「%s」↔ #%s「%s」，禁令优先",
                ref, best_row.id, best_row.name, row.id, row.name,
            )
            return row, score
    return best_row, best_score


def _guard_type(
    row: MemoryEntity, score: float, expected_type: str | None, ref: str,
    *, adjudicated: bool = False,
) -> MemoryEntity | None:
    """类型护栏：期望类型与候选冲突（且双方均非 OTHER 通配）一律拒绝。"""
    if (
        expected_type is not None
        and expected_type != row.entity_type
        and EntityType.OTHER.value not in (expected_type, row.entity_type)
    ):
        logger.warning(
            "实体 %s(%s) 与候选 #%s「%s」(%s) 余弦 %.2f 达标%s但类型冲突，拒绝合并",
            ref, expected_type, row.id, row.name, row.entity_type, score,
            "（语义裁决通过）" if adjudicated else "",
        )
        return None
    return row


def find_entity_by_ref(
    ref: str,
    memory_repo: MemoryRepository,
    vector_index: MemoryVectorIndex,
    threshold: float,
    grey_zone_lower: float = 0.0,
    merge_adjudicator: MergeAdjudicator | None = None,
) -> MemoryEntity | None:
    """读路一站式锚点解析：精确名/别名 → 向量兜底（无期望类型）。"""
    return exact_entity_match(ref, memory_repo) or vector_entity_match(
        ref, None, memory_repo, vector_index, threshold,
        grey_zone_lower=grey_zone_lower, merge_adjudicator=merge_adjudicator,
    )
