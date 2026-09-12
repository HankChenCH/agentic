"""记忆域领域端口（依赖倒置）：协议住领域层，实现由外层回填。

图快照（graph_snapshot.py）需要记忆库的只读面，编辑能力（admin_service.py）
需要记忆库的纠错写面，但领域层不得 import components——协议住
领域层、实现住在
``app/components/memory/``（``graph_reader.py`` 只读 / ``editor.py`` 编辑），
经 wireup ``@injectable(as_type=...)`` 按本协议类型注入。依赖箭头
components ──► domain 合法。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.models.domain.memory import MemoryEntity, MemoryEpisode, MemoryEpisodeLink, MemoryStatement


class MemoryGraphReader(Protocol):
    """图快照所需的记忆只读投影面。

    ``for_user`` 返回绑定用户的只读视图（用户级隔离的强制入口）：注入实例
    无作用域（维护 CLI 兜底），HTTP 管理链必须先过用户作用域。
    """

    def for_user(self, user_id: UUID) -> "MemoryGraphReader": ...

    def list_entities(self, limit: int | None = None) -> list[MemoryEntity]: ...

    def get_entities(self, entity_ids: list[int]) -> dict[int, MemoryEntity]: ...

    def list_active_statements(self, limit: int | None = None) -> list[MemoryStatement]: ...

    def statements_valid_at(
        self, moment: datetime, subject_ids: list[int] | None = None
    ) -> list[MemoryStatement]: ...

    def list_episodes(self, limit: int | None = None) -> list[MemoryEpisode]: ...

    def links_for_episodes(self, episode_ids: list[int]) -> dict[int, list[MemoryEpisodeLink]]: ...


@dataclass(frozen=True)
class FactWrite:
    """事实写入载荷（补充/纠正共用）。

    - 补充（add）：主体二选一（entity_id 直引 / name 引用不存在则建），
      谓词与客体必填；
    - 纠正（correct）：全部字段可选，None = 保持原值；summary 缺省按
      规范句式随值变化重组；note 作为人工备注落 evidence。
    """

    subject_entity_id: int | None = None
    subject_name: str | None = None
    subject_entity_type: str = "OTHER"
    predicate: str | None = None
    object_entity_id: int | None = None
    object_text: str | None = None
    summary: str | None = None
    valid_from: datetime | None = None
    note: str | None = None


@dataclass(frozen=True)
class StatementMutation:
    """一次取代式纠正的结果：被取代的旧行 + 接续的新 ACTIVE 行。"""

    old: MemoryStatement
    new: MemoryStatement


@dataclass(frozen=True)
class EntitySplitSpec:
    """拆分载荷：新实体定义 + 要迁移过去的陈述/参与/别名选择。"""

    name: str
    entity_type: str = "OTHER"
    aliases: tuple[str, ...] = ()
    statement_ids: tuple[int, ...] = ()
    episode_link_ids: tuple[int, ...] = ()


class MemoryEditor(Protocol):
    """记忆纠错写面（L1：事实增/纠正/归档 + 实体改名）。

    方法即用例：存在性/状态类机械校验由实现侧抛 3xxx 业务异常，取值
    合法性与「是否真的有变化」等请求语义判断留在领域服务。向量同步是
    方法内聚动作（SQL 提交后 best-effort），调用方无需感知。

    ``for_user`` 返回绑定用户的编辑视图（用户级隔离的强制入口）：注入
    实例无作用域（维护 CLI 兜底），HTTP 管理链必须先过用户作用域。
    """

    def for_user(self, user_id: UUID) -> "MemoryEditor": ...

    def get_statement(self, statement_id: int) -> MemoryStatement | None: ...

    def get_entity(self, entity_id: int) -> MemoryEntity | None: ...

    def find_entity_by_name(self, name: str) -> MemoryEntity | None: ...

    def find_entity_by_alias(self, alias: str) -> MemoryEntity | None: ...

    def statements_by_ids(self, statement_ids: list[int]) -> list[MemoryStatement]: ...

    def episode_links_by_ids(self, link_ids: list[int]) -> list[MemoryEpisodeLink]: ...

    def add_statement(self, spec: FactWrite, now: datetime) -> MemoryStatement: ...

    def correct_statement(self, statement_id: int, spec: FactWrite, now: datetime) -> StatementMutation: ...

    def archive_statement(self, statement_id: int, now: datetime) -> MemoryStatement: ...

    def update_entity(
        self, entity_id: int,
        name: str | None = None, aliases: list[str] | None = None,
        entity_type: str | None = None,
    ) -> MemoryEntity: ...

    # ---------------- 实体身份纠错（L2）----------------

    def merge_entity(self, source_id: int, target_id: int) -> dict:
        """错分离合并：source 全量并入 target（含历史行），source 删除。"""

    def split_entity(self, source_id: int, spec: EntitySplitSpec) -> dict:
        """错合并拆分：所选内容迁往新实体，双方互写拆分禁令；
        source 拆空时自动删除。"""

    def delete_orphan_entity(self, entity_id: int) -> MemoryEntity:
        """删除孤立实体（无任何陈述引用与事件参与），返回被删档案。"""

    # ---------------- 事件编辑（L3）----------------

    def update_episode(
        self, episode_id: int,
        summary: str | None = None, scene: str | None = None,
        occurred_at: datetime | None = None,
    ) -> MemoryEpisode:
        """直改事件档案（scene 传空串表示清除）；向量随 summary 重组。"""

    def delete_episode(self, episode_id: int) -> MemoryEpisode:
        """物理删除事件及其参与边（不可恢复），返回被删档案。"""

    def update_episode_link(
        self, link_id: int, entity_id: int | None = None, role: str | None = None,
    ) -> MemoryEpisodeLink:
        """参与改挂：换实体/改角色（role 传空串表示清除）；撞唯一约束 → 3007。"""

    # ---------------- 危险操作区（L4）----------------

    def purge_preview(
        self, *,
        from_dt: datetime | None = None, to_dt: datetime | None = None,
        thread_id: UUID | None = None,
    ) -> dict:
        """清除影响面计数：statements/activeStatements/episodes/entities。"""

    def purge_memory(
        self, *,
        from_dt: datetime | None = None, to_dt: datetime | None = None,
        thread_id: UUID | None = None,
    ) -> dict:
        """范围清除：在效事实批量归档（软删保回放）、事件物理删除、
        范围内创建的孤立实体清理（仅时间范围语义）。"""

    def export_memory(self) -> dict:
        """四表全量导出（JSON 安全结构，含非 ACTIVE 历史行）。"""

    def reset_all_memory(self) -> dict[str, int]:
        """整体重置：清空记忆四表并 drop 记忆向量 collection。"""


# ---------------- 记忆向量索引端口 ----------------
# 实现住 ``app/adapters/vector/memory_index.py``（Weaviate 机制）；组件与
# 领域服务只依赖本协议面。契约数据类型（KIND_* / VectorEntry /
# MemoryVectorHit / vector_object_id）同住此处——id 映射与命中形状是
# 消费方可见的契约，collection/schema 机制细节归实现侧。

KIND_ENTITY = "entity"
KIND_STATEMENT = "statement"
KIND_EPISODE = "episode"


@dataclass(frozen=True)
class VectorEntry:
    """一次嵌入写入的最小单元。"""

    kind: str
    ref_id: int
    content: str
    thread_id: str | None = None
    occurred_at: datetime | None = None


@dataclass(frozen=True)
class MemoryVectorHit:
    """单条命中：hybrid fusion 分（0-1），越高越相关。"""

    kind: str
    ref_id: int
    score: float


def vector_object_id(kind: str, ref_id: int) -> str:
    """{kind}_{ref_id} 的确定性合法 UUID：写入覆盖与删除共用此映射。"""
    from uuid import NAMESPACE_URL, uuid5

    return str(uuid5(NAMESPACE_URL, f"memory/{kind}/{ref_id}"))


class MemoryVectorIndexPort(Protocol):
    """记忆域向量索引协议：三类对象混居单 collection 的写入与检索（每用户一库）。

    注入实例无作用域（仅维护 CLI 兜底用），用户侧行程经 ``for_user(uid)``
    取作用域视图。向量端任何失败都只告警不上抛：SQL 是事实源，索引可随时
    全量重建。
    """

    def for_user(self, user_id: UUID) -> "MemoryVectorIndexPort":
        """返回绑定用户的轻量作用域视图：读写全部落在该用户的 collection。"""

    def upsert_many(self, entries: list[VectorEntry]) -> None:
        """批量写入/覆盖；同 (kind, ref_id) 再次写入即更新语义。"""

    def delete(self, items: list[tuple[str, int]]) -> None: ...

    def search(
        self,
        query: str,
        *,
        kinds: tuple[str, ...] = (KIND_STATEMENT, KIND_EPISODE),
        top_k: int = 8,
        alpha: float = 0.7,
    ) -> list[MemoryVectorHit]: ...

    def text_cosine(self, query: str, contents: list[str]) -> list[float]: ...

    def rebuild(self, entries: list[VectorEntry]) -> None: ...


# ---------------- 记忆图谱仓储端口 ----------------
# 实现住 ``app/adapters/persistence/memory_graph_repository/`` 包（SQLite 承载，
# 注入共享 Engine）。组件（召回/巩固/用户节点/消歧/编辑面）与领域侧只依赖
# 本协议面；存储与召回策略按 ``@injectable(as_type=...)`` 换绑切换，消费方无感。

class MemoryGraphRepositoryPort(ABC):
    """记忆图谱数据访问端口（v2 双层图谱四表）。

    覆盖语义约定：supersede 不删除——历史陈述永久保留供时点回放；实体身份
    纠错（合并/拆分）例外地**追溯改写归属**（含历史行，防悬挂 FK），被改挂
    行的 summary 按规范句式重组（见 domain/memory/vocab.fact_summary）。

    ``for_user`` 返回绑定指定用户的作用域视图（实现同接口）。作用域语义：
    视图内所有读操作仅命中该用户的行（id 直取命中他人行一律视为不存在），
    写操作把归属钉到该用户。注入的实例是**无作用域**（全局算子视角），仅供
    维护链路（application 维护用例 / for_user 工厂）使用——用户侧行程
    （run 收尾/召回/管理端点链）必须先 for_user，这是记忆用户级隔离的强制点。
    """

    def for_user(self, user_id: UUID) -> "MemoryGraphRepositoryPort":
        """返回绑定指定用户的作用域视图（实现同接口）。"""
        raise NotImplementedError

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
        """全量实体（管理侧图快照/维护用例用），按主键升序。"""

    @abstractmethod
    def list_episodes(self, limit: int | None = None) -> list[MemoryEpisode]:
        """全量事件梗概（管理侧图快照/维护用例用），按 occurred_at 倒序（新者在前）。"""

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
        """全量陈述含非 ACTIVE（导出/维护用例用）。"""

    @abstractmethod
    def reset_all(self) -> dict[str, int]:
        """整体重置：单事务清空记忆四表，返回各类删除计数。"""


class MemoryMaintenancePort(Protocol):
    """记忆维护面专用原语（全局算子视角，无用户作用域）。

    维护链路（application 的 repair / rebuild-index 编排）在
    ``MemoryGraphRepositoryPort`` 之外的少量通用读写：按谓词+客体定位陈述、
    按实体（可带角色）列参与边、陈述行全量保存。定位条件与修复顺序的
    事故级编排归 application 用例，本端口只提供与具体事故无关的通用原语。
    注入实例即全局算子视角——维护面天然跨用户操作（向量按行归属分组回写
    各用户 collection）。
    """

    def list_statements_by_object_predicate(
        self, predicate: str, object_entity_id: int,
    ) -> list[MemoryStatement]:
        """给定谓词且客体为指定实体的全部陈述（任意状态；错挂定位用）。"""

    def list_episode_links_by_entity(
        self, entity_id: int, role: str | None = None,
    ) -> list[MemoryEpisodeLink]:
        """实体（可再按角色过滤）的全部参与边（错挂定位用）。"""

    def save_statement(self, statement: MemoryStatement) -> MemoryStatement:
        """陈述按行全量保存（编辑语义：字段直改，区别于 replace 的取代写）。"""
