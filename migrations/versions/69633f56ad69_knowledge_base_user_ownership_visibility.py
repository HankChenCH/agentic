"""knowledge base user ownership + visibility

Revision ID: 69633f56ad69
Revises: 7a8e2ceb0c2e
Create Date: 2026-08-30 16:12:51.740513

知识库归属与可见性迁移：

1. ``knowledge_base`` 加 ``user_id``（FK→users.id，索引）——存量行经
   server_default 回填默认用户后移除默认值（与 user module 迁移同款范式：
   schema 保持严格，漏传归属的代码缺陷不被静默兜底）。
2. 加 ``is_public``（NOT NULL，server_default true）——存量库回填为公开，
   保持迁移前的全员可见行为；新库默认私有（代码侧 default=False）。
3. 名称唯一性从全局 ``UNIQUE(name)`` 替换为每用户 ``UNIQUE(user_id, name)``：
   不同属主可同名。SQLite 反射出的内联无名约束经 batch 命名约定计算名称后
   按名剔除（PostgreSQL 侧约束有自动名，按反射名直接删）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# 与 app.models.domain.user.DEFAULT_USER_ID 一致（迁移自包含，不反向 import）
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

# SQLite batch 重建表时给反射出的无名约束按约定补名（drop 的前提）；
# 键集与 SQLAlchemy 默认 naming_convention 对齐
_BATCH_NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# revision identifiers, used by Alembic.
revision: str = '69633f56ad69'
down_revision: Union[str, Sequence[str], None] = '7a8e2ceb0c2e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _legacy_name_unique_constraint() -> str | None:
    """定位存量全局 UNIQUE(name) 约束名：PG 返回自动名，SQLite 内联无名返回 None。"""
    for uc in sa.inspect(op.get_bind()).get_unique_constraints("knowledge_base"):
        if list(uc.get("column_names") or []) == ["name"]:
            return uc.get("name")
    return None


def upgrade() -> None:
    legacy_name = _legacy_name_unique_constraint()
    batch_kwargs: dict = {}
    if legacy_name is None:
        # SQLite：batch 重建表时先按约定给无名约束补名，再按计算名剔除
        batch_kwargs["naming_convention"] = _BATCH_NAMING
        legacy_name = _BATCH_NAMING["uq"] % {"table_name": "knowledge_base", "column_0_name": "name"}

    with op.batch_alter_table("knowledge_base", schema=None, **batch_kwargs) as batch_op:
        # NOT NULL + 常量 server_default：存量行一次性归属默认用户；
        # is_public 回填 true 保持迁移前全员可见的行为
        batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=False, server_default=_DEFAULT_USER_ID))
        batch_op.add_column(sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.create_index(batch_op.f("ix_knowledge_base_user_id"), ["user_id"], unique=False)
        batch_op.create_foreign_key("fk_knowledge_base_user_id", "users", ["user_id"], ["id"])
        batch_op.drop_constraint(legacy_name, type_="unique")
        batch_op.create_unique_constraint("uq_kb_user_name", ["user_id", "name"])

    # 回填完成后移除 server_default：归属/可见性必须由代码显式提供
    with op.batch_alter_table("knowledge_base", schema=None) as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Uuid(), server_default=None)
        batch_op.alter_column("is_public", existing_type=sa.Boolean(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("knowledge_base", schema=None) as batch_op:
        batch_op.drop_constraint("fk_knowledge_base_user_id", type_="foreignkey")
        batch_op.drop_constraint("uq_kb_user_name", type_="unique")
        batch_op.drop_index(batch_op.f("ix_knowledge_base_user_id"))
        batch_op.drop_column("is_public")
        batch_op.drop_column("user_id")
        # 恢复迁移前的全局名称唯一。batch 重建要求约束具名（初始 schema 的
        # 内联无名 UNIQUE 无法原样复刻），语义等价、仅约束名不同
        batch_op.create_unique_constraint("uq_knowledge_base_name", ["name"])
