"""知识库定位读取能力：检索命中片段后的确定性下钻——邻域窗口 / 范围读文 / 文档清单。

与检索能力（retrieval）互补：knowledge_search 按相似度返回孤立片段（切块软
上限 800 字符，命中常是某段论述/表格的中间部分），本模块让 agent 以检索结果
自带的 doc_id + position 为定位句柄，确定性地读取邻域或通读文档——检索找点、
定位读面（同编码 agent 的 Grep→Read 配合）。守卫口径与检索完全一致：可见性
圈定（私有=属主 + 公开库，不可见与不存在同话术不泄露）→ KB enabled → 文档
enabled；不涉向量，嵌入模型一致性守卫不适用。
"""

from dataclasses import dataclass
from typing import List
from uuid import UUID

from wireup import injectable

from app.core.logging import LoggerFactory
from app.models.domain.knowledge import DocumentSegment, KnowledgeBase, KnowledgeDocument, KnowledgeStatus
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository

# 邻域窗口每侧最大段数：LLM 传入更大值时钳制（防止单次拉全文档）
_MAX_CONTEXT_SPAN = 3
# 范围读文预算：段数与字符双上限，先到先停——TOOL_RESULT 会原样落库并在
# 后续每轮 replay 进 LLM 上下文，读取面必须有硬预算
_READ_MAX_SEGMENTS = 12
_READ_MAX_CHARS = 6000
# 文档清单上限：导航用途不需要全量枚举超大库
_LIST_MAX_DOCS = 50


@dataclass
class SegmentWindow:
    """定位读取的领域结果：有序分段 + 导航信息（供 agent 感知文档规模与当前位置）。"""

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
    kb_repo: KnowledgeBaseRepository
    document_repo: KnowledgeDocumentRepository
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    def segment_context(
        self, user_id: UUID | None, doc_id: UUID, position: int, before: int = 1, after: int = 1
    ) -> SegmentWindow | None:
        """命中片段的邻域窗口：[position-before, position+after] 内全部分段（有序）。

        每侧钳制 ≤ _MAX_CONTEXT_SPAN；position 越界（如幻觉的 position）时窗口
        可能不含该片段本身——返回实际取到的窗口，越界信息由 notes 说明。
        """
        before = max(0, min(before, _MAX_CONTEXT_SPAN))
        after = max(0, min(after, _MAX_CONTEXT_SPAN))
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
        start = max(0, position - before)
        end = min(seg_total - 1, position + after)
        segments = self.document_repo.list_segments_by_doc(doc_id, start=start, end=end)
        if not segments:
            notes.append(f"文档「{doc.name}」在位置 {position} 附近没有分段")
        return SegmentWindow(
            kb_id=doc.kb_id,
            doc_id=doc.id,
            doc_name=doc.name,
            seg_total=seg_total,
            start=start,
            end=end,
            segments=segments,
            notes=notes,
        )

    def read_document(
        self, user_id: UUID | None, doc_id: UUID, start: int = 0, end: int | None = None
    ) -> SegmentWindow | None:
        """范围读文：从 start（缺省 0）按 position 序读取，段数与字符预算先到先停。

        预算截断时附 note 指引用 start/end 续读；end 为 None 表示读到文档末尾。
        返回的 start/end 是实际返回区间（预算截断后收窄），agent 由此感知进度。
        """
        start = max(0, start)
        doc = self._visible_document(user_id, doc_id)
        if doc is None:
            return None
        seg_total = doc.seg_num
        requested_end = seg_total - 1 if end is None else min(end, seg_total - 1)
        notes: List[str] = []
        if start > requested_end:
            notes.append(
                f"start {start} 超出文档范围（共 {seg_total} 段，position 0-{seg_total - 1}）"
            )
            return SegmentWindow(
                kb_id=doc.kb_id, doc_id=doc.id, doc_name=doc.name, seg_total=seg_total,
                start=start, end=requested_end, segments=[], notes=notes,
            )
        segments = self.document_repo.list_segments_by_doc(doc_id, start=start, end=requested_end)
        # 预算截断：段数上限优先，字符预算在其后再收
        if len(segments) > _READ_MAX_SEGMENTS:
            segments = segments[:_READ_MAX_SEGMENTS]
            notes.append(f"已达单次读取段数上限（{_READ_MAX_SEGMENTS} 段），可从 position {segments[-1].position + 1} 续读")
        chars = 0
        cut = len(segments)
        for index, segment in enumerate(segments):
            chars += segment.word_count
            if chars > _READ_MAX_CHARS:
                cut = index
                break
        if cut < len(segments):
            segments = segments[:cut]
            notes.append(f"已达单次读取字符预算（{_READ_MAX_CHARS} 字符），可从 position {segments[-1].position + 1} 续读")
        returned_start = segments[0].position if segments else start
        returned_end = segments[-1].position if segments else requested_end
        return SegmentWindow(
            kb_id=doc.kb_id,
            doc_id=doc.id,
            doc_name=doc.name,
            seg_total=seg_total,
            start=returned_start,
            end=returned_end,
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
