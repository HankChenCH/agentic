"""记忆存取策略抽象：memory 组件自有的图谱数据访问入口（v2）。

从 v1 的「批量写入 + 全量召回」扩展为图谱操作集；存储与召回策略按
``@injectable(as_type=MemoryRepository)`` 换绑切换，门面与调用方无感。
覆盖语义约定：supersede 不删除——历史陈述永久保留供时点回放。
"""

from abc import ABC, abstractmethod
from datetime import datetime

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
    def supersede_statement(self, statement_id: int, valid_to: datetime, invalidated_at: datetime) -> bool:
        """置 SUPERSEDED 并补双时间轴终点；返回是否确实存在且原为 ACTIVE。"""

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
