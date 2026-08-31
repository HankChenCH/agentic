"""定位读取编排：可见性/启用守卫、邻域窗口边界、读文预算截断、文档清单。"""

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
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
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

    assert service.segment_context(TEST_USER_ID, other_doc, 0) is None  # 他人私有库
    assert service.segment_context(TEST_USER_ID, ready_doc, 0) is None  # 库未启用
    assert service.segment_context(TEST_USER_ID, uuid4(), 0) is None  # 幻觉 doc_id


def test_context_rejects_disabled_document(engine):
    kb, doc = seed_doc(engine)
    with Session(engine) as session:
        session.get(KnowledgeDocument, doc).status = KnowledgeStatus.DISABLED
        session.commit()
    assert make_service(engine).segment_context(TEST_USER_ID, doc, 0) is None


# ---------- 邻域窗口（knowledge_context） ----------


def test_context_returns_ordered_window(engine):
    _kb, doc = seed_doc(engine)
    window = make_service(engine).segment_context(TEST_USER_ID, doc, 1, before=1, after=1)

    assert window.seg_total == 4
    assert window.start == 0 and window.end == 2  # 两端被文档边界收拢
    assert [seg.position for seg in window.segments] == [0, 1, 2]
    assert window.segments[1].content == "二、配置。"
    assert window.notes == []


def test_context_clamps_span_and_edge(engine):
    _kb, doc = seed_doc(engine)
    service = make_service(engine)

    # before/after 钳制 ≤3；末段邻域向内收
    window = service.segment_context(TEST_USER_ID, doc, 3, before=9, after=9)
    assert window.start == 0 and window.end == 3
    assert [seg.position for seg in window.segments] == [0, 1, 2, 3]


def test_context_position_out_of_range_noted(engine):
    _kb, doc = seed_doc(engine)
    window = make_service(engine).segment_context(TEST_USER_ID, doc, 10)
    assert window.segments == []  # 空窗口，不猜近似
    assert any("超出" in note for note in window.notes)


# ---------- 范围读文（knowledge_document_read）与预算截断 ----------


def test_read_document_full_and_range(engine):
    _kb, doc = seed_doc(engine)
    service = make_service(engine)

    whole = service.read_document(TEST_USER_ID, doc)
    assert [seg.position for seg in whole.segments] == [0, 1, 2, 3]
    assert whole.end == 3

    tail = service.read_document(TEST_USER_ID, doc, start=2, end=3)
    assert [seg.position for seg in tail.segments] == [2, 3]


def test_read_document_truncates_by_segment_budget(engine, monkeypatch):
    import app.components.knowledge.ability.navigation as navigation

    monkeypatch.setattr(navigation, "_READ_MAX_SEGMENTS", 2)
    kb, doc = seed_doc(engine)
    window = navigation.KnowledgeNavigationService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        logger_factory=LoggerFactory(),
    ).read_document(TEST_USER_ID, doc)

    assert [seg.position for seg in window.segments] == [0, 1]
    assert window.end == 1  # 实际返回区间收窄
    assert any("续读" in note and "2" in note for note in window.notes)  # 指引从 position 2 续读


def test_read_document_truncates_by_char_budget(engine, monkeypatch):
    import app.components.knowledge.ability.navigation as navigation

    monkeypatch.setattr(navigation, "_READ_MAX_CHARS", 12)  # 每段 5 字符：只装得下 2 段
    kb, doc = seed_doc(engine)
    window = navigation.KnowledgeNavigationService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        logger_factory=LoggerFactory(),
    ).read_document(TEST_USER_ID, doc)

    assert [seg.position for seg in window.segments] == [0, 1]
    assert any("字符" in note for note in window.notes)


def test_read_document_start_out_of_range(engine):
    _kb, doc = seed_doc(engine)
    window = make_service(engine).read_document(TEST_USER_ID, doc, start=99)
    assert window.segments == []
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
