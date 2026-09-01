"""claim 门闸（幂等重投的核心）与看门狗对账（reap）决策逻辑。

纯单测：临时 SQLite + 真实 KnowledgeDocumentRepository；service 侧仅
reap_stuck_documents 的决策逻辑（补发清单/终结），未触达的依赖用简单替身。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlmodel import Session

from app.models.domain.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeStatus
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.services.domain.knowledge.ingestion_service import DocumentIngestionService
from tests.conftest import TEST_USER_ID


def make_doc(engine, status: KnowledgeStatus, *, updated_at: datetime | None = None, reap_count: int = 0):
    with Session(engine) as session:
        kb = KnowledgeBase(
            user_id=TEST_USER_ID, name=f"kb-{uuid4().hex[:8]}", embedding_model="ollama-embedding"
        )
        session.add(kb)
        session.commit()
        session.refresh(kb)
        doc = KnowledgeDocument(
            kb_id=kb.id, doc_path="knowledge/x/y.pdf", name="y.pdf",
            status=status, error_message="旧错误" if status == KnowledgeStatus.FAILED else None,
            reap_count=reap_count,
        )
        if updated_at is not None:
            doc.updated_at = updated_at
        session.add(doc)
        session.commit()
        session.refresh(doc)
        return kb.id, doc.id


def get_doc(engine, doc_id) -> KnowledgeDocument:
    with Session(engine, expire_on_commit=False) as session:
        doc = session.get(KnowledgeDocument, doc_id)
        session.commit()
        return doc


# ---------- claim_document：幂等重投门闸 ----------


def test_claim_from_pending_and_failed_succeeds(engine):
    repo = KnowledgeDocumentRepository(engine=engine)
    for status in (KnowledgeStatus.PENDING, KnowledgeStatus.FAILED):
        kb_id, doc_id = make_doc(engine, status)
        claimed = repo.claim_document(kb_id, doc_id, stale_before=None)
        assert claimed is not None
        assert claimed.status == KnowledgeStatus.PROCESSING
        assert claimed.error_message is None


def test_claim_fresh_processing_rejected_regardless_of_threshold(engine):
    # 已在跑的处理中文档：没有 stale 阈值或阈值早于 updated_at 都不可接管（幂等跳过）
    repo = KnowledgeDocumentRepository(engine=engine)
    kb_id, doc_id = make_doc(engine, KnowledgeStatus.PROCESSING)

    assert repo.claim_document(kb_id, doc_id, stale_before=None) is None
    assert repo.claim_document(
        kb_id, doc_id, stale_before=datetime.now(timezone.utc) - timedelta(seconds=60)
    ) is None


def test_claim_stale_processing_takeover(engine):
    # 卡死处理中（updated_at 早于阈值）可被重投消息接管
    repo = KnowledgeDocumentRepository(engine=engine)
    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=1000)
    kb_id, doc_id = make_doc(engine, KnowledgeStatus.PROCESSING, updated_at=stale_ts)

    threshold = datetime.now(timezone.utc) - timedelta(seconds=660)
    claimed = repo.claim_document(kb_id, doc_id, stale_before=threshold)
    assert claimed is not None
    assert claimed.status == KnowledgeStatus.PROCESSING
    assert _naive_utc(claimed.updated_at) > _naive_utc(stale_ts)


def test_claim_completed_or_deleting_documents_rejected(engine):
    # 游离消息不得重刷已完成/删除中的文档
    repo = KnowledgeDocumentRepository(engine=engine)
    for status in (
        KnowledgeStatus.READY,
        KnowledgeStatus.ENABLED,
        KnowledgeStatus.DISABLED,
        KnowledgeStatus.DELETING,
    ):
        kb_id, doc_id = make_doc(engine, status)
        threshold = datetime.now(timezone.utc) - timedelta(seconds=660)
        assert repo.claim_document(kb_id, doc_id, stale_before=threshold) is None, status


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


# ---------- 看门狗：候选捞取 / 重投计数 / 对账决策 ----------


def test_list_reap_candidates_only_processing_and_pending(engine):
    repo = KnowledgeDocumentRepository(engine=engine)
    _, processing_id = make_doc(engine, KnowledgeStatus.PROCESSING)
    _, pending_id = make_doc(engine, KnowledgeStatus.PENDING)
    _, ready_id = make_doc(engine, KnowledgeStatus.READY)

    candidates = {doc.id for doc in repo.list_reap_candidates()}
    assert candidates == {processing_id, pending_id}
    assert ready_id not in candidates


def test_mark_reaped_counts_then_finalizes_at_limit(engine):
    repo = KnowledgeDocumentRepository(engine=engine)
    kb_id, doc_id = make_doc(engine, KnowledgeStatus.PROCESSING)

    assert repo.mark_reaped(doc_id, max_attempts=3) is True
    assert get_doc(engine, doc_id).reap_count == 1
    assert repo.mark_reaped(doc_id, max_attempts=3) is True
    assert repo.mark_reaped(doc_id, max_attempts=3) is True
    assert get_doc(engine, doc_id).reap_count == 3

    # 第 4 次达上限：终结为 failed + 原因，返回 False（不再重投）
    assert repo.mark_reaped(doc_id, max_attempts=3) is False
    final = get_doc(engine, doc_id)
    assert final.status == KnowledgeStatus.FAILED
    assert final.error_message and "manual retry" in final.error_message


def test_complete_document_resets_reap_count(engine):
    repo = KnowledgeDocumentRepository(engine=engine)
    kb_id, doc_id = make_doc(engine, KnowledgeStatus.PROCESSING, reap_count=2)

    assert repo.complete_document(doc_id) is True
    doc = get_doc(engine, doc_id)
    assert doc.status == KnowledgeStatus.READY
    assert doc.reap_count == 0


@dataclass
class _Stub:
    """reap 决策路径未触达的依赖占位。"""


def make_service(engine) -> DocumentIngestionService:
    return DocumentIngestionService(
        kb_repo=_Stub(),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        object_store=_Stub(),
        document_parser=_Stub(),
        vector_index=_Stub(),
        logger_factory=type("LF", (), {"get_logger": staticmethod(logging.getLogger)}),
    )


def test_reap_dispatches_stale_and_skips_fresh(engine):
    service = make_service(engine)
    _, stale_processing = make_doc(
        engine, KnowledgeStatus.PROCESSING,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=1000),
    )
    _, fresh_processing = make_doc(engine, KnowledgeStatus.PROCESSING)
    _, stale_pending = make_doc(
        engine, KnowledgeStatus.PENDING,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=2000),
    )
    _, fresh_pending = make_doc(engine, KnowledgeStatus.PENDING)

    now = datetime.now(timezone.utc)
    result = service.reap_stuck_documents(
        stale_processing=now - timedelta(seconds=660),
        stale_pending=now - timedelta(seconds=600),
        max_attempts=3,
    )

    dispatched = {doc_id for _, doc_id in result["dispatch"]}
    assert dispatched == {stale_processing, stale_pending}
    assert result["finalized"] == []


def test_reap_finalizes_poison_document_at_limit(engine):
    service = make_service(engine)
    kb_id, doc_id = make_doc(
        engine, KnowledgeStatus.PROCESSING,
        updated_at=datetime.now(timezone.utc) - timedelta(seconds=1000),
        reap_count=3,
    )

    now = datetime.now(timezone.utc)
    result = service.reap_stuck_documents(
        stale_processing=now - timedelta(seconds=660),
        stale_pending=now - timedelta(seconds=600),
        max_attempts=3,
    )

    # 已重投 3 次仍卡死：不再进入重投清单，就地终结为 failed
    assert result["dispatch"] == []
    assert result["finalized"] == [doc_id]
    assert get_doc(engine, doc_id).status == KnowledgeStatus.FAILED
