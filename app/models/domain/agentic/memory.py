from typing import Union

from uuid import UUID
from sqlmodel import SQLModel, Field

from app.models.domain.mixin import TimeFieldMixin


class AgenticMemory(TimeFieldMixin, SQLModel, table=True):
    """长期记忆条目：轮次结束后由 LLM 从对话中抽取的跨轮有价值事实/偏好。"""

    __tablename__ = "agentic_memory" # type: ignore

    id: int | None = Field(
        title="主键id",
        description="主键id",
        default=None,
        primary_key=True,
    )
    content: str = Field(
        title="记忆内容",
        description="抽取出的记忆条目，eg: 用户叫小明，在上海做后端开发",
    )

    # 溯源字段：仅用于追溯记忆来源；召回为全局全量，不按会话隔离
    source_thread_id: Union[UUID, None] = Field(
        default=None,
        index=True,
        title="来源会话id",
        description="记忆来源会话id",
    )
    source_turn_id: Union[UUID, None] = Field(
        default=None,
        title="来源会话轮次id",
        description="记忆来源会话轮次id",
    )
