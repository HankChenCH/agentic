"""用量流水：一次 LLM 调用一行的计量事实表。

与会话域的 ``token_usage`` JSON 列（消息/轮次级，服务会话详情展示）解耦：
本表是统计专属数据源，行粒度为单次模型调用（ReAct 多步一轮 = 多行 chat 行），
带 scene/model 维度，聚合口径在领域服务 ``UsageService``（SQL SUM/GROUP BY，
不碰 JSON 列）。

场景（scene）以小写字符串存储（不用 SAEnum：统计侧要频繁按值过滤，且规避
PG 枚举类型的迁移负担与「DB 存枚举名」的大小写口径坑）；合法性由领域服务
在写入时强制（业务不变量只在领域服务强制，见 models/domain 既定取舍）。
"""

from uuid import UUID

from sqlmodel import SQLModel, Field
from sqlalchemy import Column, Index, String

from app.models.domain.mixin import TimeFieldMixin


class UsageScene:
    """用量场景常量（DB 存储值 = 字符串小写）。"""

    CHAT = "chat"      # 对话主链路（agent ReAct 循环，每次模型调用一行）
    TITLE = "title"    # 会话标题生成（TurnFinalizer 收尾）
    MEMORY = "memory"  # 记忆巩固（抽取/实体消歧裁决/陈述裁决）

    ALL = (CHAT, TITLE, MEMORY)


class UsageRecord(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "usage_record"  # type: ignore

    id: int | None = Field(
        title="主键id",
        description="主键id",
        default=None,
        primary_key=True,
    )

    user_id: UUID = Field(
        title="所属用户id",
        description="用量归属用户（users.id）；个人用量视角的归属过滤在仓储查询条件内强制",
        foreign_key="users.id",
    )

    scene: str = Field(
        sa_column=Column(String(16), nullable=False),
        title="用量场景",
        description=f"产生用量的业务场景：{' / '.join(UsageScene.ALL)}（见 UsageScene）",
    )

    model: str = Field(
        sa_column=Column(String(128), nullable=False),
        title="模型名",
        description="产生本次调用的模型名（取模型实例的 model_name/model 属性）",
    )

    input_tokens: int = Field(
        title="输入token数",
        description="本次调用的输入 token 数（usage_metadata 的 input_tokens，即 prompt_tokens）",
        default=0,
    )

    output_tokens: int = Field(
        title="输出token数",
        description="本次调用的输出 token 数（usage_metadata 的 output_tokens，即 completion_tokens）",
        default=0,
    )

    total_tokens: int = Field(
        title="总token数",
        description="本次调用的总 token 数（缺失时由输入+输出补齐）",
        default=0,
    )

    thread_id: UUID | None = Field(
        default=None,
        title="会话id",
        description="产生调用的会话（标题场景为首轮所在会话）；无会话上下文的调用为 NULL",
    )

    turn_id: UUID | None = Field(
        default=None,
        title="轮次id",
        description="产生调用的轮次（chat 行 = 该次模型调用所属轮次，ReAct 多步共用 turn_id）；"
                    "标题/记忆行携带被加工的轮次",
    )

    agentic_id: str | None = Field(
        default=None,
        title="智能体标识",
        description="会话绑定的智能体标识（chat 场景填写），按智能体维度统计免 join",
    )

    # 统计主查询口径：按用户圈定 + 时间范围过滤（个人用量页的时间序列/汇总）
    __table_args__ = (
        Index("ix_usage_record_user_id_created_at", "user_id", "created_at"),
    )
