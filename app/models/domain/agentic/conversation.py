from typing import Union, List, Dict
from enum import Enum

from uuid import UUID
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON, Enum as SAEnum

from app.models.domain.mixin import TimeFieldMixin


class AgenticTurnStatus(Enum):
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELED = 'canceled'

class AgenticMessageRole(Enum):
    SYSTEM = 'system'
    ASSISTANT = 'assistant'
    TOOL = 'tool'
    USER = 'user'
    pass


class AgenticMessageType(Enum):
    THOUGHT = 'thought'
    MESSAGE = 'message'
    TOOL_CALL = 'tool_call'
    TOOL_RESULT = 'tool_result'
    ERROR = 'error'
    CUSTOM = 'custom'


class AgenticConversation(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "agentic_conversation" # type: ignore

    id: int | None = Field(
        title="会话id",
        description="会话id",
        primary_key=True,
        default=None,
    )

    # eg: buildin:rag_agent
    agentic_id: str = Field(
        title="智能体标识",
        description="会话对应使用的智能体标识：<agent_type:agent_name>表示"
    )

    user_id: UUID = Field(
        index=True,
        title="所属用户id",
        description="会话归属用户（users.id）；归属过滤在仓储查询条件内强制",
        foreign_key="users.id",
    )

    thread_id: UUID = Field(
        index=True,
        title="会话id",
        description="会话id"
    )

    current_turn_id: Union[UUID, None] = Field(
        default=None,
        title="当前会话轮次id",
        description="当前会话轮次id"
    )

    conversation_title: str = Field(
        title="会话标题",
        description="对会话内容进行一句话摘要生成的标题"
    )

class AgenticConversationTurn(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "agentic_conversation_turn" # type: ignore

    id: int | None = Field(
        title="会话轮次id",
        description="会话轮次id",
        primary_key=True,
        default=None,
    )

    thread_id: UUID = Field(
        index=True, 
        title="会话id", 
        description="会话id"
    )

    run_id: str = Field(
        title="会话轮次id",
        description="会话轮次id（前端）"
    )

    turn_id: UUID = Field(
        title="会话轮次id",
        description="会话轮次id",
        unique=True,
        index=True,
    )

    turn_num: int = Field(
        title="会话轮次序号",
        description="会话轮次序号，从1开始"
    )

    status: AgenticTurnStatus = Field(
        title="会话轮次状态",
        description="会话轮次状态，eg: running, completed, failed, canceled",
        default=AgenticTurnStatus.RUNNING
    )

    token_usage: Dict = Field(
        sa_column=Column(JSON, nullable=True),
        title="会话轮次token使用量",
        description="会话轮次token使用量, eg: {'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300}",
        default_factory=dict
    )

    messages: List["AgenticConversationMessage"] = Relationship(back_populates="turn", sa_relationship_kwargs={"lazy": "selectin"})

    def model_dump(self, **kw):
        data = super().model_dump(**kw)
        data["messages"] = [m.model_dump(**kw) for m in self.messages]
        return data


class AgenticConversationMessage(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "agentic_conversation_message" # type: ignore

    id: int | None = Field(
        title="主键id",
        description="主键id",
        default=None,
        primary_key=True,
    )
    thread_id: UUID = Field(index=True, title="会话id", description="会话id")
    turn_id: UUID = Field(index=True, title="会话轮次id", description="会话轮次id", foreign_key="agentic_conversation_turn.turn_id")
    turn: "AgenticConversationTurn" = Relationship(back_populates="messages", sa_relationship_kwargs={"lazy": "selectin"})

    message_id: UUID = Field(
        unique=True,
        index=True,  # parent_message_id 自引用外键的指向列，PostgreSQL 要求唯一索引
        title="消息id",
        description="消息id",
    )
    parent_message_id: Union[UUID, None] = Field(
        default=None,
        title="父消息id",
        foreign_key="agentic_conversation_message.message_id",
    )
    sequence_num: int = Field(title="消息序号")

    role: AgenticMessageRole = Field(
        sa_column=Column(
            SAEnum(AgenticMessageRole, name="agentic_message_role"),
            nullable=False,
        ),
        title="消息角色",
        description="消息角色",
    )
    message_type: AgenticMessageType = Field(
        sa_column=Column(
            SAEnum(AgenticMessageType, name="agentic_message_type"),
            nullable=False,
        ),
        title="消息类型",
        description="消息类型",
    )
    content: List = Field(
        sa_column=Column(JSON, nullable=False),
        title="消息内容",
        description="消息内容，为了支持多模态消息以内容数组形式存储",
    )

    token_usage: Dict = Field(
        sa_column=Column(JSON, nullable=False),
        title="消息token使用量",
    )

    latency_ms: int = Field(title="消息延迟")
