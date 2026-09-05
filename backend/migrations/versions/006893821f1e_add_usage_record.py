"""add usage_record

用量统计模块的计量流水表：一次 LLM 调用一行（scene = chat / title / memory），
带 user_id / model / token 三元组与可选的会话/轮次归属，聚合走 SQL
SUM/GROUP BY（与会话域 agentic_conversation_{turn,message}.token_usage
JSON 列解耦——那两列服务会话详情展示，本表服务统计）。

存量数据不回填：统计反映本表上线之后的用量。
仅建新表，不触碰任何既有对象（历史漂移不属于本迁移）。

Revision ID: 006893821f1e
Revises: a4f2c91d7b36
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = '006893821f1e'
down_revision: Union[str, Sequence[str], None] = 'a4f2c91d7b36'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('usage_record',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('scene', sa.String(length=16), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('total_tokens', sa.Integer(), nullable=False),
    sa.Column('thread_id', sa.Uuid(), nullable=True),
    sa.Column('turn_id', sa.Uuid(), nullable=True),
    sa.Column('agentic_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_usage_record_user_id_created_at', 'usage_record', ['user_id', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_usage_record_user_id_created_at', table_name='usage_record')
    op.drop_table('usage_record')
