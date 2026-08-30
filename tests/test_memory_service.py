"""MemoryRecallService 快注：排序/强化/小话短路与会话内增量去重。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.components.memory import MemoryRecallService
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.models.domain.memory import MemoryEntity, MemoryStatement

from fakes_memory import FakeMemoryVectorIndex, make_service_config
from conftest import TEST_USER_ID

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)
THREAD = uuid4()


def _service(engine, **config_overrides):
    return MemoryRecallService(
        memory_repo=SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID),
        vector_index=FakeMemoryVectorIndex(),
        app_config=make_service_config(**config_overrides),
    )


def _seed(repo, object_text, *, importance=0.5, access=0, last_active=None):
    user = repo.find_entity_by_name("用户") or repo.upsert_entity(
        MemoryEntity(name="用户", entity_type="PERSON", is_user=True))
    return repo.insert_statement(MemoryStatement(
        subject_id=user.id, predicate="偏好", object_text=object_text,
        summary=f"用户的偏好是{object_text}", state="ACTIVE",
        valid_from=datetime(2026, 1, 1),
        importance=importance, access_count=access, last_accessed_at=last_active,
    ))


def _rendered_ids(block: str) -> list[int]:
    out = []
    for line in block.splitlines():
        if ". #S" in line:
            out.append(int(line.strip().split("#S")[1].split()[0]))
    return out


def test_smalltalk_and_disabled_short_circuit(engine):
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    svc = _service(engine)
    _seed(repo, "Python")

    assert svc.build_fast_context("你好呀", TEST_USER_ID, THREAD) == ""
    assert _service(engine, enabled=False).build_fast_context("我在哪工作", TEST_USER_ID, THREAD) == ""


def test_fast_context_damps_cold_items_and_bumps_accessed(engine):
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    cold = _seed(repo, "钓鱼", importance=0.2, last_active=NOW - timedelta(days=120))
    hot = _seed(repo, "Python", importance=0.9)
    stale_hot = _seed(repo, "C++", importance=0.9, access=6)

    svc = _service(engine, recall={"fast_limit": 2})
    block = svc.build_fast_context("推荐个编程语言", TEST_USER_ID, THREAD)

    assert f"#S{cold.id}" not in block  # 冰冷低重要度被自然沉底
    assert set(_rendered_ids(block)) == {hot.id, stale_hot.id}
    assert sorted(_rendered_ids(block))[0] == hot.id  # 热且未被访问过者居首

    stored = {row.id: row for row in repo.list_active_statements()}
    assert stored[hot.id].access_count == 1          # 召回即强化（提取练习）
    cold_row = stored[cold.id]
    assert cold_row.access_count == 0                # 未入选者不虚增访问计数


def test_session_registry_returns_only_increment_across_calls(engine):
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    a = _seed(repo, "Python")
    b = _seed(repo, "游泳")

    svc = _service(engine, recall={"fast_limit": 1})
    first_ids = _rendered_ids(svc.build_fast_context("聊点爱好", TEST_USER_ID, THREAD))
    second_ids = _rendered_ids(svc.build_fast_context("还有别的吗", TEST_USER_ID, THREAD))

    assert len(first_ids) == 1 and second_ids and set(first_ids).isdisjoint(second_ids)
    assert set(first_ids) | set(second_ids) == {a.id, b.id}


def test_disabled_engine_yields_empty_even_with_rows(engine):
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    _seed(repo, "Python")
    assert _service(engine, enabled=False).build_fast_context("任何问题", TEST_USER_ID, THREAD) == ""
