"""记忆存取策略抽象：memory 组件自有的图谱数据访问入口（v2）。

从 v1 的「批量写入 + 全量召回」扩展为图谱操作集；存储与召回策略按
``@injectable(as_type=MemoryRepository)`` 换绑切换，门面与调用方无感。
覆盖语义约定：supersede 不删除——历史陈述永久保留供时点回放；
实体身份纠错（合并/拆分）例外地**追溯改写归属**（含历史行，防悬挂 FK），
被改挂行的 summary 按规范句式重组（见 vocab.fact_summary）。
"""

from abc import ABC, abstractmethod
from datetime import datetime
from uuid import UUID

from app.models.domain.memory import (
    MemoryEntity,
    MemoryEpisode,
    MemoryEpisodeLink,
    MemoryStatement,
)


class MemoryRepository(ABC):
    @abstractmethod
    def find_entity_by_name(self, name: str) -> MemoryEntity | None:
        """规范名精确命中。"""

    @abstractmethod
    def find_entity_by_alias(self, alias: str) -> MemoryEntity | None:
        """别名精确命中（含规范化后的规范名匹配由调用方先试）。"""

    @abstractmethod
    def upsert_entity(self, entity: MemoryEntity) -> MemoryEntity:
        """新建或更新实体，返回带主键的持久化结果。

        更新仅发生在合并场景：补别名/属性/重要度，is_user 单向只升不降。"""

    @abstractmethod
    def get_entity(self, entity_id: int) -> MemoryEntity | None:
        """按 id 取实体（编辑入口的存在性校验用）。"""

    @abstractmethod
    def save_entity(self, entity: MemoryEntity) -> MemoryEntity:
        """按行全量保存（编辑语义：字段直改，区别于 upsert 的合并收敛）。"""

    @abstractmethod
    def get_entities(self, entity_ids: list[int]) -> dict[int, MemoryEntity]:
        """按 id 批量取实体（渲染组装用），缺失 id 静默略过。"""

    @abstractmethod
    def list_entities(self, limit: int | None = None) -> list[MemoryEntity]:
        """全量实体（管理侧图快照用），按主键升序。"""

    @abstractmethod
    def list_episodes(self, limit: int | None = None) -> list[MemoryEpisode]:
        """全量事件梗概（管理侧图快照用），按 occurred_at 倒序（新者在前）。"""

    @abstractmethod
    def list_active_statements(self, limit: int | None = None) -> list[MemoryStatement]:
        """全量 ACTIVE 陈述（当前量级下的快路径排序基准）。"""

    @abstractmethod
    def find_active_statements(self, subject_ids: list[int]) -> list[MemoryStatement]:
        """给定主体的全部 ACTIVE 陈述（裁决上下文与 1-hop 扩散共用）。"""

    @abstractmethod
    def statements_by_ids(self, statement_ids: list[int]) -> list[MemoryStatement]:
        """按 id 取陈述，含非 ACTIVE（深度工具需要展示取代脉络时用）。"""

    @abstractmethod
    def statements_valid_at(self, moment: datetime, subject_ids: list[int] | None = None) -> list[MemoryStatement]:
        """任意时点的世界状态切片：valid_from ≤ moment < (valid_to 或 ∞)。

        与 state 过滤无关——SUPERSEDED 的历史切片恰是回放的素材。"""

    @abstractmethod
    def insert_statement(self, statement: MemoryStatement) -> MemoryStatement:
        """落一条新陈述并回填主键。"""

    @abstractmethod
    def get_statement(self, statement_id: int) -> MemoryStatement | None:
        """按 id 取陈述（编辑入口的存在性与状态校验用）。"""

    @abstractmethod
    def replace_statement(
        self, old_statement_id: int, new_statement: MemoryStatement,
        valid_to: datetime, invalidated_at: datetime,
    ) -> tuple[bool, MemoryStatement | None]:
        """取代式纠正的单事务复合写：旧行置 SUPERSEDED + 新行落库，同成同败。

        返回 ``(是否成功, 持久化的新行)``；旧行不存在或已非 ACTIVE 时失败。"""

    @abstractmethod
    def supersede_statement(self, statement_id: int, valid_to: datetime, invalidated_at: datetime) -> bool:
        """置 SUPERSEDED 并补双时间轴终点；返回是否确实存在且原为 ACTIVE。"""

    @abstractmethod
    def archive_statement(self, statement_id: int, valid_to: datetime, invalidated_at: datetime) -> bool:
        """置 ARCHIVED 并补双时间轴终点（软删，时点回放仍可溯）；返回是否确实存在且原为 ACTIVE。"""

    @abstractmethod
    def insert_episode(self, episode: MemoryEpisode) -> MemoryEpisode:
        """落事件梗概并回填主键。"""

    @abstractmethod
    def episodes_by_ids(self, episode_ids: list[int]) -> list[MemoryEpisode]:
        """按 id 取事件梗概（深度 timeline 命中回查用）。"""

    @abstractmethod
    def link_episode_entities(self, links: list[MemoryEpisodeLink]) -> None:
        """批量挂接；唯一约束冲突（重复挂接）静默幂等。"""

    @abstractmethod
    def episodes_for_entities(self, entity_ids: list[int], limit: int | None = None) -> list[MemoryEpisode]:
        """实体参与过的全部事件（去重），按 occurred_at 倒序。"""

    @abstractmethod
    def links_for_episodes(self, episode_ids: list[int]) -> dict[int, list[MemoryEpisodeLink]]:
        """事件的全部挂接边，按 episode_id 分组（渲染 §E 节点邻域用）。"""

    @abstractmethod
    def bump_access(self, statements: list[int], episodes: list[int], entities: list[int], now: datetime) -> None:
        """召回即强化：access_count+1 并刷新 last_accessed_at（三类齐发）。"""

    # ---------------- 实体身份纠错（合并/拆分/孤立清理，L2）----------------

    @abstractmethod
    def absorb_entity(self, source_id: int, target_id: int) -> dict:
        """错分离合并的单事务复合写：source 的全部陈述（含 SUPERSEDED/ARCHIVED
        历史行，防悬挂 FK）与事件参与改挂 target，名字+别名+attributes 并入
        target（merge_blocklist 中指向 source 的记录随之清除）后删除 source 行；
        被改挂行的 summary 按新归属重组。返回
        ``{"merged": bool, "statements": int, "links": int, "moved": [...], "target": row}``。"""

    @abstractmethod
    def split_entity(
        self, source_id: int, new_entity: MemoryEntity,
        statement_ids: list[int], link_ids: list[int], alias_names: list[str],
    ) -> dict:
        """错合并拆分的单事务复合写：新建实体（身份追改语义），所选陈述/
        参与/别名迁移过去；双方 attributes.merge_blocklist 互写名字与别名
        （消歧的拆分禁令，禁令优先于余弦排序）；source 被拆空（任意状态
        陈述引用=0 且参与=0 且别名=0）时自动删除。返回
        ``{"new": row|None, "statements": int, "links": int, "moved": [...],
        "source_deleted": bool}``。"""

    @abstractmethod
    def delete_fully_orphan_entity(self, entity_id: int) -> bool:
        """仅当实体无任何状态陈述引用且无事件参与时物理删除，返回是否删除。"""

    @abstractmethod
    def episode_links_by_ids(self, link_ids: list[int]) -> list[MemoryEpisodeLink]:
        """按 id 取事件参与边（拆分选择的归属校验用）。"""

    # ---------------- 事件编辑（L3）----------------

    @abstractmethod
    def get_episode(self, episode_id: int) -> MemoryEpisode | None:
        """按 id 取事件梗概（编辑入口的存在性校验用）。"""

    @abstractmethod
    def save_episode(self, episode: MemoryEpisode) -> MemoryEpisode:
        """按行全量保存（编辑语义：字段直改，向量随编辑端口同步）。"""

    @abstractmethod
    def delete_episode(self, episode_id: int) -> MemoryEpisode | None:
        """物理删除事件及其全部参与边（事件无状态机，不走软删），
        返回被删档案；不存在返回 None。"""

    @abstractmethod
    def get_episode_link(self, link_id: int) -> MemoryEpisodeLink | None:
        """按 id 取参与边（改挂入口的存在性校验用）。"""

    @abstractmethod
    def save_episode_link(self, link: MemoryEpisodeLink) -> MemoryEpisodeLink | None:
        """按行全量保存参与边；撞唯一约束（episode/entity/role 组合已存在）
        返回 None（编辑端口据此抛 3007）。"""

    # ---------------- 危险操作区（L4：当日清除 / 会话遗忘 / 整体重置）----------------

    @abstractmethod
    def list_statements_created_between(self, from_dt: datetime, to_dt: datetime) -> list[MemoryStatement]:
        """created_at 落在 [from, to] 的全部陈述（任意状态）。"""

    @abstractmethod
    def list_statements_by_thread(self, thread_id: UUID) -> list[MemoryStatement]:
        """source_thread_id 命中的全部陈述（任意状态；会话遗忘用）。"""

    @abstractmethod
    def list_episodes_created_between(self, from_dt: datetime, to_dt: datetime) -> list[MemoryEpisode]:
        """created_at 落在 [from, to] 的事件。"""

    @abstractmethod
    def list_episodes_by_thread(self, thread_id: UUID) -> list[MemoryEpisode]:
        """thread_id 命中的事件（会话遗忘用）。"""

    @abstractmethod
    def archive_statements(self, statement_ids: list[int], valid_to: datetime, invalidated_at: datetime) -> int:
        """批量把 ACTIVE 陈述置 ARCHIVED 并补双时间轴终点，返回归档条数。"""

    @abstractmethod
    def list_orphan_entities_created_between(self, from_dt: datetime, to_dt: datetime) -> list[MemoryEntity]:
        """created_at 在范围内且无任何状态陈述引用、无事件参与、非用户节点
        的实体（清除后的孤立扫描）。"""

    @abstractmethod
    def list_all_statements(self, limit: int | None = None) -> list[MemoryStatement]:
        """全量陈述含非 ACTIVE（导出用）。"""

    @abstractmethod
    def reset_all(self) -> dict[str, int]:
        """整体重置：单事务清空记忆四表，返回各类删除计数。"""
