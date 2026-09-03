"""检索编排：可见性圈定、状态收敛、自选库子集、嵌入模型守卫、文档后滤、
禁用分段剔除、双通道召回（混合检索 + 邻域扩展）与 RRF 融合排序。"""

from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlmodel import Session, col, select

from app.components.knowledge.ability.retrieval import (
    KnowledgeRetrievalService,
    _Candidate,
    _rrf_fuse,
)
from app.services.domain.knowledge.vector_index import VectorHit
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


def hit_for(doc_id: UUID, score=0.9, content="片段内容", position=0) -> VectorHit:
    return VectorHit(
        content=content, score=score, kb_id="kb", doc_id=str(doc_id), position=position,
        meta={"page_start": 1, "page_end": 1, "heading_path": ["H1"], "types": ["text"]},
    )


def make_segments(engine, kb_id, doc_id, contents: dict):
    """按 {position: content} 落库文档片段（邻域扩展的数据源）。"""
    with Session(engine) as session:
        for position, content in contents.items():
            session.add(DocumentSegment(
                kb_id=kb_id,
                doc_id=doc_id,
                position=position,
                content=content,
                word_count=len(content),
                status=KnowledgeStatus.ENABLED,
                meta={"page_start": 2, "page_end": 2, "heading_path": ["H1"], "types": ["text"]},
            ))
        session.commit()


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
        hit_for(doc_off, score=0.99, position=0),  # 停用文档分数最高也必须被后滤
        hit_for(doc_on, score=0.90, position=0),
        hit_for(doc_on, score=0.80, position=1),
    ])
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=2)
    assert [hit.doc_id for hit in hits] == [str(doc_on), str(doc_on)]
    assert [hit.position for hit in hits] == [0, 1]  # 融合序（此处无邻段即 A 通道排名序）
    assert all(hit.score != 0.99 for hit in hits)  # 停用文档被剔除


def test_search_overfetch_filtered_to_top_k(engine):
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "文档")
    vector = StubVectorIndex(
        [hit_for(doc, score=1 - i / 10, position=i) for i in range(5)]
    )
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=2)
    assert len(hits) == 2
    # top_k 只控返回数：融合序头部胜出（此处无邻段，即 A 通道排名序）
    assert [hit.position for hit in hits] == [0, 1]
    assert [hit.score for hit in hits] == [1.0, 0.9]


# ---------- 双通道召回与 RRF 融合 ----------


def test_rrf_fuse_ranks_dual_channel_first():
    """同片段双通道在榜（互证）压过单通道头部；纯函数排序确定性。"""
    a_only = _Candidate(key=("d", 0), score=1.0, content="", meta={}, kb_id="k", rank_a=1)
    dual = _Candidate(key=("d", 1), score=0.5, content="", meta={}, kb_id="k", rank_a=9, rank_b=1)
    b_only = _Candidate(key=("d", 2), score=0.0, content="", meta={}, kb_id="k", rank_b=2)

    assert [c.key for c in _rrf_fuse([a_only, dual, b_only])] == [("d", 1), ("d", 0), ("d", 2)]


def test_top_k_controls_return_size_not_pool_depth(engine):
    """top_k 只控返回数：内部检索深度固定为候选池常量，大值钳制到返回上限。"""
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "文档")
    vector = StubVectorIndex(
        [hit_for(doc, score=1 - i / 20, position=i) for i in range(12)]
    )
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=3)
    assert len(hits) == 3
    assert vector.calls[-1][1] == 8  # 扇出深度 = max(池常量, top_k)，与返回数解耦

    service.search([kb], "问题", top_k=999)
    assert vector.calls[-1][1] == 20  # 大 top_k 钳制到返回上限，池深随之封顶


def test_neighbor_hits_assembled_with_inherited_score(engine):
    """邻段入榜：内容/meta/文档名取自 SQL 行，score 继承种子混合检索分。"""
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "产品手册")
    make_segments(engine, kb, doc, {4: "第四段", 5: "第五段", 6: "第六段"})
    vector = StubVectorIndex([hit_for(doc, score=0.9, position=5)])
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=4)
    assert [hit.position for hit in hits] == [5, 4, 6]  # 种子先于邻段（A 排名并列时按 rank_a 破平）
    # 种子内容取自向量命中，邻段内容取自 segment 行
    assert [hit.content for hit in hits] == ["片段内容", "第四段", "第六段"]
    neighbors = [hit for hit in hits if hit.position != 5]
    assert all(hit.score == 0.9 for hit in neighbors)
    assert all(hit.doc_name == "产品手册" for hit in hits)
    assert hits[1].meta["page_start"] == 2  # 邻段溯源 meta 来自 segment 行


def test_dual_channel_hit_outranks_single_channel(engine):
    """既直接命中又互为邻段的片段，融合排名压过更高分的孤立命中。"""
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "文档")
    make_segments(engine, kb, doc, {3: "三", 4: "四", 5: "五", 9: "九"})
    vector = StubVectorIndex([
        hit_for(doc, score=0.9, position=4),
        hit_for(doc, score=0.8, position=5),
        hit_for(doc, score=0.7, position=9),  # 孤段：直接命中但无邻段
    ])
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=5)
    assert {hit.position for hit in hits[:2]} == {4, 5}  # 双通道互证占前两席
    assert hits[2].position == 3  # 纯邻段压过孤立的直接命中
    assert hits[3].position == 9


def test_neighbor_window_respects_document_bounds(engine):
    """窗口在文档首/末段处自然收边：不产生越界邻段。"""
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "文档")
    make_segments(engine, kb, doc, {0: "首段", 1: "次段", 4: "末段"})
    vector = StubVectorIndex([
        hit_for(doc, score=0.9, position=0),
        hit_for(doc, score=0.8, position=4),
    ])
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=6)
    assert [hit.position for hit in hits] == [0, 1, 4]  # 无 position=-1/3/5 越界项


# ---------- 禁用分段剔除（分段级禁用 = 不参与召回） ----------


def disable_segment(engine, doc_id: UUID, position: int) -> None:
    """把指定 position 的分段置为 disabled（管理侧禁用语义的数据准备）。"""
    with Session(engine) as session:
        seg = session.exec(
            select(DocumentSegment)
            .where(col(DocumentSegment.doc_id) == doc_id)
            .where(col(DocumentSegment.position) == position)
        ).first()
        seg.status = KnowledgeStatus.DISABLED
        session.add(seg)
        session.commit()


def test_disabled_segment_seed_filtered(engine):
    """禁用分段即使被向量召回也不入榜：向量库不带状态，行库是事实源。"""
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "文档")
    make_segments(engine, kb, doc, {0: "禁用段", 1: "可用段"})
    disable_segment(engine, doc, 0)
    vector = StubVectorIndex([
        hit_for(doc, score=0.99, position=0),  # 禁用段分数最高也必须被剔除
        hit_for(doc, score=0.90, position=1),
    ])
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=4)
    assert [hit.position for hit in hits] == [1]
    assert all(hit.content != "禁用段" for hit in hits)


def test_disabled_segment_neighbor_filtered(engine):
    """禁用邻段不入榜也不占邻域名次：剩余邻段排名前移。"""
    kb = make_kb(engine, "kb1")
    doc = make_doc(engine, kb, "文档")
    make_segments(engine, kb, doc, {4: "禁用邻段", 5: "种子", 6: "可用邻段"})
    disable_segment(engine, doc, 4)
    vector = StubVectorIndex([hit_for(doc, score=0.9, position=5)])
    service = make_service(engine, vector)

    hits = service.search([kb], "问题", top_k=4)
    assert [hit.position for hit in hits] == [5, 6]
    assert [hit.content for hit in hits] == ["片段内容", "可用邻段"]
