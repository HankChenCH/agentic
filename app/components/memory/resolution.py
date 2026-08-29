"""实体消歧单源：两段式「向量候选发现 + 真余弦判定」的唯一实现点。

两段式语义（docs/memory-v2-design.md §8 事故教训）：``MemoryVectorIndex.search``
的 hybrid 融合分只是相对排序，仅用于圈定候选、绝不与绝对阈值比较；
命中与否由 ``text_cosine`` 对候选档案文本（``entity_content``，与向量
写入同内容）现算的真实余弦 ≥ similarity_threshold 决定。拆分禁令
（attributes.merge_blocklist，人工拆分时互写）优先于余弦排序——阈值
附近的余弦抖动不应推翻人工拆分意图。类型护栏只在「存在期望类型」时
生效（写路的抽取候选自带 entity_type；读路锚点解析没有期望类型，
结构性跳过）。本模块无状态：仓储与向量索引一律以参数传入。
"""

import logging

from app.components.memory.repositories import MemoryRepository
from app.models.domain.memory import EntityType, MemoryEntity
from app.services.domain.memory import KIND_ENTITY, MemoryVectorIndex

logger = logging.getLogger(__name__)

# 向量候选圈定数量：融合分只排序，取最近几个进余弦复判
_VECTOR_CANDIDATES = 3


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
) -> MemoryEntity | None:
    """两段式向量消歧：search 只圈 top-3 候选，合并判定用 text_cosine 真余弦。

    达标候选多于一个时拆分禁令优先于余弦排序；``expected_type`` 非空时
    类型冲突一律拒绝（OTHER 通配）。低分/冲突/失败一律返回 None，
    由调用方按新建（写路）或未命中（读路）处理。
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
    passing = [
        (row, score) for row, score in zip(rows.values(), cosines) if score >= threshold
    ]
    if not passing:
        logger.info(
            "实体 %r 最近候选余弦均低于阈值 %.2f，按未命中处理", ref, threshold,
        )
        return None
    best_row, best_score = max(passing, key=lambda pair: pair[1])
    for row, score in passing:
        if row.id != best_row.id and block_paired(best_row, row):
            logger.info(
                "实体 %r 命中人工拆分禁令对 #%s「%s」↔ #%s「%s」，禁令优先",
                ref, best_row.id, best_row.name, row.id, row.name,
            )
            best_row, best_score = row, score
            break
    if (
        expected_type is not None
        and expected_type != best_row.entity_type
        and EntityType.OTHER.value not in (expected_type, best_row.entity_type)
    ):
        logger.warning(
            "实体 %r(%s) 与候选 #%s「%s」(%s) 余弦 %.2f 达标但类型冲突，拒绝合并",
            ref, expected_type,
            best_row.id, best_row.name, best_row.entity_type, best_score,
        )
        return None
    return best_row


def find_entity_by_ref(
    ref: str,
    memory_repo: MemoryRepository,
    vector_index: MemoryVectorIndex,
    threshold: float,
) -> MemoryEntity | None:
    """读路一站式锚点解析：精确名/别名 → 向量兜底（无期望类型）。"""
    return exact_entity_match(ref, memory_repo) or vector_entity_match(
        ref, None, memory_repo, vector_index, threshold
    )
