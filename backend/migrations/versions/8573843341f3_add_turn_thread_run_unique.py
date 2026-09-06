"""add turn thread run unique

agentic_conversation_turn 加 (thread_id, run_id) 复合唯一约束（uq_turn_thread_run，
与模型 __table_args__ 同名声明）：同一会话内重复 run_id 的提交（传输层重试/
双击重放）在库层硬拒绝，仓储把约束冲突翻译为 RepositoryConflictError、领域层
转 DuplicateRunError（1014/409），不产生重复轮次。

注意：若存量数据已有同 (thread_id, run_id) 的重复轮次（约束引入前的重复提交），
本迁移会失败——需先人工去重（保留最新一条：删重复轮次的 messages 行与 turn 行，
并修正 current_turn_id 的悬挂引用）再升级；不自动删数据。

batch_alter_table 两方言通吃（SQLite 走表重建，先例 a4f2c91d7b36 对本表的
batch 改造）。

Revision ID: 8573843341f3
Revises: c3f5a9d21b47
Create Date: 2026-09-06 08:17:21.967941

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8573843341f3'
down_revision: Union[str, Sequence[str], None] = 'c3f5a9d21b47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('agentic_conversation_turn', schema=None) as batch_op:
        batch_op.create_unique_constraint(
            'uq_turn_thread_run', ['thread_id', 'run_id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('agentic_conversation_turn', schema=None) as batch_op:
        # batch API 无 drop_unique_constraint，统一走 drop_constraint(type_='unique')
        batch_op.drop_constraint('uq_turn_thread_run', type_='unique')
