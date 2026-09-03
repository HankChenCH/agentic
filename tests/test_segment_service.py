"""分段管理：仓储（分页/搜索/seg_num 维护/重分段状态闸门）与服务（守卫、
状态闸门、向量同步——编辑/新增/删除必须同步维护向量库对象）。"""

from uuid import UUID, uuid4

import pytest
from sqlmodel import Session

from app.core.logging import LoggerFactory
from app.exceptions import (
    KnowledgeDocumentNotFoundError,
    KnowledgeDocumentStatusError,
    KnowledgeNotFoundError,
    KnowledgeSegmentNotFoundError,
    KnowledgeSegmentStateError,
)
from app.models.domain.knowledge import (
    DocumentSegment,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)
from app.models.schema.request.knowledge import (
    KnowledgeSegmentCreateRequest,
    KnowledgeSegmentUpdateRequest,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.services.domain.knowledge.segment_service import KnowledgeSegmentService
from tests.conftest import OTHER_USER_ID, TEST_USER_ID


class RecordingVectorIndex:
    """向量索引桩：记录增删调用顺序（编辑 = 先删后写，同 UUID 原地替换）。"""

    def __init__(self):
        self.events = []  # ("add" | "delete", kb_id, [str ids])

    def add_segments(self, kb_id, segments):
        self.events.append(("add", kb_id, [str(s.id) for s in segments]))

    def delete_segments(self, kb_id, segment_ids):
        self.events.append(("delete", kb_id, [str(i) for i in segment_ids]))


def make_kb(engine, user_id=TEST_USER_ID, is_public=False) -> UUID:
    with Session(engine) as session:
        kb = KnowledgeBase(
            user_id=user_id,
            name=f"kb-{uuid4()}",
            is_public=is_public,
            embedding_model="ollama-embedding",
            status=KnowledgeStatus.ENABLED,
        )
        session.add(kb)
        session.commit()
        session.refresh(kb)
        return kb.id


def make_doc(engine, kb_id, status=KnowledgeStatus.READY) -> KnowledgeDocument:
    doc = KnowledgeDocument(
        kb_id=kb_id, doc_path=f"knowledge/{kb_id}/{uuid4()}.pdf", name="文档.pdf", status=status
    )
    return KnowledgeDocumentRepository(engine=engine).create_document(doc)


def make_segments(engine, kb_id, doc_id, contents: dict, status=KnowledgeStatus.READY):
    """按 {position: content} 落库分段（管理侧读写的数据源）；seg_num 基线同步对齐。"""
    with Session(engine) as session:
        for position, content in contents.items():
            session.add(DocumentSegment(
                kb_id=kb_id,
                doc_id=doc_id,
                position=position,
                content=content,
                word_count=len(content),
                status=status,
            ))
        doc = session.get(KnowledgeDocument, doc_id)
        if doc is not None:
            doc.seg_num += len(contents)
        session.commit()


def make_service(engine, vector=None) -> tuple[KnowledgeSegmentService, RecordingVectorIndex]:
    vector = vector or RecordingVectorIndex()
    service = KnowledgeSegmentService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        vector_index=vector,
        logger_factory=LoggerFactory(),
    )
    return service, vector


# ---------- 仓储：分页/搜索/计数 ----------


def test_list_segments_paginated_and_keyword_filtered(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id)
    make_segments(engine, kb_id, doc.id, {i: f"第{i}段内容" for i in range(5)})
    repo = KnowledgeDocumentRepository(engine=engine)

    rows, total = repo.list_segments(doc.id, page=1, page_size=3)
    assert total == 5
    assert [r.position for r in rows] == [0, 1, 2]

    rows, total = repo.list_segments(doc.id, page=2, page_size=3)
    assert [r.position for r in rows] == [3, 4]

    # keyword 过滤作用于计数与列表两端（ilike 在 SQLite 退化为 lower+LIKE）
    rows, total = repo.list_segments(doc.id, page=1, page_size=10, keyword="3段")
    assert total == 1
    assert [r.position for r in rows] == [3]

    _, total = repo.list_segments(doc.id, page=1, page_size=10, keyword="不存在")
    assert total == 0


def test_append_and_delete_segment_maintain_seg_num(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id)
    make_segments(engine, kb_id, doc.id, {0: "一", 1: "二"})
    repo = KnowledgeDocumentRepository(engine=engine)

    appended = repo.append_segment(doc.id, DocumentSegment(
        kb_id=kb_id, doc_id=doc.id, position=repo.next_segment_position(doc.id),
        content="三", word_count=1, status=KnowledgeStatus.READY,
    ))
    assert appended.position == 2
    assert repo.get_document(kb_id, doc.id).seg_num == 3

    assert repo.delete_segment(doc.id, appended.id) is True
    assert repo.get_document(kb_id, doc.id).seg_num == 2
    assert repo.get_segment(doc.id, appended.id) is None
    # 重复删除幂等返回 False
    assert repo.delete_segment(doc.id, appended.id) is False


def test_reset_document_for_rechunk_state_matrix(engine):
    kb_id = make_kb(engine)
    repo = KnowledgeDocumentRepository(engine=engine)

    for status, expected in [
        (KnowledgeStatus.READY, KnowledgeStatus.PENDING),
        (KnowledgeStatus.ENABLED, KnowledgeStatus.PENDING),
        (KnowledgeStatus.DISABLED, KnowledgeStatus.PENDING),
        (KnowledgeStatus.PROCESSING, None),
        (KnowledgeStatus.DELETING, None),
        (KnowledgeStatus.FAILED, None),
        (KnowledgeStatus.PENDING, None),
    ]:
        doc = make_doc(engine, kb_id, status=status)
        reset = repo.reset_document_for_rechunk(kb_id, doc.id)
        if expected is None:
            assert reset is None
            assert repo.get_document(kb_id, doc.id).status == status
        else:
            assert reset.status == expected
            assert reset.error_message is None


# ---------- 服务：守卫与状态闸门 ----------


def test_list_segments_visible_but_not_writable_for_others(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id)
    make_segments(engine, kb_id, doc.id, {0: "内容"})
    service, _ = make_service(engine)

    # 他人私有库：读不可见（不泄露存在性），写同样拒绝
    with pytest.raises(KnowledgeNotFoundError):
        service.list_segments(kb_id, OTHER_USER_ID, doc.id, 1, 10)
    with pytest.raises(KnowledgeNotFoundError):
        service.create_segment(kb_id, OTHER_USER_ID, doc.id, KnowledgeSegmentCreateRequest(content="x"))

    public_kb = make_kb(engine, user_id=TEST_USER_ID, is_public=True)
    public_doc = make_doc(engine, public_kb)
    make_segments(engine, public_kb, public_doc.id, {0: "内容"})
    # 公开库可读…
    envelope = service.list_segments(public_kb, OTHER_USER_ID, public_doc.id, 1, 10)
    assert envelope["total"] == 1
    # …但不可写（公开只让渡可见性，不让渡管理权）
    with pytest.raises(KnowledgeNotFoundError):
        service.create_segment(public_kb, OTHER_USER_ID, public_doc.id, KnowledgeSegmentCreateRequest(content="x"))


def test_segment_write_rejected_while_document_processing(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id, status=KnowledgeStatus.PROCESSING)
    service, vector = make_service(engine)

    with pytest.raises(KnowledgeSegmentStateError):
        service.create_segment(kb_id, TEST_USER_ID, doc.id, KnowledgeSegmentCreateRequest(content="x"))
    with pytest.raises(KnowledgeSegmentStateError):
        service.set_segment_enabled(kb_id, TEST_USER_ID, doc.id, uuid4(), enabled=False)
    assert vector.events == []


def test_segment_operations_require_segment_existence(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id)
    service, _ = make_service(engine)

    with pytest.raises(KnowledgeSegmentNotFoundError):
        service.describe_segment(kb_id, TEST_USER_ID, doc.id, uuid4())
    with pytest.raises(KnowledgeDocumentNotFoundError):
        service.describe_segment(kb_id, TEST_USER_ID, uuid4(), uuid4())


# ---------- 服务：写操作与向量同步 ----------


def test_create_segment_appends_with_next_position_and_vector(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id)
    make_segments(engine, kb_id, doc.id, {0: "一", 5: "六"})
    service, vector = make_service(engine)

    segment = service.create_segment(
        kb_id, TEST_USER_ID, doc.id, KnowledgeSegmentCreateRequest(content="手动补充内容")
    )
    assert segment.position == 6  # 现有最大 position +1（追加，不中间插入）
    assert segment.status == KnowledgeStatus.READY
    assert segment.word_count == 6
    assert vector.events == [("add", kb_id, [str(segment.id)])]

    rows, total = KnowledgeDocumentRepository(engine=engine).list_segments(doc.id, 1, 10)
    assert total == 3
    assert [r.position for r in rows] == [0, 5, 6]


def test_update_segment_content_rewrites_vector_in_place(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id)
    make_segments(engine, kb_id, doc.id, {0: "旧内容"})
    service, vector = make_service(engine)
    repo = KnowledgeDocumentRepository(engine=engine)
    rows, _ = repo.list_segments(doc.id, 1, 10)
    segment = rows[0]

    updated = service.update_segment_content(
        kb_id, TEST_USER_ID, doc.id, segment.id, KnowledgeSegmentUpdateRequest(content="新内容更长")
    )
    assert updated.content == "新内容更长"
    assert updated.word_count == 5
    # 同 UUID 先删后写：行与向量保持一一对应
    assert vector.events == [
        ("delete", kb_id, [str(segment.id)]),
        ("add", kb_id, [str(segment.id)]),
    ]


def test_set_segment_enabled_toggles_without_vector_touch(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id)
    make_segments(engine, kb_id, doc.id, {0: "内容"})
    service, vector = make_service(engine)
    rows, _ = KnowledgeDocumentRepository(engine=engine).list_segments(doc.id, 1, 10)
    segment = rows[0]

    disabled = service.set_segment_enabled(kb_id, TEST_USER_ID, doc.id, segment.id, enabled=False)
    assert disabled.status == KnowledgeStatus.DISABLED
    enabled = service.set_segment_enabled(kb_id, TEST_USER_ID, doc.id, segment.id, enabled=True)
    assert enabled.status == KnowledgeStatus.READY
    assert vector.events == []  # 启停纯行级状态，不触发向量写


def test_delete_segment_removes_row_and_vector(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id)
    make_segments(engine, kb_id, doc.id, {0: "一", 1: "二"})
    service, vector = make_service(engine)
    repo = KnowledgeDocumentRepository(engine=engine)
    rows, _ = repo.list_segments(doc.id, 1, 10)
    segment = rows[0]

    snapshot = service.delete_segment(kb_id, TEST_USER_ID, doc.id, segment.id)
    assert snapshot.id == segment.id
    assert repo.get_segment(doc.id, segment.id) is None
    assert repo.get_document(kb_id, doc.id).seg_num == 1
    assert vector.events == [("delete", kb_id, [str(segment.id)])]


def test_rechunk_resets_stable_document(engine):
    kb_id = make_kb(engine)
    doc = make_doc(engine, kb_id, status=KnowledgeStatus.ENABLED)
    service, _ = make_service(engine)

    reset = service.rechunk_document(kb_id, TEST_USER_ID, doc.id)
    assert reset.status == KnowledgeStatus.PENDING

    processing_doc = make_doc(engine, kb_id, status=KnowledgeStatus.PROCESSING)
    with pytest.raises(KnowledgeDocumentStatusError):
        service.rechunk_document(kb_id, TEST_USER_ID, processing_doc.id)
