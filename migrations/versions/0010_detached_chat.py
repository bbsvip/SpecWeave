"""Durable detached Pi chat queue and resumable event log.
Revision ID: 0010_detached_chat
Revises: 0009_pi_native
"""
from alembic import op
import sqlalchemy as sa

revision='0010_detached_chat'
down_revision='0009_pi_native'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    existing={col['name'] for col in sa.inspect(bind).get_columns('agent_runs')}
    with op.batch_alter_table('agent_runs') as batch:
        if 'request_payload' not in existing:
            batch.add_column(sa.Column('request_payload',sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
        if 'requester_id' not in existing:
            batch.add_column(sa.Column('requester_id',sa.String(36),nullable=True))
        if 'worker_owner' not in existing:
            batch.add_column(sa.Column('worker_owner',sa.String(100),nullable=True))
        if 'lease_expires_at' not in existing:
            batch.add_column(sa.Column('lease_expires_at',sa.DateTime(),nullable=True))
    # Old SSE requests could leave "running" after a disconnected tab. No
    # worker owned those legacy executions, so mark them interrupted once.
    bind.execute(sa.text("""UPDATE agent_runs
        SET status='error', error='Legacy tab-bound run interrupted during upgrade',
            finished_at=CURRENT_TIMESTAMP
        WHERE status='running' AND worker_owner IS NULL"""))
    if not sa.inspect(bind).has_table('chat_stream_events'):
        op.create_table('chat_stream_events',
          sa.Column('id',sa.Integer(),primary_key=True,autoincrement=True),
          sa.Column('run_id',sa.String(36),sa.ForeignKey('agent_runs.id'),nullable=False),
          sa.Column('event',sa.JSON(),nullable=False),
          sa.Column('created_at',sa.DateTime(),nullable=False))
    if 'ix_chat_stream_events_run_id' not in {x['name'] for x in sa.inspect(bind).get_indexes('chat_stream_events')}:
        op.create_index('ix_chat_stream_events_run_id','chat_stream_events',['run_id'])

def downgrade():
    raise RuntimeError('Non-destructive migration: restore backup to downgrade')
