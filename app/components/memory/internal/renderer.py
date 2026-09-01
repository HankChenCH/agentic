"""召回渲染器：§7 模板契约的唯一实现点。

渲染文本自此是对 LLM 与审计双方的接口契约——格式改动必须走本文件并
同步快照测试（test_memory_renderer.py）。两形态共用同一语法：
- render_brief      快速回忆注入（仅事实拓扑行的简报态）
- render_fragments  深度工具输出（拓扑+摘要+证据+存疑的完整态）
行内不写序号：两个出口统一按 enumerate 编号，杜绝两套计数漂移。
分片组装（assemble_fragments）：按共享实体把命中条目聚成连通分量，
每分量一个片段、根取分量内最高分条目；单条孤目即独立小片段。
事件节点投影约定：情节层 episode 渲染为 ``§E{id}`` 节点，其挂接边
以 role 作谓词；陈述中若引用了事件性实体则照常按实体渲染，不去重。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import re


# ---------------- 内部溯源引用 ----------------
# 渲染输出携带的数据库溯源键：#S 陈述 / §E 事件 / #T 证据轮次 / #实体 兜底
# （简报态头部的「＃编号」为全角静态文案，不在其列）。它们是给人看的键，
# 不是实体名——曾被助手复读后经抽取管线吸回实体 aliases（"#S13" 污染），
# 入库前必须以本处正则拦截。
INTERNAL_REF_TOKEN_RE = re.compile(r"^(?:#S\d+|§E\d+|#T[0-9a-f]+|#实体\d+)$")
INTERNAL_REF_IN_TEXT_RE = re.compile(r"#S\d+|§E\d+|#T[0-9a-f]+|#实体\d+")


def is_internal_ref(text: str) -> bool:
    """该 token 是否为渲染层内部溯源引用（不可作为实体名/别名入库）。"""
    return bool(INTERNAL_REF_TOKEN_RE.match(text.strip()))


def strip_internal_refs(text: str) -> str:
    """从文本中剥离内部溯源引用（抽取 transcript 的助手侧净化）。"""
    return INTERNAL_REF_IN_TEXT_RE.sub("", text)


# ---------------- 输出结构 ----------------


@dataclass
class Fragment:
    """一个连通分量对应的完整态片段。"""

    root_label: str = ""
    score: float = 0.0
    span: str = ""
    topology_lines: list[str] = field(default_factory=list)
    summary: str | None = None
    evidence: list[str] = field(default_factory=list)
    doubts: list[str] = field(default_factory=list)


def brief_context(now: datetime, lines: list[str]) -> str:
    """简报态：快速回忆块的扁平事实行。

    使用提示随块输出（进模板 ``{memory}`` 槽或 RAG 折叠）：块空则整节移除，
    提示与数据同生同灭——agent 人设不复写两级记忆策略（聚合在
    ``components/memory/manifest.py``、随深度工具 description 触达）。
    """
    if not lines:
        return ""
    head = f"## 快速记忆上下文（共{len(lines)}条 · 取自 {now.strftime('%Y-%m-%d')}）"
    usage = "本节为用户级常驻摘要，请优先直接利用；不足以还原时再用记忆工具深挖。"
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))
    return f"{head}\n{usage}\n【事实拓扑】＃编号=数据库溯源键\n{numbered}"


def fragments_output(now: datetime, fragments: list[Fragment]) -> str:
    """完整态：分片段的四段式输出。"""
    if not fragments:
        return ""
    blocks = []
    for index, frag in enumerate(fragments, start=1):
        section = [
            f"### 记忆片段 {index} ｜根：{frag.root_label} ｜"
            f"相关度 {max(frag.score, 0.0):.2f} ｜跨度 {frag.span}",
            "【事实拓扑】",
            *(f"{i}. {line}" for i, line in enumerate(frag.topology_lines, start=1)),
        ]
        if frag.summary:
            section += ["【叙事摘要】", frag.summary]
        if frag.evidence:
            section += ["【原始证据】"] + [f"- {item}" for item in frag.evidence]
        if frag.doubts:
            section += ["【存疑备注】"] + [f"- {item}" for item in frag.doubts]
        blocks.append("\n".join(section))
    head = f"## 深度记忆上下文（共{len(fragments)}片段 · 取自 {now.strftime('%Y-%m-%d')}）"
    return "\n\n".join([head, *blocks])


# ---------------- 分片组装 ----------------


def assemble_fragments(
    statements_scored: list[tuple],
    episodes_scored: list[tuple],
    links_by_episode: dict[int, list],
    entities: dict,
    *,
    now: datetime,
    low_confidence_below: float = 0.7,
) -> list[Fragment]:
    """按共享实体做连通分量聚簇。

    statements_scored / episodes_scored 为 ``(row, score)`` 列表；
    ``entities`` 是 {id: MemoryEntity} 邻域名册。返回按最高分降序的片段。
    """
    parent: dict[tuple, tuple] = {}

    def find(node: tuple) -> tuple:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(a: tuple, b: tuple) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    def touch(node: tuple) -> tuple:
        parent.setdefault(node, node)
        return node

    for statement, _score in statements_scored:
        union(touch(("s", statement.id)), touch(("ent", statement.subject_id)))
        if statement.object_entity_id is not None:
            union(touch(("s", statement.id)), touch(("ent", statement.object_entity_id)))
    for episode, _score in episodes_scored:
        enode = touch(("e", episode.id))
        for link in links_by_episode.get(episode.id, []):
            union(enode, touch(("ent", link.entity_id)))

    groups: dict[tuple, dict] = {}
    for statement, score in statements_scored:
        groups.setdefault(find(("s", statement.id)), {"s": [], "e": []})["s"].append((statement, score))
    for episode, score in episodes_scored:
        groups.setdefault(find(("e", episode.id)), {"s": [], "e": []})["e"].append((episode, score))

    fragments = [
        _build_fragment(bucket, links_by_episode, entities, now, low_confidence_below)
        for bucket in groups.values()
    ]
    fragments.sort(key=lambda frag: frag.score, reverse=True)
    return fragments


def _build_fragment(
    bucket: dict, links_by_episode: dict, entities: dict, now: datetime, low_below: float
) -> Fragment:
    statements: list[tuple] = bucket.get("s", [])
    episodes: list[tuple] = bucket.get("e", [])

    all_scores = [score for _, score in statements + episodes]
    frag = Fragment(score=max(all_scores) if all_scores else 0.0)

    times: list[datetime] = []
    anchor_names: list[str] = []
    summaries: list[str] = []

    for statement, _score in sorted(statements, key=lambda pair: pair[0].id):
        subject = entities.get(statement.subject_id)
        anchor_names.append(subject.name if subject else f"#S{statement.id}")
        frag.topology_lines.append(statement_topology_line(statement, entities, now=now))
        if statement.confidence < low_below:
            frag.doubts.append(f"#S{statement.id} 置信度偏低（{statement.confidence:.1f}）")
        if statement.valid_from:
            times.append(statement.valid_from)
        frag.evidence.extend(
            line for line in (_evidence(e) for e in (statement.evidence or [])[:3]) if line
        )

    for episode, _score in episodes:
        scene_bit = f"（场景：{episode.scene}）" if episode.scene else ""
        event_label = f"§E{episode.id} {_shorten(episode.summary, 12)}·EVENT"
        links = links_by_episode.get(episode.id, [])
        if not links:
            frag.topology_lines.append(f"{event_label}{scene_bit} {time_annotated(episode.occurred_at, now)}")
        else:
            for link in links:
                person = entities.get(link.entity_id)
                frag.topology_lines.append(
                    f"{event_label} -[{link.role or '涉及'}]-> "
                    f"({_entity_tag(person, link.entity_id)}) {time_annotated(episode.occurred_at, now)}"
                )
        if episode.summary:
            summaries.append(episode.summary + scene_bit)
        if episode.occurred_at:
            times.append(episode.occurred_at)
        frag.evidence.extend(
            line for line in (_evidence(e) for e in (episode.evidence or [])[:3]) if line
        )

    root = _shorten(summaries[0], 18) if summaries else None
    frag.root_label = root or (anchor_names[0] if anchor_names else "未命名")
    frag.summary = "\n".join(summaries[:3]) or None
    frag.span = _span(times)
    frag.topology_lines = frag.topology_lines[:32]
    return frag


# ---------------- 行级小件 ----------------


def statement_topology_line(statement, entities: dict, *, now: datetime | None = None) -> str:
    """单条陈述的事实拓扑行；``now`` 提供时追加相对时间与模糊原文双写。"""
    subject = entities.get(statement.subject_id)
    parts = [
        f"#S{statement.id} ({_entity_tag(subject, statement.subject_id)}) "
        f"-[{statement.predicate}]-> {_object_label(statement, entities)}",
        time_annotated(statement.valid_from, now, statement.time_remark),
    ]
    return " ".join(part.rstrip() for part in parts if part.strip())


def time_annotated(dt: datetime | None, now: datetime | None = None, remark: str | None = None) -> str:
    """对外标准时间标注：@绝对日期 + 相对半角 + 模糊原文兜底。"""
    if dt is None:
        return f"@（{remark}）" if remark else "@时间未知"
    tail = f"@{dt.strftime('%Y-%m-%d')}"
    if now is not None:
        tail += f"（{relative_time(dt, now)}）"
    if remark:
        tail += f"〔表述：{remark}〕"
    return tail


def relative_time(dt: datetime, now: datetime) -> str:
    # SQLite 读回 naive（恒 UTC 墙钟）：与 aware 的运行时锚相减前先归一
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta_days = round((dt - now).total_seconds() / 86400)
    if delta_days == 0:
        return "今天"
    if delta_days == -1:
        return "昨天"
    if delta_days == -2:
        return "前天"
    if delta_days < 0:
        past = -delta_days
        if past < 30:
            return f"{past}天前"
        months = max(int(round(past / 30)), 1)
        if months < 12:
            return f"约{months}个月前"
        years = max(int(round(past / 365)), 1)
        return f"约{years}年前"
    return f"{delta_days}天后"


def _entity_tag(entity, fallback_id) -> str:
    if entity is None:
        return f"#实体{fallback_id}"
    return f"{entity.name}·{(entity.entity_type or 'OTHER')}"


def _object_label(statement, entities: dict) -> str:
    if statement.object_entity_id is not None:
        target = entities.get(statement.object_entity_id)
        return f"({_entity_tag(target, statement.object_entity_id)})"
    if statement.object_text:
        return f"“{statement.object_text}”"
    return "(未知客体)"


def _evidence(entry) -> str | None:
    if isinstance(entry, dict) and entry.get("quote"):
        source = entry.get("source_turn_id")
        marker = f" (#T{str(source)[:6]})" if source else ""
        return f"原话：“{entry['quote']}”{marker}"
    return None


def _shorten(text: str | None, width: int) -> str | None:
    if not text:
        return None
    cleaned = str(text).strip().splitlines()[0]
    return cleaned if len(cleaned) <= width else cleaned[:width] + "…"


def _span(times: list[datetime]) -> str:
    if not times:
        return "未知时段"
    lo, hi = min(times), max(times)
    if lo.date() == hi.date():
        return lo.strftime("%Y-%m-%d")
    return f"{lo.strftime('%Y-%m-%d')} → {hi.strftime('%Y-%m-%d')}"

