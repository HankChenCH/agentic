"""知识域端口（依赖倒置）：协议住领域层，实现由 adapters / components 回填。

仓储协议（``KnowledgeBaseRepositoryPort`` / ``KnowledgeDocumentRepositoryPort``）
的实现住 ``app/adapters/persistence/``；向量索引协议
（``KnowledgeVectorIndexPort``）的实现住 ``app/adapters/vector/``——机制
（SQLModel 会话、Weaviate collection）不进 domain，领域服务与组件只依赖
本文件的协议面，wireup 经 ``@injectable(as_type=...)`` 按协议类型注入。
跨聚合的基础契约（文件存储/文档解析）在 ``app/domain/ports`` 包。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Protocol, Tuple
from uuid import UUID

from app.models.domain.knowledge import (
    DocumentSegment,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)

# 向量检索工具入参 top_k 的默认值（仅最终返回条数口径；内部检索深度见
# KnowledgeRetrievalService 的候选池常量）
DEFAULT_TOP_K = 4


@dataclass(frozen=True)
class VectorHit:
    """单条向量检索命中（``KnowledgeVectorIndexPort.search*`` 的返回形状）。

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


class KnowledgeBaseRepositoryPort(Protocol):
    """知识库聚合数据访问协议（仅关系表；向量/对象存储不进仓库）。"""

    def create_kb(self, kb: KnowledgeBase) -> KnowledgeBase: ...

    def get_kb(self, kb_id: UUID) -> KnowledgeBase | None: ...

    def get_kb_by_name(self, name: str, user_id: UUID) -> KnowledgeBase | None: ...

    def update_kb(self, kb: KnowledgeBase) -> KnowledgeBase: ...

    def delete_kb(self, kb_id: UUID) -> bool: ...

    def list_kbs(
        self, page: int, page_size: int, user_id: UUID
    ) -> Tuple[List[KnowledgeBase], int]: ...

    def list_visible_kbs(self, user_id: UUID | None) -> List[KnowledgeBase]: ...


class KnowledgeDocumentRepositoryPort(Protocol):
    """文档 + 分段聚合数据访问协议（分段与文档事务耦合，同归本仓库）。"""

    def create_document(self, doc: KnowledgeDocument) -> KnowledgeDocument: ...

    def get_document(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument | None: ...

    def update_document(self, doc: KnowledgeDocument) -> KnowledgeDocument: ...

    def claim_document(
        self, kb_id: UUID, doc_id: UUID, *, stale_before: datetime | None
    ) -> KnowledgeDocument | None: ...

    def list_reap_candidates(self) -> List[KnowledgeDocument]: ...

    def mark_reaped(self, doc_id: UUID, *, max_attempts: int) -> bool: ...

    def delete_document(self, kb_id: UUID, doc_id: UUID) -> bool: ...

    def list_documents(
        self, kb_id: UUID, page: int, page_size: int
    ) -> Tuple[List[KnowledgeDocument], int]: ...

    def list_enabled_doc_ids(self, kb_ids: List[UUID]) -> List[UUID]: ...

    def get_document_by_id(self, doc_id: UUID) -> KnowledgeDocument | None: ...

    def list_documents_by_ids(self, doc_ids: List[UUID]) -> List[KnowledgeDocument]: ...

    def replace_segments(
        self, doc_id: UUID, segments: List[DocumentSegment]
    ) -> List[DocumentSegment]: ...

    def complete_document(self, doc_id: UUID) -> bool: ...

    def list_segments_by_doc(
        self, doc_id: UUID, start: int | None = None, end: int | None = None
    ) -> List[DocumentSegment]: ...

    def list_segment_ids_by_doc(self, doc_id: UUID) -> List[UUID]: ...

    def list_segments(
        self, doc_id: UUID, page: int, page_size: int, keyword: str | None = None
    ) -> Tuple[List[DocumentSegment], int]: ...

    def get_segment(self, doc_id: UUID, segment_id: UUID) -> DocumentSegment | None: ...

    def next_segment_position(self, doc_id: UUID) -> int: ...

    def append_segment(self, doc_id: UUID, segment: DocumentSegment) -> DocumentSegment: ...

    def update_segment_content(
        self, doc_id: UUID, segment_id: UUID, *, content: str, word_count: int
    ) -> DocumentSegment | None: ...

    def set_segment_status(
        self, doc_id: UUID, segment_id: UUID, status: KnowledgeStatus
    ) -> DocumentSegment | None: ...

    def delete_segment(self, doc_id: UUID, segment_id: UUID) -> bool: ...

    def reset_document_for_rechunk(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument | None: ...


class KnowledgeVectorIndexPort(Protocol):
    """知识库向量索引协议：索引命名/显式建库、分批写入删除、检索与整库清理。

    实现收敛 Weaviate 机制（每库一 collection、gse schema、分批策略）；
    多库扇出与融合只在 ``search_many`` 一处实现。
    """

    def add_segments(self, kb_id: UUID, segments: List[DocumentSegment]) -> None: ...

    def delete_segments(self, kb_id: UUID, segment_ids: List[UUID]) -> None: ...

    def drop_collection(self, kb_id: UUID) -> None: ...

    def search(
        self, kb_id: UUID, query: str, *, top_k: int = DEFAULT_TOP_K, alpha: float = 1.0
    ) -> List[VectorHit]: ...

    def search_many(
        self, kb_ids: List[UUID], query: str, *, top_k: int = DEFAULT_TOP_K, alpha: float = 1.0
    ) -> List[VectorHit]: ...
