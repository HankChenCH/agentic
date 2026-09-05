from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field

from app.models.domain.mixin import TimeFieldMixin


class HumanAgentStatus(Enum):
    """人工客服坐席的接待状态（管理面维护，客服卡片按此展示可接待性）。"""

    ONLINE = 'online'
    BUSY = 'busy'
    OFFLINE = 'offline'


class HumanAgent(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "human_agent"  # type: ignore
    # 坐席是运营统一维护的全局资源（区别于 knowledge 的用户属主模型）：
    # 无 user_id 字段，客服会话面向全体用户展示同一份坐席列表
    __table_args__ = (
        UniqueConstraint("name", name="uq_human_agent_name"),
    )

    id: UUID | None = Field(
        title="主键id",
        description="主键id",
        default_factory=uuid4,
        primary_key=True,
    )

    name: str = Field(
        title="坐席姓名",
        description="人工客服坐席姓名（全局唯一）",
        min_length=1,
        max_length=50,
    )

    title: str = Field(
        title="职务",
        description="坐席职务，如「高级客服」「售后专员」",
        min_length=1,
        max_length=50,
    )

    specialty: str = Field(
        title="擅长问题",
        description="坐席擅长的问题类型描述，供智能体匹配转接对象",
        max_length=200,
        default="",
    )

    intro: str = Field(
        title="简介",
        description="坐席简介（卡片可选展示）",
        max_length=500,
        default="",
    )

    status: HumanAgentStatus = Field(
        title="接待状态",
        description="接待状态: online, busy, offline",
        default=HumanAgentStatus.OFFLINE,
        index=True,
    )
