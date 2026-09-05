"""记忆系统 v2 领域表：《时-人-事-物》双层图谱（设计定稿 §4）。

- memory_entity        人/物·语义层节点（规范名必须纯专名，is_user 特殊用户节点）
- memory_statement     联系·三元组陈述（双时间轴；被取代置 SUPERSEDED 不删除，可时点回放）
- memory_episode       事·情节层（事件梗概；渲染期投影为 §E 事件节点）
- memory_episode_link  事 ↔ 人/物挂接

«时»的落点：valid_from/valid_to 表达世界中成立区间（valid time），
invalidated_at 是系统取代时刻（transaction time），time_remark 兜住
无法硬解析的模糊时间原文。

«作用域»：user_id 落在 entity/statement/episode 三表（episode_link 经
episode 继承归属）。v1 设计为单用户全局，引入用户模块后改为用户级隔离，
所有读写路径必须携带 user_id 过滤（见 adapters/persistence/memory_graph_repository.py）。
"""

from datetime import datetime
from typing import Union
from uuid import UUID

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.models.domain.memory.enums import EntityType, MemoryOrigin, StatementState
from app.models.domain.mixin import TimeFieldMixin

__all__ = [
    "MemoryEntity",
    "MemoryStatement",
    "MemoryEpisode",
    "MemoryEpisodeLink",
]


class MemoryEntity(TimeFieldMixin, SQLModel, table=True):
    """语义层实体节点。

    ``name`` 必须是纯专名——职衔等关系语义一律走陈述行，禁止烘入标识符
    （如"CEO_张伟"违规：职位变更会让历史切片自相矛盾，时序语义由陈述的
    valid 时间轴承载）。
    """

    __tablename__ = "memory_entity"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID = Field(index=True, description="归属用户（users.id）；记忆作用域为用户级")
    entity_type: str = Field(
        default=EntityType.OTHER.value, index=True, description="实体类型枚举值"
    )
    name: str = Field(index=True, description="规范名·纯专名")
    aliases: list = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description="别名集：消歧合并依据",
    )
    attributes: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON),
        description="未升列为谓词的杂项属性",
    )
    is_user: bool = Field(default=False, description="特殊“用户”节点；消歧合并永不参与被吞并")
    origin: str = Field(default=MemoryOrigin.EXTRACTED.value, description="来源：EXTRACTED/MANUAL")
    importance: float = Field(default=0.5, description="重要度 0-1，抽取时 LLM 赋值")
    access_count: int = Field(default=0, description="提取练习计数：每次被召回 +1")
    last_accessed_at: Union[datetime, None] = Field(default=None, description="最近一次被召回时刻")


class MemoryStatement(TimeFieldMixin, SQLModel, table=True):
    """联系·三元组陈述：(subject) -[predicate]-> (object)。

    客体二选一：object_entity_id（实体间联系）或 object_text（字面量）。
    ``summary`` 是整句自然语言表述，兼作向量嵌入源。evidence 存原话摘录——
    锚定证据入库而非运行时反查消息表（对话删除不断链、无二次过滤成本）。
    """

    __tablename__ = "memory_statement"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID = Field(index=True, description="归属用户（users.id）；冗余自 subject 实体以省召回路径 join")
    subject_id: int = Field(foreign_key="memory_entity.id", index=True)
    predicate: str = Field(description="受控词表谓词（基数规则见 domain/memory/vocab.py）")
    object_entity_id: Union[int, None] = Field(default=None, foreign_key="memory_entity.id")
    object_text: Union[str, None] = Field(default=None, description="客体为字面量时取值")
    summary: str = Field(description="自然语言整句，嵌入与展示源")
    evidence: list = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description='[{"quote": 原话, "source_turn_id": 轮次id}]',
    )
    state: str = Field(default=StatementState.ACTIVE.value, index=True)
    valid_from: Union[datetime, None] = Field(default=None, description="valid time 起点")
    valid_to: Union[datetime, None] = Field(default=None, description="valid time 终点（被取代/归档时刻的新值起点）")
    time_remark: Union[str, None] = Field(default=None, description="模糊时间原文，如“上个月开始”")
    invalidated_at: Union[datetime, None] = Field(default=None, description="transaction time：被系统取代的时刻")
    origin: str = Field(default=MemoryOrigin.EXTRACTED.value, description="来源；MANUAL 在裁决中恒 SKIP")
    confidence: float = Field(default=0.7, description="抽取置信度 0-1；低置信在渲染存疑备注中出现")
    importance: float = Field(default=0.5)
    access_count: int = Field(default=0)
    last_accessed_at: Union[datetime, None] = Field(default=None)
    source_thread_id: Union[UUID, None] = Field(default=None, index=True)
    source_turn_id: Union[UUID, None] = Field(default=None)


class MemoryEpisode(TimeFieldMixin, SQLModel, table=True):
    """事·情节层：一段可独立叙述的事件梗概。

    保留事件颗粒度是有意为之——“原文梗概有真实召回价值”，是深度回忆
    timeline 检索的对象；它经 link 与实体挂接，不在陈述图中直接作节点。
    """

    __tablename__ = "memory_episode"  # type: ignore

    id: int | None = Field(default=None, primary_key=True)
    user_id: UUID = Field(index=True, description="归属用户（users.id）；记忆作用域为用户级")
    thread_id: UUID = Field(index=True)
    turn_id: Union[UUID, None] = Field(default=None)
    occurred_at: datetime = Field(index=True, description="事件发生时间，默认轮次时间")
    scene: Union[str, None] = Field(default=None, description="场景标签（商业谈判/日常闲聊…），自由词")
    summary: str = Field(description="事件自然语言梗概，嵌入源")
    evidence: list = Field(
        default_factory=list,
        sa_column=Column(JSON),
        description='同 statement.evidence 结构，可选',
    )
    access_count: int = Field(default=0)
    last_accessed_at: Union[datetime, None] = Field(default=None)


class MemoryEpisodeLink(TimeFieldMixin, SQLModel, table=True):
    """事 ↔ 人/物挂接：episode 与实体参与者/涉及物的关联边。"""

    __tablename__ = "memory_episode_link"  # type: ignore
    __table_args__ = (
        UniqueConstraint("episode_id", "entity_id", "role", name="uq_memory_episode_link"),
    )

    id: int | None = Field(default=None, primary_key=True)
    episode_id: int = Field(foreign_key="memory_episode.id", index=True)
    entity_id: int = Field(foreign_key="memory_entity.id", index=True)
    role: Union[str, None] = Field(default=None, description="如 参与者/提及/涉及物")
