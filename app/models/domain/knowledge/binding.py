from uuid import UUID, uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field

from app.models.domain.mixin import TimeFieldMixin


class KnowledgeAgentBinding(TimeFieldMixin, SQLModel, table=True):
    """agent ↔ 知识库绑定：一个 agent 可挂多个知识库（检索可用白名单）。

    ``agent_id`` 取 agent 注册表标识（agentic_id，如 ``builtin:demo``）。
    绑定只约束「哪些库可被该 agent 的检索工具访问」，检索时再按知识库/
    文档状态收敛到实际可召回的集合。知识库删除时绑定行由
    KnowledgeBaseService 三段式显式清理（跨域不做 ORM Relationship 级联）。
    """

    __tablename__ = "knowledge_agent_binding" # type: ignore
    __table_args__ = (
        UniqueConstraint("agent_id", "kb_id", name="uq_agent_kb_binding"),
    )

    id: UUID | None = Field(
        title="主键id",
        description="主键id",
        default_factory=uuid4,
        primary_key=True,
    )

    agent_id: str = Field(
        title="agent标识",
        description="agent 注册表标识（agentic_id），eg: builtin:demo",
        max_length=64,
        index=True,
    )

    kb_id: UUID = Field(
        title="知识库id",
        description="知识库id",
        foreign_key="knowledge_base.id",
        index=True,
    )
