"""解析块：巩固管线的 LLM 输入组装、两次结构化输出调用与结果清洗。

两次 LLM 调用均走 ``with_structured_output()``（不指定 method，由模型能力
声明自发现与思考模式对齐，见 llm.yaml ``capabilities`` 与
``ThinkingAwareChatDeepSeek``）：输出形状由 pydantic wire 模型（
``_*Payload``）表达，不再依赖 prompt 内联 JSON 示例；宽松清洗层保留——
截断/clamp/溯源引用过滤/保留键/条数上限，坏条目整条丢弃，模型输出在语义上
仍不可信。任何调用/校验失败都告警降级不上抛：抽取返回空结果、裁决返回
None，由服务层决定兜底路径。prompt 携带「当前时间」作时间锚，让模型把
“昨天/上个月”落到可解析的 ``time_hint``；无法归一时的原文进 ``time_remark``
保信息量。
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Literal
from uuid import UUID

from langchain.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field, model_validator

from app.components.memory.internal.renderer import strip_internal_refs
from app.domain.memory.vocab import is_internal_ref
from app.domain.memory.vocab import cardinality
from app.models.domain.agentic import AgenticConversationMessage, AgenticMessageType
from app.models.domain.memory import MemoryEntity

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面（惯例同 consolidation）
logger = logging.getLogger(__name__)

# 单实体抽取上限与证据摘录长度保护（防 transcript 失控膨胀）
_MAX_ENTITIES = 20
_MAX_FACTS = 30
_MAX_EPISODES = 10
_MAX_EVIDENCE_LEN = 80

_EXTRACTION_SYSTEM_PROMPT = """你是一个对话记忆抽取器，从一轮对话中提取值得跨轮长期记住的结构化信息。通过返回结构化结果作答：

规则：
1. entities 是稳定对象节点。type 取 PERSON/OBJECT/PLACE/ORG/CONCEPT/OTHER；name 必须纯专名，禁止把职衔等关系写进 name，也禁止用泛称/角色词（如"公司""项目""对方"）充当实体名——无名可指的对象省略实体、只用字面量表达；
2. "user" 固定指代用户本人（已存在，无需为其建实体）。用户自报的姓名/昵称/称呼是 user 的事实：predicate 用 姓名 或 称呼、客体填字面量，不要为它建实体；对话中其他位置提到用户本人（含直呼其名）一律仍用 "user" 作主体；
3. 输入可能附带【已知对象名册】。表述指代名册中的对象时——包括简称、别名、代称、角色指代（如"我们公司""那个项目"）——entities 对应条目必须回填该对象的 ref_id，name 照抄名册名称或给出实际听到的叫法；名册中确实没有的对象才作为新实体输出；
4. facts 是三元组断言：(subject_key) -[predicate]-> (object_key 或 object_text)。实体间联系填 object_key，字面量属性填 object_text；predicate 尽量选自词表：姓名/称呼/职业/居住地/职位/所属公司/学历/毕业院校/目标/约束（单值）· 偏好/技能/认识/参与/担任/擅长/兴趣/习惯（多值）· 引发/冲突/延期/促成/阻碍（事件关联）；
5. 时间锚以【当前时间】为准：“昨天/上个月”等换算成 YYYY-MM-DD 放 time_hint；无法确定日期的表述原文放 time_remark；两者都没有就省略；
6. evidence 只摘对话中最能佐证该事实的一句原话（不超过50字），没有可省略；
7. 只提取跨轮有价值的稳定信息：身份/背景/偏好/目标/重要事件脉络；忽略寒暄、临时上下文、与用户无关的水内容；
8. importance/confidence 用 0~1 小数。全部无新增时各数组返回空列表。"""

_ADJUDICATION_SYSTEM_PROMPT = """你是记忆裁决器。输入一批候选新事实和某主体的既有 ACTIVE 陈述（带数据库 id）。逐条判定每条新事实该执行什么操作，通过结构化结果返回 decisions 列表；只需包含你需要明确表态的条目，未提及的条目按 ADD 处理：

规则：
1. REPLACE：该断言使某条既有陈述过时（典型：单值谓词如 职业/居住地 出现了不同的新值），replace_id 填被取代陈述的 id。新值与既有值是同一事物的不同说法（简称/全称/别名/代称）时同样适用——先归一为同一值再判断取代；
2. SKIP：与某条既有陈述语义相同或近似重复（含同义变体）；
3. 其余一律 ADD。origin 为 MANUAL 的既有陈述绝对不可作为 replace_id（人工事实神圣不可自动取代，认为冲突只能 SKIP）；
4. 不确定时选 ADD。"""


# ---- 结构化输出 wire schema（pydantic v2）----
# 字段与 prompt 的“可省略”规则对齐（全部带默认）；schema 只保证形状合法，
# 语义清洗在 _sanitize_extraction / 裁决对齐处进行。


class _EntityPayload(BaseModel):
    key: str
    name: str
    type: str = "OTHER"
    aliases: list[str] = Field(default_factory=list)
    importance: float = 0.5
    # 名册回填：指代既有对象时由 prompt 提供的候选 id；清洗期校验
    ref_id: int | None = None


class _EpisodeLinkPayload(BaseModel):
    key: str
    role: str = ""


class _EpisodePayload(BaseModel):
    summary: str
    time_hint: str | None = None
    scene: str | None = None
    links: list[_EpisodeLinkPayload] = Field(default_factory=list)


class _FactPayload(BaseModel):
    subject_key: str
    predicate: str
    object_key: str | None = None
    object_text: str | None = None
    time_hint: str | None = None
    time_remark: str | None = None
    confidence: float = 0.7
    evidence: str | None = None


class _ExtractionPayload(BaseModel):
    entities: list[_EntityPayload] = Field(default_factory=list)
    episodes: list[_EpisodePayload] = Field(default_factory=list)
    facts: list[_FactPayload] = Field(default_factory=list)


class _DecisionPayload(BaseModel):
    index: int = -1  # 模型未给索引按 -1 处理：对齐期忽略，等价缺省 ADD
    action: Literal["ADD", "REPLACE", "SKIP"] = "ADD"
    replace_id: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_action(cls, data):
        # 宽松语义同旧解析：小写归一；未知 action 连同 replace_id 归为 ADD
        # （与旧“坏条目丢弃 → 缺索引补 ADD”的结果等价）
        if isinstance(data, dict) and "action" in data:
            action = str(data["action"]).strip().upper()
            if action in ("ADD", "REPLACE", "SKIP"):
                return {**data, "action": action}
            return {**data, "action": "ADD", "replace_id": None}
        return data


class _AdjudicationPayload(BaseModel):
    # 裁决输出包一层对象：function calling 的工具参数必须是 object
    decisions: list[_DecisionPayload] = Field(default_factory=list)


class _MergeDecisionPayload(BaseModel):
    """实体消歧灰度带裁决的输出形状：命中候选 id 或空（都不匹配）。"""

    merge_with: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class ExtractedEntity:
    key: str
    name: str
    entity_type: str = "OTHER"
    aliases: tuple = field(default_factory=tuple)
    importance: float = 0.5
    # 命中名册的既有实体 id（消歧直接复用，免表面形式匹配）
    ref_id: int | None = None


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


def extract_structure(
    model: BaseChatModel,
    transcript: str,
    now: datetime,
    *,
    identity_names: List[str] | None = None,
    roster: list | None = None,
) -> ExtractionResult:
    """第❶步结构化抽取；结构化输出失败告警降级为空结果（调用方放弃本轮写入）。

    ``identity_names`` 是用户本人已知称呼（身份卡，指代归一 Tier1）；``roster``
    是向量提名的既有实体行（MemoryEntity），渲染进 prompt 并限定 ref_id 的
    合法取值——模型对既有对象的指称必须回填名册 id，而不是另起新名。
    """
    try:
        result = model.with_structured_output(_ExtractionPayload).invoke([
            SystemMessage(content=_EXTRACTION_SYSTEM_PROMPT),
            HumanMessage(content=_extraction_input(
                transcript, now, identity_names=identity_names, roster=roster,
            )),
        ])
        payload = result if isinstance(result, _ExtractionPayload) else _ExtractionPayload.model_validate(result)
    except Exception:
        logger.warning("记忆抽取结构化输出失败，本轮按无新增处理", exc_info=True)
        return ExtractionResult()
    allowed_ref_ids = {row.id for row in (roster or []) if row.id is not None}
    return _sanitize_extraction(payload, allowed_ref_ids)


def _extraction_input(transcript: str, now: datetime, *, identity_names, roster) -> str:
    """抽取输入组装：时间锚 + 身份卡 + 名册 + 本轮对话（空节整段省略）。"""
    blocks = [f"【当前时间】{now.strftime('%Y-%m-%d %H:%M')}"]
    names = [n for n in (identity_names or []) if n]
    if names:
        blocks.append(
            "【用户身份】用户本人已知称呼：" + "、".join(names)
            + "。凡指用户本人的表述一律用保留键 user，不要为用户建实体。"
        )
    if roster:
        lines = [_roster_line(row) for row in roster]
        blocks.append(
            "【已知对象名册】(ref_id|名称|类型｜又名)\n" + "\n".join(lines)
            + "\n指代名册对象（含简称/代称）必须回填其 ref_id；名册没有的对象才新建。"
        )
    blocks.append(f"【本轮对话】\n{transcript}")
    return "\n\n".join(blocks)


_ROSTER_ALIAS_LIMIT = 3


def _roster_line(row) -> str:
    alias_bit = ""
    aliases = [a for a in (row.aliases or []) if a][:_ROSTER_ALIAS_LIMIT]
    if aliases:
        alias_bit = f"｜又名：{'、'.join(aliases)}"
    return f"#{row.id}|{row.name}|{row.entity_type}{alias_bit}"


def adjudicate_entity_merge(
    model: BaseChatModel,
    ref: str,
    expected_type: str | None,
    candidates: list,
    fact_summaries: dict[int, list[str]],
) -> int | None:
    """实体消歧灰度带裁决：指称 ref 是否指向某个既有候选对象。

    ``candidates`` 为 MemoryEntity 列表（余弦落灰度带的候选），
    ``fact_summaries`` 是 {候选id: [既有陈述摘要…]} 供语义判断。返回命中
    候选 id；都不匹配 / 应答无效 / id 不在候选内一律返回 None（调用方按
    新建处理——不确定宁可不错并，8-28 事故的保守取向）。
    """
    if not candidates:
        return None
    candidate_block = "\n".join(
        f"#{row.id} {row.name}（{row.entity_type}）"
        + (f"｜又名：{'、'.join(a for a in (row.aliases or []) if a)}" if row.aliases else "")
        + _fact_block(fact_summaries.get(row.id, []))
        for row in candidates
    )
    type_hint = expected_type or "未知"
    try:
        result = model.with_structured_output(_MergeDecisionPayload).invoke([
            SystemMessage(content=_MERGE_ADJUDICATION_SYSTEM_PROMPT),
            HumanMessage(content=f"【指称】{ref}（语境类型：{type_hint}）\n\n【既有候选对象】\n{candidate_block}"),
        ])
        payload = result if isinstance(result, _MergeDecisionPayload) else _MergeDecisionPayload.model_validate(result)
    except Exception:
        logger.warning("实体消歧灰度带裁决失败，按新建处理：ref=%r", ref, exc_info=True)
        return None
    valid_ids = {row.id for row in candidates}
    if payload.merge_with not in valid_ids:
        logger.info("实体 %r 灰度带裁决判为新建（%s）", ref, payload.reason or "无匹配候选")
        return None
    logger.info("实体 %r 灰度带裁决并入 #%s：%s", ref, payload.merge_with, payload.reason)
    return payload.merge_with


_MERGE_FACT_LIMIT = 3

_MERGE_ADJUDICATION_SYSTEM_PROMPT = """你是记忆实体消歧裁决器。对话中出现了一个对象的指称，需要判断它是否指向某个既有记忆对象。判断标准：同一现实世界事物才算同一——简称、全称、别名、绰号、代称、部分指代都算同一；仅文字相似但类型或所指不同的不算。只依据给出的档案信息判断，不确定时宁可判为不同。通过结构化结果返回 merge_with（命中的候选编号，全部不匹配填 null）与 reason。"""


def _fact_block(summaries: list[str]) -> str:
    if not summaries:
        return ""
    quoted = "；".join(summaries[:_MERGE_FACT_LIMIT])
    return f"｜已知事实：{quoted}"


def adjudicate_facts(
    model: BaseChatModel,
    facts: list,
    active_statements: list[dict],
) -> list[FactDecision] | None:
    """第❸步陈述裁决。``active_statements`` 为 [{id,summary,...}] 摘要字典。

    返回按 facts 顺序的决策列表（缺索引补 ADD）；LLM 调用/校验失败返回
    None（告警日志），由调用方执行程序化降级规则。
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
        result = model.with_structured_output(_AdjudicationPayload).invoke([
            SystemMessage(content=_ADJUDICATION_SYSTEM_PROMPT),
            HumanMessage(content=f"【既有 ACTIVE 陈述】\n{existing_block}\n\n【候选新事实】\n{new_block}"),
        ])
        payload = result if isinstance(result, _AdjudicationPayload) else _AdjudicationPayload.model_validate(result)
    except Exception:
        logger.warning("记忆裁决结构化输出失败，降级为程序化规则", exc_info=True)
        return None
    decisions = {
        d.index: FactDecision(action=d.action, replace_id=d.replace_id)
        for d in payload.decisions if d.index >= 0
    }
    # 补齐缺失索引为 ADD（部分覆盖视为其余默认新增）
    return [decisions.get(i, FactDecision("ADD")) for i in range(len(facts))]


def _sanitize_extraction(payload: _ExtractionPayload, allowed_ref_ids: set[int] | None = None) -> ExtractionResult:
    """宽松清洗：截断/clamp/过滤溯源引用与保留键；坏条目整条丢弃。

    ``allowed_ref_ids`` 非空时，名册外的 ref_id 一律剥除（防模型幻觉编号
    把事实挂到任意实体上）——剥除后条目仍保留，走正常消歧路径。
    """
    entities = {}
    for raw in payload.entities[:_MAX_ENTITIES]:
        key, name = _clip(raw.key, 40), _clip(raw.name, 60)
        if not key or not name or key == "user" or is_internal_ref(name):
            continue  # user 键由系统保留；内部溯源引用（#S13 等）不是实体名
        aliases = tuple(
            a.strip() for a in raw.aliases
            if a.strip() and not is_internal_ref(a)
        )
        ref_id = raw.ref_id if (allowed_ref_ids is None or raw.ref_id in allowed_ref_ids) else None
        if raw.ref_id is not None and ref_id is None:
            logger.warning("抽取实体 %r 携带名册外 ref_id=%s，已剥除", name, raw.ref_id)
        entities[key] = ExtractedEntity(
            key=key, name=name,
            entity_type=str(raw.type or "OTHER").upper()[:16],
            aliases=aliases, importance=_as_float(raw.importance, 0.5),
            ref_id=ref_id,
        )

    episodes = []
    for raw in payload.episodes[:_MAX_EPISODES]:
        summary = _clip(raw.summary, 500)
        if not summary:
            continue
        links = tuple(
            (link.key, _clip(link.role, 20))
            for link in raw.links
            if link.key
        )
        episodes.append(ExtractedEpisode(
            summary=summary,
            time_hint=_clip(raw.time_hint, 32),
            scene=_clip(raw.scene, 24),
            links=links,
        ))

    facts = []
    for raw in payload.facts[:_MAX_FACTS]:
        predicate, subject_key = _clip(raw.predicate, 24), _clip(raw.subject_key, 40)
        object_key, object_text = _clip(raw.object_key, 40), _clip(raw.object_text, 120)
        if not predicate or not subject_key or (not object_key and not object_text):
            continue
        facts.append(ExtractedFact(
            subject_key=subject_key, predicate=predicate,
            object_key=object_key, object_text=object_text,
            time_hint=_clip(raw.time_hint, 32),
            time_remark=_clip(raw.time_remark, 48),
            confidence=_as_float(raw.confidence, 0.7),
            evidence=_clip(raw.evidence, _MAX_EVIDENCE_LEN),
        ))

    result = ExtractionResult(
        entities=tuple(entities.values()), episodes=tuple(episodes), facts=tuple(facts)
    )
    if result.is_empty() and (payload.entities or payload.episodes or payload.facts):
        logger.warning(
            "记忆抽取结果清洗后全部丢弃（原始 entities=%d episodes=%d facts=%d）",
            len(payload.entities), len(payload.episodes), len(payload.facts),
        )
    return result


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
        strip_internal_refs(part.get("text", ""))
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


def _manual_tag(statement: dict) -> str:
    suffix = "" if statement.get("origin") != "MANUAL" else "（人工维护，禁改）"
    return suffix
