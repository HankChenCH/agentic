"""事件编辑（L3）：档案直改、物理删除与参与改挂（含唯一约束冲突）。"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.components.memory.admin import MemoryRepositoryEditor
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.exceptions.memory import (
    MemoryConstraintConflictError,
    MemoryInvalidParamError,
    MemoryNoChangeError,
    MemoryObjectNotFoundError,
)
from app.models.domain.memory import MemoryEpisode, MemoryEpisodeLink, MemoryEntity
from app.domain.memory import MemoryAdminService

from fakes_memory import FakeMemoryVectorIndex
from conftest import TEST_USER_ID

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _repo(engine) -> SqliteGraphMemoryRepository:
    return SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)


def _editor(engine) -> MemoryRepositoryEditor:
    return MemoryRepositoryEditor(memory_repo=_repo(engine), vector_index=FakeMemoryVectorIndex())


def _service(engine) -> MemoryAdminService:
    return MemoryAdminService(editor=_editor(engine))


def _entity(engine, name: str) -> MemoryEntity:
    return _repo(engine).upsert_entity(MemoryEntity(name=name))


def _seed_episode(engine, summary: str = "商业谈判陷入僵局", scene: str | None = "商业谈判"):
    return _repo(engine).insert_episode(MemoryEpisode(
        thread_id=uuid4(), summary=summary, scene=scene, occurred_at=NOW,
    ))


def test_update_episode_partial_fields(engine):
    editor = _editor(engine)
    episode = _seed_episode(engine)

    row = editor.update_episode(
        episode.id, summary="商业谈判延期一周", occurred_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
    )

    assert row.summary == "商业谈判延期一周"
    assert row.scene == "商业谈判"  # 未提供的字段保持原值
    assert _naive(row.occurred_at) == datetime(2026, 8, 30)
    assert any(
        e.kind == "episode" and e.ref_id == episode.id and e.content == "商业谈判延期一周"
        for e in editor.vector_index.upserts
    )


def test_update_episode_empty_scene_clears_it(engine):
    editor = _editor(engine)
    episode = _seed_episode(engine)

    row = editor.update_episode(episode.id, scene="")

    assert row.scene is None and row.summary == "商业谈判陷入僵局"


def test_update_episode_requires_fields_and_existing_target(engine):
    episode = _seed_episode(engine)
    svc = _service(engine)

    with pytest.raises(MemoryInvalidParamError):
        svc.update_episode(TEST_USER_ID, f"ep:{episode.id}", _episode_request())
    with pytest.raises(MemoryObjectNotFoundError):
        svc.update_episode(TEST_USER_ID, "ep:9999", _episode_request(summary="x"))
    with pytest.raises(MemoryInvalidParamError):
        svc.update_episode(TEST_USER_ID, "垃圾引用", _episode_request(summary="x"))


def test_delete_episode_removes_links_and_row(engine):
    editor = _editor(engine)
    episode = _seed_episode(engine)
    a = _entity(engine, "李总")
    b = _entity(engine, "极星科技")
    editor.memory_repo.link_episode_entities([
        MemoryEpisodeLink(episode_id=episode.id, entity_id=a.id, role="参与者"),
        MemoryEpisodeLink(episode_id=episode.id, entity_id=b.id, role="涉及物"),
    ])

    deleted = editor.delete_episode(episode.id)

    assert deleted.id == episode.id
    repo = _repo(engine)
    assert repo.get_episode(episode.id) is None
    assert all(link.entity_id != episode.id for bundle in
               repo.links_for_episodes([episode.id]).values() for link in bundle)
    assert repo.links_for_episodes([episode.id]) == {}
    assert ("episode", episode.id) in editor.vector_index.deleted
    with pytest.raises(MemoryObjectNotFoundError):
        editor.delete_episode(episode.id)  # 重复删除


def test_update_episode_link_repoints_and_clears_role(engine):
    editor = _editor(engine)
    episode = _seed_episode(engine)
    a = _entity(engine, "李总")
    b = _entity(engine, "极星科技")
    link = MemoryEpisodeLink(episode_id=episode.id, entity_id=a.id, role="参与者")
    editor.memory_repo.link_episode_entities([link])

    moved = editor.update_episode_link(link.id, entity_id=b.id)
    assert moved.entity_id == b.id and moved.role == "参与者"

    cleared = editor.update_episode_link(link.id, role="")
    assert cleared.entity_id == b.id and cleared.role is None


def test_update_episode_link_unique_conflict(engine):
    editor = _editor(engine)
    episode = _seed_episode(engine)
    a = _entity(engine, "李总")
    b = _entity(engine, "极星科技")
    editor.memory_repo.link_episode_entities([
        MemoryEpisodeLink(episode_id=episode.id, entity_id=a.id, role="参与者"),
        MemoryEpisodeLink(episode_id=episode.id, entity_id=b.id, role="参与者"),
    ])
    first = editor.episode_links_by_ids(
        [link.id for bundle in _repo(engine).links_for_episodes([episode.id]).values()
         for link in bundle])[0]

    # 把第一条改成第二条的 (entity, role) 组合 → 3007
    other = next(link for bundle in _repo(engine).links_for_episodes([episode.id]).values()
                 for link in bundle if link.id != first.id)
    with pytest.raises(MemoryConstraintConflictError):
        editor.update_episode_link(first.id, entity_id=other.entity_id, role=other.role)


def test_update_episode_link_guards(engine):
    editor = _editor(engine)
    episode = _seed_episode(engine)
    a = _entity(engine, "李总")
    link = MemoryEpisodeLink(episode_id=episode.id, entity_id=a.id, role="参与者")
    editor.memory_repo.link_episode_entities([link])
    svc = _service(engine)

    with pytest.raises(MemoryNoChangeError):
        svc.update_episode_link(TEST_USER_ID, f"l:{link.id}", _link_request())
    with pytest.raises(MemoryObjectNotFoundError):
        svc.update_episode_link(TEST_USER_ID, "l:9999", _link_request(role="参与者"))
    with pytest.raises(MemoryObjectNotFoundError):
        svc.update_episode_link(TEST_USER_ID, f"l:{link.id}", _link_request(entityId=9999))
    with pytest.raises(MemoryInvalidParamError):
        svc.update_episode_link(TEST_USER_ID, "不是引用", _link_request(role="参与者"))


def _episode_request(summary: str | None = None, scene: str | None = None,
                     occurredAt: str | None = None):
    from app.models.schema.request.memory import EpisodeUpdateRequest

    return EpisodeUpdateRequest(summary=summary, scene=scene, occurredAt=occurredAt)


def _link_request(entityId: int | None = None, role: str | None = None):
    from app.models.schema.request.memory import EpisodeLinkUpdateRequest

    return EpisodeLinkUpdateRequest(entityId=entityId, role=role)


def _naive(dt):
    return dt.replace(tzinfo=None) if dt is not None else None
