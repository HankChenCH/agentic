"""检索编排：可见性圈定、状态收敛、自选库子集、嵌入模型守卫与文档后滤。"""

from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlmodel import Session

from app.components.knowledge.ability.retrieval import KnowledgeRetrievalService
from app.services.domain.knowledge.vector_index import VectorHit
from app.core.logging import LoggerFactory
from app.models.domain.knowledge import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from tests.conftest import OTHER_USER_ID, TEST_USER_ID

EMBEDDING = "ollama-embedding"


class StubVectorIndex:
    """向量索引桩：记录扇出调用，返回预设命中。"""

    def __init__(self, hits=None):
        self.hits = hits or []
        self.calls = []

    def search_many(self, kb_ids, query, *, top_k=4, alpha=1.0):
        self.calls.append(([str(k) for k in kb_ids], top_k))
        return self.hits


def make_kb(
    engine,
    name,
    user_id=TEST_USER_ID,
    is_public=False,
    status=KnowledgeStatus.ENABLED,
    embedding_model=EMBEDDING,
):
    with Session(engine) as session:
        kb = KnowledgeBase(
            user_id=user_id,
            name=name,
            is_public=is_public,
            status=status,
            embedding_model=embedding_model,
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


def make_service(engine, vector, embedding=EMBEDDING):
    return KnowledgeRetrievalService(
        kb_repo=KnowledgeBaseRepository(engine=engine),
        document_repo=KnowledgeDocumentRepository(engine=engine),
        vector_index=vector,
        app_config=SimpleNamespace(vector_db=SimpleNamespace(embedding=embedding)),
        logger_factory=LoggerFactory(),
    )


def hit_for(doc_id: UUID, score=0.9, content="片段内容") -> VectorHit:
    return VectorHit(
        content=content, score=score, kb_id="kb", doc_id=str(doc_id), position=0,
        meta={"page_start": 1, "page_end": 1, "heading_path": ["H1"], "types": ["text"]},
    )


# ---------- 可见性圈定（检索范围 = 属主私有 + 公开） ----------


def test_visible_scope_owner_private_plus_public(engine):
    make_kb(engine, "我的私有")
    make_kb(engine, "我的公开", is_public=True)
    make_kb(engine, "他人私有", user_id=OTHER_USER_ID)
    make_kb(engine, "他人公开", user_id=OTHER_USER_ID, is_public=True)
    service = make_service(engine, StubVectorIndex())

    mine = [kb.name for kb in service.list_visible_knowledge(TEST_USER_ID)]
    assert sorted(mine) == ["他人公开", "我的公开", "我的私有"]

    theirs = [kb.name for kb in service.list_visible_knowledge(OTHER_USER_ID)]
    assert sorted(theirs) == ["他人公开", "他人私有", "我的公开"]

    # 无身份上下文（如单测直调）按匿名处理：只看公开库
    anonymous = [kb.name for kb in service.list_visible_knowledge(None)]
    assert sorted(anonymous) == ["他人公开", "我的公开"]


def test_deleting_kb_excluded_from_visible_scope(engine):
    make_kb(engine, "删除中", status=KnowledgeStatus.DELETING)
    make_kb(engine, "在用")
    service = make_service(engine, StubVectorIndex())

    assert [kb.name for kb in service.list_visible_knowledge(TEST_USER_ID)] == ["在用"]


def test_search_ignores_invisible_private_kb(engine):
    make_kb(engine, "他人私有", user_id=OTHER_USER_ID)
    vector = StubVectorIndex()
    service = make_service(engine, vector)

    hits, notes = service.search_for_user(TEST_USER_ID, "问题")
    assert hits == [] and vector.calls == []  # 他人私有库不进检索范围


# ---------- 状态收敛与自选库子集 ----------


def test_enabled_kb_and_doc_recalled_with_citation(engine):
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "产品手册")
    vector = StubVectorIndex([hit_for(doc)])
    service = make_service(engine, vector)

    hits, notes = service.search_for_user(TEST_USER_ID, "如何安装")
    assert notes == []
    assert len(hits) == 1
    assert hits[0].doc_name == "产品手册"
    assert hits[0].meta["heading_path"] == ["H1"]


def test_not_enabled_kb_skipped_with_note(engine):
    make_kb(engine, "kb-ready", status=KnowledgeStatus.READY)  # 处理完但未启用
    vector = StubVectorIndex()
    service = make_service(engine, vector)

    hits, notes = service.search_for_user(TEST_USER_ID, "问题")
    assert hits == []
    assert vector.calls == []  # 未启用的库不扇出查询
    assert any("未启用" in note for note in notes)


def test_kb_ids_subset_and_unknown_noted(engine):
    kb1 = make_kb(engine, "kb1")
    make_kb(engine, "kb2")
    vector = StubVectorIndex()
    service = make_service(engine, vector)

    # 自选库：只查可见库的子集（kb2 不查）
    hits, notes = service.search_for_user(TEST_USER_ID, "问题", kb_ids=[kb1])
    assert hits == []
    assert [call[0] for call in vector.calls] == [[str(kb1)]]

    # 自选库含不可见/不存在 id：静默剔除并给出说明，可见库仍被检索
    ghost = uuid4()
    _, second_notes = service.search_for_user(TEST_USER_ID, "问题", kb_ids=[kb1, ghost])
    assert any("不可见" in note or "不存在" in note for note in second_notes)


def test_embedding_model_mismatch_skipped(engine):
    make_kb(engine, "kb-old", embedding_model="old-model")
    vector = StubVectorIndex()
    service = make_service(engine, vector)

    hits, notes = service.search_for_user(TEST_USER_ID, "问题")
    assert hits == [] and vector.calls == []
    assert any("嵌入模型" in note for note in notes)


# ---------- 文档后滤与 top_k（直连检索，不涉可见性） ----------


def test_disabled_doc_filtered_and_top_k_capped(engine):
    kb = make_kb(engine, "kb1")
    doc_on = make_doc(engine, kb, "启用文档")
    doc_off = make_doc(engine, kb, "停用文档", status=KnowledgeStatus.DISABLED)
    vector = StubVectorIndex([
        hit_for(doc_off, score=0.99),  # 停用文档分数最高也必须被后滤
        hit_for(doc_on, score=0.90),
        hit_for(doc_on, score=0.80),
    ])
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=2)
    assert [hit.doc_id for hit in hits] == [str(doc_on), str(doc_on)]
    assert all(hit.score != 0.99 for hit in hits)  # 停用文档被剔除


def test_search_overfetch_filtered_to_top_k(engine):
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "文档")
    vector = StubVectorIndex([hit_for(doc, score=1 - i / 10) for i in range(5)])
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=2)
    assert len(hits) == 2
    # 过采样 2 倍后截断：分数最高的两条胜出
    assert [hit.score for hit in hits] == [1.0, 0.9]
