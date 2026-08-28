"""实体消歧：精确名→别名→向量阈值合并（is_user 保护）→新建。"""

from datetime import datetime, timezone

from app.components.memory.extraction import ExtractedEntity
from app.components.memory.service import USER_ENTITY_NAME, MemoryService
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.models.domain.memory import EntityType, MemoryEntity
from app.services.domain.memory import MemoryVectorHit

from fakes_memory import FakeMemoryVectorIndex, make_service_config

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


def _service(engine, vector=None):
    return MemoryService(
        memory_repo=SqliteGraphMemoryRepository(engine=engine),
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

    resolved = svc._resolve_entities((_candidate(name="张伟", aliases=["老张"]),))

    assert resolved["z"].id == seeded.id
    assert "老张" in resolved["z"].aliases and "大伟" in resolved["z"].aliases
    assert svc.memory_repo.find_entity_by_alias("老张").id == seeded.id


def test_alias_path_resolves_existing(engine):
    svc = _service(engine)
    seeded = svc.memory_repo.upsert_entity(MemoryEntity(
        name="张伟", entity_type="PERSON", aliases=["Z威"]))

    resolved = svc._resolve_entities((_candidate(name="Z威"),))
    assert resolved["z"].id == seeded.id


def test_vector_threshold_merge_below_creates_new(engine):
    repo = SqliteGraphMemoryRepository(engine=engine)
    similar_row = repo.upsert_entity(MemoryEntity(name="王小明", entity_type="PERSON"))
    vector = FakeMemoryVectorIndex(preset_hits=[
        MemoryVectorHit(kind="entity", ref_id=similar_row.id, score=0.9)])
    svc_high, svc_low = _service(engine, vector), _service(engine)

    merged = svc_high._resolve_entities((_candidate(name="小明", ),))
    created = svc_low._resolve_entities((_candidate(name="完全陌生的人"),))

    assert merged["z"].id == similar_row.id          # 阈值内：复用并回填别名
    assert created["z"].id != similar_row.id         # 阈值外且无库内命中：新建
    assert svc_low.vector_index.searches[0]["kinds"] == ("entity",)


def test_user_node_never_demoted_and_singleton(engine):
    svc = _service(engine)
    first = svc._ensure_user_entity()
    assert first.is_user and first.name == USER_ENTITY_NAME

    # 名字精确命中“用户”实体的合并请求不得剥掉 is_user 标记
    merged = svc._resolve_entities((_candidate(key="u", name=USER_ENTITY_NAME),))
    reloaded = svc.memory_repo.get_entities([first.id])[first.id]
    assert merged["u"].id == first.id and reloaded.is_user
