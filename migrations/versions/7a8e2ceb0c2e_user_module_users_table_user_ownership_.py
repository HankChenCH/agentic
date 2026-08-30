"""user module: users table + user ownership on conversations and memory

Revision ID: 7a8e2ceb0c2e
Revises: b947c08fec26
Create Date: 2026-08-30 08:39:53.536140

用户模块迁移：

1. 建 ``users`` 表并插入固定 UUID 的默认用户（密码哈希空串=不可登录，
   承接存量数据归属；常量见 ``app.models.domain.user.DEFAULT_USER_ID``）。
2. ``agentic_conversation`` 加 ``user_id``（FK→users.id），存量行经
   server_default 回填默认用户后移除默认值（保持 schema 严格，漏传归属
   的代码缺陷不被静默兜底）。
3. 记忆三表（entity/statement/episode）加 ``user_id``（索引，无 FK——
   表归 components 仓储管理，批量重置/纠错路径保持松耦合）。

autogenerate 噪音已剔除：历史遗留表 agentic_memory（v1 记忆表，保留不动）、
message/turn 表的既有 FK/索引漂移（与本次改动无关，不在此修）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

# 与 app.models.domain.user.DEFAULT_USER_ID 一致（迁移自包含，不反向 import）
_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

# revision identifiers, used by Alembic.
revision: str = '7a8e2ceb0c2e'
down_revision: Union[str, Sequence[str], None] = 'b947c08fec26'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('username', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.Column('password_hash', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('nickname', sqlmodel.sql.sqltypes.AutoString(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)

    # 默认用户：承接存量会话/记忆行的归属（密码哈希空串 → 验密恒失败）
    op.execute(
        "INSERT INTO users (created_at, updated_at, id, username, password_hash, nickname) "
        f"VALUES (CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{_DEFAULT_USER_ID}', 'default', '', '')"
    )

    with op.batch_alter_table('agentic_conversation', schema=None) as batch_op:
        # NOT NULL + 常量 server_default：存量行一次性归属默认用户，随后移除默认值
        batch_op.add_column(sa.Column('user_id', sa.Uuid(), nullable=False, server_default=_DEFAULT_USER_ID))
        batch_op.create_index(batch_op.f('ix_agentic_conversation_user_id'), ['user_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_agentic_conversation_user_id', 'users', ['user_id'], ['id'],
        )

    for table in ('memory_entity', 'memory_statement', 'memory_episode'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('user_id', sa.Uuid(), nullable=False, server_default=_DEFAULT_USER_ID))
            batch_op.create_index(batch_op.f(f'ix_{table}_user_id'), ['user_id'], unique=False)

    # 回填完成后移除 server_default：归属必须由代码显式提供，漏传即报错而非静默落默认用户
    with op.batch_alter_table('agentic_conversation', schema=None) as batch_op:
        batch_op.alter_column('user_id', existing_type=sa.Uuid(), server_default=None)
    for table in ('memory_entity', 'memory_statement', 'memory_episode'):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column('user_id', existing_type=sa.Uuid(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table('memory_episode', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memory_episode_user_id'))
        batch_op.drop_column('user_id')

    with op.batch_alter_table('memory_statement', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memory_statement_user_id'))
        batch_op.drop_column('user_id')

    with op.batch_alter_table('memory_entity', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memory_entity_user_id'))
        batch_op.drop_column('user_id')

    with op.batch_alter_table('agentic_conversation', schema=None) as batch_op:
        batch_op.drop_constraint('fk_agentic_conversation_user_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_agentic_conversation_user_id'))
        batch_op.drop_column('user_id')

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username'))

    op.drop_table('users')
