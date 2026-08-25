"""知识库向量索引适配器：索引命名/显式建库、分批写入删除、检索与整库清理。

服务层（摄取/删除）与检索组件不直接触碰 VectorStoreFactory——索引命名
（每库一 collection）、schema 显式化与分批策略是知识库域的私有约定，
统一收敛于此。多库扇出与融合只在 ``search_many`` 一处实现，未来迁移
共享 collection / multi-tenancy 时只动本文件。
"""

from dataclasses import dataclass
from typing import Any, List
from uuid import UUID

from langchain_core.documents import Document
from wireup import injectable

from app.components.knowledge.collection import collection_schema, index_name
from app.core.logging import LoggerFactory
from app.infrastructures.vector import VectorStoreFactory
from app.models.domain.knowledge import DocumentSegment

# 嵌入写入向量库的分批大小：单条 add_texts 过大易触发请求体/超时限制
_EMBED_BATCH_SIZE = 32

# 向量删除的单批大小（gRPC 消息体保护）
_VECTOR_DELETE_BATCH_SIZE = 100

# 检索默认返回条数
DEFAULT_TOP_K = 4

# 检索命中里已提升为独立字段的 metadata 键（其余键整体进入 VectorHit.meta）
_PROMOTED_METADATA_KEYS = frozenset({"kb_id", "doc_id", "position"})


@dataclass
class VectorHit:
    """单条向量检索命中。

    ``score`` 为 Weaviate hybrid fusion 分数（0-1，越高越相关；alpha=1
    纯向量时即语义相似度归一分）。跨库可比的前提是同一 embedding 模型，
    由检索服务（KnowledgeRetrievalService）的模型守卫保证。
    """

    content: str
    score: float
    kb_id: str
    doc_id: str
    position: int
    meta: dict


@injectable
@dataclass
class KnowledgeVectorIndex:
    vector_store_factory: VectorStoreFactory
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def add_segments(self, kb_id: UUID, segments: List[DocumentSegment]) -> None:
        # segment.id 即向量对象 UUID：行与向量一一对应，重写前调用方已清旧向量
        store = self._open(kb_id)
        for start in range(0, len(segments), _EMBED_BATCH_SIZE):
            batch = segments[start : start + _EMBED_BATCH_SIZE]
            store.add_texts(
                texts=[seg.content for seg in batch],
                metadatas=[self._metadata(seg) for seg in batch],
                ids=[str(seg.id) for seg in batch],
            )

    def delete_segments(self, kb_id: UUID, segment_ids: List[UUID]) -> None:
        # 向量清理 best-effort：行删除已成功，清理失败只告警（孤儿向量可后续对账清理）
        if not segment_ids:
            return
        try:
            store = self._open(kb_id)
        except Exception:
            self.logger.warning("failed to open vector store for kb %s", kb_id, exc_info=True)
            return
        ids = [str(segment_id) for segment_id in segment_ids]
        for start in range(0, len(ids), _VECTOR_DELETE_BATCH_SIZE):
            batch = ids[start : start + _VECTOR_DELETE_BATCH_SIZE]
            try:
                store.delete(ids=batch)
            except Exception:
                self.logger.warning(
                    "failed to delete %d vectors for kb %s", len(batch), kb_id, exc_info=True
                )

    def drop_collection(self, kb_id: UUID) -> None:
        # 删库清理直接 drop collection：替代全量 segment id 逐批删除（快且不留空壳）。
        # best-effort：collection 不存在或清理失败不阻断删库流程
        try:
            self.vector_store_factory.drop_index(index_name=index_name(kb_id))
        except Exception:
            self.logger.warning("failed to drop collection for kb %s", kb_id, exc_info=True)

    def search(
        self, kb_id: UUID, query: str, *, top_k: int = DEFAULT_TOP_K, alpha: float = 1.0
    ) -> List[VectorHit]:
        """单库检索：``alpha=1.0`` 纯向量（语义），``<1`` 混合 BM25+向量。

        底层直通 Weaviate 原生 ``hybrid()``——future-proof：升级混合检索
        只需调 alpha，不改调用方。空库/查询失败返回空命中，不阻断对话。
        """
        try:
            store = self._open(kb_id)
            pairs = store.similarity_search_with_score(query, k=top_k, alpha=alpha)
        except Exception:
            self.logger.warning("vector search failed for kb %s", kb_id, exc_info=True)
            return []
        return [self._hit(kb_id, doc, score) for doc, score in pairs]

    def search_many(
        self, kb_ids: List[UUID], query: str, *, top_k: int = DEFAULT_TOP_K, alpha: float = 1.0
    ) -> List[VectorHit]:
        """多库扇出检索 + 融合：逐库各取 top_k 后按分数统一排序截断。"""
        hits: List[VectorHit] = []
        for kb_id in kb_ids:
            hits.extend(self.search(kb_id, query, top_k=top_k, alpha=alpha))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:top_k]

    def _open(self, kb_id: UUID):
        # 统一打开：每次显式传入 schema 实现幂等 ensure（collection 已存在则
        # 直接复用，不会重建/迁移）；并发首建竞态时后到者重试一次即命中已存在分支
        name = index_name(kb_id)
        try:
            return self.vector_store_factory.create(
                index_name=name, text_key="content", schema=collection_schema(name)
            )
        except Exception:
            self.logger.warning("initial open failed for collection %s, retrying", name)
            return self.vector_store_factory.create(
                index_name=name, text_key="content", schema=collection_schema(name)
            )

    @staticmethod
    def _metadata(segment: DocumentSegment) -> dict:
        metadata = {
            "kb_id": str(segment.kb_id),
            "doc_id": str(segment.doc_id),
            "position": segment.position,
            **(segment.meta or {}),
        }
        # bboxes（对象数组）不能直接进 Weaviate：展平成 bbox_pages/bbox_coords
        # 平行数组（coords 每 4 个数对应 pages 一个条目），检索侧 _hit 还原
        bboxes = metadata.pop("bboxes", None)
        if bboxes:
            metadata["bbox_pages"] = [int(b["page"]) for b in bboxes]
            coords: List[float] = []
            for b in bboxes:
                coords.extend(float(v) for v in b["bbox"])
            metadata["bbox_coords"] = coords
        return metadata

    @staticmethod
    def _hit(kb_id: UUID, doc: Document, score: float) -> VectorHit:
        meta = dict(doc.metadata or {})
        bboxes = _pair_bboxes(meta.pop("bbox_pages", None), meta.pop("bbox_coords", None))
        if bboxes:
            meta["bboxes"] = bboxes
        return VectorHit(
            content=doc.page_content,
            score=float(score),
            kb_id=meta.get("kb_id", str(kb_id)),
            doc_id=meta.get("doc_id", ""),
            position=meta.get("position", 0),
            meta={k: v for k, v in meta.items() if k not in _PROMOTED_METADATA_KEYS},
        )


def _pair_bboxes(pages: Any, coords: Any) -> list[dict] | None:
    """展平数组 → ``[{"page", "bbox"}]``：长度不符（脏数据/旧版对象）返回 None 降级。"""
    if not isinstance(pages, list) or not isinstance(coords, list):
        return None
    if not pages or len(coords) != 4 * len(pages):
        return None
    bboxes = []
    for i, page in enumerate(pages):
        quad = [round(float(v), 3) for v in coords[4 * i : 4 * i + 4]]
        bboxes.append({"page": int(page), "bbox": quad})
    return bboxes
