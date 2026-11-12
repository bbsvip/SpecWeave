"""Persist provider-reported chat usage and run timings.

Revision ID: 0008_conversation_stream
Revises: 0007_provider_deletion
"""
from alembic import op
import sqlalchemy as sa

revision = '0008_conversation_stream'
down_revision = '0007_provider_deletion'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    existing = {c['name'] for c in sa.inspect(bind).get_columns('agent_runs')}
    with op.batch_alter_table('agent_runs') as b:
        if 'usage' not in existing:
            b.add_column(sa.Column('usage', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        if 'events' not in existing:
            b.add_column(sa.Column('events', sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
        if 'first_token_ms' not in existing:
            b.add_column(sa.Column('first_token_ms', sa.Integer(), nullable=True))
        if 'duration_ms' not in existing:
            b.add_column(sa.Column('duration_ms', sa.Integer(), nullable=True))
    message_cols = {c['name'] for c in sa.inspect(bind).get_columns('messages')}
    if 'run_id' not in message_cols:
        with op.batch_alter_table('messages') as b:
            b.add_column(sa.Column('run_id', sa.String(36), nullable=True))
            b.create_foreign_key('fk_messages_run_id', 'agent_runs', ['run_id'], ['id'])
        op.create_index('ix_messages_run_id', 'messages', ['run_id'])

def downgrade():
    raise RuntimeError('Non-destructive migration: restore backup to downgrade')
