from typing import Union, List, Dict
from enum import Enum

from uuid import UUID
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON, Enum as SAEnum, UniqueConstraint

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


class ContentPartType(str, Enum):
    """消息 content 数组部件的 type 词汇表——存储面 JSON 的序列化键，也是
    四条转换边（ag2ui/ag2store/store2ui/store2ag）分派部件的共同键（总览见
    app/application/translator/matrix.py）。str Enum：成员即存储字符串，可
    直接与裸字面量比较。行级类型见 AgenticMessageType——THOUGHT/MESSAGE 行
    都持有 TEXT part，用户行可持 IMAGE part，二者是行/部件两层词汇。"""
    TEXT = 'text'
    TOOL_CALL = 'tool_call'
    TOOL_RESULT = 'tool_result'
    CUSTOM = 'custom'
    IMAGE = 'image'


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
        title="活跃叶子轮次id",
        description="活跃路径的叶子轮次（分支树的 current_node，对齐 ChatGPT 语义）："
                    "仅在轮次 COMPLETED 时推进；重试失败保留原叶子，被重试轮次经"
                    " parent 链仍可达。沿叶子回溯 parent 链即 LLM 回放与历史展示的活跃路径"
    )

    conversation_title: str = Field(
        title="会话标题",
        description="对会话内容进行一句话摘要生成的标题"
    )

class AgenticConversationTurn(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "agentic_conversation_turn" # type: ignore

    # 同一会话内 run_id 唯一：重复提交（传输层重试/双击重放）在库层硬拒绝，
    # 仓储把约束冲突翻译为 RepositoryConflictError、领域层转 DuplicateRunError
    __table_args__ = (
        UniqueConstraint("thread_id", "run_id", name="uq_turn_thread_run"),
    )

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
        title="运行id",
        description="前端本次运行标识（每次 run 新生成，与轮次非一一对应语义由 turn_id 承担）"
    )

    turn_id: UUID = Field(
        title="会话轮次id",
        description="会话轮次id",
        unique=True,
        index=True,
    )

    turn_num: int = Field(
        title="会话轮次序号",
        description="会话轮次序号（线程内插入序，从0开始）：只反映创建先后，"
                    "活跃/展示顺序由 parent_turn_id 链派生"
    )

    parent_turn_id: Union[UUID, None] = Field(
        default=None,
        title="分支基点轮次id",
        description="自引用 FK → 本表 turn_id。兄弟语义：与其共享同一 parent 的轮次互为"
                    "同一问答的重试/编辑变体（对齐 ChatGPT 节点树 / LibreChat parentMessageId）；"
                    "普通续聊 parent = 会话活跃叶子，NULL = 会话起点",
        foreign_key="agentic_conversation_turn.turn_id",
    )

    attempt_no: int = Field(
        title="尝试序号",
        description="同一问答（兄弟轮次间）第几次尝试，从1开始；重试 = 兄弟间 max+1",
        default=1,
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
