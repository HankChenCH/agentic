"""记忆域领域端口（依赖倒置）：协议住领域层，实现由外层回填。

图快照（graph_snapshot.py）需要记忆库的只读面，编辑能力（admin_service.py）
需要记忆库的纠错写面，但领域层不得 import components——协议住
领域层、实现住在
``app/components/memory/``（``graph_reader.py`` 只读 / ``editor.py`` 编辑），
经 wireup ``@injectable(as_type=...)`` 按本协议类型注入。依赖箭头
components ──► domain 合法。
"""

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
