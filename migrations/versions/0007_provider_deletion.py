"""Preserve completed review metadata if an administrator deletes provider models.

Revision ID: 0007_provider_deletion
Revises: 0006_openai_oauth_owner
"""
from alembic import op
import sqlalchemy as sa

revision='0007_provider_deletion'
down_revision='0006_openai_oauth_owner'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    cols={col['name']:col for col in sa.inspect(bind).get_columns('review_jobs')}
    if 'model_snapshot' not in cols:
        with op.batch_alter_table('review_jobs') as batch:
            batch.add_column(sa.Column('model_snapshot',sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
    review_cols={col['name'] for col in sa.inspect(bind).get_columns('spec_reviews')}
    if 'model_snapshot' not in review_cols:
        with op.batch_alter_table('spec_reviews') as batch:
            batch.add_column(sa.Column('model_snapshot',sa.JSON(),nullable=False,server_default=sa.text("'{}'")))
    if not cols['model_id']['nullable']:
        with op.batch_alter_table('review_jobs') as batch:
            batch.alter_column('model_id',existing_type=sa.String(36),nullable=True)

def downgrade():
    raise RuntimeError('Non-destructive migrations: restore a database backup to downgrade')
