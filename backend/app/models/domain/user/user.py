from uuid import UUID, uuid4

from sqlmodel import SQLModel, Field

from app.models.domain.mixin import TimeFieldMixin

# 迁移回填存量数据归属的默认用户（密码哈希为空串，恒验密失败不可登录）；
# Alembic 迁移脚本中硬编码同一值（迁移自包含，不反向 import 业务常量）
DEFAULT_USER_ID = UUID("00000000-0000-0000-0000-000000000001")


class User(TimeFieldMixin, SQLModel, table=True):
    __tablename__ = "users" # type: ignore

    id: UUID | None = Field(
        title="用户id",
        description="用户id",
        default_factory=uuid4,
        primary_key=True,
    )

    username: str = Field(
        title="用户名",
        description="登录用户名，全局唯一；3-32 字符",
        min_length=3,
        max_length=32,
        unique=True,
        index=True,
    )

    # PBKDF2-SHA256：格式 "pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>"；
    # 迁移回填的默认用户为空串，恒验密失败（不可登录）
    password_hash: str = Field(
        title="密码哈希",
        description="PBKDF2-SHA256 密码哈希，空串表示不可登录（迁移回填的默认用户）",
    )

    nickname: str = Field(
        title="昵称",
        description="展示昵称，缺省用用户名",
        max_length=32,
        default="",
    )
