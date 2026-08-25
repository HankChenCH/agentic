"""知识库文档聚合数据访问：文档 + 分段（分段与文档事务耦合，同归本仓库）。

``knowledge_base.doc_num`` 是跨聚合的冗余计数：与文档行增删同会话维护，
避免计数漂移——本仓库因此会在同一事务里触碰 KnowledgeBase 表（有意的
例外，勿推广到其它跨聚合写）。
"""

from dataclasses import dataclass
from typing import List, Tuple
from uuid import UUID

from sqlalchemy import Engine, delete, func, update
from sqlmodel import Session, col, select
from wireup import injectable

from app.models.domain.knowledge import (
    DocumentSegment,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)


@injectable
@dataclass
class KnowledgeDocumentRepository:
    engine: Engine

    def create_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        # doc_num 与文档行同事务维护，避免冗余计数漂移
        with Session(self.engine, expire_on_commit=False) as session:
            kb = session.get(KnowledgeBase, doc.kb_id)
            if kb is not None:
                kb.doc_num += 1
            session.add(doc)
            session.commit()
            session.refresh(doc)
        return doc

    def get_document(self, kb_id: UUID, doc_id: UUID) -> KnowledgeDocument | None:
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.id) == doc_id)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
            ).first()
            session.commit()
        return doc

    def update_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        with Session(self.engine, expire_on_commit=False) as session:
            session.add(doc)
            session.commit()
            session.refresh(doc)
        return doc

    def delete_document(self, kb_id: UUID, doc_id: UUID) -> bool:
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.id) == doc_id)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
            ).first()
            if doc is None:
                session.commit()
                return False
            kb = session.get(KnowledgeBase, kb_id)
            if kb is not None:
                kb.doc_num = max(kb.doc_num - 1, 0)
            session.delete(doc)  # segments 由 delete-orphan 级联删除
            session.commit()
        return True

    def list_documents(self, kb_id: UUID, page: int, page_size: int) -> Tuple[List[KnowledgeDocument], int]:
        # deleting 文档视同已删除：列表与计数都不返回（详情接口仍可查到）
        offset = (page - 1) * page_size
        with Session(self.engine, expire_on_commit=False) as session:
            total = session.exec(
                select(func.count())
                .select_from(KnowledgeDocument)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
                .where(col(KnowledgeDocument.status) != KnowledgeStatus.DELETING)
            ).one()
            result = session.exec(
                select(KnowledgeDocument)
                .where(col(KnowledgeDocument.kb_id) == kb_id)
                .where(col(KnowledgeDocument.status) != KnowledgeStatus.DELETING)
                .order_by(col(KnowledgeDocument.weight).desc(), col(KnowledgeDocument.created_at).desc())
                .offset(offset)
                .limit(page_size)
            ).all()
            session.commit()
        return list(result), int(total)

    # ---------- 文档分段 ----------

    def list_enabled_doc_ids(self, kb_ids: List[UUID]) -> List[UUID]:
        # 检索召回前的可用文档集：仅 enabled 文档的分段可被召回
        if not kb_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            ids = session.exec(
                select(col(KnowledgeDocument.id))
                .where(col(KnowledgeDocument.kb_id).in_(kb_ids))
                .where(col(KnowledgeDocument.status) == KnowledgeStatus.ENABLED)
            ).all()
            session.commit()
        return list(ids)

    def list_documents_by_ids(self, doc_ids: List[UUID]) -> List[KnowledgeDocument]:
        # 检索命中后的溯源信息组装（文档名等），去重后的 id 批量取回
        if not doc_ids:
            return []
        with Session(self.engine, expire_on_commit=False) as session:
            rows = session.exec(
                select(KnowledgeDocument).where(col(KnowledgeDocument.id).in_(doc_ids))
            ).all()
            session.commit()
        return list(rows)

    def replace_segments(self, doc_id: UUID, segments: List[DocumentSegment]) -> List[DocumentSegment]:
        """整体替换文档分段：删旧段 + 批量插入 + seg_num 同事务维护。

        重试/重解析的幂等入口——调用方已先行清理旧向量（segment.id 即向量
        库对象 UUID），本方法保证行与计数一致。segments 需携带完整字段
       （id/position/content/word_count/meta/状态）。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            session.exec(delete(DocumentSegment).where(col(DocumentSegment.doc_id) == doc_id))
            for segment in segments:
                session.add(segment)
            doc = session.get(KnowledgeDocument, doc_id)
            if doc is not None:
                doc.seg_num = len(segments)
            session.commit()
        return segments

    def complete_document(self, doc_id: UUID) -> bool:
        """入库收尾：doc 与全部分段置 ready（同一事务，避免中间态可见）。

        同时把库从 pending 提升为 ready（状态机的启用闸门要求 ready）：
        与 doc_num 维护同属本仓库有意的跨聚合例外；仅从 pending 提升，
        enabled/disabled/deleting 状态不受影响。
        """
        with Session(self.engine, expire_on_commit=False) as session:
            doc = session.get(KnowledgeDocument, doc_id)
            if doc is None:
                session.commit()
                return False
            doc.status = KnowledgeStatus.READY
            session.exec(
                update(DocumentSegment)
                .where(col(DocumentSegment.doc_id) == doc_id)
                .values(status=KnowledgeStatus.READY)
            )
            kb = session.get(KnowledgeBase, doc.kb_id)
            if kb is not None and kb.status == KnowledgeStatus.PENDING:
                kb.status = KnowledgeStatus.READY
            session.commit()
        return True

    def list_segment_ids_by_doc(self, doc_id: UUID) -> List[UUID]:
        # 删除文档/重解析前收集分段 id，供上层清理向量库对象
        with Session(self.engine, expire_on_commit=False) as session:
            ids = session.exec(
                select(col(DocumentSegment.id)).where(col(DocumentSegment.doc_id) == doc_id)
            ).all()
            session.commit()
        return list(ids)
