"""memory 域维护命令：实体错合并存量修复 + 记忆向量索引全量重建。

「广州市卫生职业技术学院」被错并入禹通公司档案的事故（2026-08-28）：实体
消歧曾把 Weaviate hybrid 融合分当余弦阈值用，statement / episode_link 随之
错挂，别名还吸入了「学校名」「#S13/#S14」等污染项。代码侧修复见
``services/domain/memory/vector_index.py``（text_cosine）与
``components/memory/internal/resolution.py``（两段式消歧）；本模块的 repair 清存量。

仓储的 upsert_entity 对 aliases 只并集不删除、也没有按 id 改 statement 的
接口，因此修复直开 SQLModel Session 写（一次性维护命令可接受的例外）。
"""

import logging
from dataclasses import dataclass, field
from typing import Annotated
from uuid import UUID

import typer
from sqlmodel import Session, select

from app.components.memory.internal.renderer import INTERNAL_REF_TOKEN_RE
from app.components.memory.internal.resolution import entity_content as _entity_content
from app.core.container import build_sync_container
from app.infrastructures.db import DatabaseFactory
from app.models.domain.memory import (
    EntityType,
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryOrigin,
    MemoryStatement,
    StatementState,
)
from app.models.domain.user import DEFAULT_USER_ID
from app.services.domain.memory import (
    KIND_ENTITY,
    KIND_EPISODE,
    KIND_STATEMENT,
    MemoryVectorIndex,
    VectorEntry,
)

logger = logging.getLogger(__name__)

# 事故当事实体（按名定位而非硬编码 id，其他环境同形数据也能修）
COMPANY_NAME = "广东禹通互联网科技有限公司"
SCHOOL_NAME = "广州市卫生职业技术学院"

# statement 错挂谓词（错挂行的定位条件：该谓词 + 客体=公司实体）
MISBOUND_PREDICATE = "验收方"


@dataclass
class RepairReport:
    """逐条动作记录：``[修复]/[计划]/[跳过]`` 前缀标记状态。"""

    lines: list[str] = field(default_factory=list)
    changed: bool = False

    def log(self, status: str, message: str) -> None:
        self.lines.append(f"[{status}] {message}")
        if status != "跳过":
            self.changed = True


def repair(engine, vector_index, *, apply: bool = False) -> RepairReport:
    """清污染别名 → 建学校实体 → 改挂 statement/episode_link → 定向量重写。

    ``apply=False`` 为 dry-run：完整探测并列出将执行的动作，不落库。
    """
    report = RepairReport()
    with Session(engine) as session:
        touched_entities: dict[int, MemoryEntity] = {}
        rebound_statements: list[MemoryStatement] = []

        # ---- ❶ 全实体清理内部溯源引用别名（#S13 等，事故副产物）----
        for entity in session.exec(select(MemoryEntity)).all():
            bad = [a for a in (entity.aliases or []) if INTERNAL_REF_TOKEN_RE.match(a)]
            if not bad:
                continue
            entity.aliases = [a for a in entity.aliases if a not in bad]
            touched_entities[entity.id] = entity
            report.log(
                "修复" if apply else "计划",
                f"实体#{entity.id}「{entity.name}」移除内部引用别名：{'、'.join(bad)}",
            )

        # ---- ❷ 学校名从公司档案别名中摘除 ----
        company = session.exec(
            select(MemoryEntity).where(MemoryEntity.name == COMPANY_NAME)
        ).first()
        if company is not None and SCHOOL_NAME in (company.aliases or []):
            company.aliases = [a for a in company.aliases if a != SCHOOL_NAME]
            touched_entities[company.id] = company
            report.log(
                "修复" if apply else "计划",
                f"实体#{company.id}「{COMPANY_NAME}」移除错合并别名：「{SCHOOL_NAME}」",
            )
        elif company is None:
            report.log("跳过", f"未找到实体「{COMPANY_NAME}」（可能已修复或无此数据）")

        # ---- ❸ 建/复用学校实体 ----
        school = session.exec(
            select(MemoryEntity).where(MemoryEntity.name == SCHOOL_NAME)
        ).first()
        if school is None:
            school = MemoryEntity(
                # 归属沿用被拆档（公司）的用户；公司也不存在时落默认用户（存量回填归属）
                user_id=company.user_id if company is not None else DEFAULT_USER_ID,
                entity_type=EntityType.ORG.value,
                name=SCHOOL_NAME,
                aliases=[],
                importance=0.5,
                origin=MemoryOrigin.EXTRACTED.value,
            )
            session.add(school)
            session.flush()  # 取自增 id 供改挂引用
            report.log("修复" if apply else "计划", f"新建 ORG 实体#{school.id}「{SCHOOL_NAME}」")
        else:
            report.log("跳过", f"实体#{school.id}「{SCHOOL_NAME}」已存在，复用")
        touched_entities[school.id] = school

        # ---- ❹ 错挂 statement 改挂 + summary 重写 ----
        if company is not None:
            wrong = session.exec(
                select(MemoryStatement).where(
                    MemoryStatement.predicate == MISBOUND_PREDICATE,
                    MemoryStatement.object_entity_id == company.id,
                )
            ).all()
            for row in wrong:
                subject = session.get(MemoryEntity, row.subject_id)
                obj_label = subject.name if subject else "?"
                row.object_entity_id = school.id
                # 与 _fact_summary 通用句式对齐（验收方为 multi 谓词，无「的/是」）
                row.summary = f"{obj_label}{MISBOUND_PREDICATE}{SCHOOL_NAME}"
                rebound_statements.append(row)
                report.log(
                    "修复" if apply else "计划",
                    f"statement#{row.id}「{obj_label}-[{MISBOUND_PREDICATE}]->」"
                    f"改挂实体#{school.id}「{SCHOOL_NAME}」，summary={row.summary!r}",
                )

        # ---- ❺ 错挂 episode_link（role=验收方 且 指向公司）改挂 ----
        if company is not None:
            links = session.exec(
                select(MemoryEpisodeLink).where(
                    MemoryEpisodeLink.entity_id == company.id,
                    MemoryEpisodeLink.role == MISBOUND_PREDICATE,
                )
            ).all()
            for link in links:
                link.entity_id = school.id
                report.log(
                    "修复" if apply else "计划",
                    f"episode_link#{link.id}（episode#{link.episode_id}，"
                    f"role={MISBOUND_PREDICATE}）改挂实体#{school.id}「{SCHOOL_NAME}」",
                )

        if not report.changed:
            report.log("跳过", "未发现需修复的数据")
            return report

        if not apply:
            report.log("计划", "以上为 dry-run 结果；确认后加 --apply 执行")
            session.rollback()
            return report

        session.commit()

        # ---- ❻ 定向向量重写（touched 实体 + 改挂 statement；按行归属分组进各用户 collection）----
        grouped: dict[UUID, list[VectorEntry]] = {}
        for row in touched_entities.values():
            grouped.setdefault(row.user_id, []).append(
                VectorEntry(KIND_ENTITY, row.id, _entity_content(row), None, None)
            )
        for row in rebound_statements:
            owner = row.user_id or DEFAULT_USER_ID
            grouped.setdefault(owner, []).append(
                VectorEntry(
                    KIND_STATEMENT, row.id, row.summary,
                    str(row.source_thread_id) if isinstance(row.source_thread_id, UUID) else None,
                    row.valid_from,
                )
            )
        total = 0
        for owner, entries in grouped.items():
            vector_index.for_user(owner).upsert_many(entries)
            total += len(entries)
        report.log("修复", f"向量重写 {total} 条（{KIND_ENTITY}"
                         f" {len(touched_entities)} / {KIND_STATEMENT} {len(rebound_statements)}）")

    logger.info("记忆实体修复完成：%d 条动作", len(report.lines))
    return report


def collect_index_entries(engine) -> tuple[dict[UUID, list[VectorEntry]], dict[str, int]]:
    """按用户分组收集全量向量条目与分类计数（rebuild-index 探测与执行共用）。

    SQL 为事实源：entity 全量 / statement 仅 ACTIVE（SUPERSEDED 历史切片
    不在索引契约内，重嵌顺带清掉 REPLACE 残留的孤儿向量）/ episode 全量。
    每用户一个 collection（Memory_{uid.hex}），重建按用户分组 drop+重嵌。
    """
    grouped: dict[UUID, list[VectorEntry]] = {}
    counts = {KIND_ENTITY: 0, KIND_STATEMENT: 0, KIND_EPISODE: 0}
    with Session(engine) as session:
        entities = session.exec(select(MemoryEntity)).all()
        statements = session.exec(
            select(MemoryStatement).where(MemoryStatement.state == StatementState.ACTIVE.value)
        ).all()
        episodes = session.exec(select(MemoryEpisode)).all()

        def bucket(uid: UUID) -> list[VectorEntry]:
            return grouped.setdefault(uid, [])

        for row in entities:
            bucket(row.user_id).append(
                VectorEntry(KIND_ENTITY, row.id, _entity_content(row), None, None)
            )
        for row in statements:
            bucket(row.user_id).append(
                VectorEntry(
                    KIND_STATEMENT, row.id, row.summary,
                    str(row.source_thread_id) if isinstance(row.source_thread_id, UUID) else None,
                    row.valid_from,
                )
            )
        for row in episodes:
            bucket(row.user_id).append(
                VectorEntry(KIND_EPISODE, row.id, row.summary, str(row.thread_id), row.occurred_at)
            )
        counts = {
            KIND_ENTITY: len(entities),
            KIND_STATEMENT: len(statements),
            KIND_EPISODE: len(episodes),
        }
    return grouped, counts


def rebuild_index(engine, vector_index) -> dict[str, int]:
    """按用户 drop 各自 collection 后全量重嵌，返回分类计数。"""
    grouped, counts = collect_index_entries(engine)
    for user_id, entries in grouped.items():
        vector_index.for_user(user_id).rebuild(entries)
    return counts


def _services():
    """命令体内惰性建容器：顶层 --config-dir/--env-file 已先桥接环境变量。"""
    container = build_sync_container()
    return container.get(DatabaseFactory).create(), container.get(MemoryVectorIndex)


app = typer.Typer(no_args_is_help=True, help="记忆域维护")


@app.command("repair")
def repair_cmd(
    apply: Annotated[
        bool, typer.Option("--apply", help="真实写入并重写触达对象向量；缺省 dry-run")
    ] = False,
) -> None:
    """修复实体错合并的存量数据（幂等，可安全重跑）。"""
    engine, vector_index = _services()
    report = repair(engine, vector_index, apply=apply)
    typer.echo(f"== 记忆实体修复（{'APPLY' if apply else 'DRY-RUN'}）==")
    for line in report.lines:
        typer.echo(line)


@app.command("rebuild-index")
def rebuild_index_cmd(
    yes: Annotated[
        bool, typer.Option("--yes", help="真执行 drop+全量重嵌；缺省只打印条数统计")
    ] = False,
) -> None:
    """全量重建记忆向量索引（换 embedding 模型后必跑；SQL 为事实源）。"""
    engine, vector_index = _services()
    grouped, counts = collect_index_entries(engine)
    total = sum(len(entries) for entries in grouped.values())
    detail = "、".join(f"{kind} {n}" for kind, n in counts.items())
    if not yes:
        typer.echo("== 记忆向量索引重建（DRY-RUN）==")
        typer.echo(f"将按用户 drop 并重嵌 {total} 条、{len(grouped)} 个用户 collection（{detail}）")
        typer.echo("确认后加 --yes 执行")
        return
    rebuild_index(engine, vector_index)
    typer.echo(f"== 记忆向量索引重建完成：{total} 条、{len(grouped)} 个用户 collection（{detail}）==")
