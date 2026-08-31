"""MemoryRecallService 快注：排序/小话短路/常驻摘要稳定性与会话内增量登记。"""

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


def test_fast_context_damps_cold_items_without_bumping(engine):
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
    # 快注是常驻摘要：不自我强化访问计数（否则稳定 top-10 挤占新记忆）；
    # 提取练习强化只由深度工具触发
    assert all(row.access_count == (6 if row.id == stale_hot.id else 0)
               for row in stored.values())


def test_fast_block_is_stable_and_marks_registry_for_tools(engine):
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    a = _seed(repo, "Python")
    b = _seed(repo, "游泳")

    svc = _service(engine, recall={"fast_limit": 1})
    first_ids = _rendered_ids(svc.build_fast_context("聊点爱好", TEST_USER_ID, THREAD))
    second_ids = _rendered_ids(svc.build_fast_context("还有别的吗", TEST_USER_ID, THREAD))

    # 块不跳过会话内已投喂 id：同库状态下跨轮稳定（system 常驻摘要语义，
    # 跳过会造成事实只可见一轮的滚动丢失）；id 仍登记供深度工具只返回增量
    assert first_ids and first_ids == second_ids
    assert svc.inject_registry.seen(THREAD) == {f"s:{first_ids[0]}"}
    assert {a.id, b.id}  # 两条均在库，仅 top-1 入块


def test_disabled_engine_yields_empty_even_with_rows(engine):
    repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
    _seed(repo, "Python")
    assert _service(engine, enabled=False).build_fast_context("任何问题", TEST_USER_ID, THREAD) == ""
