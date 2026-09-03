"""知识库检索能力（knowledge 唯一能力模块）：可见性圈定 → 状态/模型守卫 →
双通道召回（混合检索 + 邻域扩展）→ RRF 排名融合 → 命中组装。

消费方是 manifest 的 agent 工具：LLM 先经 knowledge_list 了解可用知识库，
再以 kb_ids 自选库检索（工具层运行时硬闸：缺省或全非法不触发检索，返回
引导话术——服务签名上的 kb_ids=None 全库口径仅供非工具消费方使用）。可用
口径是归属可见性：私有库仅属主可见，公开库所有人可见（与
knowledge_base.user_id/is_public 的管理侧读路径同规则）。状态收敛规则：
知识库须为 enabled、文档须为 enabled 才可被召回——管理侧的启停开关即检
索开关；分段级禁用（行 status=disabled）同样不参与召回（种子与邻段在
候选构建期按行状态剔除）。

检索管线（``search``）：通道 A = Weaviate hybrid 混合检索（语义+BM25，
每库固定池深，文档 enabled 后滤）；通道 B = 命中片段的邻域扩展（SQL 仓储
按 position 取前后段）。两路按排名做 RRF（Reciprocal Rank Fusion）融合
——语义相关分与邻域邻近性不可比，排名融合才是正确的合并口径，顺带消除
跨库分数硬比；同片段双通道在榜（直接命中且是其他命中的邻段）即邻域互证
加分。入参 ``top_k`` 只控制最终返回条数（融合排序后截断），与内部检索深
度解耦。
"""

from dataclasses import dataclass
from typing import List, Tuple
from uuid import UUID

from wireup import injectable

from app.services.domain.knowledge.vector_index import DEFAULT_TOP_K, KnowledgeVectorIndex, VectorHit
from app.core.config import AppConfig
from app.core.logging import LoggerFactory
from app.models.domain.knowledge import DocumentSegment, KnowledgeBase, KnowledgeStatus
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository

# 混合检索默认权重：1.0 纯向量语义，<1 开启 Weaviate hybrid（BM25+向量，gse 中文分词）
DEFAULT_HYBRID_ALPHA = 0.5

# 内部候选池：向量层每库固定取回条数——与入参 top_k 解耦（邻域扩展会把候选
# 集放大约 3 倍，检索深度若仍跟随 top_k 会一起膨胀）
_POOL_PER_KB = 8

# 邻域扩展窗口：每个命中片段向前/向后各取几段
_NEIGHBOR_WINDOW = 1

# RRF（Reciprocal Rank Fusion）常数：行业默认 60，平缓头部名次间的贡献差距
_RRF_K = 60

# 最终返回条数上限：防 LLM 传大值把内部候选池撑穿
_MAX_RETURN_TOP_K = 20


@dataclass
class RetrievalHit:
    """面向引用展示的检索命中：文档名 + 溯源 meta（页码/标题路径/资产 key）。"""

    content: str
    score: float
    kb_id: str
    doc_id: str
    doc_name: str
    position: int
    meta: dict


@dataclass
class _Candidate:
    """RRF 融合候选：身份键 (doc_id, position)，携带两通道排名与展示数据。

    ``score`` 是展示口径（KnowledgeSource.score，前端「相关度」）：通道 A
    命中 = 混合检索相关分；纯邻域命中 = 种子命中分（相关性沿证据链继承）。
    融合排序只看排名（rank_a/rank_b），与该分数无关——排序与展示分可能不
    完全单调，这是已接受的取舍。
    """

    key: Tuple[str, int]
    score: float
    content: str
    meta: dict
    kb_id: str
    rank_a: int | None = None  # 通道 A（混合检索）排名，1 起；不在该通道为 None
    rank_b: int | None = None  # 通道 B（邻域扩展）排名，1 起；不在该通道为 None


def _rrf_fuse(candidates: List[_Candidate]) -> List[_Candidate]:
    """RRF 排名融合：贡献 = Σ 1/(K + rank)，只由各通道排名决定。

    同片段出现在两通道即叠加两份贡献（邻域互证）。并列时按通道 A 排名、
    再按身份键排定，保证确定性。
    """

    def fused(candidate: _Candidate) -> Tuple[float, float, Tuple[str, int]]:
        total = 0.0
        if candidate.rank_a is not None:
            total += 1.0 / (_RRF_K + candidate.rank_a)
        if candidate.rank_b is not None:
            total += 1.0 / (_RRF_K + candidate.rank_b)
        return (
            -total,
            float(candidate.rank_a) if candidate.rank_a is not None else float("inf"),
            candidate.key,
        )

    return sorted(candidates, key=fused)


@injectable
@dataclass
class KnowledgeRetrievalService:
    kb_repo: KnowledgeBaseRepository
    document_repo: KnowledgeDocumentRepository
    vector_index: KnowledgeVectorIndex
    app_config: AppConfig
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def list_visible_knowledge(self, user_id: UUID | None) -> List[KnowledgeBase]:
        """当前用户可见（私有=属主本人 + 公开）且未在删除中的知识库，供 knowledge_list 展示。"""
        return self.kb_repo.list_visible_kbs(user_id)

    def search_for_user(
        self,
        user_id: UUID | None,
        query: str,
        kb_ids: List[UUID] | None = None,
        top_k: int = DEFAULT_TOP_K,
        alpha: float = DEFAULT_HYBRID_ALPHA,
    ) -> Tuple[List[RetrievalHit], List[str]]:
        """用户域检索：返回（命中列表, 跳过说明——工具会把说明附在结果尾部）。

        编排：可见性圈定 → LLM 自选库子集过滤 → 库级守卫（enabled + 嵌入
        模型一致）→ 多库扇出 → 文档级 enabled 后滤 → 双通道融合（混合检
        索 + 邻域扩展，RRF）→ 截 ``top_k`` 返回。``alpha`` 透传向量层
        （1.0 纯向量，<1 混合 BM25+向量）。
        """
        kbs = self.list_visible_knowledge(user_id)
        notes: List[str] = []

        selected = kbs
        if kb_ids:
            wanted = set(kb_ids)
            selected = [kb for kb in kbs if kb.id in wanted]
            unknown = wanted - {kb.id for kb in kbs}
            if unknown:
                notes.append(
                    "不可见或不存在的知识库已忽略: " + ", ".join(sorted(str(u) for u in unknown))
                )

        usable: List[KnowledgeBase] = []
        for kb in selected:
            if kb.status != KnowledgeStatus.ENABLED:
                notes.append(f"知识库「{kb.name}」未启用（{kb.status.value}），已跳过")
                continue
            if kb.embedding_model != self.app_config.vector_db.embedding:
                # 跨向量空间检索只会得到噪声：建库固化的模型与全局配置不一致必须跳过
                notes.append(
                    f"知识库「{kb.name}」嵌入模型与当前配置不一致（{kb.embedding_model}），已跳过"
                )
                self.logger.warning(
                    "kb %s embedding model mismatch: kb=%s config=%s",
                    kb.id,
                    kb.embedding_model,
                    self.app_config.vector_db.embedding,
                )
                continue
            usable.append(kb)

        if not usable:
            return [], notes
        hits = self.search([kb.id for kb in usable if kb.id], query, top_k=top_k, alpha=alpha)
        return hits, notes

    def search(
        self, kb_ids: List[UUID], query: str, top_k: int = DEFAULT_TOP_K, alpha: float = DEFAULT_HYBRID_ALPHA
    ) -> List[RetrievalHit]:
        """直连多库检索（管理/调试与 RAG 预检索节点复用）。

        双通道召回 + RRF 融合：通道 A = 混合检索（每库 ``_POOL_PER_KB``
        池深 + 文档 enabled 后滤）；通道 B = 命中片段邻域扩展（SQL 按
        position 取前后段）。``top_k`` 仅在融合排序后截断生效，与内部检
        索深度无关；``alpha`` 透传向量层（1.0 纯向量，<1 混合）。
        """
        top_k = min(max(top_k, 1), _MAX_RETURN_TOP_K)
        # hit.doc_id 是向量 metadata 里的字符串 id，统一为 str 比较
        allowed_doc_ids = {str(doc_id) for doc_id in self.document_repo.list_enabled_doc_ids(kb_ids)}
        pool = max(_POOL_PER_KB, top_k)
        vector_hits: List[VectorHit] = self.vector_index.search_many(kb_ids, query, top_k=pool, alpha=alpha)
        seeds = [hit for hit in vector_hits if hit.doc_id in allowed_doc_ids]
        if not seeds:
            return []
        fused = _rrf_fuse(self._candidates(seeds))
        docs = {
            doc.id: doc
            for doc in self.document_repo.list_documents_by_ids(
                list({UUID(key) for key in {candidate.key[0] for candidate in fused}})
            )
        }
        return [self._to_hit(candidate, docs) for candidate in fused[:top_k]]

    def _candidates(self, seeds: List[VectorHit]) -> List[_Candidate]:
        """构建双通道候选：通道 A = 种子命中（分数序即排名）；通道 B = 邻域扩展。

        邻段按种子顺序发射（排名随种子检索质量衰减），同一邻段多种子命中
        只记首见排名；已在通道 A 的邻段不重复建条目，仅补记 rank_b——融合
        时两通道贡献叠加，即邻域互证。种子只来自 enabled 文档，邻段同文档，
        状态守卫天然满足。

        禁用分段（行 status=disabled）不参与召回：向量库不带状态字段，行库
        是唯一事实源，种子与邻段 alike 在此按行状态剔除——被剔除的邻段不占
        rank_b 名次，被剔除的种子不影响其余种子的通道 A 排名。
        """
        # 先取窗口行（覆盖全部种子与邻段，含行级状态）：禁用分段过滤的事实源
        windows: dict[str, Tuple[int, int]] = {}
        for hit in seeds:
            low, high = windows.get(hit.doc_id, (hit.position, hit.position))
            windows[hit.doc_id] = (min(low, hit.position), max(high, hit.position))
        rows: dict[Tuple[str, int], DocumentSegment] = {}
        for doc_id, (low, high) in windows.items():
            # 窗口越界侧查不到行即为文档边界（首/末段），无需 seg_total
            for seg in self.document_repo.list_segments_by_doc(
                UUID(doc_id), start=max(0, low - _NEIGHBOR_WINDOW), end=high + _NEIGHBOR_WINDOW
            ):
                rows[(str(seg.doc_id), seg.position)] = seg

        candidates: List[_Candidate] = []
        by_key: dict = {}
        for rank, hit in enumerate(seeds, start=1):
            row = rows.get((hit.doc_id, hit.position))
            if row is not None and row.status == KnowledgeStatus.DISABLED:
                continue
            candidate = _Candidate(
                key=(hit.doc_id, hit.position),
                score=hit.score,
                content=hit.content,
                meta=hit.meta,
                kb_id=hit.kb_id,
                rank_a=rank,
            )
            candidates.append(candidate)
            by_key[candidate.key] = candidate

        seen: set = set()
        for seed in seeds:
            for offset in (-_NEIGHBOR_WINDOW, _NEIGHBOR_WINDOW):
                key = (seed.doc_id, seed.position + offset)
                if key not in rows or key in seen:
                    continue
                seg: DocumentSegment = rows[key]
                if seg.status == KnowledgeStatus.DISABLED:
                    continue
                rank_b = len(seen) + 1
                seen.add(key)
                if key in by_key:
                    by_key[key].rank_b = rank_b
                    continue
                candidates.append(_Candidate(
                    key=key,
                    score=seed.score,  # 纯邻域命中继承种子的混合检索分（展示口径）
                    content=seg.content,
                    meta=dict(seg.meta or {}),
                    kb_id=str(seg.kb_id),
                    rank_b=rank_b,
                ))
                by_key[key] = candidates[-1]
        return candidates

    @staticmethod
    def _to_hit(candidate: _Candidate, docs: dict) -> RetrievalHit:
        doc_id = UUID(candidate.key[0])
        return RetrievalHit(
            content=candidate.content,
            score=candidate.score,
            kb_id=candidate.kb_id,
            doc_id=candidate.key[0],
            doc_name=docs[doc_id].name if doc_id in docs else "",
            position=candidate.key[1],
            meta=candidate.meta,
        )
