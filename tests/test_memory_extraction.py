"""结构化抽取与裁决的解析健壮性：全部降级路径不抛出。"""

from datetime import datetime, timezone

from app.components.memory.extraction import (
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


def test_resolve_time_hint_variants():
    assert resolve_time_hint("2026-08-25", NOW) == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert resolve_time_hint("2026年8月25日", NOW) == datetime(2026, 8, 25, tzinfo=timezone.utc)
    assert resolve_time_hint("昨天", NOW) == datetime(2026, 8, 26, tzinfo=timezone.utc)
    assert resolve_time_hint("今天 09:30", NOW) is None  # 相对词不带空格匹配，落 ISO 失败→None
    assert resolve_time_hint("上个月左右", NOW) is None
    assert resolve_time_hint("", NOW) is None


def test_adjudicate_aligns_partial_decisions():
    llm = CannedLLM('[{"index": 0, "action": "SKIP"}, {"index": 2, "action": "REPLACE", "replace_id": 9}]')
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
