"""深度回忆三件套：timeline / expand / state_at 的检索、渲染与去重。"""

from datetime import datetime, timezone
from uuid import uuid4

from app.components.memory import MemoryRecallService
from app.components.memory.repositories.sqlite import SqliteGraphMemoryRepository
from app.models.domain.memory import (
    StatementState,
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryStatement,
)
from app.domain.memory import MemoryVectorHit

from fakes_memory import FakeMemoryVectorIndex, make_service_config
from conftest import TEST_USER_ID

NOW = datetime(2026, 8, 27, tzinfo=timezone.utc)


class Harness:
    """共享底座：真实 SQL 仓储 + 预置命中向量替身 + 手工配置。"""

    def __init__(self, engine, preset_hits=None):
        self.engine = engine
        self.repo = SqliteGraphMemoryRepository(engine=engine).for_user(TEST_USER_ID)
        self.vector = FakeMemoryVectorIndex(preset_hits=preset_hits)
        self.svc = MemoryRecallService(
            memory_repo=self.repo, vector_index=self.vector,
            app_config=make_service_config(),
            model_factory=None,
        )

    def user(self):
        return self.repo.upsert_entity(MemoryEntity(name="用户", entity_type="PERSON", is_user=True))

    def other(self, name="李总"):
        return self.repo.upsert_entity(MemoryEntity(name=name, entity_type="PERSON"))


def test_timeline_renders_event_fragments_and_marks_seen(engine):
    h = Harness(engine)
    session = uuid4()
    ep = h.repo.insert_episode(MemoryEpisode(
        thread_id=uuid4(), occurred_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
        scene="商业谈判", summary="发布会时间与回款节点起冲突"))
    h.repo.link_episode_entities([MemoryEpisodeLink(episode_id=ep.id, entity_id=h.other().id, role="对手方")])
    h.vector._preset.append(MemoryVectorHit(kind="episode", ref_id=ep.id, score=0.91))

    out = h.svc.timeline("发布会为什么延期", TEST_USER_ID, session)
    assert "记忆片段 1" in out and f"§E{ep.id}" in out and "对手方" in out
    assert "场景：商业谈判" in out

    again = h.svc.timeline("再讲讲发布会", TEST_USER_ID, session)
    assert again == ""  # 同会话已投喂内容不重复返回（SessionInjectRegistry）


def test_expand_by_alias_and_vector_fallback_paths(engine):
    # 主路径：别名精确命中后输出邻域陈述
    h = Harness(engine)
    boss = h.other()
    stmt = h.repo.insert_statement(MemoryStatement(
        subject_id=boss.id, predicate="职位", object_text="投资方合伙人",
        summary=f"{boss.name}的职位是投资方合伙人", state="ACTIVE",
        valid_from=datetime(2026, 5, 1), confidence=0.9,
    ))
    # 对既有实体行补别名（同名重复建行会让消歧锚到空实体——正是要防的反例）
    boss.aliases = ["老李"]
    h.repo.upsert_entity(boss)

    out = h.svc.expand("老李", TEST_USER_ID, uuid4())
    assert "记忆片段" in out and f"#S{stmt.id}" in out

    # 向量兜底路径：名称不同但余弦超阈值仍能锚定同一实体（融合分仅圈候选）
    v_h = Harness(engine)
    target = v_h.other("王小明")
    hit_stmt = v_h.repo.insert_statement(MemoryStatement(
        subject_id=target.id, predicate="认识", object_text="核心团队",
        summary=f"{target.name}认识核心团队", state="ACTIVE",
        valid_from=datetime(2026, 5, 1),
    ))
    v_h.vector._preset.append(MemoryVectorHit(kind="entity", ref_id=target.id, score=1.0))
    v_h.vector._cosines.append(0.9)
    out_v = v_h.svc.expand("明仔", TEST_USER_ID, uuid4())
    assert f"#S{hit_stmt.id}" in out_v

    # 假向量对 query 文本不敏感，须用零预置命中的干净实例验证未命中语义
    clean = Harness(engine)
    assert clean.svc.expand("查无此人", TEST_USER_ID, uuid4()).startswith("（未找到")


def test_expand_vector_fallback_honors_merge_blocklist(engine):
    """读路向量兜底与写路消歧同规（resolution 单源）：拆分禁令对优先于余弦排序。"""
    h = Harness(engine)
    a = h.repo.upsert_entity(MemoryEntity(
        name="禹通档案", entity_type="ORG",
        attributes={"merge_blocklist": ["卫职院档案"]}))
    b = h.repo.upsert_entity(MemoryEntity(
        name="卫职院档案", entity_type="ORG",
        attributes={"merge_blocklist": ["禹通档案"]}))
    stmt_a = h.repo.insert_statement(MemoryStatement(
        subject_id=a.id, predicate="职位", object_text="CTO",
        summary="禹通档案的职位是CTO", state="ACTIVE", valid_from=datetime(2026, 5, 1)))
    stmt_b = h.repo.insert_statement(MemoryStatement(
        subject_id=b.id, predicate="职位", object_text="校长",
        summary="卫职院档案的职位是校长", state="ACTIVE", valid_from=datetime(2026, 5, 1)))
    # A 余弦更高本该 A 胜出；但 A/B 是人工拆分禁令对，禁令优先 → 锚到 B
    h.vector._preset.append(MemoryVectorHit(kind="entity", ref_id=a.id, score=0.99))
    h.vector._preset.append(MemoryVectorHit(kind="entity", ref_id=b.id, score=0.98))
    h.vector._cosines.append(0.90)
    h.vector._cosines.append(0.86)

    out = h.svc.expand("学校新表述", TEST_USER_ID, uuid4())

    assert f"#S{stmt_b.id}" in out and f"#S{stmt_a.id}" not in out


def test_state_at_slices_bi_temporal_history(engine):
    h = Harness(engine)
    user = h.user()
    old_job = h.repo.insert_statement(MemoryStatement(
        subject_id=user.id, predicate="职业", object_text="教师",
        summary="用户的职业是教师",
        state=StatementState.SUPERSEDED.value,           # 已被取代的历史切片
        valid_from=datetime(2026, 1, 10),
        valid_to=datetime(2026, 8, 20),
        invalidated_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
    ))
    current_job = h.repo.insert_statement(MemoryStatement(
        subject_id=user.id, predicate="职业", object_text="后端开发",
        summary="用户的职业是后端开发", state=StatementState.ACTIVE.value,
        valid_from=datetime(2026, 8, 20),
    ))

    historical = h.svc.state_at("2026-03-01", TEST_USER_ID, uuid4())
    assert f"#S{old_job.id}" in historical and "教师" in historical
    assert f"#S{current_job.id}" not in historical   # 尚未生效

    recent = h.svc.state_at("2026-08-25", TEST_USER_ID, uuid4())
    assert f"#S{current_job.id}" in recent and f"#S{old_job.id}" not in recent

    hint = h.svc.state_at("大概是上个月吧", TEST_USER_ID, uuid4())
    assert "YYYY-MM-DD" in hint                      # 不可解析给出格式指引
