"""入库收尾的状态推进：doc/segments 置 ready，KB 从 pending 提升 ready。"""

from sqlmodel import Session, col, select

from app.models.domain.knowledge import (
    DocumentSegment,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)
from app.adapters.persistence.knowledge_document_repository import KnowledgeDocumentRepository
from tests.conftest import TEST_USER_ID


def make_kb_and_doc(engine, kb_status=KnowledgeStatus.PENDING):
    with Session(engine) as session:
        kb = KnowledgeBase(
            user_id=TEST_USER_ID, name="kb", embedding_model="ollama-embedding", status=kb_status
        )
        session.add(kb)
        session.commit()
        session.refresh(kb)
        doc = KnowledgeDocument(
            kb_id=kb.id, doc_path="knowledge/x/y.pdf", name="y.pdf",
            status=KnowledgeStatus.PROCESSING,
        )
        session.add(doc)
        session.add(DocumentSegment(
            kb_id=kb.id, doc_id=doc.id, position=0, content="正文",
            status=KnowledgeStatus.PROCESSING,
        ))
        session.commit()
        session.refresh(doc)
        return kb.id, doc.id


def get_kb_status(engine, kb_id):
    with Session(engine) as session:
        kb = session.get(KnowledgeBase, kb_id)
        session.commit()
        return kb.status


def test_complete_document_promotes_doc_segments_and_pending_kb(engine):
    repo = KnowledgeDocumentRepository(engine=engine)
    kb_id, doc_id = make_kb_and_doc(engine)

    assert repo.complete_document(doc_id) is True
    assert get_kb_status(engine, kb_id) == KnowledgeStatus.READY

    with Session(engine) as session:
        doc = session.get(KnowledgeDocument, doc_id)
        seg = session.exec(
            select(DocumentSegment).where(col(DocumentSegment.doc_id) == doc_id)
        ).first()
        doc_status, seg_status = doc.status, seg.status
        session.commit()
    assert doc_status == KnowledgeStatus.READY
    assert seg_status == KnowledgeStatus.READY


def test_complete_document_keeps_non_pending_kb_status(engine):
    # enabled 的库新文档入库完成：不降级、不变更
    repo = KnowledgeDocumentRepository(engine=engine)
    kb_id, doc_id = make_kb_and_doc(engine, kb_status=KnowledgeStatus.ENABLED)

    repo.complete_document(doc_id)
    assert get_kb_status(engine, kb_id) == KnowledgeStatus.ENABLED
