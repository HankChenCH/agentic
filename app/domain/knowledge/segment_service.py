"""知识库分段管理：查看/搜索、手动新增、内容编辑、启停、删除与整篇重分段。

分段是文档的子资源：读路径 require_visible_kb + require_document（属主或
公开库可见），写路径 require_owned_kb（仅属主，公开只让渡可见性）。分段行
与向量库对象一一对应（segment.id 即向量 UUID）：内容编辑/新增/删除必须同步
维护向量——行成功而向量失败不回滚（delete_segments 内部 best-effort，孤儿
向量可对账清理），与文档删除同口径。

分段写操作的状态闸门：所属文档须处于稳定态（ready/enabled/disabled）——
pending/processing 期间分段集合正被流水线整体重写（replace_segments 先清
后写），此时编辑/删除会被覆盖或产生竞态；deleting 则整个文档即将消失。
分段级禁用语义 = 不参与召回（检索侧按行 status 过滤，见 retrieval.py），
对定位读取（segment_at）不生效——显式按位置读文不受召回闸门约束。

整篇重分段只做受理（稳定态置回 pending），Celery 分发归端点层（services
禁向上 import tasks）：管线幂等重跑，全部分段被重新分块替换——手动新增/
禁用一并被覆盖，这是重分段的定义而非副作用。
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from wireup import injectable

from .vector_index import KnowledgeVectorIndex
from app.core.logging import LoggerFactory
from app.exceptions import (
    KnowledgeDocumentStatusError,
    KnowledgeSegmentNotFoundError,
    KnowledgeSegmentStateError,
)
from app.models.domain.knowledge import (
    DocumentSegment,
    KnowledgeDocument,
    KnowledgeStatus,
)
from app.models.schema.request.knowledge import (
    KnowledgeSegmentCreateRequest,
    KnowledgeSegmentUpdateRequest,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from .support import page_envelope, require_document, require_owned_kb, require_visible_kb

# 分段写操作允许的文档状态：稳定态才可管理（处理中分段集合正被流水线重写）
SEGMENT_WRITE_ALLOWED = {
    KnowledgeStatus.READY,
    KnowledgeStatus.ENABLED,
    KnowledgeStatus.DISABLED,
}


@injectable
@dataclass
class KnowledgeSegmentService:
    kb_repo: KnowledgeBaseRepository  # 父资源归属/可见性锚定（require_owned_kb / require_visible_kb）
    document_repo: KnowledgeDocumentRepository
    vector_index: KnowledgeVectorIndex
    logger_factory: LoggerFactory

    def __post_init__(self):
        self.logger = self.logger_factory.get_logger(__name__)

    # ---------- 查询 ----------

    def list_segments(
        self,
        kb_id: UUID,
        user_id: UUID,
        doc_id: UUID,
        page: int,
        page_size: int,
        keyword: str | None = None,
    ) -> dict:
        require_visible_kb(self.kb_repo, kb_id, user_id)
        require_document(self.document_repo, kb_id, doc_id)
        items, total = self.document_repo.list_segments(
            doc_id, page, page_size, keyword=(keyword or "").strip() or None
        )
        return page_envelope(items, total, page, page_size)

    def describe_segment(
        self, kb_id: UUID, user_id: UUID, doc_id: UUID, segment_id: UUID
    ) -> DocumentSegment:
        require_visible_kb(self.kb_repo, kb_id, user_id)
        require_document(self.document_repo, kb_id, doc_id)
        return self._require_segment(self.document_repo, doc_id, segment_id)

    # ---------- 写操作 ----------

    def create_segment(
        self, kb_id: UUID, user_id: UUID, doc_id: UUID, request: KnowledgeSegmentCreateRequest
    ) -> DocumentSegment:
        """手动新增分段：追加到文档末尾，即时就绪并写入向量库。"""
        require_owned_kb(self.kb_repo, kb_id, user_id)
        doc = self._require_stable_document(kb_id, doc_id)
        segment = DocumentSegment(
            id=uuid4(),
            kb_id=doc.kb_id,
            doc_id=doc.id,
            position=self.document_repo.next_segment_position(doc_id),
            content=request.content,
            word_count=len(request.content),
            # 手动分段无解析溯源元数据（页码/标题路径/bbox 均缺省）
            meta=None,
            status=KnowledgeStatus.READY,
        )
        try:
            segment = self.document_repo.append_segment(doc_id, segment)
        except IntegrityError as exc:
            # (doc_id, position) 唯一约束冲突：并发追加落败方整体回滚，提示重试
            raise KnowledgeSegmentStateError(
                "segment position conflict caused by concurrent append, please retry"
            ) from exc
        self.vector_index.add_segments(doc.kb_id, [segment])
        return segment

    def update_segment_content(
        self,
        kb_id: UUID,
        user_id: UUID,
        doc_id: UUID,
        segment_id: UUID,
        request: KnowledgeSegmentUpdateRequest,
    ) -> DocumentSegment:
        """编辑分段内容：行更新成功后按同 UUID 原地重写向量（先删后写）。"""
        require_owned_kb(self.kb_repo, kb_id, user_id)
        self._require_stable_document(kb_id, doc_id)
        self._require_segment(self.document_repo, doc_id, segment_id)
        segment = self.document_repo.update_segment_content(
            doc_id, segment_id, content=request.content, word_count=len(request.content)
        )
        if segment is None:
            # 校验后被并发删除的极小竞态窗口：按不存在处理而非空指针
            raise KnowledgeSegmentNotFoundError(f"knowledge document segment not found: {segment_id}")
        self.vector_index.delete_segments(kb_id, [segment_id])
        self.vector_index.add_segments(kb_id, [segment])
        return segment

    def set_segment_enabled(
        self, kb_id: UUID, user_id: UUID, doc_id: UUID, segment_id: UUID, enabled: bool
    ) -> DocumentSegment:
        """分段启停：禁用（disabled）不参与召回，启用恢复 ready；纯行级状态，不动向量。"""
        require_owned_kb(self.kb_repo, kb_id, user_id)
        self._require_stable_document(kb_id, doc_id)
        segment = self._require_segment(self.document_repo, doc_id, segment_id)
        target = KnowledgeStatus.READY if enabled else KnowledgeStatus.DISABLED
        if segment.status != target:
            segment = self.document_repo.set_segment_status(doc_id, segment_id, target)
        return segment

    def delete_segment(
        self, kb_id: UUID, user_id: UUID, doc_id: UUID, segment_id: UUID
    ) -> DocumentSegment:
        """删除单段：行删除（seg_num 同事务递减）后清理对应向量，返回被删快照。"""
        require_owned_kb(self.kb_repo, kb_id, user_id)
        self._require_stable_document(kb_id, doc_id)
        segment = self._require_segment(self.document_repo, doc_id, segment_id)
        self.document_repo.delete_segment(doc_id, segment_id)
        self.vector_index.delete_segments(kb_id, [segment_id])
        return segment

    def rechunk_document(self, kb_id: UUID, user_id: UUID, doc_id: UUID) -> KnowledgeDocument:
        """整篇重分段受理：稳定态文档置回 pending（清错误信息），由端点补发处理任务。

        受理后文档暂时退出召回集（pending 不满足启用闸门）；管线完成时文档与
        全部分段回 ready——原 enabled 文档需重新启用才会参与召回。
        """
        require_owned_kb(self.kb_repo, kb_id, user_id)
        doc = require_document(self.document_repo, kb_id, doc_id)
        reset = self.document_repo.reset_document_for_rechunk(kb_id, doc_id)
        if reset is None:
            raise KnowledgeDocumentStatusError(
                f"cannot rechunk in status '{doc.status.value}': allowed statuses are "
                f"{', '.join(sorted(s.value for s in SEGMENT_WRITE_ALLOWED))}"
            )
        return reset

    # ---------- 内部 ----------

    def _require_stable_document(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument:
        """分段写路径的文档状态闸门：仅稳定态可管理分段（先 require_document 挡 4004）。"""
        doc = require_document(self.document_repo, kb_id, doc_id)
        if doc.status not in SEGMENT_WRITE_ALLOWED:
            raise KnowledgeSegmentStateError(
                f"cannot manage segments while document is '{doc.status.value}': allowed statuses are "
                f"{', '.join(sorted(s.value for s in SEGMENT_WRITE_ALLOWED))}"
            )
        return doc

    @staticmethod
    def _require_segment(
        document_repo: KnowledgeDocumentRepository, doc_id: UUID, segment_id: UUID
    ) -> DocumentSegment:
        segment = document_repo.get_segment(doc_id, segment_id)
        if segment is None:
            raise KnowledgeSegmentNotFoundError(f"knowledge document segment not found: {segment_id}")
        return segment
