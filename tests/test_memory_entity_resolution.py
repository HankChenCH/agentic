"""实体消歧：精确名→别名→向量阈值合并（is_user 保护）→新建。"""

from datetime import datetime, timezone

from app.components.memory.internal.extraction import ExtractedEntity
from app.components.memory import MemoryConsolidationService
from app.components.memory.ability.consolidation import USER_ENTITY_NAME
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.models.domain.memory import EntityType, MemoryEntity
from app.services.domain.memory import MemoryVectorHit

from fakes_memory import FakeMemoryVectorIndex, make_service_config
from conftest import TEST_USER_ID

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _service(engine, vector=None):
    return MemoryConsolidationService(
        memory_repo=SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID),
        vector_index=vector or FakeMemoryVectorIndex(),
        model_factory=None,
        app_config=make_service_config(),
    )


def _candidate(key="z", name="张伟", **kw):
    return ExtractedEntity(key=key, name=name,
                           entity_type=kw.pop("entity_type", EntityType.PERSON.value), **kw)


def test_exact_name_hit_merges_aliases_without_new_row(engine):
    svc = _service(engine)
    seeded = svc.memory_repo.upsert_entity(MemoryEntity(
        name="张伟", entity_type="PERSON", aliases=["大伟"]))

    resolved = svc._resolve_entities((_candidate(name="张伟", aliases=["老张"]),), svc.memory_repo, svc.vector_index)

    assert resolved["z"].id == seeded.id
    assert "老张" in resolved["z"].aliases and "大伟" in resolved["z"].aliases
    assert svc.memory_repo.find_entity_by_alias("老张").id == seeded.id


def test_alias_path_resolves_existing(engine):
    svc = _service(engine)
    seeded = svc.memory_repo.upsert_entity(MemoryEntity(
        name="张伟", entity_type="PERSON", aliases=["Z威"]))

    resolved = svc._resolve_entities((_candidate(name="Z威"),), svc.memory_repo, svc.vector_index)
    assert resolved["z"].id == seeded.id


def test_cosine_above_threshold_merges_even_with_max_fusion_score(engine):
    """合并判定只认客户端余弦；search 融合分给到满分也不影响。"""
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    similar_row = repo.upsert_entity(MemoryEntity(name="王小明", entity_type="PERSON"))
    vector = FakeMemoryVectorIndex(
        preset_hits=[MemoryVectorHit(kind="entity", ref_id=similar_row.id, score=1.0)],
        preset_cosines=[0.9],
    )
    svc = _service(engine, vector)

    resolved = svc._resolve_entities((_candidate(name="小明"),), svc.memory_repo, svc.vector_index)

    assert resolved["z"].id == similar_row.id        # 阈值内：复用并回填别名
    assert "小明" in resolved["z"].aliases
    assert vector.cosine_calls[0]["contents"]        # 判定输入 = 候选档案文本
    assert vector.searches[0]["kinds"] == ("entity",)  # search 仅候选发现


def test_incident_fusion_score_1_but_cosine_044_never_merges(engine):
    """事故回归钉子：广州市卫生职业技术学院曾以融合分 1.0 并入禹通公司。

    真实 bge-m3 余弦仅 0.44 —— 新判定下必须新建实体、不得污染公司档案。
    """
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    company = repo.upsert_entity(MemoryEntity(
        name="广东禹通互联网科技有限公司", entity_type="ORG", aliases=["禹通"]))
    vector = FakeMemoryVectorIndex(
        preset_hits=[MemoryVectorHit(kind="entity", ref_id=company.id, score=1.0)],
        preset_cosines=[0.44],
    )
    svc = _service(engine, vector)

    resolved = svc._resolve_entities((_candidate(
        key="school", name="广州市卫生职业技术学院",
        entity_type=EntityType.ORG.value),), svc.memory_repo, svc.vector_index)

    assert resolved["school"].id != company.id
    assert "广州市卫生职业技术学院" not in (company.aliases or [])


def test_type_conflict_rejects_merge_below_no_similarity(engine):
    """余弦达标但实体类型冲突（且双方均非 OTHER）→ 拒绝合并。"""
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    person = repo.upsert_entity(MemoryEntity(name="王小明", entity_type="PERSON"))
    vector = FakeMemoryVectorIndex(
        preset_hits=[MemoryVectorHit(kind="entity", ref_id=person.id, score=0.9)],
        preset_cosines=[0.95],
    )
    svc = _service(engine, vector)

    resolved = svc._resolve_entities((_candidate(
        key="org", name="王小明科技", entity_type=EntityType.ORG.value),), svc.memory_repo, svc.vector_index)

    assert resolved["org"].id != person.id


def test_internal_ref_candidate_never_becomes_entity(engine):
    """#S13 形态的候选名是渲染层溯源键，不是实体——跳过不建行。"""
    svc = _service(engine)

    resolved = svc._resolve_entities((_candidate(key="s13", name="#S13"),), svc.memory_repo, svc.vector_index)

    assert "s13" not in resolved
    assert svc.memory_repo.find_entity_by_name("#S13") is None


def test_user_node_never_demoted_and_singleton(engine):
    svc = _service(engine)
    first = svc._ensure_user_entity(svc.memory_repo)
    assert first.is_user and first.name == USER_ENTITY_NAME

    # 名字精确命中“用户”实体的合并请求不得剥掉 is_user 标记
    merged = svc._resolve_entities((_candidate(key="u", name=USER_ENTITY_NAME),), svc.memory_repo, svc.vector_index)
    reloaded = svc.memory_repo.get_entities([first.id])[first.id]
    assert merged["u"].id == first.id and reloaded.is_user
