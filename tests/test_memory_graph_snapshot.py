"""图快照领域服务：当前态 / 时点回放双视图 + 时间参数校验。

纯单测：真实 SQLite 仓储 + 真实端口适配（MemoryRepositoryGraphReader），
覆盖边裁剪（ACTIVE vs 历史切片）、事件节点投影、字面量客体、限额与
非法 at 的业务异常。
"""

from datetime import datetime
import pytest

from app.components.memory.graph_reader import MemoryRepositoryGraphReader
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.exceptions.memory import MemoryInvalidTimeParamError
from app.models.domain.memory import (
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryStatement,
    StatementState,
)
from app.services.domain.memory import MemoryGraphService


def _world(engine):
    repo = SqliteGraphMemoryRepository(engine=engine)
    user = repo.upsert_entity(MemoryEntity(name="用户", entity_type="PERSON", is_user=True))
    boss = repo.upsert_entity(MemoryEntity(name="李总", entity_type="PERSON"))

    repo.insert_statement(MemoryStatement(  # 历史切片：已取代
        subject_id=user.id, predicate="职业", object_text="教师",
        summary="用户的职业是教师", state=StatementState.SUPERSEDED.value,
        valid_from=datetime(2026, 1, 10), valid_to=datetime(2026, 8, 20),
        invalidated_at=datetime(2026, 8, 20),
        source_thread_id=None, confidence=0.9,
    ))
    new_job = repo.insert_statement(MemoryStatement(  # 当前态
        subject_id=user.id, predicate="职业", object_text="后端开发",
        summary="用户的职业是后端开发", state=StatementState.ACTIVE.value,
        valid_from=datetime(2026, 8, 20),
    ))
    knows = repo.insert_statement(MemoryStatement(  # 实体间边
        subject_id=user.id, predicate="认识", object_entity_id=boss.id,
        summary="用户认识李总", state=StatementState.ACTIVE.value,
        valid_from=datetime(2026, 2, 1),
    ))
    ep_negotiation = repo.insert_episode(MemoryEpisode(
        thread_id=__import__("uuid").uuid4(),
        occurred_at=datetime(2026, 1, 15),
        scene="商业谈判", summary="回款节点谈判",
    ))
    repo.link_episode_entities([MemoryEpisodeLink(episode_id=ep_negotiation.id, entity_id=boss.id, role="对手方")])
    ep_future = repo.insert_episode(MemoryEpisode(
        thread_id=__import__("uuid").uuid4(),
        occurred_at=datetime(2026, 12, 1), summary="年底复盘会",
    ))
    return repo, user, boss, new_job, knows, ep_negotiation, ep_future


def _service(engine) -> MemoryGraphService:
    repo = SqliteGraphMemoryRepository(engine=engine)
    return MemoryGraphService(reader=MemoryRepositoryGraphReader(memory_repo=repo))


def test_current_snapshot_shape_and_active_only(engine):
    repo, user, boss, new_job, knows, ep_neg, ep_future = _world(engine)
    snap = _service(engine).graph_snapshot()

    node_kinds = {n["kind"] for n in snap["nodes"]}
    assert node_kinds == {"entity", "episode"}
    user_node = next(n for n in snap["nodes"] if n.get("isUser"))
    assert user_node["id"] == f"e:{user.id}" and user_node["name"] == "用户"

    stmt_edges = [e for e in snap["edges"] if e["kind"] == "statement"]
    assert {e["state"] for e in stmt_edges} == {"ACTIVE"}  # 当前态不含历史切片
    by_id = {e["id"]: e for e in stmt_edges}

    literal = by_id[f"s:{new_job.id}"]
    assert literal["target"] is None and literal["objectText"] == "后端开发"
    entity_edge = by_id[f"s:{knows.id}"]
    assert entity_edge["target"] == f"e:{boss.id}"

    assert f"ep:{ep_neg.id}" in {n["id"] for n in snap["nodes"]}
    assert f"ep:{ep_future.id}" in {n["id"] for n in snap["nodes"]}
    link_edges = [e for e in snap["edges"] if e["kind"] == "episode_link"]
    assert link_edges[0]["source"] == f"ep:{ep_neg.id}" and link_edges[0]["role"] == "对手方"

    assert snap["at"] is None and snap["generatedAt"]
    assert snap["stats"]["statementEdges"] == len(stmt_edges)


def test_time_slice_replays_history_with_occurrence_gate(engine):
    _world(engine)
    svc = _service(engine)

    march = svc.graph_snapshot(at_iso="2026-03-01")
    march_states = {e["state"] for e in march["edges"] if e["kind"] == "statement"}
    assert "SUPERSEDED" in march_states                    # 回放看到“当时是什么”
    march_ep_ids = {n["id"] for n in march["nodes"] if n["kind"] == "episode"}
    assert march_ep_ids == set() or all("年底复盘" not in
        next(n["summary"] for n in march["nodes"] if n["id"] == nid) for nid in march_ep_ids)

    late = svc.graph_snapshot(at_iso="2026-08-25")
    late_predicates = [e["objectText"] for e in late["edges"] if e["kind"] == "statement"]
    assert "后端开发" in late_predicates and "教师" not in late_predicates
    late_ep_summaries = [n["summary"] for n in late["nodes"] if n["kind"] == "episode"]
    assert any("回款节点谈判" in s for s in late_ep_summaries)
    assert not any("年底复盘" in s for s in late_ep_summaries)  # occurred_at 晚于锚不出现


def test_invalid_at_raises_business_error(engine):
    with pytest.raises(MemoryInvalidTimeParamError):
        _service(engine).graph_snapshot(at_iso="大概是上个月吧")


def test_limit_caps_collections(engine):
    repo = SqliteGraphMemoryRepository(engine=engine)
    user = repo.upsert_entity(MemoryEntity(name="用户", entity_type="PERSON", is_user=True))
    for index in range(4):
        repo.insert_statement(MemoryStatement(
            subject_id=user.id, predicate="偏好", object_text=f"爱好{index}",
            summary=f"用户的偏好是爱好{index}", state=StatementState.ACTIVE.value,
            valid_from=datetime(2026, 1, 1),
        ))
    snap = _service(engine).graph_snapshot(limit=2)
    assert snap["stats"]["statementEdges"] == 2
    assert snap["stats"]["episodeLinkEdges"] == 0
