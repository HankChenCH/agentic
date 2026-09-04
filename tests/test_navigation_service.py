"""定位读取编排：可见性/启用守卫、精确单点读取（含越界引导）、文档清单。"""

from uuid import uuid4

from sqlmodel import Session

from app.components.knowledge.ability.navigation import KnowledgeNavigationService
from app.core.logging import LoggerFactory
from app.models.domain.knowledge import (
    DocumentSegment,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)
from app.adapters.persistence.knowledge_base_repository import KnowledgeBaseRepository
from app.adapters.persistence.knowledge_document_repository import KnowledgeDocumentRepository
from tests.conftest import OTHER_USER_ID, TEST_USER_ID


def make_kb(
    engine,
    name,
    user_id=TEST_USER_ID,
    is_public=False,
    status=KnowledgeStatus.ENABLED,
):
    with Session(engine) as session:
        kb = KnowledgeBase(
            user_id=user_id,
            name=name,
            is_public=is_public,
            status=status,
            embedding_model="ollama-embedding",
        )
        session.add(kb)
        session.commit()
        session.refresh(kb)
        return kb.id


def make_doc(engine, kb_id, name, status=KnowledgeStatus.ENABLED):
    doc = KnowledgeDocument(
        kb_id=kb_id, doc_path=f"knowledge/{kb_id}/{name}.pdf", name=name, status=status
    )
    return KnowledgeDocumentRepository(engine=engine).create_document(doc).id


def make_segments(engine, doc_id, kb_id, contents):
    """按顺序种分段（position = 枚举序，word_count = len(content)）。"""
    with Session(engine) as session:
        for position, content in enumerate(contents):
            session.add(
                DocumentSegment(
                    id=uuid4(), kb_id=kb_id, doc_id=doc_id, position=position,
                    content=content, word_count=len(content), status=KnowledgeStatus.READY,
                )
            )
        doc = session.get(KnowledgeDocument, doc_id)
        doc.seg_num = len(contents)
        session.commit()


def make_service(engine):
    return KnowledgeNavigationService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        logger_factory=LoggerFactory(),
    )


def seed_doc(engine, name="产品手册", contents=("一、安装。", "二、配置。", "三、使用。", "四、维护。"), **kb_kwargs):
    kb = make_kb(engine, name + "-kb", **kb_kwargs)
    doc = make_doc(engine, kb, name)
    make_segments(engine, doc, kb, contents)
    return kb, doc


# ---------- 守卫口径（可见性 + enabled，与检索一致） ----------


def test_context_rejects_invisible_and_disabled(engine):
    other_kb, other_doc = seed_doc(engine, name="他人文档", user_id=OTHER_USER_ID)
    ready_kb, ready_doc = seed_doc(engine, name="未启用")
    with Session(engine) as session:
        session.get(KnowledgeBase, ready_kb).status = KnowledgeStatus.READY
        session.commit()
    service = make_service(engine)

    assert service.segment_at(TEST_USER_ID, other_doc, 0) is None  # 他人私有库
    assert service.segment_at(TEST_USER_ID, ready_doc, 0) is None  # 库未启用
    assert service.segment_at(TEST_USER_ID, uuid4(), 0) is None  # 幻觉 doc_id


def test_context_rejects_disabled_document(engine):
    kb, doc = seed_doc(engine)
    with Session(engine) as session:
        session.get(KnowledgeDocument, doc).status = KnowledgeStatus.DISABLED
        session.commit()
    assert make_service(engine).segment_at(TEST_USER_ID, doc, 0) is None


# ---------- 精确单点读取（knowledge_context） ----------


def test_segment_at_returns_exact_segment(engine):
    _kb, doc = seed_doc(engine)
    service = make_service(engine)

    window = service.segment_at(TEST_USER_ID, doc, 1)
    assert window.seg_total == 4
    assert window.start == 1 and window.end == 1  # 单点：区间即该 position
    assert [seg.position for seg in window.segments] == [1]
    assert window.segments[0].content == "二、配置。"
    assert window.notes == []

    # 首/末段同样可读（无邻域泛化）
    assert [seg.position for seg in service.segment_at(TEST_USER_ID, doc, 0).segments] == [0]
    assert [seg.position for seg in service.segment_at(TEST_USER_ID, doc, 3).segments] == [3]


def test_segment_at_position_out_of_range_noted(engine):
    _kb, doc = seed_doc(engine)
    window = make_service(engine).segment_at(TEST_USER_ID, doc, 10)
    assert window.segments == []  # 空窗口，不猜近似
    assert any("超出" in note for note in window.notes)


# ---------- 文档清单（knowledge_document_list） ----------


def test_list_documents_visible_enabled_only(engine):
    kb = make_kb(engine, "kb1")
    make_doc(engine, kb, "启用文档")
    make_doc(engine, kb, "停用文档", status=KnowledgeStatus.DISABLED)
    service = make_service(engine)

    docs = service.list_documents(TEST_USER_ID, kb)
    # 与管理侧列表同口径：仅 deleting 视同已删除，停用文档仍在清单中
    # （清单是导航入口，停用文档可见但不可读——read/context 会拦）
    assert sorted(doc.name for doc in docs) == ["停用文档", "启用文档"]

    other_kb, _ = seed_doc(engine, name="他人库", user_id=OTHER_USER_ID)
    assert service.list_documents(TEST_USER_ID, other_kb) is None  # 他人私有库不可见


def test_list_documents_requires_enabled_kb(engine):
    kb, _doc = seed_doc(engine, name="未启用库")
    with Session(engine) as session:
        session.get(KnowledgeBase, kb).status = KnowledgeStatus.READY
        session.commit()
    assert make_service(engine).list_documents(TEST_USER_ID, kb) is None
