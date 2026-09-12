from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime
from sqlmodel import SQLModel, Field

from app.models.domain.mixin import TimeFieldMixin

# status 状态机的合法取值（String 列而非 PG 原生枚举——CI 的迁移降级圆环
# 会抓枚举类型残留）。状态迁移只在领域服务与仓储的条件 UPDATE 内发生。
STATUS_ACTIVE = "active"
STATUS_ROTATED = "rotated"
STATUS_REVOKED = "revoked"


class RefreshToken(TimeFieldMixin, SQLModel, table=True):
    """refresh token 登记表：jti → 状态机，旋转与复用检测的服务端事实源。

    jti 主键 = JWT 的 ``jti`` claim（uuid4 hex，签发时生成并随
    TokenBundle 透出）。一次登录一个族（family_id = 族根 refresh 的
    jti），旋转继承族；复用检测按族连坐吊销。expires_at 与 JWT exp
    同源，只服务 purge_expired 的清理口径——令牌过期本体由 JWT 兜住。
    """

    __tablename__ = "refresh_token"  # type: ignore

    jti: str = Field(
        title="令牌一次性身份",
        description="JWT jti claim（uuid4 hex），登记表主键",
        max_length=32,
        primary_key=True,
    )

    user_id: UUID = Field(
        index=True,
        title="属主用户",
        description="签发该令牌的用户（users.id）",
        foreign_key="users.id",
    )

    family_id: str = Field(
        index=True,
        title="令牌族",
        description="一次登录一个族（族根 refresh 的 jti），旋转继承；复用检测按族连坐",
        max_length=32,
    )

    status: str = Field(
        title="状态",
        description="active 可用 / rotated 已被旋转作废 / revoked 已吊销（含族连坐）",
        max_length=16,
        default=STATUS_ACTIVE,
    )

    expires_at: datetime = Field(
        index=True,
        title="过期时刻",
        description="与 JWT exp 同源；purge_expired 按它清理失效行",
        sa_type=DateTime(timezone=True),
    )

    rotated_at: datetime | None = Field(
        title="旋转时刻",
        description="active→rotated 的认领时刻；宽限期判定在 SQL 侧按它比较",
        default=None,
        sa_type=DateTime(timezone=True),
    )
