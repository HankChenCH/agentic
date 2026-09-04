"""记忆编辑（L1）：取代式纠正、归档、手工补充、实体改名的落库与守卫语义。"""

from datetime import datetime, timezone

import pytest

from app.components.memory.admin import MemoryRepositoryEditor
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.exceptions.memory import (
    MemoryInvalidParamError,
    MemoryNameConflictError,
    MemoryNoChangeError,
    MemoryObjectNotFoundError,
)
from app.models.domain.memory import MemoryEntity, MemoryOrigin, StatementState
from app.models.schema.request.memory import (
    EntityUpdateRequest,
    StatementCorrectRequest,
)
from app.domain.memory import MemoryAdminService
from app.domain.memory.ports import FactWrite

from conftest import TEST_USER_ID
from fakes_memory import FakeMemoryVectorIndex

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 8, 29, 9, 0, tzinfo=timezone.utc)


def _repo(engine) -> SqliteGraphMemoryRepository:
    # 作用域视图：插入行自动钉归属（与用户侧行程一致的全局算子禁入语义）
    return SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)


def _editor(engine) -> MemoryRepositoryEditor:
    return MemoryRepositoryEditor(memory_repo=_repo(engine), vector_index=FakeMemoryVectorIndex())


def _service(engine) -> MemoryAdminService:
    return MemoryAdminService(editor=_editor(engine))


def _seed_fact(engine, predicate="职业", object_text="教师", valid_from=None):
    """经编辑端口落一条字面量事实（主体「用户」随建），返回 (editor, row)。"""
    editor = _editor(engine)
    row = editor.add_statement(
        FactWrite(subject_name="用户", predicate=predicate, object_text=object_text,
                  valid_from=valid_from),
        now=NOW,
    )
    return editor, row


def _naive(dt):
    return dt.replace(tzinfo=None) if dt is not None else None


# ---------------- 手工补充 ----------------


def test_add_creates_subject_entity_and_literal_fact(engine):
    editor = _editor(engine)
    row = editor.add_statement(
        FactWrite(subject_name="张伟", predicate="职业", object_text="产品", note="聊天里他自己说的"),
        now=NOW,
    )

    assert row.origin == MemoryOrigin.MANUAL.value and row.confidence == 1.0
    assert row.summary == "张伟的职业是产品"  # 单值谓词句式
    assert row.evidence == [{"quote": "聊天里他自己说的"}]
    subject = editor.find_entity_by_name("张伟")
    assert subject.origin == MemoryOrigin.MANUAL.value
    kinds = {(e.kind, e.ref_id) for e in editor.vector_index.upserts}
    assert ("statement", row.id) in kinds
    assert ("entity", subject.id) in kinds


def test_add_with_entity_object_uses_generic_wording_for_multi_predicate(engine):
    editor = _editor(engine)
    company = editor.memory_repo.upsert_entity(
        _entity(name="极星科技", entity_type="ORG"))
    school = editor.memory_repo.upsert_entity(
        _entity(name="广州市卫生职业技术学院", entity_type="ORG"))

    row = editor.add_statement(
        FactWrite(subject_entity_id=company.id, predicate="服务学校", object_entity_id=school.id),
        now=NOW,
    )
    assert row.summary == "极星科技服务学校广州市卫生职业技术学院"  # 多值谓词无「的/是」


def test_add_rejects_bad_object_and_missing_subject(engine):
    editor = _editor(engine)
    with pytest.raises(MemoryInvalidParamError):
        editor.add_statement(FactWrite(subject_name="张伟", predicate="职业"), now=NOW)
    with pytest.raises(MemoryInvalidParamError):
        editor.add_statement(
            FactWrite(subject_name="张伟", predicate="职业",
                      object_text="产品", object_entity_id=1), now=NOW)
    with pytest.raises(MemoryObjectNotFoundError):
        editor.add_statement(
            FactWrite(subject_name="张伟", predicate="职业", object_entity_id=999), now=NOW)
    with pytest.raises(MemoryInvalidParamError):
        editor.add_statement(FactWrite(predicate="职业", object_text="产品"), now=NOW)
    with pytest.raises(MemoryInvalidParamError):
        editor.add_statement(
            FactWrite(subject_name="张伟", predicate="  ", object_text="产品"), now=NOW)


# ---------------- 取代式纠正 ----------------


def test_correct_literal_value_supersedes_old_and_keeps_valid_from(engine):
    editor, old = _seed_fact(engine, valid_from=datetime(2026, 8, 1, tzinfo=timezone.utc))

    mutation = editor.correct_statement(old.id, FactWrite(object_text="后端开发"), now=LATER)

    reloaded_old = _repo(engine).get_statement(mutation.old.id)
    assert reloaded_old.state == StatementState.SUPERSEDED.value
    assert reloaded_old.valid_to is not None and reloaded_old.invalidated_at is not None
    assert reloaded_old.object_text == "教师"

    new = mutation.new
    assert new.id != old.id
    assert new.state == StatementState.ACTIVE.value
    assert new.origin == MemoryOrigin.MANUAL.value
    assert new.object_text == "后端开发"
    assert new.summary == "用户的职业是后端开发"  # 按新值重组
    assert _naive(new.valid_from) == datetime(2026, 8, 1)  # 未提供则沿用原生效起点

    assert ("statement", old.id) in editor.vector_index.deleted  # 旧值嵌入退场
    assert any(e.ref_id == new.id and e.kind == "statement" for e in editor.vector_index.upserts)


def test_correct_switch_to_entity_object_recomposes_summary(engine):
    editor, old = _seed_fact(engine)  # 用户的职业是教师
    target = editor.memory_repo.upsert_entity(_entity(name="管理层", entity_type="CONCEPT"))

    mutation = editor.correct_statement(
        old.id, FactWrite(object_entity_id=target.id, note="转管理岗了"), now=LATER)

    assert mutation.new.object_entity_id == target.id and mutation.new.object_text is None
    assert mutation.new.summary == "用户的职业是管理层"
    assert mutation.new.evidence == [{"quote": "转管理岗了"}]


def test_correct_partial_request_keeps_unspecified_fields(engine):
    _, old = _seed_fact(engine)
    svc = _service(engine)

    result = svc.correct_statement(TEST_USER_ID, 
        f"s:{old.id}", StatementCorrectRequest(summary="用户当前职业为教师"))

    assert result["new"]["objectText"] == "教师"  # 未提供的字段保持原值
    assert result["new"]["summary"] == "用户当前职业为教师"
    assert result["old"]["state"] == "SUPERSEDED" and result["new"]["state"] == "ACTIVE"


def test_correct_identical_values_rejected_as_no_change(engine):
    _, old = _seed_fact(engine)
    svc = _service(engine)
    with pytest.raises(MemoryNoChangeError):
        svc.correct_statement(TEST_USER_ID, f"s:{old.id}", StatementCorrectRequest(objectText="教师"))
    with pytest.raises(MemoryNoChangeError):
        svc.correct_statement(TEST_USER_ID, f"s:{old.id}", StatementCorrectRequest(note="只想补个备注"))


def test_correct_rejects_missing_and_non_active_targets(engine):
    editor, old = _seed_fact(engine)
    editor.archive_statement(old.id, now=LATER)

    with pytest.raises(MemoryObjectNotFoundError):
        editor.correct_statement(9999, FactWrite(object_text="x"), now=LATER)
    with pytest.raises(MemoryInvalidParamError):
        editor.correct_statement(old.id, FactWrite(object_text="x"), now=LATER)  # 已归档


def test_ref_parsing_accepts_snapshot_shape_and_rejects_garbage(engine):
    _, old = _seed_fact(engine)
    svc = _service(engine)
    with pytest.raises(MemoryObjectNotFoundError):
        svc.archive_statement(TEST_USER_ID, "s:9999")  # 形态合法、目标不存在
    with pytest.raises(MemoryInvalidParamError):
        svc.archive_statement(TEST_USER_ID, "e:1")  # 跨种类引用
    with pytest.raises(MemoryInvalidParamError):
        svc.archive_statement(TEST_USER_ID, "not-a-ref")


# ---------------- 归档 ----------------


def test_archive_soft_deletes_and_syncs_vector(engine):
    editor, old = _seed_fact(engine)

    row = editor.archive_statement(old.id, now=LATER)

    assert row.state == StatementState.ARCHIVED.value
    assert row.valid_to is not None and row.invalidated_at is not None
    assert old.id not in {r.id for r in _repo(engine).list_active_statements()}
    assert ("statement", old.id) in editor.vector_index.deleted
    with pytest.raises(MemoryInvalidParamError):
        editor.archive_statement(old.id, now=LATER)  # 重复归档


# ---------------- 实体改名 / 别名 / 类型 ----------------


def test_update_entity_replaces_aliases_and_rewrites_entity_vector(engine):
    editor = _editor(engine)
    entity = editor.memory_repo.upsert_entity(_entity(name="禹通", aliases=["禹通公司"]))

    row = editor.update_entity(entity.id, name="广东禹通", aliases=["禹通", "YT"])

    assert row.name == "广东禹通" and row.aliases == ["YT", "禹通"]  # 全量替换语义
    assert any(
        e.kind == "entity" and e.ref_id == entity.id and "广东禹通" in e.content
        for e in editor.vector_index.upserts
    )


def test_update_entity_name_conflicts_with_other_entity_name_or_alias(engine):
    editor = _editor(engine)
    mine = editor.memory_repo.upsert_entity(_entity(name="禹通"))
    editor.memory_repo.upsert_entity(_entity(name="极星科技", aliases=["极星"]))
    svc = _service(engine)

    with pytest.raises(MemoryNameConflictError):
        svc.update_entity(TEST_USER_ID, f"e:{mine.id}", EntityUpdateRequest(name="极星科技"))
    with pytest.raises(MemoryNameConflictError):
        svc.update_entity(TEST_USER_ID, f"e:{mine.id}", EntityUpdateRequest(name="极星"))
    with pytest.raises(MemoryNameConflictError):
        svc.update_entity(TEST_USER_ID, f"e:{mine.id}", EntityUpdateRequest(aliases=["极星"]))
    # 撞自己的名字/别名不报错（原名入别名、别名去重属正常收敛）
    row = svc.update_entity(TEST_USER_ID, f"e:{mine.id}", EntityUpdateRequest(name="禹通互联网", aliases=["禹通", "禹通互联网"]))
    assert row["name"] == "禹通互联网"


def test_update_entity_validates_type_and_missing_target(engine):
    editor = _editor(engine)
    entity = editor.memory_repo.upsert_entity(_entity(name="禹通"))
    svc = _service(engine)

    with pytest.raises(MemoryInvalidParamError):
        svc.update_entity(TEST_USER_ID, f"e:{entity.id}", EntityUpdateRequest(entityType="COMPANY"))
    with pytest.raises(MemoryObjectNotFoundError):
        svc.update_entity(TEST_USER_ID, "e:9999", EntityUpdateRequest(name="x"))

    row = svc.update_entity(TEST_USER_ID, f"e:{entity.id}", EntityUpdateRequest(entityType="ORG"))
    assert row["entityType"] == "ORG"


def _entity(name: str, entity_type: str = "ORG", aliases: list[str] | None = None) -> MemoryEntity:
    return MemoryEntity(name=name, entity_type=entity_type, aliases=aliases or [])
