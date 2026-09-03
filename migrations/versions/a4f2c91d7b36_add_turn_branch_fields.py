"""add turn branch fields

轮次级分支建模：agentic_conversation_turn 新增 parent_turn_id（自引用分支基点，
兄弟语义 = 同一问答的重试/编辑变体）与 attempt_no（兄弟间尝试序号）。
存量数据 parent 全 NULL、attempt 全 1，即退化为与旧模型等价的线性链。

自引用外键按方言分叉（口径同 initial 迁移的 parent_message_id）：PostgreSQL 在
ALTER 场景由 create_foreign_key 补挂；SQLite 不支持 ALTER 挂约束（测试环境走
SQLModel.metadata.create_all 内联声明，不经本迁移）。

Revision ID: a4f2c91d7b36
Revises: 321cf37782e3
Create Date: 2026-09-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4f2c91d7b36'
down_revision: Union[str, Sequence[str], None] = '321cf37782e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('agentic_conversation_turn', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parent_turn_id', sa.Uuid(), nullable=True))
        # server_default 兜住存量行；模型侧 default=1 覆盖新插入
        batch_op.add_column(sa.Column('attempt_no', sa.Integer(), nullable=False, server_default='1'))

    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            'fk_conversation_turn_parent_turn_id',
            'agentic_conversation_turn', 'agentic_conversation_turn',
            ['parent_turn_id'], ['turn_id'],
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            'fk_conversation_turn_parent_turn_id',
            'agentic_conversation_turn',
            type_='foreignkey',
        )
    with op.batch_alter_table('agentic_conversation_turn', schema=None) as batch_op:
        batch_op.drop_column('attempt_no')
        batch_op.drop_column('parent_turn_id')
