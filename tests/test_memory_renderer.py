"""渲染器契约测试：两形态模板 + 分片组装的分组/排序/证据/存疑规则。"""

from datetime import datetime, timezone

from app.components.memory.renderer import (
    Fragment,
    assemble_fragments,
    brief_context,
    fragments_output,
    statement_topology_line,
)
from app.models.domain.memory import MemoryEntity, MemoryEpisode, MemoryEpisodeLink, MemoryStatement

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)

USER = MemoryEntity(id=1, name="用户", entity_type="PERSON", is_user=True)
ZHANG = MemoryEntity(id=2, name="张伟", entity_type="PERSON")
ENTITIES = {1: USER, 2: ZHANG}


def _statement(sid, subject=1, predicate="职业", object_text=None, object_entity=None,
               valid_from=None, confidence=0.8, remark=None):
    return MemoryStatement(
        id=sid, subject_id=subject, predicate=predicate,
        object_entity_id=object_entity, object_text=object_text,
        valid_from=valid_from or datetime(2026, 7, 1, tzinfo=timezone.utc),
        time_remark=remark, confidence=confidence, summary="占位",
    )


def test_brief_layout_and_numbering():
    lines = [
        statement_topology_line(
            _statement(3, object_text="后端开发"), ENTITIES, now=NOW
        ),
        statement_topology_line(
            _statement(4, object_entity=2, predicate="认识"), ENTITIES, now=NOW
        ),
    ]
    text = brief_context(NOW, lines)
    assert "共2条" in text and "取自 2026-08-27" in text
    assert "1. #S3" in text and "2. #S4" in text
    # 字面量客体带引号包装；实体客体带类型标签
    assert '-[职业]-> “后端开发”' in text
    assert "-[认识]-> (张伟·PERSON)" in text


def test_time_annotation_dual_write_with_remark():
    line = statement_topology_line(_statement(9, remark="上个月开始"), ENTITIES, now=NOW)
    assert "@2026-07-01（约2个月前）〔表述：上个月开始〕" in line


def test_fragments_sections_appear_only_when_present():
    frag = Fragment(root_label="回款", score=0.9, span="2026-08-25 → 09-15",
                    topology_lines=["#S31 (x) -[主导]-> (y)"])
    empty = Fragment(root_label="空", score=0.5, span="-")
    out = fragments_output(NOW, [frag, empty])
    assert "记忆片段 1" in out and "相关度 0.90" in out
    assert out.index("记忆片段 1") < out.index("记忆片段 2")  # 按分数降序
    block2 = out.split("记忆片段 2")[1]
    for section in ("【叙事摘要】", "【原始证据】", "【存疑备注】"):
        assert section not in block2


def test_assemble_groups_by_shared_entity():
    s1 = _statement(11, subject=1, confidence=0.95)
    s2 = _statement(12, subject=1, predicate="偏好", object_text="Python")
    bridged = _statement(13, subject=2, predicate="认识", object_entity=1, confidence=0.6)

    frags = assemble_fragments([(s1, 0.6), (s2, 0.5), (bridged, 0.4)], [], {}, ENTITIES, now=NOW)
    # 三条经实体边全部连通（对象实体 1 同时挂在两个主体下）：单一分片
    assert len(frags) == 1
    frag = frags[0]
    ids = [line.split("#S")[1].split()[0] for line in frag.topology_lines]
    assert ids == ["11", "12", "13"]          # 片段内按陈述 id 升序稳定输出
    assert frag.root_label == "用户"           # 根取最高分条目的主体名
    assert any("置信度偏低（0.6）" in d for d in frag.doubts)


def test_assemble_projects_episode_event_nodes():
    import uuid as uuid_mod

    ep = MemoryEpisode(id=21, thread_id=uuid_mod.uuid4(),
                       occurred_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
                       scene="商业谈判", summary="发布会时间与回款节点起冲突")
    links = {21: [MemoryEpisodeLink(id=1, episode_id=21, entity_id=2, role="对手方")]}
    frags = assemble_fragments([], [(ep, 0.88)], links, ENTITIES, now=NOW)
    line = frags[0].topology_lines[0]
    # 事件节点投影：§E 前缀 + role 作谓词 + 挂接目标带类型标签
    assert line.startswith(f"§E{ep.id}")
    assert "-[对手方]-> (张伟·PERSON)" in line and "@2026-08-25" in line
    assert frags[0].summary and "场景：商业谈判" in frags[0].summary
