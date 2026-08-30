"""实体身份纠错（L2）：错分离合并、错合并拆分、孤立清理与拆分禁令。"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.components.memory.admin import MemoryRepositoryEditor
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.components.memory import MemoryConsolidationService
from app.exceptions.memory import (
    MemoryInvalidParamError,
    MemoryNameConflictError,
    MemoryNoChangeError,
    MemoryObjectNotFoundError,
    MemoryProtectedObjectError,
)
from app.models.domain.memory import (
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryOrigin,
    MemoryStatement,
    StatementState,
)
from app.services.domain.memory import MemoryAdminService
from app.services.domain.memory.ports import EntitySplitSpec
from app.services.domain.memory.vector_index import MemoryVectorHit

from conftest import TEST_USER_ID
from fakes_memory import (
    CannedLLM,
    FakeMemoryVectorIndex,
    FakeModelFactory,
    make_service_config,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)


def _repo(engine) -> SqliteGraphMemoryRepository:
    return SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)


def _editor(engine) -> MemoryRepositoryEditor:
    return MemoryRepositoryEditor(memory_repo=_repo(engine), vector_index=FakeMemoryVectorIndex())


def _service(engine) -> MemoryAdminService:
    return MemoryAdminService(editor=_editor(engine))


def _entity(name: str, entity_type: str = "ORG", aliases: list[str] | None = None,
            **kwargs) -> MemoryEntity:
    return MemoryEntity(name=name, entity_type=entity_type, aliases=aliases or [], **kwargs)


def _statement(engine, subject: MemoryEntity, predicate: str,
               object_entity: MemoryEntity | None = None,
               object_text: str | None = None,
               state: str = StatementState.ACTIVE.value) -> MemoryStatement:
    return _repo(engine).insert_statement(MemoryStatement(
        subject_id=subject.id,
        predicate=predicate,
        object_entity_id=object_entity.id if object_entity else None,
        object_text=object_text if object_entity is None else None,
        summary=f"{subject.name}{predicate}"
                f"{object_entity.name if object_entity else object_text}",
        state=state,
        origin=MemoryOrigin.EXTRACTED.value,
    ))


def _episode_with_link(engine, entity: MemoryEntity, role: str = "参与者") -> MemoryEpisodeLink:
    repo = _repo(engine)
    episode = repo.insert_episode(MemoryEpisode(
        thread_id=uuid4(), summary="一次事件", occurred_at=NOW))
    link = MemoryEpisodeLink(episode_id=episode.id, entity_id=entity.id, role=role)
    repo.link_episode_entities([link])
    return link


# ---------------- 合并（错分离 → 归一）----------------


def _seed_duplicate(engine):
    """错分离现场：同一公司散成两行——A=广东禹通（挂用户的现存事实），
    B=禹通科技（挂着张伟就职行、一条 SUPERSEDED 历史行与事件参与）。"""
    editor = _editor(engine)
    a = editor.memory_repo.upsert_entity(_entity("广东禹通"))
    b = editor.memory_repo.upsert_entity(_entity("禹通科技", aliases=["禹通"]))
    zhang = editor.memory_repo.upsert_entity(_entity("张伟", entity_type="PERSON"))
    user = editor.memory_repo.upsert_entity(_entity("用户", entity_type="PERSON", is_user=True))
    history = _statement(engine, user, "所属公司", object_entity=b,
                         state=StatementState.SUPERSEDED.value)
    job = _statement(engine, zhang, "就职于", object_entity=b)
    current = _statement(engine, user, "所属公司", object_entity=a)
    link = _episode_with_link(engine, b)
    return editor, a, b, {"history": history, "job": job, "current": current, "link": link}


def test_merge_moves_all_rows_and_deletes_source(engine):
    editor, a, b, seeded = _seed_duplicate(engine)

    result = editor.merge_entity(b.id, a.id)

    repo = _repo(engine)
    assert result["merged"] is True
    assert result["statements"] == 2 and result["links"] == 1
    assert editor.get_entity(b.id) is None
    # 零悬挂引用：两行（含 SUPERSEDED 历史行）都改挂到 a，现存行不受影响
    for row in (*result["moved"], seeded["current"]):
        assert b.id not in (row.subject_id, row.object_entity_id)
    reloaded = {r.id: r for r in repo.statements_by_ids(
        [seeded["history"].id, seeded["job"].id])}
    assert reloaded[seeded["history"].id].object_entity_id == a.id
    assert reloaded[seeded["history"].id].state == StatementState.SUPERSEDED.value
    assert reloaded[seeded["history"].id].summary == "用户的所属公司是广东禹通"  # 单值谓词句式
    assert reloaded[seeded["job"].id].summary == "张伟就职于广东禹通"  # multi 谓词句式
    # 名字+别名并入 target
    target = editor.get_entity(a.id)
    assert {"禹通科技", "禹通"} <= set(target.aliases)
    # 向量同步：source 档案退场、target 档案重写、被改挂行覆盖嵌入
    assert ("entity", b.id) in editor.vector_index.deleted
    assert any(e.kind == "entity" and e.ref_id == a.id for e in editor.vector_index.upserts)
    assert {(e.kind, e.ref_id) for e in editor.vector_index.upserts} >= {
        ("statement", seeded["history"].id), ("statement", seeded["job"].id),
    }


def test_merge_guards(engine):
    editor, a, b, _seeded = _seed_duplicate(engine)
    with pytest.raises(MemoryInvalidParamError):
        editor.merge_entity(a.id, a.id)  # 自并
    user = editor.find_entity_by_name("用户")
    with pytest.raises(MemoryProtectedObjectError):
        editor.merge_entity(user.id, a.id)  # 用户节点不可作被并方
    with pytest.raises(MemoryObjectNotFoundError):
        editor.merge_entity(9999, a.id)
    with pytest.raises(MemoryObjectNotFoundError):
        editor.merge_entity(b.id, 9999)


# ---------------- 拆分（错合并 → 分离）----------------


def _seed_wrong_merge(engine):
    """错合并现场：学校的事实/别名/事件参与全部挂在公司「广东禹通」档案上。"""
    editor = _editor(engine)
    company = editor.memory_repo.upsert_entity(
        _entity("广东禹通", aliases=["广州市卫生职业技术学院"]))
    user = editor.memory_repo.upsert_entity(_entity("用户", entity_type="PERSON", is_user=True))
    school_fact = _statement(engine, user, "毕业院校", object_entity=company)
    company_fact = _statement(engine, user, "所属公司", object_entity=company)
    link = _episode_with_link(engine, company, role="就读")
    return editor, company, school_fact, company_fact, link


def test_split_moves_selected_content_and_writes_blocklist(engine):
    editor, company, school_fact, company_fact, link = _seed_wrong_merge(engine)

    result = editor.split_entity(
        company.id,
        EntitySplitSpec(
            name="广州市卫生职业技术学院",
            entity_type="ORG",
            aliases=("广州市卫生职业技术学院",),
            statement_ids=(school_fact.id,),
            episode_link_ids=(link.id,),
        ),
    )

    school = result["new"]
    assert school.origin == MemoryOrigin.MANUAL.value
    assert school.aliases == ["广州市卫生职业技术学院"]
    # 客体侧陈述改挂 + summary 重组（单值谓词「的…是」句式）
    moved = result["moved"]
    assert [r.id for r in moved] == [school_fact.id]
    assert moved[0].object_entity_id == school.id
    assert moved[0].summary == "用户的毕业院校是广州市卫生职业技术学院"
    # source 仍持有公司事实 → 不自动删除；别名已迁出
    assert result["source_deleted"] is False
    company_after = editor.get_entity(company.id)
    assert company_after is not None
    assert "广州市卫生职业技术学院" not in company_after.aliases
    # 拆分禁令双方互写（attributes.merge_blocklist）
    assert "广州市卫生职业技术学院" in (company_after.attributes or {}).get("merge_blocklist", [])
    assert "广东禹通" in (school.attributes or {}).get("merge_blocklist", [])
    # 向量：双方档案 + 被改挂陈述
    upsert_ids = {(e.kind, e.ref_id) for e in editor.vector_index.upserts}
    assert ("entity", school.id) in upsert_ids and ("entity", company.id) in upsert_ids
    assert ("statement", school_fact.id) in upsert_ids


def test_split_deletes_emptied_source(engine):
    editor = _editor(engine)
    a = editor.memory_repo.upsert_entity(_entity("错合并体", aliases=["被拆走的名"]))
    user = editor.memory_repo.upsert_entity(_entity("用户", entity_type="PERSON", is_user=True))
    only_fact = _statement(engine, user, "偏好", object_text="游泳")

    result = editor.split_entity(
        a.id,
        EntitySplitSpec(name="游泳爱好者", entity_type="PERSON",
                        aliases=("被拆走的名",), statement_ids=(only_fact.id,)),
    )

    # 唯一陈述与别名都拆走 → source 拆空自动删除（blocklist 随行消亡）
    assert result["source_deleted"] is True
    assert editor.get_entity(a.id) is None
    assert ("entity", a.id) in editor.vector_index.deleted
    assert editor.get_entity(result["new"].id).aliases == ["被拆走的名"]


def test_split_request_guards(engine):
    editor, company, school_fact, company_fact, link = _seed_wrong_merge(engine)
    svc = _service(engine)
    ref = f"e:{company.id}"
    other = editor.memory_repo.upsert_entity(_entity("别的实体"))

    with pytest.raises(MemoryNoChangeError):
        svc.split_entity(TEST_USER_ID, ref, _split_request(name="新学校"))  # 什么都没选
    with pytest.raises(MemoryInvalidParamError):
        svc.split_entity(TEST_USER_ID, ref, _split_request(name="广东禹通", statementIds=[f"s:{school_fact.id}"]))
    with pytest.raises(MemoryNameConflictError):
        svc.split_entity(TEST_USER_ID, ref, _split_request(name="别的实体", statementIds=[f"s:{school_fact.id}"]))
    with pytest.raises(MemoryInvalidParamError):
        svc.split_entity(TEST_USER_ID, ref, _split_request(
            name="新学校", statementIds=[f"s:{school_fact.id}"], entityType="VEHICLE"))
    with pytest.raises(MemoryInvalidParamError):
        svc.split_entity(TEST_USER_ID, ref, _split_request(
            name="新学校", statementIds=[f"s:{school_fact.id}"],
            episodeLinkIds=[f"l:{_episode_with_link(engine, other).id}"]))
    # 陈述不属于 source（挂在别的实体上）→ 拒绝跨实体误迁移
    foreign_statement = _statement(engine, other, "偏好", object_text="爬山")
    with pytest.raises(MemoryInvalidParamError):
        svc.split_entity(TEST_USER_ID, ref, _split_request(
            name="新学校", statementIds=[f"s:{foreign_statement.id}"]))


def _split_request(name: str, statementIds: list[str] | None = None,
                   episodeLinkIds: list[str] | None = None,
                   aliases: list[str] | None = None, entityType: str = "ORG"):
    from app.models.schema.request.memory import EntitySplitRequest

    return EntitySplitRequest(
        name=name, entityType=entityType,
        aliases=aliases or [],
        statementIds=statementIds or [],
        episodeLinkIds=episodeLinkIds or [],
    )


# ---------------- 孤立清理 ----------------


def test_delete_orphan_entity_and_guards(engine):
    editor, a, b, _seeded = _seed_duplicate(engine)
    orphan = editor.memory_repo.upsert_entity(_entity("空壳实体"))

    deleted = editor.delete_orphan_entity(orphan.id)
    assert deleted.name == "空壳实体"
    assert ("entity", orphan.id) in editor.vector_index.deleted

    # 仍被事实引用 → 拒绝；用户节点 → 保护
    with pytest.raises(MemoryInvalidParamError):
        editor.delete_orphan_entity(a.id)
    user = editor.find_entity_by_name("用户")
    with pytest.raises(MemoryProtectedObjectError):
        editor.delete_orphan_entity(user.id)


# ---------------- 拆分禁令：优先于余弦排序 ----------------


def test_merge_blocklist_overrides_cosine_ranking(engine):
    repo = _repo(engine)
    a = repo.upsert_entity(_entity("禹通档案", aliases=["禹通公司"],
                                   attributes={"merge_blocklist": ["卫职院", "卫生职院"]}))
    b = repo.upsert_entity(_entity("卫职院档案", aliases=["卫职院"],
                                   attributes={"merge_blocklist": ["禹通档案", "禹通公司"]}))
    svc = MemoryConsolidationService(
        memory_repo=repo,
        vector_index=FakeMemoryVectorIndex(
            preset_hits=[
                MemoryVectorHit(kind="entity", ref_id=a.id, score=0.99),
                MemoryVectorHit(kind="entity", ref_id=b.id, score=0.98),
            ],
            preset_cosines=[0.90, 0.86, 0.95, 0.94],
            # 第 1 次判定弹出 [0.90, 0.86]：A 余弦更高本该 A 胜出；
            # 第 2 次（对照）弹出 [0.95, 0.94]：与候选行顺序无关，A 恢复胜出
        ),
        model_factory=FakeModelFactory(CannedLLM()),
        app_config=make_service_config(),
    )
    from app.components.memory.internal.extraction import ExtractedEntity

    # A/B 是禁令对且双双达标：禁令优先于余弦排序，归属 B 而非 A
    candidate = ExtractedEntity(key="k1", name="卫职院新表述", entity_type="ORG")
    assert svc._resolve_entities((candidate,), svc.memory_repo, svc.vector_index)["k1"].id == b.id

    # 对照：禁令对整体解除后（合并语义会清两侧），余弦排序恢复决定权
    # （换新表述避开上轮并入的别名）
    a.attributes = {}
    b.attributes = {}
    repo.save_entity(a)
    repo.save_entity(b)
    control = ExtractedEntity(key="k2", name="另一处新表述", entity_type="ORG")
    assert svc._resolve_entities((control,), svc.memory_repo, svc.vector_index)["k2"].id == a.id
