"""add knowledge document reap_count

Revision ID: 387b148b4e88
Revises: 69633f56ad69
Create Date: 2026-09-01 20:18:49.872660

看门狗（卡死对账）重投计数列。autogenerate 曾夹带本地开发库与迁移历史的
无关漂移（旧 agentic_memory 表、会话消息 FK/索引），此处手工修剪为仅此一列。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '387b148b4e88'
down_revision: Union[str, Sequence[str], None] = '69633f56ad69'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 存量行回填 0（新链路引入前不存在看门狗重投概念）
    with op.batch_alter_table('knowledge_base_document', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('reap_count', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade() -> None:
    with op.batch_alter_table('knowledge_base_document', schema=None) as batch_op:
        batch_op.drop_column('reap_count')
