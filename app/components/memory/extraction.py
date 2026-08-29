"""解析块：巩固管线的 LLM 输入组装、两次裸模型调用与裁决输出解析。

风格延续 v1 抽取器：手工 JSON 截取解析、任何解析/校验失败都静默降级
不上抛——抽取返回空结果、裁决返回 None，由服务层决定兜底路径。
prompt 携带「当前时间」作时间锚，让模型把“昨天/上个月”落到可解析的
``time_hint``；无法归一时的原文进 ``time_remark`` 保信息量。
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List
from uuid import UUID

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel

from app.components.memory.renderer import is_internal_ref
from app.components.memory.vocab import cardinality
from app.models.domain.agentic import AgenticConversationMessage, AgenticMessageType
from app.models.domain.memory import MemoryEntity

# 单实体抽取上限与证据摘录长度保护（防 transcript 失控膨胀）
_MAX_ENTITIES = 20
_MAX_FACTS = 30
_MAX_EPISODES = 10
_MAX_EVIDENCE_LEN = 80

_EXTRACTION_SYSTEM_PROMPT = """你是一个对话记忆抽取器，从一轮对话中提取值得跨轮长期记住的结构化信息。只输出一个 JSON 对象，无任何其他文字或代码块标记：

{"entities": [{"key": "zhangwei", "type": "PERSON", "name": "张伟", "aliases": [], "importance": 0.5}],
 "episodes": [{"summary": "发布会时间与回款节点起冲突的谈判", "time_hint": "2026-08-25", "scene": "商业谈判", "links": [{"key": "zhangwei", "role": "参与者"}]}],
 "facts": [{"subject_key": "user", "predicate": "职业", "object_text": "后端开发", "time_hint": "", "time_remark": "", "confidence": 0.8, "evidence": "原话摘录"}]}

规则：
1. entities 是稳定对象节点。type 取 PERSON/OBJECT/PLACE/ORG/CONCEPT/OTHER；name 必须纯专名，禁止把职衔等关系写进 name。"user" 固定指代用户本人（已存在，无需为其建实体）；
2. facts 是三元组断言：(subject_key) -[predicate]-> (object_key 或 object_text)。实体间联系填 object_key，字面量属性填 object_text；predicate 尽量选自词表：姓名/称呼/职业/居住地/职位/所属公司/学历/毕业院校/目标/约束（单值）· 偏好/技能/认识/参与/担任/擅长/兴趣/习惯（多值）· 引发/冲突/延期/促成/阻碍（事件关联）；
3. 时间锚以【当前时间】为准：“昨天/上个月”等换算成 YYYY-MM-DD 放 time_hint；无法确定日期的表述原文放 time_remark；两者都没有就省略；
4. evidence 只摘对话中最能佐证该事实的一句原话（不超过50字），没有可省略；
5. 只提取跨轮有价值的稳定信息：身份/背景/偏好/目标/重要事件脉络；忽略寒暄、临时上下文、与用户无关的水内容；
6. importance/confidence 用 0~1 小数。全部无新增时输出 {"entities":[],"episodes":[],"facts":[]}。"""

_ADJUDICATION_SYSTEM_PROMPT = """你是记忆裁决器。输入一批候选新事实和某主体的既有 ACTIVE 陈述（带数据库 id）。逐条判定每条新事实该执行什么操作，只输出一个 JSON 数组：

[{"index": 0, "action": "ADD", "replace_id": null},
 {"index": 1, "action": "REPLACE", "replace_id": 17},
 {"index": 2, "action": "SKIP", "replace_id": null}]

规则：
1. REPLACE：该断言使某条既有陈述过时（典型：单值谓词如 职业/居住地 出现了不同的新值），replace_id 填被取代陈述的 id；
2. SKIP：与某条既有陈述语义相同或近似重复；
3. 其余一律 ADD。origin 为 MANUAL 的既有陈述绝对不可作为 replace_id（人工事实神圣不可自动取代，认为冲突只能 SKIP）；
4. 不确定时选 ADD。"""


@dataclass(frozen=True)
class ExtractedEntity:
    key: str
    name: str
    entity_type: str = "OTHER"
    aliases: tuple = field(default_factory=tuple)
    importance: float = 0.5


@dataclass(frozen=True)
class ExtractedEpisode:
    summary: str
    time_hint: str | None = None
    scene: str | None = None
    # (entity_key, role)；participants 与涉及物统一为链接表达
    links: tuple = field(default_factory=tuple)


@dataclass(frozen=True)
class ExtractedFact:
    subject_key: str
    predicate: str
    object_key: str | None = None
    object_text: str | None = None
    time_hint: str | None = None
    time_remark: str | None = None
    confidence: float = 0.7
    evidence: str | None = None


@dataclass(frozen=True)
class ExtractionResult:
    entities: tuple = field(default_factory=tuple)
    episodes: tuple = field(default_factory=tuple)
    facts: tuple = field(default_factory=tuple)

    def is_empty(self) -> bool:
        return not (self.entities or self.episodes or self.facts)


@dataclass(frozen=True)
class FactDecision:
    action: str  # ADD / REPLACE / SKIP
    replace_id: int | None = None


@dataclass(frozen=True)
class PairedFact:
    """裁决前的事实-实体配对投影（收尾侧产出，裁决与落库侧消费）。"""

    fact: ExtractedFact
    subject: MemoryEntity
    object_entity: MemoryEntity | None
    object_text: str | None
    evidence: list
    valid_from: datetime
    source_thread_id: UUID | None = None
    source_turn_id: UUID | None = None


def extract_structure(model: BaseChatModel, transcript: str, now: datetime) -> ExtractionResult:
    """第❶步结构化抽取；任何解析异常降级为空结果（调用方放弃本轮写入）。"""
    response = model.invoke([
        SystemMessage(content=_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=f"【当前时间】{now.strftime('%Y-%m-%d %H:%M')}\n\n【本轮对话】\n{transcript}"),
    ])
    return _build_extraction_result(response.content if isinstance(response.content, str) else str(response.content))


def adjudicate_facts(
    model: BaseChatModel,
    facts: list,
    active_statements: list[dict],
) -> list[FactDecision] | None:
    """第❸步陈述裁决。``active_statements`` 为 [{id,summary,...}] 摘要字典。

    返回按 facts 顺序的决策列表；LLM 调用失败或解析失败返回 None，
    由调用方执行程序化降级规则。
    """
    if not facts:
        return []
    existing_block = "\n".join(
        f"- #{s['id']} ({s.get('subject_name', '')}) -[{s.get('predicate', '')}]-> "
        f"{s.get('object_label', '')}{_manual_tag(s)}"
        for s in active_statements
    ) or "（该主体暂无既有陈述）"
    new_block = "\n".join(
        f"{i}. ({f.subject_key}) -[{f.predicate}]-> {f.object_text or f.object_key or ''}"
        for i, f in enumerate(facts)
    )
    try:
        response = model.invoke([
            SystemMessage(content=_ADJUDICATION_SYSTEM_PROMPT),
            HumanMessage(content=f"【既有 ACTIVE 陈述】\n{existing_block}\n\n【候选新事实】\n{new_block}"),
        ])
    except Exception:
        return None
    decisions = _parse_decisions(response.content if isinstance(response.content, str) else str(response.content))
    if decisions is None:
        return None
    # 补齐缺失索引为 ADD（部分覆盖视为其余默认新增）
    aligned = [decisions.get(i, FactDecision("ADD")) for i in range(len(facts))]
    return aligned


def resolve_time_hint(hint: str | None, now: datetime) -> datetime | None:
    """宽松解析 time_hint → UTC 感知时刻；不可解析返回 None。

    支持 ISO 日期/日期时间与常见中文格式，以及 今天/昨天/前天 相对词。
    纯年份/月份映射到其起点（保守取向：宁可早端不含糊）。
    """
    if not hint:
        return None
    text = str(hint).strip()
    if not text:
        return None
    base = now
    relative_days = {"今天": 0, "昨日": 1, "昨天": 1, "前天": 2}
    if text in relative_days:
        day = (base - timedelta(days=relative_days[text])).date()
        return datetime(day.year, day.month, day.day, tzinfo=base.tzinfo)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M", "%Y/%m/%d", "%Y年%m月%d日", "%Y-%m", "%Y年%m月"):
        try:
            parsed = datetime.strptime(text.replace(" ", ""), fmt)
            break
        except ValueError:
            continue
    else:
        parsed = _try_iso(text)
        if parsed is None:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=base.tzinfo)
    return parsed


def build_transcript(query: str, turn_messages: List[AgenticConversationMessage]) -> str:
    """抽取输入组装：与标题生成同款拼接——用户 query + 助手 MESSAGE 文本。

    助手侧剥离内部溯源引用（#S13/§E3 等）——防止被复读的编号经抽取吸回实体档案。
    """
    assistant_text = "\n".join(
        renderer.strip_internal_refs(part.get("text", ""))
        for msg in turn_messages
        if msg.message_type == AgenticMessageType.MESSAGE
        for part in msg.content
        if part.get("type") == "text"
    )
    return f"用户：{query}\n助手：{assistant_text}".strip()


def statement_digest(rows, names: dict[int, str]) -> list[dict]:
    """把既有 ACTIVE 陈述序列化成裁决 prompt 的摘要块。"""
    return [
        {
            "id": row.id,
            "subject_name": names.get(row.subject_id, "?"),
            "predicate": row.predicate,
            "object_label": f"{names.get(row.object_entity_id, '')}"
            if row.object_entity_id is not None
            else (row.object_text or ""),
            "origin": row.origin,
        }
        for row in rows
    ]


def programmatic_decisions(paired: list[PairedFact], active_rows) -> list[FactDecision]:
    """LLM 裁决失败时的程序化降级：重复→SKIP；单值谓词已有值→REPLACE 最新行；否则 ADD。"""
    group: dict[int, list] = {}
    for row in active_rows:
        group.setdefault(row.subject_id, []).append(row)
    decisions = []
    for pair in paired:
        same_pred = [
            r for r in group.get(pair.subject.id, [])
            if r.predicate == pair.fact.predicate
        ]
        duplicate = any(
            (pair.object_entity is not None and r.object_entity_id == pair.object_entity.id)
            or (pair.object_text is not None and r.object_text == pair.object_text)
            for r in same_pred
        )
        if duplicate:
            decisions.append(FactDecision("SKIP"))
        elif cardinality(pair.fact.predicate) == "single" and same_pred:
            newest = max(same_pred, key=lambda r: r.updated_at or r.created_at)
            decisions.append(FactDecision("REPLACE", replace_id=newest.id))
        else:
            decisions.append(FactDecision("ADD"))
    return decisions


def _try_iso(text: str):
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_json_object(content: str) -> dict | None:
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _clip(value, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value[:limit] if value else None


def _as_float(value, default: float) -> float:
    try:
        number = float(value)
        return min(max(number, 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def _build_extraction_result(content: str) -> ExtractionResult:
    data = _parse_json_object(content)
    if data is None:
        return ExtractionResult()

    entities = {}
    for raw in (data.get("entities") or [])[:_MAX_ENTITIES]:
        if not isinstance(raw, dict):
            continue
        key, name = _clip(raw.get("key"), 40), _clip(raw.get("name"), 60)
        if not key or not name or key == "user" or is_internal_ref(name):
            continue  # user 键由系统保留；内部溯源引用（#S13 等）不是实体名
        aliases = tuple(
            a.strip() for a in raw.get("aliases") or []
            if isinstance(a, str) and a.strip() and not is_internal_ref(a)
        )
        entities[key] = ExtractedEntity(
            key=key, name=name,
            entity_type=str(raw.get("type") or "OTHER").upper()[:16],
            aliases=aliases, importance=_as_float(raw.get("importance"), 0.5),
        )

    episodes = []
    for raw in (data.get("episodes") or [])[:_MAX_EPISODES]:
        if not isinstance(raw, dict):
            continue
        summary = _clip(raw.get("summary"), 500)
        if not summary:
            continue
        links = tuple(
            (l.get("key", ""), _clip(l.get("role"), 20))
            for l in (raw.get("links") or [])
            if isinstance(l, dict) and isinstance(l.get("key"), str) and l["key"]
        )
        episodes.append(ExtractedEpisode(
            summary=summary,
            time_hint=_clip(raw.get("time_hint"), 32),
            scene=_clip(raw.get("scene"), 24),
            links=links,
        ))

    facts = []
    for raw in (data.get("facts") or [])[:_MAX_FACTS]:
        if not isinstance(raw, dict):
            continue
        predicate, subject_key = _clip(raw.get("predicate"), 24), _clip(raw.get("subject_key"), 40)
        object_key, object_text = _clip(raw.get("object_key"), 40), _clip(raw.get("object_text"), 120)
        if not predicate or not subject_key or (not object_key and not object_text):
            continue
        facts.append(ExtractedFact(
            subject_key=subject_key, predicate=predicate,
            object_key=object_key if object_key in entities else object_key,
            object_text=object_text,
            time_hint=_clip(raw.get("time_hint"), 32),
            time_remark=_clip(raw.get("time_remark"), 48),
            confidence=_as_float(raw.get("confidence"), 0.7),
            evidence=_clip(raw.get("evidence"), _MAX_EVIDENCE_LEN),
        ))

    return ExtractionResult(
        entities=tuple(entities.values()), episodes=tuple(episodes), facts=tuple(facts)
    )


def _parse_decisions(content: str) -> dict[int, FactDecision] | None:
    start, end = content.find("["), content.rfind("]")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, list):
        return None
    decisions: dict[int, FactDecision] = {}
    for raw in data:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action", "")).upper()
        if action not in ("ADD", "REPLACE", "SKIP"):
            continue
        replace_id = raw.get("replace_id")
        decisions[int(raw.get("index", -1))] = FactDecision(
            action=action,
            replace_id=int(replace_id) if isinstance(replace_id, int) else None,
        )
    return decisions


def _manual_tag(statement: dict) -> str:
    suffix = "" if statement.get("origin") != "MANUAL" else "（人工维护，禁改）"
    return suffix
