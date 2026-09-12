"""add human_agent

人工客服坐席表：客服智能体「转人工」场景的数据源（坐席姓名/职务/擅长/
状态），由 app/commands/human_agent.py 的管理命令维护，components/human_agent
的数据工具查库。

全局资源（无 user_id 属主，区别于 knowledge 的用户属主模型）：坐席由运营
统一维护，客服会话面向全体用户展示同一份坐席列表；姓名全局唯一。
仅建新表，不触碰任何既有对象。

Revision ID: c3f5a9d21b47
Revises: 006893821f1e
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'c3f5a9d21b47'
down_revision: Union[str, Sequence[str], None] = '006893821f1e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('human_agent',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(length=50), nullable=False),
    sa.Column('specialty', sqlmodel.sql.sqltypes.AutoString(length=200), nullable=False),
    sa.Column('intro', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
    sa.Column('status', sa.Enum('ONLINE', 'BUSY', 'OFFLINE', name='humanagentstatus'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', name='uq_human_agent_name')
    )
    op.create_index('ix_human_agent_status', 'human_agent', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_human_agent_status', table_name='human_agent')
    op.drop_table('human_agent')
    # PG 原生枚举类型不随 op.drop_table 清理（SQLite 走 VARCHAR+CHECK，无类型可摘），
    # 不显式 DROP TYPE 则 downgrade base 后再 upgrade 撞 CREATE TYPE already exists。
    if op.get_bind().dialect.name != "sqlite":
        op.execute("DROP TYPE IF EXISTS humanagentstatus")
