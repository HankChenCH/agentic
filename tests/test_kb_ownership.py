"""知识库归属与可见性：属主/公开的读写分界与每用户名称唯一。

读路径（详情/列表/文档读）属主或公开库可见；写路径（更新/删除/启停/上传/
文档写）仅属主可操作——他人资源一律 KnowledgeNotFoundError（404）不泄露
存在性，与会话归属同口径。
"""

import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.core.logging import LoggerFactory
from app.exceptions import (
    KnowledgeDocumentNotFoundError,
    KnowledgeNameDuplicatedError,
    KnowledgeNotFoundError,
)
from app.models.domain.knowledge import KnowledgeBase, KnowledgeStatus
from app.models.schema.request.knowledge import (
    KnowledgeBaseCreateRequest,
    KnowledgeBaseUpdateRequest,
)
from app.adapters.persistence.knowledge_base_repository import KnowledgeBaseRepository
from app.adapters.persistence.knowledge_document_repository import KnowledgeDocumentRepository
from app.domain.knowledge.document_service import KnowledgeDocumentService
from app.domain.knowledge.kb_service import KnowledgeBaseService
from app.domain.knowledge.object_store import KnowledgeObjectStore
from app.adapters.vector.knowledge_index import KnowledgeVectorIndex
from tests.conftest import OTHER_USER_ID, TEST_USER_ID
from tests.test_document_service import MemoryFilesystem, PDF_BYTES

EMBEDDING = "ollama-embedding"


class StubVectorIndex(KnowledgeVectorIndex):
    """知识库服务只用它 drop 整库 collection，其余能力不在测试面内。"""

    def __init__(self):
        self.dropped = []

    def drop_collection(self, kb_id):
        self.dropped.append(kb_id)


def make_kb(
    engine,
    name,
    user_id=TEST_USER_ID,
    is_public=False,
    status=KnowledgeStatus.ENABLED,
) -> KnowledgeBase:
    with Session(engine) as session:
        kb = KnowledgeBase(
            user_id=user_id,
            name=name,
            embedding_model=EMBEDDING,
            is_public=is_public,
            status=status,
        )
        session.add(kb)
        session.commit()
        session.refresh(kb)
        return kb


def make_kb_service(engine) -> KnowledgeBaseService:
    return KnowledgeBaseService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        vector_index=StubVectorIndex(),
        object_store=KnowledgeObjectStore(
            filesystem=MemoryFilesystem(), logger_factory=LoggerFactory()
        ),
        app_config=SimpleNamespace(vector_db=SimpleNamespace(embedding=EMBEDDING)),
        logger_factory=LoggerFactory(),
    )


def make_doc_service(engine) -> KnowledgeDocumentService:
    return KnowledgeDocumentService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        vector_index=StubVectorIndex(),
        object_store=KnowledgeObjectStore(
            filesystem=MemoryFilesystem(), logger_factory=LoggerFactory()
        ),
        logger_factory=LoggerFactory(),
    )


def _upload(service, kb_id, user_id=TEST_USER_ID):
    return service.create_document(
        kb_id, user_id, filename="a.pdf", content_type="application/pdf",
        stream=io.BytesIO(PDF_BYTES),
    )


# ---------- 创建：归属落库 + 每用户重名 ----------


def test_create_knowledge_sets_owner_and_visibility(engine):
    service = make_kb_service(engine)
    kb = service.create_knowledge(
        KnowledgeBaseCreateRequest(name="指南", isPublic=True), TEST_USER_ID
    )
    assert kb.user_id == TEST_USER_ID
    assert kb.is_public is True

    private = service.create_knowledge(KnowledgeBaseCreateRequest(name="私有库"), TEST_USER_ID)
    assert private.is_public is False


def test_create_knowledge_name_unique_per_user(engine):
    service = make_kb_service(engine)
    service.create_knowledge(KnowledgeBaseCreateRequest(name="重名库"), TEST_USER_ID)
    with pytest.raises(KnowledgeNameDuplicatedError):
        service.create_knowledge(KnowledgeBaseCreateRequest(name="重名库"), TEST_USER_ID)
    # 不同属主可同名
    other = service.create_knowledge(KnowledgeBaseCreateRequest(name="重名库"), OTHER_USER_ID)
    assert other.user_id == OTHER_USER_ID


# ---------- 列表：属主或公开可见 ----------


def test_list_knowledge_scoped_to_owner_plus_public(engine):
    service = make_kb_service(engine)
    make_kb(engine, "我的私有", user_id=TEST_USER_ID, is_public=False)
    make_kb(engine, "我的公开", user_id=TEST_USER_ID, is_public=True)
    make_kb(engine, "他人私有", user_id=OTHER_USER_ID, is_public=False)
    make_kb(engine, "他人公开", user_id=OTHER_USER_ID, is_public=True)

    mine = service.list_knowledge(1, 12, TEST_USER_ID)
    # 自己的全部 + 他人的公开库；他人的私有库不可见
    assert sorted(item.name for item in mine["items"]) == ["他人公开", "我的公开", "我的私有"]
    assert mine["total"] == 3

    theirs = service.list_knowledge(1, 12, OTHER_USER_ID)
    assert sorted(item.name for item in theirs["items"]) == ["他人公开", "他人私有", "我的公开"]
    assert theirs["total"] == 3


def test_list_visible_kbs_non_paged(engine):
    # 检索组件用的非分页口径：与 list_kbs 同一可见性谓词，无身份时仅公开库
    repo = KnowledgeBaseRepository(engine=engine)
    make_kb(engine, "我的私有", user_id=TEST_USER_ID, is_public=False)
    make_kb(engine, "我的公开", user_id=TEST_USER_ID, is_public=True)
    make_kb(engine, "他人私有", user_id=OTHER_USER_ID, is_public=False)
    make_kb(engine, "他人公开", user_id=OTHER_USER_ID, is_public=True)

    assert sorted(kb.name for kb in repo.list_visible_kbs(TEST_USER_ID)) == [
        "他人公开", "我的公开", "我的私有",
    ]
    assert sorted(kb.name for kb in repo.list_visible_kbs(None)) == ["他人公开", "我的公开"]


# ---------- 详情：公开库他人可读，私有库他人 404 ----------


def test_describe_public_kb_visible_to_others(engine):
    service = make_kb_service(engine)
    kb = make_kb(engine, "公开库", user_id=TEST_USER_ID, is_public=True)
    assert service.describe_knowledge(kb.id, OTHER_USER_ID).id == kb.id


def test_describe_private_kb_hidden_from_others(engine):
    service = make_kb_service(engine)
    kb = make_kb(engine, "私有库", user_id=TEST_USER_ID, is_public=False)
    with pytest.raises(KnowledgeNotFoundError):
        service.describe_knowledge(kb.id, OTHER_USER_ID)
    # 与「库可见但文档不存在」区分：可见库下未知文档是 4004 文档级 404
    with pytest.raises(KnowledgeNotFoundError):
        service.describe_knowledge(uuid4(), OTHER_USER_ID)


# ---------- 写路径：仅属主，公开不让渡管理权 ----------


@pytest.mark.parametrize("op", ["update", "delete", "enable", "disable"])
def test_write_ops_owner_only(engine, op):
    service = make_kb_service(engine)
    kb = make_kb(engine, "公开库", user_id=TEST_USER_ID, is_public=True)
    with pytest.raises(KnowledgeNotFoundError):
        if op == "update":
            service.update_knowledge(kb.id, KnowledgeBaseUpdateRequest(name="改名"), OTHER_USER_ID)
        elif op == "delete":
            service.delete_knowledge(kb.id, OTHER_USER_ID)
        elif op == "enable":
            service.set_knowledge_enabled(kb.id, enabled=True, user_id=OTHER_USER_ID)
        else:
            service.set_knowledge_enabled(kb.id, enabled=False, user_id=OTHER_USER_ID)


def test_delete_knowledge_as_owner(engine):
    service = make_kb_service(engine)
    kb = make_kb(engine, "待删库", user_id=TEST_USER_ID)
    snapshot = service.delete_knowledge(kb.id, TEST_USER_ID)
    assert snapshot.status == KnowledgeStatus.DELETING
    assert service.kb_repo.get_kb(kb.id) is None


def test_update_visibility_toggle_and_rename(engine):
    service = make_kb_service(engine)
    kb = make_kb(engine, "旧名", user_id=TEST_USER_ID, is_public=False)
    updated = service.update_knowledge(
        kb.id, KnowledgeBaseUpdateRequest(name="新名", isPublic=True), TEST_USER_ID
    )
    assert updated.name == "新名"
    assert updated.is_public is True


def test_rename_checks_dup_against_owner_scope(engine):
    # 他人已有同名库：改名不受影响（唯一性按属主作用域）
    make_kb(engine, "同名", user_id=OTHER_USER_ID)
    service = make_kb_service(engine)
    kb = make_kb(engine, "原名", user_id=TEST_USER_ID)
    updated = service.update_knowledge(kb.id, KnowledgeBaseUpdateRequest(name="同名"), TEST_USER_ID)
    assert updated.name == "同名"


# ---------- 文档子资源：读可见、写归属 ----------


def test_document_read_follows_kb_visibility(engine):
    doc_service = make_doc_service(engine)
    public_kb = make_kb(engine, "他人公开库", user_id=OTHER_USER_ID, is_public=True)
    private_kb = make_kb(engine, "他人私有库", user_id=OTHER_USER_ID, is_public=False)

    doc = _upload(doc_service, public_kb.id, user_id=OTHER_USER_ID)

    # 公开库：非属主可读（文档详情/列表），且未知文档按 4004 文档级 404 区分
    assert doc_service.describe_document(public_kb.id, TEST_USER_ID, doc.id).id == doc.id
    listed = doc_service.list_documents(public_kb.id, TEST_USER_ID, 1, 20)
    assert listed["total"] == 1
    with pytest.raises(KnowledgeDocumentNotFoundError):
        doc_service.describe_document(public_kb.id, TEST_USER_ID, uuid4())
    # 私有库：非属主读库/读文档都是 4001 库级 404
    with pytest.raises(KnowledgeNotFoundError):
        doc_service.describe_document(private_kb.id, TEST_USER_ID, doc.id)


def test_document_write_owner_only(engine):
    doc_service = make_doc_service(engine)
    public_kb = make_kb(engine, "他人公开库", user_id=OTHER_USER_ID, is_public=True)
    doc = _upload(doc_service, public_kb.id, user_id=OTHER_USER_ID)

    with pytest.raises(KnowledgeNotFoundError):
        _upload(doc_service, public_kb.id, user_id=TEST_USER_ID)
    with pytest.raises(KnowledgeNotFoundError):
        doc_service.delete_document(public_kb.id, TEST_USER_ID, doc.id)
