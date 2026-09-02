"""跨轮指代归一：身份专项（Tier3）+ 名册复用（Tier1）+ 灰度带语义裁决（Tier2）。

背景：用户说「我是xx，我的公司是YY」时，xx 与用户节点、YY 简称与既有公司
全称各自裂成新实体，靠人工频繁合并。修复后抽取上游归指（身份卡+名册），
写路径三层消歧漏斗收口，读路径 expand 同规。
"""

import json
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

from app.components.memory import MemoryConsolidationService, MemoryRecallService
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.models.domain.memory import MemoryEntity, MemoryStatement
from app.services.domain.memory import MemoryVectorHit

from fakes_memory import CannedLLM, FakeMemoryVectorIndex, FakeModelFactory, make_service_config
from conftest import TEST_USER_ID


def _service(engine, llm, vector=None) -> MemoryConsolidationService:
    return MemoryConsolidationService(
        memory_repo=SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID),
        vector_index=vector or FakeMemoryVectorIndex(),
        model_factory=FakeModelFactory(llm),
        app_config=make_service_config(),
    )


def _msg():
    return SimpleNamespace(message_type="MESSAGE", content=[{"type": "text", "text": "收到"}])


# ==================== Tier3：用户身份专项 ====================

def test_self_introduction_never_creates_entity(engine):
    """「我是张三」：张三是用户的称呼——字面量陈述 + 用户别名，不建实体行。"""
    extraction = json.dumps({
        "entities": [{"key": "zhang", "type": "PERSON", "name": "张三"}],
        "episodes": [],
        "facts": [{"subject_key": "user", "predicate": "姓名",
                   "object_key": "zhang", "confidence": 0.95}],
    }, ensure_ascii=False)
    llm = CannedLLM(extraction, '{"decisions": [{"index": 0, "action": "ADD"}]}')
    svc = _service(engine, llm)

    created = svc.remember("我是张三", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    repo = svc.memory_repo
    assert repo.find_entity_by_name("张三") is None          # 不为自报姓名建实体
    user = repo.find_entity_by_name("用户")
    assert "张三" in (user.aliases or [])                    # 称呼登记进用户别名（读路径可锚定）
    name_rows = [r for r in repo.list_active_statements() if r.predicate == "姓名"]
    assert len(name_rows) == 1 and created
    assert name_rows[0].object_text == "张三"                 # 客体字面量化，不挂实体边
    assert name_rows[0].object_entity_id is None


def test_known_self_name_routes_followup_facts_to_user_node(engine):
    """后续轮次直呼其名（张三的项目…）：主体归到用户节点，不再裂新实体。"""
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    repo.upsert_entity(MemoryEntity(name="用户", entity_type="PERSON",
                                    is_user=True, aliases=["张三"]))
    extraction = json.dumps({
        "entities": [{"key": "zhang", "type": "PERSON", "name": "张三"}],
        "episodes": [],
        "facts": [{"subject_key": "zhang", "predicate": "偏好",
                   "object_text": "咖啡", "confidence": 0.8}],
    }, ensure_ascii=False)
    llm = CannedLLM(extraction, '{"decisions": [{"index": 0, "action": "ADD"}]}')
    svc = _service(engine, llm)

    svc.remember("张三平时喜欢什么", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    pref = [r for r in repo.list_active_statements() if r.predicate == "偏好"][0]
    assert pref.subject_id == repo.find_entity_by_name("用户").id
    assert repo.find_entity_by_name("张三") is None


# ==================== Tier1：名册 ref_id 复用 ====================

def test_roster_ref_id_reuses_existing_company(engine):
    """「我的公司是广东禹通」且名册含全称实体：ref_id 直取，别名回填，零新实体。"""
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    company = repo.upsert_entity(MemoryEntity(
        name="广东禹通互联网科技有限公司", entity_type="ORG"))
    vector = FakeMemoryVectorIndex(
        preset_hits=[MemoryVectorHit(kind="entity", ref_id=company.id, score=0.9)])
    extraction = json.dumps({
        "entities": [{"key": "g", "type": "ORG", "name": "广东禹通", "ref_id": company.id}],
        "episodes": [],
        "facts": [{"subject_key": "user", "predicate": "所属公司",
                   "object_key": "g", "confidence": 0.9}],
    }, ensure_ascii=False)
    llm = CannedLLM(extraction, '{"decisions": [{"index": 0, "action": "ADD"}]}')
    svc = _service(engine, llm, vector)

    svc.remember("我的公司是广东禹通", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    row = [r for r in repo.list_active_statements() if r.predicate == "所属公司"][0]
    assert row.object_entity_id == company.id                 # 并进既有实体，不再另起一行
    assert repo.find_entity_by_name("广东禹通") is None
    assert "广东禹通" in (repo.get_entity(company.id).aliases or [])  # 简称沉淀为别名


# ==================== Tier2：灰度带语义裁决 ====================

def _grey_zone_fixtures(engine, *, cosine):
    """种子全称公司 + 向量替身（余弦落灰度带的候选）。"""
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    company = repo.upsert_entity(MemoryEntity(
        name="广东禹通互联网科技有限公司", entity_type="ORG"))
    vector = FakeMemoryVectorIndex(
        preset_hits=[MemoryVectorHit(kind="entity", ref_id=company.id, score=0.9)],
        preset_cosines=[cosine],
    )
    return repo, company, vector


def _short_form_extraction():
    return json.dumps({
        "entities": [{"key": "g", "type": "ORG", "name": "广东禹通"}],
        "episodes": [],
        "facts": [{"subject_key": "user", "predicate": "所属公司",
                   "object_key": "g", "confidence": 0.9}],
    }, ensure_ascii=False)


def test_grey_zone_adjudication_merges_short_form(engine):
    """余弦 0.7 落灰度带：LLM 语义裁决判同一 → 并入既有实体（LLM 调用 3 次）。"""
    repo, company, vector = _grey_zone_fixtures(engine, cosine=0.7)
    llm = CannedLLM(
        _short_form_extraction(),
        json.dumps({"merge_with": company.id, "reason": "简称"}, ensure_ascii=False),
        '{"decisions": [{"index": 0, "action": "ADD"}]}',
    )
    svc = _service(engine, llm, vector)

    svc.remember("我们公司是广东禹通", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    assert len(llm.calls) == 3                      # 抽取 + 灰度带裁决 + 陈述裁决
    row = [r for r in repo.list_active_statements() if r.predicate == "所属公司"][0]
    assert row.object_entity_id == company.id
    assert repo.find_entity_by_name("广东禹通") is None
    assert "广东禹通" in (repo.get_entity(company.id).aliases or [])


def test_grey_zone_adjudication_uncertain_creates_new(engine):
    """裁决判非同一（或不可用）→ 保守新建，宁错过不错并（8-28 事故取向）。"""
    repo, company, vector = _grey_zone_fixtures(engine, cosine=0.7)
    llm = CannedLLM(
        _short_form_extraction(),
        json.dumps({"merge_with": None, "reason": "不同事物"}, ensure_ascii=False),
        '{"decisions": [{"index": 0, "action": "ADD"}]}',
    )
    svc = _service(engine, llm, vector)

    svc.remember("我们公司是广东禹通", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    row = [r for r in repo.list_active_statements() if r.predicate == "所属公司"][0]
    assert row.object_entity_id != company.id       # 新实体接住了陈述
    assert repo.find_entity_by_name("广东禹通") is not None


def test_grey_zone_below_lower_bound_skips_llm(engine):
    """余弦低于灰度带下限（0.44 事故值）：直接新建，不烧裁决调用。"""
    repo, company, vector = _grey_zone_fixtures(engine, cosine=0.44)
    llm = CannedLLM(
        _short_form_extraction().replace("广东禹通", "广州市卫生职业技术学院"),
        '{"decisions": [{"index": 0, "action": "ADD"}]}',
    )
    svc = _service(engine, llm, vector)

    svc.remember("我们合作方是广州市卫生职业技术学院", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    assert len(llm.calls) == 2                      # 无灰度带裁决调用
    row = [r for r in repo.list_active_statements() if r.predicate == "所属公司"][0]
    assert row.object_entity_id != company.id
    assert "广州市卫生职业技术学院" not in (repo.get_entity(company.id).aliases or [])


def test_grey_zone_type_conflict_overrides_adjudication(engine):
    """语义裁决通过但类型冲突：护栏仍拒绝（裁决不越类型护栏）。"""
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    person = repo.upsert_entity(MemoryEntity(name="王小明", entity_type="PERSON"))
    vector = FakeMemoryVectorIndex(
        preset_hits=[MemoryVectorHit(kind="entity", ref_id=person.id, score=0.9)],
        preset_cosines=[0.7],
    )
    extraction = json.dumps({
        "entities": [{"key": "o", "type": "ORG", "name": "王小明科技"}],
        "episodes": [],
        "facts": [{"subject_key": "user", "predicate": "认识",
                   "object_key": "o", "confidence": 0.8}],
    }, ensure_ascii=False)
    llm = CannedLLM(
        extraction,
        json.dumps({"merge_with": person.id, "reason": "同名"}, ensure_ascii=False),
        '{"decisions": [{"index": 0, "action": "ADD"}]}',
    )
    svc = _service(engine, llm, vector)

    svc.remember("我认识王小明科技", [_msg()], TEST_USER_ID, uuid4(), uuid4())

    row = [r for r in repo.list_active_statements() if r.predicate == "认识"][0]
    assert row.object_entity_id != person.id        # 类型护栏否决裁决，新建 ORG


# ==================== 读路径同规：expand 锚点 ====================

def test_expand_anchor_resolves_via_grey_zone_adjudication(engine):
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    company = repo.upsert_entity(MemoryEntity(
        name="广东禹通互联网科技有限公司", entity_type="ORG"))
    stmt = repo.insert_statement(MemoryStatement(
        subject_id=company.id, predicate="所属行业", object_text="医疗器械",
        summary="广东禹通互联网科技有限公司的所属行业是医疗器械",
        state="ACTIVE", valid_from=datetime(2026, 5, 1),
    ))
    vector = FakeMemoryVectorIndex(
        preset_hits=[MemoryVectorHit(kind="entity", ref_id=company.id, score=0.9)],
        preset_cosines=[0.7],
    )
    llm = CannedLLM(json.dumps({"merge_with": company.id, "reason": "简称"},
                               ensure_ascii=False))
    svc = MemoryRecallService(
        memory_repo=repo, vector_index=vector,
        app_config=make_service_config(),
        model_factory=FakeModelFactory(llm),
    )

    out = svc.expand("广东禹通", TEST_USER_ID, uuid4())

    assert f"#S{stmt.id}" in out                    # 简称锚定到全称实体的邻域
    assert len(llm.calls) == 1
