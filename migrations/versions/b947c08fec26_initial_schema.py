"""initial schema

「create_all 时代」的全量基线：11 张表（agentic 3 + knowledge 4 + memory 4）。
既有库接入：``python -m app.cmd.admin db stamp head``（只补 alembic_version，不动结构）。

Revision ID: b947c08fec26
Revises:
Create Date: 2026-08-29 16:42:19.811759

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = 'b947c08fec26'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('agentic_conversation',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('agentic_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('thread_id', sa.Uuid(), nullable=False),
    sa.Column('current_turn_id', sa.Uuid(), nullable=True),
    sa.Column('conversation_title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agentic_conversation', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agentic_conversation_thread_id'), ['thread_id'], unique=False)

    op.create_table('agentic_conversation_turn',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('thread_id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('turn_id', sa.Uuid(), nullable=False),
    sa.Column('turn_num', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('RUNNING', 'COMPLETED', 'FAILED', 'CANCELED', name='agenticturnstatus'), nullable=False),
    sa.Column('token_usage', sa.JSON(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('agentic_conversation_turn', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agentic_conversation_turn_thread_id'), ['thread_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agentic_conversation_turn_turn_id'), ['turn_id'], unique=True)

    op.create_table('knowledge_base',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=25), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
    sa.Column('embedding_model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'READY', 'ENABLED', 'DISABLED', 'DELETING', 'FAILED', name='knowledgestatus'), nullable=False),
    sa.Column('doc_num', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    with op.batch_alter_table('knowledge_base', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_base_status'), ['status'], unique=False)

    op.create_table('memory_entity',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('entity_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('aliases', sa.JSON(), nullable=True),
    sa.Column('attributes', sa.JSON(), nullable=True),
    sa.Column('is_user', sa.Boolean(), nullable=False),
    sa.Column('origin', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('importance', sa.Float(), nullable=False),
    sa.Column('access_count', sa.Integer(), nullable=False),
    sa.Column('last_accessed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('memory_entity', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memory_entity_entity_type'), ['entity_type'], unique=False)
        batch_op.create_index(batch_op.f('ix_memory_entity_name'), ['name'], unique=False)

    op.create_table('memory_episode',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('thread_id', sa.Uuid(), nullable=False),
    sa.Column('turn_id', sa.Uuid(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(), nullable=False),
    sa.Column('scene', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=True),
    sa.Column('access_count', sa.Integer(), nullable=False),
    sa.Column('last_accessed_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('memory_episode', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memory_episode_occurred_at'), ['occurred_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_memory_episode_thread_id'), ['thread_id'], unique=False)

    # 自引用外键（parent_message_id → message_id）按方言分叉：SQLite 不校验被
    # 引用列的唯一性、且不支持 ALTER 语句挂约束，随建表内联声明；PostgreSQL
    # 在 CREATE TABLE 时刻即校验被引用列唯一性，先建表 + message_id 唯一索引，
    # 再由下方 create_foreign_key 补挂。
    on_sqlite = op.get_bind().dialect.name == "sqlite"
    message_constraints = [
        sa.ForeignKeyConstraint(['turn_id'], ['agentic_conversation_turn.turn_id'], ),
        sa.PrimaryKeyConstraint('id')
    ]
    if on_sqlite:
        message_constraints.append(
            sa.ForeignKeyConstraint(['parent_message_id'], ['agentic_conversation_message.message_id'], ),
        )
    op.create_table('agentic_conversation_message',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('thread_id', sa.Uuid(), nullable=False),
    sa.Column('turn_id', sa.Uuid(), nullable=False),
    sa.Column('message_id', sa.Uuid(), nullable=False),
    sa.Column('parent_message_id', sa.Uuid(), nullable=True),
    sa.Column('sequence_num', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('SYSTEM', 'ASSISTANT', 'TOOL', 'USER', name='agentic_message_role'), nullable=False),
    sa.Column('message_type', sa.Enum('THOUGHT', 'MESSAGE', 'TOOL_CALL', 'TOOL_RESULT', 'ERROR', 'CUSTOM', name='agentic_message_type'), nullable=False),
    sa.Column('content', sa.JSON(), nullable=False),
    sa.Column('token_usage', sa.JSON(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    *message_constraints,
    )
    with op.batch_alter_table('agentic_conversation_message', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agentic_conversation_message_thread_id'), ['thread_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agentic_conversation_message_turn_id'), ['turn_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_agentic_conversation_message_message_id'), ['message_id'], unique=True)
    if not on_sqlite:
        op.create_foreign_key(
            'fk_conversation_message_parent_message_id',
            'agentic_conversation_message', 'agentic_conversation_message',
            ['parent_message_id'], ['message_id'],
        )

    op.create_table('knowledge_agent_binding',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('agent_id', sqlmodel.sql.sqltypes.AutoString(length=64), nullable=False),
    sa.Column('kb_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_base.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('agent_id', 'kb_id', name='uq_agent_kb_binding')
    )
    with op.batch_alter_table('knowledge_agent_binding', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_agent_binding_agent_id'), ['agent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_agent_binding_kb_id'), ['kb_id'], unique=False)

    op.create_table('knowledge_base_document',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('kb_id', sa.Uuid(), nullable=False),
    sa.Column('doc_path', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(length=250), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(length=500), nullable=False),
    sa.Column('mime_type', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('file_size', sa.Integer(), nullable=True),
    sa.Column('checksum', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'READY', 'ENABLED', 'DISABLED', 'DELETING', 'FAILED', name='knowledgestatus'), nullable=False),
    sa.Column('error_message', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('seg_num', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_base.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('knowledge_base_document', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_base_document_kb_id'), ['kb_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_base_document_status'), ['status'], unique=False)

    op.create_table('memory_episode_link',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('episode_id', sa.Integer(), nullable=False),
    sa.Column('entity_id', sa.Integer(), nullable=False),
    sa.Column('role', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.ForeignKeyConstraint(['entity_id'], ['memory_entity.id'], ),
    sa.ForeignKeyConstraint(['episode_id'], ['memory_episode.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('episode_id', 'entity_id', 'role', name='uq_memory_episode_link')
    )
    with op.batch_alter_table('memory_episode_link', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memory_episode_link_entity_id'), ['entity_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_memory_episode_link_episode_id'), ['episode_id'], unique=False)

    op.create_table('memory_statement',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('subject_id', sa.Integer(), nullable=False),
    sa.Column('predicate', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('object_entity_id', sa.Integer(), nullable=True),
    sa.Column('object_text', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('evidence', sa.JSON(), nullable=True),
    sa.Column('state', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('valid_from', sa.DateTime(), nullable=True),
    sa.Column('valid_to', sa.DateTime(), nullable=True),
    sa.Column('time_remark', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('invalidated_at', sa.DateTime(), nullable=True),
    sa.Column('origin', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.Column('importance', sa.Float(), nullable=False),
    sa.Column('access_count', sa.Integer(), nullable=False),
    sa.Column('last_accessed_at', sa.DateTime(), nullable=True),
    sa.Column('source_thread_id', sa.Uuid(), nullable=True),
    sa.Column('source_turn_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['object_entity_id'], ['memory_entity.id'], ),
    sa.ForeignKeyConstraint(['subject_id'], ['memory_entity.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('memory_statement', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_memory_statement_source_thread_id'), ['source_thread_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_memory_statement_state'), ['state'], unique=False)
        batch_op.create_index(batch_op.f('ix_memory_statement_subject_id'), ['subject_id'], unique=False)

    op.create_table('knowledge_base_document_segment',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('kb_id', sa.Uuid(), nullable=False),
    sa.Column('doc_id', sa.Uuid(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('content', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('meta', sa.JSON(), nullable=True),
    sa.Column('word_count', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'PROCESSING', 'READY', 'ENABLED', 'DISABLED', 'DELETING', 'FAILED', name='knowledgestatus'), nullable=False),
    sa.ForeignKeyConstraint(['doc_id'], ['knowledge_base_document.id'], ),
    sa.ForeignKeyConstraint(['kb_id'], ['knowledge_base.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('doc_id', 'position', name='uq_segment_doc_position')
    )
    with op.batch_alter_table('knowledge_base_document_segment', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_knowledge_base_document_segment_doc_id'), ['doc_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_base_document_segment_kb_id'), ['kb_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_knowledge_base_document_segment_status'), ['status'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('knowledge_base_document_segment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_base_document_segment_status'))
        batch_op.drop_index(batch_op.f('ix_knowledge_base_document_segment_kb_id'))
        batch_op.drop_index(batch_op.f('ix_knowledge_base_document_segment_doc_id'))

    op.drop_table('knowledge_base_document_segment')
    with op.batch_alter_table('memory_statement', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memory_statement_subject_id'))
        batch_op.drop_index(batch_op.f('ix_memory_statement_state'))
        batch_op.drop_index(batch_op.f('ix_memory_statement_source_thread_id'))

    op.drop_table('memory_statement')
    with op.batch_alter_table('memory_episode_link', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memory_episode_link_episode_id'))
        batch_op.drop_index(batch_op.f('ix_memory_episode_link_entity_id'))

    op.drop_table('memory_episode_link')
    with op.batch_alter_table('knowledge_base_document', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_base_document_status'))
        batch_op.drop_index(batch_op.f('ix_knowledge_base_document_kb_id'))

    op.drop_table('knowledge_base_document')
    with op.batch_alter_table('knowledge_agent_binding', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_agent_binding_kb_id'))
        batch_op.drop_index(batch_op.f('ix_knowledge_agent_binding_agent_id'))

    op.drop_table('knowledge_agent_binding')
    with op.batch_alter_table('agentic_conversation_message', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agentic_conversation_message_turn_id'))
        batch_op.drop_index(batch_op.f('ix_agentic_conversation_message_thread_id'))
        batch_op.drop_index(batch_op.f('ix_agentic_conversation_message_message_id'))

    op.drop_table('agentic_conversation_message')
    with op.batch_alter_table('memory_episode', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memory_episode_thread_id'))
        batch_op.drop_index(batch_op.f('ix_memory_episode_occurred_at'))

    op.drop_table('memory_episode')
    with op.batch_alter_table('memory_entity', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_memory_entity_name'))
        batch_op.drop_index(batch_op.f('ix_memory_entity_entity_type'))

    op.drop_table('memory_entity')
    with op.batch_alter_table('knowledge_base', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_knowledge_base_status'))

    op.drop_table('knowledge_base')
    with op.batch_alter_table('agentic_conversation_turn', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agentic_conversation_turn_turn_id'))
        batch_op.drop_index(batch_op.f('ix_agentic_conversation_turn_thread_id'))

    op.drop_table('agentic_conversation_turn')
    with op.batch_alter_table('agentic_conversation', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_agentic_conversation_thread_id'))

    op.drop_table('agentic_conversation')
