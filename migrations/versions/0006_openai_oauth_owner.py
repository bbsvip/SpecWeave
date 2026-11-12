"""Owner-bound OpenAI local OAuth provider (no credential columns or token migration).

Revision ID: 0006_openai_oauth_owner
Revises: 0005_multi_model_review
"""
from alembic import op
import sqlalchemy as sa
revision='0006_openai_oauth_owner'
down_revision='0005_multi_model_review'
branch_labels=None
depends_on=None

def upgrade():
    connection=op.get_bind()
    columns={col['name'] for col in sa.inspect(connection).get_columns('providers')}
    if 'owner_id' not in columns:
        with op.batch_alter_table('providers') as batch:
            batch.add_column(sa.Column('owner_id',sa.String(36),nullable=True))
    # Old API-key providers remain ownerless; all OpenAI OAuth providers require owner_id.

def downgrade():
    raise RuntimeError('Non-destructive migrations: restore database backup to downgrade')
