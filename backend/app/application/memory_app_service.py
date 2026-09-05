"""记忆管理用例：图快照 + 编辑（L1–L4）委托 + 维护链路编排（repair / rebuild-index）。

api 层唯一消费面：管理端点的读写全部在用户作用域内进行（领域服务强制）。
维护链路是全局算子视角——注入的 ``MemoryGraphRepositoryPort`` /
``MemoryVectorIndexPort`` 单例无作用域，跨用户操作、向量按行归属分组回写；
repair 的事故级定位条件与修复顺序（公司/学校实体、错挂谓词）收敛在本
用例，仓储侧只有与具体事故无关的通用原语（``MemoryMaintenancePort``）。

dry-run 语义：仓储逐方法短会话（无跨方法事务可回滚），故 repair 先做
**纯读检测**再按 ``apply`` 决定是否落写——检测到的动作以「[计划]」入报，
apply 以「[修复]」入报并在写完后做定向向量重写。
"""

import logging
from dataclasses import dataclass, field
from uuid import UUID

from wireup import injectable

from app.domain.memory import (
    KIND_ENTITY,
    KIND_EPISODE,
    KIND_STATEMENT,
    MemoryAdminService,
    MemoryGraphRepositoryPort,
    MemoryGraphService,
    MemoryMaintenancePort,
    MemoryVectorIndexPort,
    VectorEntry,
    entity_content,
    fact_summary,
)
from app.domain.memory.vocab import INTERNAL_REF_TOKEN_RE
from app.models.domain.memory import EntityType, MemoryEntity, MemoryOrigin
from app.models.domain.user import DEFAULT_USER_ID

# 模块级 stdlib logger：经 InterceptHandler 桥入统一日志面
logger = logging.getLogger(__name__)

# ---- 2026-08-28 错合并事故的定位常量（按名定位而非硬编码 id，同形数据可修）----
COMPANY_NAME = "广东禹通互联网科技有限公司"
SCHOOL_NAME = "广州市卫生职业技术学院"
# 错挂谓词（错挂行的定位条件：该谓词 + 客体=公司实体）
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


@dataclass(frozen=True)
class RebuildPlan:
    """rebuild-index 的收集结果（探测与执行共用）。"""

    grouped: dict[UUID, list[VectorEntry]]
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(len(entries) for entries in self.grouped.values())


@injectable
@dataclass
class MemoryAppService:
    """记忆管理/维护用例门面。"""

    graph_service: MemoryGraphService
    admin_service: MemoryAdminService
    memory_repo: MemoryGraphRepositoryPort
    maintenance_repo: MemoryMaintenancePort
    vector_index: MemoryVectorIndexPort

    # ---------------- 管理侧（用户作用域，领域服务委托） ----------------

    def graph_snapshot(self, user_id: UUID, *, at_iso: str | None, limit: int) -> dict:
        return self.graph_service.graph_snapshot(user_id=user_id, at_iso=at_iso, limit=limit)

    def add_statement(self, user_id: UUID, request):
        return self.admin_service.add_statement(user_id, request)

    def correct_statement(self, user_id: UUID, statement_ref: str, request):
        return self.admin_service.correct_statement(user_id, statement_ref, request)

    def archive_statement(self, user_id: UUID, statement_ref: str):
        return self.admin_service.archive_statement(user_id, statement_ref)

    def update_entity(self, user_id: UUID, entity_ref: str, request):
        return self.admin_service.update_entity(user_id, entity_ref, request)

    def merge_entity(self, user_id: UUID, entity_ref: str, request):
        return self.admin_service.merge_entity(user_id, entity_ref, request)

    def split_entity(self, user_id: UUID, entity_ref: str, request):
        return self.admin_service.split_entity(user_id, entity_ref, request)

    def delete_entity(self, user_id: UUID, entity_ref: str):
        return self.admin_service.delete_entity(user_id, entity_ref)

    def update_episode(self, user_id: UUID, episode_ref: str, request):
        return self.admin_service.update_episode(user_id, episode_ref, request)

    def delete_episode(self, user_id: UUID, episode_ref: str):
        return self.admin_service.delete_episode(user_id, episode_ref)

    def update_episode_link(self, user_id: UUID, link_ref: str, request):
        return self.admin_service.update_episode_link(user_id, link_ref, request)

    def purge_preview(self, user_id: UUID, request):
        return self.admin_service.purge_preview(user_id, request)

    def purge_memory(self, user_id: UUID, request):
        return self.admin_service.purge_memory(user_id, request)

    def export_memory(self, user_id: UUID) -> dict:
        return self.admin_service.export_memory(user_id)

    def reset_all_memory(self, user_id: UUID, request):
        return self.admin_service.reset_all_memory(user_id, request)

    # ---------------- 维护链路（全局算子视角） ----------------

    def repair(self, *, apply: bool = False) -> RepairReport:
        """实体错合并存量修复（幂等，可安全重跑）：清污染别名 → 摘错并别名 →
        建/复用学校实体 → 改挂错挂陈述/参与 → 定向向量重写。"""
        report = RepairReport()
        graph = self.memory_repo
        status = "修复" if apply else "计划"

        # ---- 纯读检测（dry-run 零写入的前提）----
        polluted: list[tuple[MemoryEntity, list[str]]] = []
        for entity in graph.list_entities():
            bad = [a for a in (entity.aliases or []) if INTERNAL_REF_TOKEN_RE.match(a)]
            if bad:
                polluted.append((entity, bad))

        company = graph.find_entity_by_name(COMPANY_NAME)
        drop_school_alias = company is not None and SCHOOL_NAME in (company.aliases or [])
        wrong_statements = (
            self.maintenance_repo.list_statements_by_object_predicate(MISBOUND_PREDICATE, company.id)
            if company is not None else []
        )
        wrong_links = (
            self.maintenance_repo.list_episode_links_by_entity(company.id, role=MISBOUND_PREDICATE)
            if company is not None else []
        )

        # ---- 报告检测结论（不改库）----
        for entity, bad in polluted:
            report.log(status, f"实体#{entity.id}「{entity.name}」移除内部引用别名：{'、'.join(bad)}")
        if company is None:
            report.log("跳过", f"未找到实体「{COMPANY_NAME}」（可能已修复或无此数据）")
        elif drop_school_alias:
            report.log(status, f"实体#{company.id}「{COMPANY_NAME}」移除错合并别名：「{SCHOOL_NAME}」")
        school = graph.find_entity_by_name(SCHOOL_NAME)
        if school is None:
            report.log(status, f"新建 ORG 实体「{SCHOOL_NAME}」")
        else:
            report.log("跳过", f"实体#{school.id}「{SCHOOL_NAME}」已存在，复用")
        for row in wrong_statements:
            report.log(status, f"statement#{row.id}「{row.summary!r}」改挂实体「{SCHOOL_NAME}」")
        for link in wrong_links:
            report.log(
                status,
                f"episode_link#{link.id}（episode#{link.episode_id}，role={MISBOUND_PREDICATE}）"
                f"改挂实体「{SCHOOL_NAME}」",
            )

        if not report.changed:
            report.log("跳过", "未发现需修复的数据")
            return report
        if not apply:
            report.log("计划", "以上为 dry-run 结果；确认后加 --apply 执行")
            return report

        # ---- 落写（仅 apply）----
        touched: dict[int, MemoryEntity] = {}
        for entity, bad in polluted:
            entity.aliases = [a for a in entity.aliases if a not in bad]
            saved = graph.save_entity(entity)
            touched[saved.id] = saved
        if drop_school_alias:
            company.aliases = [a for a in company.aliases if a != SCHOOL_NAME]
            saved = graph.save_entity(company)
            touched[saved.id] = saved
        if school is None:
            # 归属沿用被拆档（公司）的用户；公司也不存在时落默认用户（存量回填归属）
            school = graph.upsert_entity(MemoryEntity(
                user_id=company.user_id if company is not None else DEFAULT_USER_ID,
                entity_type=EntityType.ORG.value,
                name=SCHOOL_NAME,
                aliases=[],
                importance=0.5,
                origin=MemoryOrigin.EXTRACTED.value,
            ))
        touched[school.id] = school

        rebound: list = []
        for row in wrong_statements:
            subject = graph.get_entity(row.subject_id)
            obj_label = subject.name if subject else "?"
            row.object_entity_id = school.id
            # 规范句式与 _recomposed_summary 对齐（验收方为 multi 谓词，无「的/是」）
            row.summary = fact_summary(obj_label, MISBOUND_PREDICATE, SCHOOL_NAME)
            rebound.append(self.maintenance_repo.save_statement(row))
        for link in wrong_links:
            link.entity_id = school.id
            # 撞唯一组合（同事件同角色已挂学校）返回 None：视为已修复，静默跳过
            self.memory_repo.save_episode_link(link)

        self._rewrite_vectors(list(touched.values()), rebound)
        report.log(
            "修复", f"向量重写 {len(touched) + len(rebound)} 条（{KIND_ENTITY}"
                    f" {len(touched)} / {KIND_STATEMENT} {len(rebound)}）",
        )
        logger.info("记忆实体修复完成：%d 条动作", len(report.lines))
        return report

    def collect_index_entries(self) -> RebuildPlan:
        """按用户分组收集全量向量条目与分类计数（rebuild 探测与执行共用）。

        SQL 为事实源：entity 全量 / statement 仅 ACTIVE（SUPERSEDED 历史切片
        不在索引契约内，重嵌顺带清掉 REPLACE 残留的孤儿向量）/ episode 全量。
        """
        graph = self.memory_repo
        entities = graph.list_entities()
        statements = graph.list_active_statements()
        episodes = graph.list_episodes()

        grouped: dict[UUID, list[VectorEntry]] = {}
        for row in entities:
            grouped.setdefault(row.user_id, []).append(
                VectorEntry(KIND_ENTITY, row.id, entity_content(row), None, None)
            )
        for row in statements:
            grouped.setdefault(row.user_id, []).append(
                VectorEntry(
                    KIND_STATEMENT, row.id, row.summary,
                    str(row.source_thread_id) if isinstance(row.source_thread_id, UUID) else None,
                    row.valid_from,
                )
            )
        for row in episodes:
            grouped.setdefault(row.user_id, []).append(
                VectorEntry(KIND_EPISODE, row.id, row.summary, str(row.thread_id), row.occurred_at)
            )
        counts = {
            KIND_ENTITY: len(entities),
            KIND_STATEMENT: len(statements),
            KIND_EPISODE: len(episodes),
        }
        return RebuildPlan(grouped=grouped, counts=counts)

    def rebuild_index(self) -> dict[str, int]:
        """按用户 drop 各自 collection 后全量重嵌，返回分类计数。"""
        plan = self.collect_index_entries()
        for user_id, entries in plan.grouped.items():
            self.vector_index.for_user(user_id).rebuild(entries)
        return plan.counts

    def _rewrite_vectors(self, touched_entities: list[MemoryEntity], rebound_statements: list) -> None:
        """触达对象定向向量重写（touched 实体 + 改挂陈述；按行归属分组进各用户 collection）。"""
        grouped: dict[UUID, list[VectorEntry]] = {}
        for row in touched_entities:
            grouped.setdefault(row.user_id, []).append(
                VectorEntry(KIND_ENTITY, row.id, entity_content(row), None, None)
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
        for owner, entries in grouped.items():
            self.vector_index.for_user(owner).upsert_many(entries)
