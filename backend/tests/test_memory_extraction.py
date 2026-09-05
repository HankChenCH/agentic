"""结构化抽取与裁决的解析健壮性：全部降级路径不抛出。"""

from datetime import datetime, timezone

from app.components.memory.internal.extraction import (
    ExtractedFact,
    FactDecision,
    adjudicate_facts,
    extract_structure,
    resolve_time_hint,
)

from fakes_memory import CannedLLM

NOW = datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)


GOOD_JSON = """{
  "entities": [
    {"key": "zhang", "type": "PERSON", "name": "张三", "aliases": ["老张"], "importance": 0.7},
    {"key": "user", "type": "PERSON", "name": "不应存在的用户实体"}
  ],
  "episodes": [{"summary": "谈判破裂", "time_hint": "2026-08-20", "scene": "商业谈判",
                "links": [{"key": "user", "role": "参与者"}, {"key": "zhang", "role": "对手方"}]}],
  "facts": [
    {"subject_key": "user", "predicate": "职业", "object_text": "后端开发", "confidence": 1.4,
     "evidence": "我是做后端的"},
    {"subject_key": "ghost", "predicate": "", "object_text": "缺失主体与谓词应被丢弃"},
    {"subject_key": "user", "predicate": "偏好", "object_text": "Python", "time_hint": "bad-time",
     "time_remark": "最近"}
  ]
}"""


def test_extract_structure_parses_and_sanitizes():
    llm = CannedLLM(f"前置说明 {GOOD_JSON} 后缀")
    result = extract_structure(llm, "用户：略\n助手：略", NOW)

    # user 键为系统保留：永不为它建候选实体
    assert [e.key for e in result.entities] == ["zhang"]
    assert result.entities[0].name == "张三"
    assert result.entities[0].aliases == ("老张",)

    # 缺失谓词/主体的条目在清洗期整条丢弃；越界置信度钳制到 1.0
    kept_facts = [(f.subject_key, f.predicate) for f in result.facts]
    assert ("user", "职业") in kept_facts and ("user", "偏好") in kept_facts
    assert all(f.predicate for f in result.facts)
    assert result.facts[0].confidence == 1.0

    ep = result.episodes[0]
    assert ep.scene == "商业谈判"
    assert ep.links[0] == ("user", "参与者")
    assert ("zhang", "对手方") in ep.links


def test_extract_structure_degrades_to_empty_on_garbage():
    assert extract_structure(CannedLLM("抱歉我无法回答"), "x", NOW).is_empty()
    assert extract_structure(CannedLLM("[1,2,3] 不是对象"), "x", NOW).is_empty()


def test_extraction_prompt_carries_identity_and_roster():
    """身份卡与名册随 HumanMessage 下发；缺省时两节整段省略。"""
    from app.models.domain.memory import MemoryEntity

    llm = CannedLLM('{"entities":[],"episodes":[],"facts":[]}')
    roster_row = MemoryEntity(id=3, name="广东禹通互联网科技有限公司",
                              entity_type="ORG", aliases=["禹通"])
    extract_structure(llm, "用户：略", NOW,
                      identity_names=["demo", "张三"], roster=[roster_row])
    human = llm.calls[0][-1].content
    assert "【用户身份】用户本人已知称呼：demo、张三" in human
    assert "#3|广东禹通互联网科技有限公司|ORG｜又名：禹通" in human
    assert "必须回填其 ref_id" in human

    bare = CannedLLM('{"entities":[],"episodes":[],"facts":[]}')
    extract_structure(bare, "用户：略", NOW)
    assert "【用户身份】" not in bare.calls[0][-1].content
    assert "【已知对象名册】" not in bare.calls[0][-1].content


def test_extraction_ref_id_validated_against_roster():
    """名册外 ref_id 剥除（防幻觉编号把事实挂到任意实体），条目保留走正常消歧。"""
    from app.models.domain.memory import MemoryEntity

    payload = """{"entities": [
        {"key": "a", "name": "禹通全称", "type": "ORG", "ref_id": 3},
        {"key": "b", "name": "野实体", "type": "ORG", "ref_id": 99}
      ], "episodes": [], "facts": []}"""
    roster = [MemoryEntity(id=3, name="广东禹通互联网科技有限公司", entity_type="ORG")]
    result = extract_structure(CannedLLM(payload), "x", NOW, roster=roster)

    refs = {e.key: e.ref_id for e in result.entities}
    assert refs == {"a": 3, "b": None}


REF_JSON = """{
  "entities": [
    {"key": "ghost", "type": "ORG", "name": "#S14", "aliases": ["#S13", "禹通"]},
    {"key": "real", "type": "ORG", "name": "禹通公司", "aliases": ["#T8821ab", "#实体9"]}
  ]
}"""


def test_extract_structure_filters_internal_ref_tokens():
    """渲染层溯源编号（#S/#T/§E/#实体）不是实体名或别名——一律拦在入库前。"""
    result = extract_structure(CannedLLM(REF_JSON), "x", NOW)

    assert [e.key for e in result.entities] == ["real"]
    real = result.entities[0]
    assert real.name == "禹通公司"
    assert real.aliases == ()


def test_resolve_time_hint_variants():
    assert resolve_time_hint("2026-08-25", NOW) == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert resolve_time_hint("2026年8月25日", NOW) == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert resolve_time_hint("昨天", NOW) == datetime(2026, 8, 26, tzinfo=timezone.utc)
    assert resolve_time_hint("今天 09:30", NOW) is None  # 相对词不带空格匹配，落 ISO 失败→None
    assert resolve_time_hint("上个月左右", NOW) is None
    assert resolve_time_hint("", NOW) is None


def test_adjudicate_aligns_partial_decisions():
    llm = CannedLLM('{"decisions": [{"index": 0, "action": "SKIP"},'
                    ' {"index": 2, "action": "REPLACE", "replace_id": 9}]}')
    facts = [
        ExtractedFact(subject_key="user", predicate="职业", object_text="A"),
        ExtractedFact(subject_key="user", predicate="偏好", object_text="B"),
        ExtractedFact(subject_key="user", predicate="居住地", object_text="C"),
    ]
    existing = [{"id": 9, "subject_name": "用户", "predicate": "居住地", "object_label": "旧城", "origin": "EXTRACTED"}]
    decisions = adjudicate_facts(llm, facts, existing)

    assert decisions == [
        FactDecision("SKIP"),
        FactDecision("ADD"),  # 缺失索引补默认新增
        FactDecision("REPLACE", replace_id=9),
    ]


def test_adjudicate_returns_none_on_garbage():
    assert adjudicate_facts(CannedLLM("模型跑题了"), [ExtractedFact("u", "偏好", object_text="x")], []) is None


def test_adjudicate_normalizes_lenient_actions():
    """小写归一；未知 action 连同 replace_id 归为 ADD（同旧坏条目丢弃语义）。"""
    llm = CannedLLM('{"decisions": [{"index": 0, "action": "skip"},'
                    ' {"index": 1, "action": "DEPRECATE", "replace_id": 9}]}')
    facts = [
        ExtractedFact(subject_key="user", predicate="职业", object_text="A"),
        ExtractedFact(subject_key="user", predicate="偏好", object_text="B"),
    ]
    assert adjudicate_facts(llm, facts, []) == [
        FactDecision("SKIP"),
        FactDecision("ADD"),
    ]


class _NoToolCallingLLM:
    """with_structured_output 直接失败的替身（模拟不支持 tool calling 的网关）。"""

    def with_structured_output(self, schema, method=None, **kwargs):
        raise NotImplementedError("provider does not support tool calling")


def test_structured_output_unsupported_degrades():
    assert extract_structure(_NoToolCallingLLM(), "x", NOW).is_empty()
    assert adjudicate_facts(
        _NoToolCallingLLM(), [ExtractedFact("u", "偏好", object_text="x")], []
    ) is None
