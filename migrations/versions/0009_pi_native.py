"""Switch all projects to the sole Pi SDK agent backend.
Revision ID: 0009_pi_native
Revises: 0008_conversation_stream
"""
from alembic import op
import sqlalchemy as sa
revision='0009_pi_native'
down_revision='0008_conversation_stream'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    bind.execute(sa.text("UPDATE projects SET agent_backend = 'pi' WHERE agent_backend IS DISTINCT FROM 'pi'"))

def downgrade():
    raise RuntimeError('Pi-native migration is intentionally one-way; restore backup to downgrade')
