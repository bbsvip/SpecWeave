"""Multi-provider, per-role review model provenance.

Revision ID: 0005_multi_model_review
Revises: 0004_chat_coordination
"""
from alembic import op
import sqlalchemy as sa

revision='0005_multi_model_review'
down_revision='0004_chat_coordination'
branch_labels=None
depends_on=None


def upgrade():
    conn=op.get_bind()
    inspector=sa.inspect(conn)
    jobs={col['name'] for col in inspector.get_columns('review_jobs')}
    steps={col['name'] for col in inspector.get_columns('review_job_steps')}
    if 'role_models' not in jobs:
        with op.batch_alter_table('review_jobs') as batch:
            batch.add_column(sa.Column('role_models',sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
    if 'model_id' not in steps:
        with op.batch_alter_table('review_job_steps') as batch:
            batch.add_column(sa.Column('model_id',sa.String(36),nullable=True))


def downgrade():
    raise RuntimeError('Non-destructive migrations: restore a database backup to downgrade')
