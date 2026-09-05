"""危险操作区（L4）：当日清除、会话遗忘、整体重置与导出。"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.components.memory.admin import MemoryRepositoryEditor
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.exceptions.memory import MemoryInvalidParamError
from app.models.domain.memory import (
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryStatement,
    StatementState,
)
from app.domain.memory import MemoryAdminService
from app.domain.memory.ports import FactWrite

from fakes_memory import FakeMemoryVectorIndex
from conftest import TEST_USER_ID

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
YESTERDAY = NOW - timedelta(days=1)


def _repo(engine) -> SqliteGraphMemoryRepository:
    return SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)


def _editor(engine) -> MemoryRepositoryEditor:
    return MemoryRepositoryEditor(memory_repo=_repo(engine), vector_index=FakeMemoryVectorIndex())


def _service(engine, editor=None) -> MemoryAdminService:
    return MemoryAdminService(editor=editor or _editor(engine))


def _seed_today(engine):
    """今天：用户一条在效事实、一条已归档历史行、一个事件（含孤立参与实体）。"""
    editor = _editor(engine)
    user = editor.memory_repo.upsert_entity(MemoryEntity(
        name="用户", entity_type="PERSON", is_user=True, importance=0.9))
    today_fact = editor.add_statement(
        FactWrite(subject_entity_id=user.id, predicate="偏好", object_text="游泳"), now=NOW)
    stale = editor.add_statement(
        FactWrite(subject_entity_id=user.id, predicate="职业", object_text="实习"),
        now=datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc))
    editor.archive_statement(stale.id, now=datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc))
    episode = _repo(engine).insert_episode(MemoryEpisode(
        thread_id=uuid4(), summary="今日事件", occurred_at=NOW))
    link = MemoryEpisodeLink(episode_id=episode.id, entity_id=user.id, role="参与者")
    _repo(engine).link_episode_entities([link])
    participant = editor.memory_repo.upsert_entity(_entity("今日纯参与实体"))
    link2 = MemoryEpisodeLink(episode_id=episode.id, entity_id=participant.id, role="参与")
    _repo(engine).link_episode_entities([link2])
    return editor, user, {"today_fact": today_fact, "stale": stale,
                          "episode": episode, "participant": participant}


def _entity(name: str, **kwargs) -> MemoryEntity:
    return MemoryEntity(name=name, **kwargs)


def _purge_request(scope: str, from_dt=None, to_dt=None, thread_id=None):
    from app.models.schema.request.memory import MaintenancePurgeRequest

    if scope == "day":
        return MaintenancePurgeRequest(
            scope=scope,
            **({"from": _naive_iso(from_dt), "to": _naive_iso(to_dt)}
               if from_dt and to_dt else {}),
        )
    return MaintenancePurgeRequest(scope=scope, **({"threadId": thread_id} if thread_id else {}))


def _naive_iso(d: datetime) -> str:
    return d.replace(tzinfo=None).isoformat(sep=" ")


def test_purge_day_archives_statements_deletes_episodes_and_sweeps_orphans(engine):
    editor, user, seeded = _seed_today(engine)
    svc = _service(engine, editor)  # 复用同一 editor：向量断言才对得上同一个 fake
    # created_at 是种子落库的真实时刻，窗口以当前时间锚定（写死日期只会在
    # 种子当日跑绿——历史遗留的时间炸弹，见 2026-08-30 基线复现）
    now = datetime.now(timezone.utc)
    window_from = now - timedelta(minutes=5)
    window_to = now + timedelta(minutes=5)

    preview = svc.purge_preview(TEST_USER_ID, _purge_request("day", window_from, window_to))
    assert preview["statements"] == 2 and preview["activeStatements"] == 1
    assert preview["episodes"] == 1

    result = svc.purge_memory(TEST_USER_ID, _purge_request("day", window_from, window_to))

    assert result["archivedStatements"] == 1  # 仅在效行归档，已归档历史行不动
    assert result["deletedEpisodes"] == 1
    # 事件删除后，「今日纯参与实体」零引用且在范围内 → 清理；用户节点保留
    assert result["deletedEntities"] == 1
    repo = _repo(engine)
    assert editor.get_entity(seeded["participant"].id) is None
    assert editor.get_entity(user.id) is not None
    fact_row = repo.get_statement(seeded["today_fact"].id)
    assert fact_row.state == StatementState.ARCHIVED.value
    assert fact_row.valid_to is not None and fact_row.invalidated_at is not None
    stale_row = repo.get_statement(seeded["stale"].id)
    assert stale_row.state == StatementState.ARCHIVED.value  # 范围外/已归档行原样
    # 向量：归档事实 + 删除事件 + 清理实体全部退场
    assert ("statement", seeded["today_fact"].id) in editor.vector_index.deleted
    assert ("episode", seeded["episode"].id) in editor.vector_index.deleted
    assert ("entity", seeded["participant"].id) in editor.vector_index.deleted


def test_purge_thread_scopes_by_source_thread(engine):
    editor, user, seeded = _seed_today(engine)
    thread = uuid4()
    thread_fact = editor.add_statement(
        FactWrite(subject_entity_id=user.id, predicate="目标", object_text="会话内目标"),
        now=datetime(2026, 8, 29, 10, 0, tzinfo=timezone.utc),
    )
    # 把该行溯源指向目标会话
    with Session(_repo(engine).engine) as session:
        row = session.get(MemoryStatement, thread_fact.id)
        row.source_thread_id = thread
        session.add(row)
        session.commit()

    result = editor.purge_memory(thread_id=thread)

    assert result["archivedStatements"] == 1 and result["deletedEntities"] == 0
    row = _repo(engine).get_statement(thread_fact.id)
    assert row.state == StatementState.ARCHIVED.value
    # 其他事实不受影响
    assert _repo(engine).get_statement(seeded["today_fact"].id).state == StatementState.ACTIVE.value


def test_purge_requires_scope_params(engine):
    svc = _service(engine)
    with pytest.raises(MemoryInvalidParamError):
        svc.purge_memory(TEST_USER_ID, _purge_request("day"))  # 缺 from/to
    with pytest.raises(MemoryInvalidParamError):
        svc.purge_memory(TEST_USER_ID, _purge_request("thread"))  # 缺 threadId
    with pytest.raises(MemoryInvalidParamError):
        svc.purge_preview(TEST_USER_ID, _purge_request("thread", thread_id="不是uuid"))


def test_export_contains_all_tables_including_history(engine):
    editor, _user, seeded = _seed_today(engine)
    result = editor.export_memory()

    assert {"entities", "statements", "episodes", "episodeLinks", "exportedAt"} <= set(result)
    ids = {s["id"] for s in result["statements"]}
    assert f"s:{seeded['today_fact'].id}" in ids
    assert f"s:{seeded['stale'].id}" in ids  # 已归档历史行也在导出里
    assert result["episodes"][0]["summary"] == "今日事件"


def test_reset_requires_exact_confirmation_and_wipes(engine):
    editor, user, seeded = _seed_today(engine)
    svc = _service(engine, editor)  # 复用同一 editor：rebuild 断言才对得上同一个 fake

    with pytest.raises(MemoryInvalidParamError):
        svc.reset_all_memory(TEST_USER_ID, _reset_request("reset"))

    counts = svc.reset_all_memory(TEST_USER_ID, _reset_request("重置"))

    assert counts["memory_entity"] >= 2 and counts["memory_statement"] >= 2
    assert counts["memory_episode"] == 1 and counts["memory_episode_link"] == 2
    assert editor.get_entity(user.id) is None
    assert _repo(engine).get_statement(seeded["today_fact"].id) is None
    assert _repo(engine).list_episodes() == []
    # 向量 collection 已 drop 重建为空
    assert len(editor.vector_index.rebuilds) == 1 and editor.vector_index.rebuilds[0] == []


def _reset_request(confirmation: str):
    from app.models.schema.request.memory import MaintenanceResetRequest

    return MaintenanceResetRequest(confirmation=confirmation)
