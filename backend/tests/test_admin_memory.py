"""admin 复合命令行的 memory 域：repair 按事故形态验证修复与幂等，rebuild-index 验证收集。"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Session, select

from app.adapters.persistence.memory_graph_repository import MemoryGraphRepository
from app.adapters.persistence.memory_maintenance_repository import MemoryMaintenanceRepository
from app.application import MemoryAppService
from app.application.memory_app_service import COMPANY_NAME, SCHOOL_NAME
from app.models.domain.memory import (
    EntityType,
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryStatement,
    StatementState,
)
from app.domain.memory import KIND_ENTITY, KIND_EPISODE, KIND_STATEMENT

from fakes_memory import FakeMemoryVectorIndex
from conftest import TEST_USER_ID


def _service(engine, vector) -> MemoryAppService:
    """维护用例单测装配：全局算子视角仓储 + 假向量索引（管理面服务不参与）。"""
    return MemoryAppService(
        graph_service=None,
        admin_service=None,
        memory_repo=MemoryGraphRepository(engine=engine),
        maintenance_repo=MemoryMaintenanceRepository(engine=engine),
        vector_index=vector,
    )


def _seed_polluted(engine):
    """复刻事故形态：公司档案含学校别名与 #S 编号，statement/link 错挂。"""
    with Session(engine) as session:
        company = MemoryEntity(
            user_id=TEST_USER_ID,
            entity_type=EntityType.ORG.value, name=COMPANY_NAME,
            aliases=["#S14", COMPANY_NAME, SCHOOL_NAME, "禹通", "禹通公司"],
        )
        system = MemoryEntity(
            user_id=TEST_USER_ID,
            entity_type=EntityType.OBJECT.value, name="智慧系统",
            aliases=["#S13", "智慧系统", "智慧考场"],
        )
        session.add(company)
        session.add(system)
        session.flush()
        wrong_stmt = MemoryStatement(
            user_id=TEST_USER_ID,
            subject_id=system.id, predicate="验收方",
            object_entity_id=company.id,
            summary=f"智慧系统验收方{COMPANY_NAME}",       # 事故中的错误 summary
            state="ACTIVE", valid_from=datetime(2026, 8, 28),
            source_thread_id=uuid4(), source_turn_id=uuid4(),
            confidence=0.7,
        )
        session.add(wrong_stmt)
        episode = MemoryEpisode(
            user_id=TEST_USER_ID,
            thread_id=uuid4(), occurred_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
            summary=f"评估智慧考场系统2年建设周期合理性，确定验收方为{SCHOOL_NAME}",
        )
        session.add(episode)
        session.flush()
        wrong_link = MemoryEpisodeLink(
            episode_id=episode.id, entity_id=company.id, role="验收方",
        )
        session.add(wrong_link)
        session.commit()
        return company.id, system.id, wrong_stmt.id, episode.id, wrong_link.id


def test_repairs_polluted_archive_and_rebinds(engine):
    company_id, system_id, stmt_id, _episode_id, link_id = _seed_polluted(engine)
    vector = FakeMemoryVectorIndex()

    report = _service(engine, vector).repair(apply=True)

    # 别名清理：#S 编号全清，学校名离开公司档案
    with Session(engine) as session:
        company = session.get(MemoryEntity, company_id)
        system = session.get(MemoryEntity, system_id)
        assert "#S14" not in company.aliases and SCHOOL_NAME not in company.aliases
        assert "#S13" not in system.aliases

        # 学校实体存在且为 ORG；statement/link 已改挂
        school = session.exec(
            select(MemoryEntity).where(MemoryEntity.name == SCHOOL_NAME)
        ).first()
        assert school is not None and school.entity_type == EntityType.ORG.value
        stmt = session.get(MemoryStatement, stmt_id)
        assert stmt.object_entity_id == school.id
        assert stmt.summary == f"智慧系统验收方{SCHOOL_NAME}"
        link = session.get(MemoryEpisodeLink, link_id)
        assert link.entity_id == school.id

    # 触达对象定向向量重写：3 实体（公司/系统/学校）+ 1 陈述
    kinds = [(entry.kind, entry.ref_id) for entry in vector.upserts]
    assert sum(1 for kind, _ in kinds if kind == KIND_ENTITY) == 3
    assert sum(1 for kind, _ in kinds if kind == KIND_STATEMENT) == 1
    assert report.changed


def test_repair_is_idempotent(engine):
    _seed_polluted(engine)
    first_vector = FakeMemoryVectorIndex()
    second_vector = FakeMemoryVectorIndex()

    first = _service(engine, first_vector).repair(apply=True)
    second = _service(engine, second_vector).repair(apply=True)

    assert first.changed and not second.changed
    assert any("未发现需修复的数据" in line for line in second.lines)
    assert second_vector.upserts == []              # 重跑不产生任何写入


def test_dry_run_writes_nothing(engine):
    company_id, _system_id, stmt_id, _episode_id, link_id = _seed_polluted(engine)

    report = _service(engine, FakeMemoryVectorIndex()).repair(apply=False)

    assert report.changed and any(line.startswith("[计划]") for line in report.lines)
    with Session(engine) as session:
        company = session.get(MemoryEntity, company_id)
        assert SCHOOL_NAME in company.aliases          # 原样未动
        stmt = session.get(MemoryStatement, stmt_id)
        assert stmt.object_entity_id == company_id
        link = session.get(MemoryEpisodeLink, link_id)
        assert link.entity_id == company_id


def test_rebuild_index_collects_active_truth_only(engine):
    """全量重建以 SQL ACTIVE 事实为准：SUPERSEDED 历史切片不入索引。"""
    _company_id, system_id, _stmt_id, _episode_id, _link_id = _seed_polluted(engine)
    with Session(engine) as session:
        session.add(MemoryStatement(
            user_id=TEST_USER_ID,
            subject_id=system_id, predicate="职业", object_text="历史值",
            summary="历史切片不应入索引",
            state=StatementState.SUPERSEDED.value,
            valid_from=datetime(2026, 1, 1), valid_to=datetime(2026, 8, 1),
        ))
        session.commit()
    vector = FakeMemoryVectorIndex()

    counts = _service(engine, vector).rebuild_index().counts

    assert counts == {KIND_ENTITY: 2, KIND_STATEMENT: 1, KIND_EPISODE: 1}
    entries = vector.rebuilds[0]
    kinds = [entry.kind for entry in entries]
    assert kinds.count(KIND_ENTITY) == 2
    assert kinds.count(KIND_STATEMENT) == 1
    assert kinds.count(KIND_EPISODE) == 1
    assert all("历史切片" not in entry.content for entry in entries)
