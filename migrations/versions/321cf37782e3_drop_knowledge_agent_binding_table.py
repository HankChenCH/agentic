"""drop knowledge agent binding table

agent↔KB 绑定功能移除（知识库权属已改为用户体系，检索按归属可见性圈定，
不消费绑定），删除 knowledge_agent_binding 表。

Revision ID: 321cf37782e3
Revises: 387b148b4e88
Create Date: 2026-09-02 18:57:17.439670

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '321cf37782e3'
down_revision: Union[str, Sequence[str], None] = '387b148b4e88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('knowledge_agent_binding', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_agent_binding_kb_id'))
        batch_op.drop_index(batch_op.f('ix_knowledge_agent_binding_agent_id'))
    op.drop_table('knowledge_agent_binding')


def downgrade() -> None:
    op.create_table('knowledge_agent_binding',
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('agent_id', sa.VARCHAR(length=64), nullable=False),
        sa.Column('kb_id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['kb_id'], ['knowledge_base.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_id', 'kb_id', name='uq_agent_kb_binding')
    )
    with op.batch_alter_table('knowledge_agent_binding', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_agent_binding_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_agent_binding_kb_id'), ['kb_id'], unique=False)
