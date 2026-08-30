"""记忆域向量适配器：三类对象混居单 collection 的写入与检索（每用户一库）。

归位领域层（镜像知识域 services/domain/knowledge/vector_index.py 的先例）：
组件侧经合法 components→domain 边注入本类，不直触 ``VectorStoreFactory``。
确定性向量 id 用 ``uuid5("memory/{kind}/{ref_id}")``——同 id 重写即覆盖、
取代时可反算删除；不直接拼字符串是因为 Weaviate 对象键须为合法 UUID。
用户隔离走**每用户一 collection**（``Memory_{uid.hex}``，见 collection.py）：
注入实例无作用域（仅维护 CLI 兜底用），用户侧行程经 ``for_user(uid)`` 取
作用域视图。向量端任何失败都只告警不上抛：SQL 是事实源，索引可随时全量重建。
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.documents import Document
from wireup import injectable

from app.core.logging import LoggerFactory
from app.infrastructures.vector import VectorStoreFactory
from app.services.domain.memory.collection import collection_schema, memory_index_name

KIND_ENTITY = "entity"
KIND_STATEMENT = "statement"
KIND_EPISODE = "episode"

# 嵌入写入的分批大小（单条 add_texts 过大易触发请求体/超时限制）
_EMBED_BATCH_SIZE = 32
# 向量删除的单批大小（gRPC 消息体保护）
_DELETE_BATCH_SIZE = 100
# 检索时的 kind 过采样倍数：VectorStore 接口不暴露属性过滤，
# 三类混居一 collection，先过量取回再按 kind 客户端筛
_OVERSAMPLE = 3


def vector_object_id(kind: str, ref_id: int) -> str:
    """{kind}_{ref_id} 的确定性合法 UUID：写入覆盖与删除共用此映射。"""
    return str(uuid5(NAMESPACE_URL, f"memory/{kind}/{ref_id}"))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


@dataclass(frozen=True)
class VectorEntry:
    """一次嵌入写入的最小单元。"""

    kind: str
    ref_id: int
    content: str
    thread_id: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class MemoryVectorHit:
    """单条命中：hybrid fusion 分（0-1），越高越相关。"""

    kind: str
    ref_id: int
    score: float


@injectable
@dataclass
class MemoryVectorIndex:
    vector_store_factory: VectorStoreFactory
    logger_factory: LoggerFactory
    # 作用域标记：不进 __init__（init=False）——wireup 按 __init__ 签名提取依赖，
    # 可选的 UUID 参数会与其注册表校验冲突；单例实例恒为 None（无作用域，仅维护
    # CLI 兜底路径），作用域视图由 for_user 构造后回填。
    user_id: UUID | None = field(default=None, init=False, compare=False)

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def for_user(self, user_id: UUID) -> "MemoryVectorIndex":
        """返回绑定用户的轻量作用域视图：读写全部落在该用户的 collection。"""
        scoped = MemoryVectorIndex(
            vector_store_factory=self.vector_store_factory,
            logger_factory=self.logger_factory,
        )
        scoped.user_id = user_id
        return scoped

    def upsert_many(self, entries: list[VectorEntry]) -> None:
        """批量写入/覆盖；同 (kind, ref_id) 再次写入即更新语义。"""
        if not entries:
            return
        try:
            store = self._open()
            for start in range(0, len(entries), _EMBED_BATCH_SIZE):
                batch = entries[start : start + _EMBED_BATCH_SIZE]
                store.add_texts(
                    texts=[e.content for e in batch],
                    metadatas=[self._metadata(e) for e in batch],
                    ids=[vector_object_id(e.kind, e.ref_id) for e in batch],
                )
        except Exception:
            self.logger.warning("memory vector upsert failed (%d entries)", len(entries), exc_info=True)

    def delete(self, items: list[tuple[str, int]]) -> None:
        """按 (kind, ref_id) 删除对应向量；best-effort，孤儿可 rebuild 对账清理。"""
        if not items:
            return
        try:
            store = self._open()
        except Exception:
            self.logger.warning("failed to open memory collection for delete", exc_info=True)
            return
        ids = [vector_object_id(kind, ref_id) for kind, ref_id in items]
        for start in range(0, len(ids), _DELETE_BATCH_SIZE):
            try:
                store.delete(ids=ids[start : start + _DELETE_BATCH_SIZE])
            except Exception:
                self.logger.warning("memory vector delete failed", exc_info=True)

    def search(
        self,
        query: str,
        *,
        kinds: tuple[str, ...] = (KIND_STATEMENT, KIND_EPISODE),
        top_k: int = 8,
        alpha: float = 0.7,
    ) -> list[MemoryVectorHit]:
        """混合检索（alpha<1 混 BM25）：过采样取回后按 kind 筛选截断。

        空库/查询失败返回空命中——召回失败不阻断对话主链路。
        注意 hit.score 是结果窗口内的相对融合分，只可用于排序/展示，
        不可与绝对相似度阈值比较（绝对判定用 text_cosine）。
        """
        try:
            store = self._open()
            pairs = store.similarity_search_with_score(query, k=top_k * _OVERSAMPLE, alpha=alpha)
        except Exception:
            self.logger.warning("memory vector search failed", exc_info=True)
            return []
        hits = [self._hit(doc, score) for doc, score in pairs]
        wanted = [h for h in hits if h.kind in kinds]
        return wanted[:top_k]

    def text_cosine(self, query: str, contents: list[str]) -> list[float]:
        """查询词与若干候选文本的真实余弦相似度（实体消歧的判定依据）。

        hybrid 融合分随查询漂移且封顶 1.0，曾导致「学校并入公司」的实体
        错合并（见 docs/memory-v2-design.md §8 教训）；本方法用与写入同源
        的嵌入模型（工厂注入 store.embeddings）在客户端现算绝对余弦。
        任何失败返回全 0——失败方向 =「不相似」，消歧宁可新建不错合并。
        """
        if not contents:
            return []
        try:
            embeddings = self._open().embeddings
            query_vec = embeddings.embed_query(query)
            doc_vecs = embeddings.embed_documents(list(contents))
            return [_cosine(query_vec, doc_vec) for doc_vec in doc_vecs]
        except Exception:
            self.logger.warning("memory text cosine failed (%d candidates)", len(contents), exc_info=True)
            return [0.0] * len(contents)

    def _open(self):
        # 幂等 ensure：每次显式传 schema，collection 已存在则直接复用；
        # 并发首建竞态重试一次即命中已存在分支（惯例同知识域）
        name = memory_index_name(self.user_id)
        try:
            return self.vector_store_factory.create(
                index_name=name, text_key="content", schema=collection_schema(name)
            )
        except Exception:
            self.logger.warning("initial open failed for collection %s, retrying", name)
            return self.vector_store_factory.create(
                index_name=name, text_key="content", schema=collection_schema(name)
            )

    def rebuild(self, entries: list[VectorEntry]) -> None:
        """全量重建：drop 本作用域 collection 后重新写入（换 embedding 模型等场景）。"""
        self.vector_store_factory.drop_index(index_name=memory_index_name(self.user_id))
        self.upsert_many(entries)

    @staticmethod
    def _metadata(entry: VectorEntry) -> dict:
        meta = {"kind": entry.kind, "ref_id": entry.ref_id}
        if entry.thread_id:
            meta["thread_id"] = entry.thread_id
        if entry.occurred_at is not None:
            meta["occurred_at"] = int(entry.occurred_at.timestamp())
        return meta

    @staticmethod
    def _hit(doc: Document, score: float) -> MemoryVectorHit:
        meta = doc.metadata or {}
        return MemoryVectorHit(
            kind=str(meta.get("kind", "")),
            ref_id=int(meta.get("ref_id", -1)),
            score=float(score),
        )
