"""Persistent cross-worker chat cancellation.
Revision ID: 0004_chat_coordination
Revises: 0003_review_jobs
"""
from alembic import op
import sqlalchemy as sa
revision = '0004_chat_coordination'
down_revision = '0003_review_jobs'
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    # The baseline uses current ORM metadata for new installations. In that
    # case the column already exists; legacy installations need the ALTER.
    columns = {col['name'] for col in sa.inspect(bind).get_columns('agent_runs')}
    if 'cancel_requested' not in columns:
        op.add_column('agent_runs', sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade():
    raise RuntimeError('Non-destructive migrations: restore a database backup to downgrade')
