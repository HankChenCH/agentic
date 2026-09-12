"""记忆管理面（L1–L4 编辑闭环）的 HTTP 集成测试。

真实 MemoryAppService（容器解析，仅向量索引换替身）+ 临时 SQLite 全迁移。
此前只有 401 鉴权 sweep 与下层 editor/门面单测，HTTP 面（信封/错误码映射/
归属作用域/快照 id 引用形态）在本文件锁定：
- L1 事实：手工补充（subjectName 引用不存在则建实体）→ 取代式纠正 → 归档；
- L2 身份：档案直改 / 错分离合并 / 错合并拆分 / 孤立清理（守卫口径）；
- L3 事件：档案直改 / 参与改挂（事件与参与边直接种库——编辑 API 无建档入口）；
- L4 危险操作：清除预览 / 范围清除 / 导出 / 整体重置。
"""

import os
from uuid import uuid4

import pytest
from sqlmodel import Session, create_engine

import http_test_kit  # noqa: F401  必须先于 app.* 导入（env 就位）
from fakes_memory import FakeMemoryVectorIndex
from http_test_kit import (  # noqa: E402
    build_app,
    new_username,
    register_and_login,
    http_client_for,
)

from app.domain.memory.ports import MemoryVectorIndexPort  # noqa: E402
from app.models.domain.memory import MemoryEpisode, MemoryEpisodeLink  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with http_client_for(build_app([(MemoryVectorIndexPort, FakeMemoryVectorIndex())])) as test_client:
        yield test_client


def _engine():
    """与被测 app 同库的直连引擎（种事件行用——编辑 API 无建档入口）。"""
    engine = create_engine(f"sqlite:///{os.environ['SQLITE_DB_PATH']}")
    return engine


def _num(ref: str) -> int:
    """图快照 id 形态（e:5 / s:13 / ep:1 / l:2）→ 裸数字 id。"""
    return int(ref.split(":", 1)[1])


def _snapshot(client, headers):
    resp = client.get("/memory/graph", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["response"]


def _nodes(snapshot, kind):
    return {n["id"]: n for n in snapshot["nodes"] if n["kind"] == kind}


def _add_statement(client, headers, **fields):
    resp = client.post("/memory/statements", headers=headers, json=fields)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["error_code"] == 0
    return body["response"]


def _seed_entity_with_statement(client, headers, name, object_text, predicate="偏好"):
    """经手工补充事实建实体（subjectName 引用不存在则建）：返回 (entity_ref, statement)。"""
    statement = _add_statement(client, headers, subjectName=name, predicate=predicate, objectText=object_text)
    return statement["source"], statement


# ==================== L1 事实：补充 / 纠正 / 归档 ====================


def test_manual_statement_creates_entity_and_appears_in_graph(client):
    headers, _ = register_and_login(client, new_username("mem"))

    edge = _add_statement(client, headers, subjectName="张三", predicate="职业", objectText="工程师")
    assert edge["kind"] == "statement"
    assert edge["source"].startswith("e:")

    snapshot = _snapshot(client, headers)
    entity = _nodes(snapshot, "entity")[edge["source"]]
    assert entity["name"] == "张三"
    statements = [e for e in snapshot["edges"] if e["kind"] == "statement"]
    assert any(e["id"] == edge["id"] for e in statements)


def test_correct_statement_supersedes_and_archive_hides(client):
    headers, _ = register_and_login(client, new_username("mem"))
    edge = _add_statement(client, headers, subjectName="李四", predicate="职业", objectText="讲师")

    # 取代式纠正：旧行 SUPERSEDED、新行 ACTIVE（非原地改写）
    corrected = client.patch(
        f"/memory/statements/{edge['id']}", headers=headers,
        json={"objectText": "副教授"},
    )
    assert corrected.status_code == 200, corrected.text
    mutation = corrected.json()["response"]
    # 取代链：新行新 id，旧行退出在效集（字面量客体的 target 恒为 None）
    assert mutation["new"]["id"] != edge["id"]

    # 无变化的纠正拒绝：3006/400
    nochange = client.patch(
        f"/memory/statements/{mutation['new']['id']}", headers=headers,
        json={"objectText": "副教授"},
    )
    assert nochange.status_code == 400 and nochange.json()["error_code"] == 3006

    # 归档（软删）：退出当前图谱
    archived = client.delete(f"/memory/statements/{mutation['new']['id']}", headers=headers)
    assert archived.status_code == 200, archived.text
    snapshot = _snapshot(client, headers)
    assert all(e["id"] != mutation["new"]["id"] for e in snapshot["edges"] if e["kind"] == "statement")


def test_statement_object_entity_choice_enforced(client):
    """客体二选一：objectEntityId 与 objectText 同给 → 3008/400。"""
    headers, _ = register_and_login(client, new_username("mem"))
    entity_ref, _ = _seed_entity_with_statement(client, headers, "王五", "跑步", predicate="兴趣")
    resp = client.post(
        "/memory/statements", headers=headers,
        json={"subjectName": "王五", "predicate": "偏好", "objectText": "茶",
              "objectEntityId": _num(entity_ref)},
    )
    assert resp.status_code == 400 and resp.json()["error_code"] == 3008


# ==================== L2 身份：直改 / 合并 / 拆分 / 清理 ====================


def test_entity_update_and_merge_flow(client):
    headers, _ = register_and_login(client, new_username("mem"))
    alice_ref, _ = _seed_entity_with_statement(client, headers, "爱丽丝", "跑步", predicate="兴趣")
    bob_ref, _ = _seed_entity_with_statement(client, headers, "鲍勃", "游泳", predicate="兴趣")

    # 档案直改：别名全量替换
    updated = client.patch(f"/memory/entities/{alice_ref}", headers=headers, json={"aliases": ["Alice"]})
    assert updated.status_code == 200, updated.text
    assert "Alice" in updated.json()["response"]["aliases"]

    # 改名撞他实体名 → 3005/409
    conflict = client.patch(f"/memory/entities/{alice_ref}", headers=headers, json={"name": "鲍勃"})
    assert conflict.status_code == 409 and conflict.json()["error_code"] == 3005

    # 错分离合并：source（含历史行）并入 target 后删除 source
    merged = client.post(
        f"/memory/entities/{alice_ref}/merge", headers=headers, json={"targetRef": bob_ref},
    )
    assert merged.status_code == 200, merged.text
    snapshot = _snapshot(client, headers)
    assert alice_ref not in _nodes(snapshot, "entity")
    # 存留方档案文本收敛两实体事实（合并后 source 的陈述改挂 target）
    kept_statements = [e for e in snapshot["edges"] if e["kind"] == "statement"]
    assert {e["source"] for e in kept_statements} == {bob_ref}


def test_split_entity_moves_statements_and_blocklists(client):
    headers, _ = register_and_login(client, new_username("mem"))
    source_ref, first = _seed_entity_with_statement(client, headers, "小明", "围棋", predicate="兴趣")
    second = _add_statement(client, headers, subjectName="小明", predicate="技能", objectText="编程")

    split = client.post(
        f"/memory/entities/{source_ref}/split", headers=headers,
        json={"name": "小明（技能面）", "statementIds": [second["id"]]},
    )
    assert split.status_code == 200, split.text
    snapshot = _snapshot(client, headers)
    moved = next(e for e in snapshot["edges"] if e["id"] == second["id"])
    new_ref = moved["source"]
    assert new_ref != source_ref
    assert _nodes(snapshot, "entity")[new_ref]["name"] == "小明（技能面）"
    # 原陈述留在 source
    assert next(e for e in snapshot["edges"] if e["id"] == first["id"])["source"] == source_ref


def test_delete_entity_guards(client):
    headers, _ = register_and_login(client, new_username("mem"))
    entity_ref, statement = _seed_entity_with_statement(client, headers, "小红", "阅读", predicate="兴趣")

    # 仍被事实引用：3008/400，先归档关联事实
    blocked = client.delete(f"/memory/entities/{entity_ref}", headers=headers)
    assert blocked.status_code == 400 and blocked.json()["error_code"] == 3008

    # 归档（软删）不改引用关系：历史行仍在，清理守卫同样拒绝——孤立清理
    # 只对「零引用」实体开放（真孤儿只出现在合并删除 source 的路径里）
    client.delete(f"/memory/statements/{statement['id']}", headers=headers)
    still_blocked = client.delete(f"/memory/entities/{entity_ref}", headers=headers)
    assert still_blocked.status_code == 400 and still_blocked.json()["error_code"] == 3008


def test_memory_scoped_to_owner(client):
    """归属作用域：他人对象按不存在处理（404/3002），不泄露存在性。"""
    headers_a, _ = register_and_login(client, new_username("mem"))
    entity_ref, _ = _seed_entity_with_statement(client, headers_a, "私有实体", "私有事实")

    headers_b, _ = register_and_login(client, new_username("mem"))
    resp = client.patch(f"/memory/entities/{entity_ref}", headers=headers_b, json={"name": "抢占"})
    assert resp.status_code == 404 and resp.json()["error_code"] == 3002

    resp = client.delete("/memory/statements/s:99999999", headers=headers_b)
    assert resp.status_code == 404 and resp.json()["error_code"] == 3002


# ==================== L3 事件：档案直改 / 参与改挂 ====================


def _seed_episode(user_id: str, entity_id: int):
    """直接种事件行 + 参与边（编辑 API 无建档入口，建档只发生在巩固链路）。"""
    from uuid import UUID as _UUID

    with Session(_engine()) as session:
        from datetime import datetime, timezone

        episode = MemoryEpisode(
            user_id=_UUID(user_id), thread_id=uuid4(), summary="产品发布会议", scene="工作",
            occurred_at=datetime.now(timezone.utc),
        )
        session.add(episode)
        session.flush()
        link = MemoryEpisodeLink(episode_id=episode.id, entity_id=entity_id, role="参与者")
        session.add(link)
        session.commit()
        return episode.id, link.id


def test_episode_update_delete_and_link_role_clear(client):
    headers, token = register_and_login(client, new_username("mem"))
    user_id = client.get("/auth/me", headers=headers).json()["response"]["id"]
    entity_ref, _ = _seed_entity_with_statement(client, headers, "赵六", "主持", predicate="担任")

    episode_id, link_id = _seed_episode(user_id, _num(entity_ref))
    episode = client.patch(
        f"/memory/episodes/ep:{episode_id}", headers=headers,
        json={"summary": "产品发布会议（改）", "scene": ""},
    )
    assert episode.status_code == 200, episode.text
    assert episode.json()["response"]["summary"] == "产品发布会议（改）"
    assert episode.json()["response"]["scene"] is None

    # 参与改挂：清除角色（空串语义）
    relink = client.patch(
        f"/memory/episode-links/l:{link_id}", headers=headers, json={"role": ""},
    )
    assert relink.status_code == 200, relink.text
    assert relink.json()["response"]["role"] is None

    deleted = client.delete(f"/memory/episodes/ep:{episode_id}", headers=headers)
    assert deleted.status_code == 200, deleted.text


# ==================== L4 危险操作区 ====================


def test_maintenance_preview_purge_export_reset(client):
    headers, _ = register_and_login(client, new_username("mem"))
    _seed_entity_with_statement(client, headers, "周七", "钓鱼", predicate="兴趣")

    # 清除影响面预览（scope=day）：返回计数结构
    preview = client.get(
        "/memory/maintenance/purge-preview", headers=headers,
        params={"scope": "day", "from": "2026-01-01", "to": "2030-01-01"},
    )
    assert preview.status_code == 200, preview.text
    counts = preview.json()["response"]
    assert counts["statements"] >= 1

    # 范围清除（scope=day 全窗口）：在效事实归档、事件物理删除、孤立实体清理
    purge = client.post(
        "/memory/maintenance/purge", headers=headers,
        json={"scope": "day", "from": "2026-01-01", "to": "2030-01-01"},
    )
    assert purge.status_code == 200, purge.text

    # 导出：四表全量 JSON（Content-Disposition 附件头）
    export = client.get("/memory/maintenance/export", headers=headers)
    assert export.status_code == 200, export.text
    assert "attachment" in export.headers.get("content-disposition", "")

    # 整体重置：confirmation 逐字校验
    wrong = client.post("/memory/maintenance/reset", headers=headers, json={"confirmation": "删除"})
    assert wrong.status_code == 400
    reset = client.post("/memory/maintenance/reset", headers=headers, json={"confirmation": "重置"})
    assert reset.status_code == 200, reset.text
    snapshot = _snapshot(client, headers)
    assert snapshot["nodes"] == [] and snapshot["edges"] == []
