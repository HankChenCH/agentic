"""add refresh token registry

refresh 令牌登记表（jti → 状态机）：旋转/复用检测的服务端事实源。
一次登录一个族（family_id = 族根 refresh 的 jti），旋转继承族；宽限期
外的复用判定为窃取、按族连坐吊销；登出按族吊销、改密按用户全吊销。
status 用 String 而非 PG 原生枚举——迁移降级圆环不留枚举类型残留。

Revision ID: 2c5a66097ec0
Revises: 8573843341f3
Create Date: 2026-09-12 10:30:34.324912

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '2c5a66097ec0'
down_revision: Union[str, Sequence[str], None] = '8573843341f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'refresh_token',
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('jti', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('family_id', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
        sa.Column('status', sqlmodel.sql.sqltypes.AutoString(length=16), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('jti'),
    )
    with op.batch_alter_table('refresh_token', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_refresh_token_expires_at'), ['expires_at'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_refresh_token_family_id'), ['family_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_refresh_token_user_id'), ['user_id'], unique=False
        )


def downgrade() -> None:
    op.drop_table('refresh_token')
