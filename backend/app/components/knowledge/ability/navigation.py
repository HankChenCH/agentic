"""知识库定位读取能力：检索命中片段后的确定性下钻——精确读取指定位置 / 文档清单。

与检索能力（retrieval）互补：knowledge_search 按相似度返回节选（切块软
上限 800 字符，命中常是某段论述/表格的中间部分，且结果已自带命中邻域），
本模块让 agent 以检索结果自带的 doc_id + position 为定位句柄，确定性地
读取某个确切位置的内容——检索找点、定位读点（同编码 agent 的 Grep→Read
配合），不做邻域泛化也没有通读用法。守卫口径与检索完全一致：可见性圈定
（私有=属主 + 公开库，不可见与不存在同话术不泄露）→ KB enabled → 文档
enabled；不涉向量，嵌入模型一致性守卫不适用。
"""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from wireup import injectable

from app.core.logging import LoggerFactory
from app.models.domain.knowledge import DocumentSegment, KnowledgeBase, KnowledgeDocument, KnowledgeStatus
from app.domain.knowledge.ports import KnowledgeBaseRepositoryPort
from app.domain.knowledge.ports import KnowledgeDocumentRepositoryPort

# 文档清单上限：导航用途不需要全量枚举超大库
_LIST_MAX_DOCS = 50


@dataclass
class SegmentWindow:
    """定位读取的领域结果：分段 + 导航信息（供 agent 感知文档规模与当前位置）。"""

    kb_id: UUID
    doc_id: UUID
    doc_name: str
    seg_total: int
    start: int
    end: int
    segments: List[DocumentSegment]
    notes: List[str]


@injectable
@dataclass
class KnowledgeNavigationService:
    kb_repo: KnowledgeBaseRepositoryPort
    document_repo: KnowledgeDocumentRepositoryPort
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def segment_at(self, user_id: UUID | None, doc_id: UUID, position: int) -> SegmentWindow | None:
        """精确定位读取：文档指定 position 的单个分段。

        position 越界（如幻觉的 position）不猜近似——返回空窗口 + 越界说明
        （含合法区间），由工具层转 sources JSON 引导 LLM 纠正。
        """
        doc = self._visible_document(user_id, doc_id)
        if doc is None:
            return None
        seg_total = doc.seg_num
        notes: List[str] = []
        if position < 0 or position > seg_total - 1:
            notes.append(f"position {position} 超出文档范围（共 {seg_total} 段，position 0-{seg_total - 1}）")
            return SegmentWindow(
                kb_id=doc.kb_id, doc_id=doc.id, doc_name=doc.name, seg_total=seg_total,
                start=position, end=position, segments=[], notes=notes,
            )
        segments = self.document_repo.list_segments_by_doc(doc_id, start=position, end=position)
        if not segments:
            notes.append(f"文档「{doc.name}」在位置 {position} 没有分段")
        return SegmentWindow(
            kb_id=doc.kb_id,
            doc_id=doc.id,
            doc_name=doc.name,
            seg_total=seg_total,
            start=position,
            end=position,
            segments=segments,
            notes=notes,
        )

    def list_documents(self, user_id: UUID | None, kb_id: UUID) -> List[KnowledgeDocument] | None:
        """知识库的文档清单（导航入口）：仅 enabled 库，上限 _LIST_MAX_DOCS 条。

        返回 None 表示库不可见或未启用（调用方转统一话术）；清单按管理侧同序
        （weight desc, created_at desc）。
        """
        kb = self._visible_enabled_kb(user_id, kb_id)
        if kb is None:
            return None
        docs, total = self.document_repo.list_documents(kb_id, page=1, page_size=_LIST_MAX_DOCS)
        if total > len(docs):
            self.logger.info("kb %s document list truncated: %s/%s", kb_id, len(docs), total)
        return docs

    def _visible_document(self, user_id: UUID | None, doc_id: UUID) -> KnowledgeDocument | None:
        """doc_id → 可见且可读的文档行（库可见 + 库 enabled + 文档 enabled）。"""
        doc = self.document_repo.get_document_by_id(doc_id)
        if doc is None:
            return None
        kb = self.kb_repo.get_kb(doc.kb_id)
        if kb is None or not self._kb_visible(kb, user_id):
            return None
        if kb.status != KnowledgeStatus.ENABLED or doc.status != KnowledgeStatus.ENABLED:
            return None
        return doc

    def _visible_enabled_kb(self, user_id: UUID | None, kb_id: UUID) -> KnowledgeBase | None:
        kb = self.kb_repo.get_kb(kb_id)
        if kb is None or not self._kb_visible(kb, user_id):
            return None
        if kb.status != KnowledgeStatus.ENABLED:
            return None
        return kb

    @staticmethod
    def _kb_visible(kb: KnowledgeBase, user_id: UUID | None) -> bool:
        # 与 list_visible_kbs 的可见性谓词一致：匿名仅公开库，登录属主或公开
        return kb.is_public or (user_id is not None and kb.user_id == user_id)
