"""检索编排：状态收敛、自选库子集、嵌入模型守卫与文档后滤。"""

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session

from app.components.knowledge.service import KnowledgeRetrievalService
from app.components.knowledge.vector_index import VectorHit
from app.core.logging import LoggerFactory
from app.models.domain.knowledge import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeStatus,
)
from app.repositories.knowledge_base_repository import KnowledgeBaseRepository
from app.repositories.knowledge_binding_repository import KnowledgeBindingRepository
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository

EMBEDDING = "ollama-embedding"


class StubVectorIndex:
    """向量索引桩：记录扇出调用，返回预设命中。"""

    def __init__(self, hits=None):
        self.hits = hits or []
        self.calls = []

    def search_many(self, kb_ids, query, *, top_k=4, alpha=1.0):
        self.calls.append(([str(k) for k in kb_ids], top_k))
        return self.hits


def make_kb(engine, name, status=KnowledgeStatus.ENABLED, embedding_model=EMBEDDING):
    with Session(engine) as session:
        kb = KnowledgeBase(name=name, embedding_model=embedding_model, status=status)
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
        binding_repo=KnowledgeBindingRepository(engine=engine),
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


def bind(engine, agent_id, kb_ids):
    KnowledgeBindingRepository(engine=engine).replace_by_agent(agent_id, kb_ids)


def test_enabled_kb_and_doc_recalled_with_citation(engine):
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "产品手册")
    bind(engine, "builtin:demo", [kb])
    vector = StubVectorIndex([hit_for(doc)])
    service = make_service(engine, vector)

    hits, notes = service.search_for_agent("builtin:demo", "如何安装")
    assert notes == []
    assert len(hits) == 1
    assert hits[0].doc_name == "产品手册"
    assert hits[0].meta["heading_path"] == ["H1"]


def test_not_enabled_kb_skipped_with_note(engine):
    kb = make_kb(engine, "kb-ready", status=KnowledgeStatus.READY)  # 处理完但未启用
    bind(engine, "builtin:demo", [kb])
    vector = StubVectorIndex()
    service = make_service(engine, vector)

    hits, notes = service.search_for_agent("builtin:demo", "问题")
    assert hits == []
    assert vector.calls == []  # 未启用的库不扇出查询
    assert any("未启用" in note for note in notes)


def test_kb_ids_subset_and_unknown_noted(engine):
    kb1 = make_kb(engine, "kb1")
    kb2 = make_kb(engine, "kb2")
    bind(engine, "builtin:demo", [kb1, kb2])
    vector = StubVectorIndex()
    service = make_service(engine, vector)

    # 自选库：只查绑定的子集（kb2 不查）
    hits, notes = service.search_for_agent("builtin:demo", "问题", kb_ids=[kb1])
    assert hits == []
    assert [call[0] for call in vector.calls] == [[str(kb1)]]

    # 自选库含未绑定 id：静默剔除并给出说明，绑定库仍被检索
    ghost = uuid4()
    _, second_notes = service.search_for_agent("builtin:demo", "问题", kb_ids=[kb1, ghost])
    assert any("未绑定" in note or "不存在" in note for note in second_notes)


def test_embedding_model_mismatch_skipped(engine):
    kb = make_kb(engine, "kb-old", embedding_model="old-model")
    bind(engine, "builtin:demo", [kb])
    vector = StubVectorIndex()
    service = make_service(engine, vector)

    hits, notes = service.search_for_agent("builtin:demo", "问题")
    assert hits == [] and vector.calls == []
    assert any("嵌入模型" in note for note in notes)


def test_disabled_doc_filtered_and_top_k_capped(engine):
    kb = make_kb(engine, "kb1")
    doc_on = make_doc(engine, kb, "启用文档")
    doc_off = make_doc(engine, kb, "停用文档", status=KnowledgeStatus.DISABLED)
    bind(engine, "builtin:demo", [kb])
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
