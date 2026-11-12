"""Retain every planning answer when a user reopens a question."""
from alembic import op
import sqlalchemy as sa

revision = '0014_planning_history'
down_revision = '0013_native_harness'
branch_labels = None
depends_on = None


def upgrade():
    if 'answer_history' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('planning_questions')}:
        op.add_column('planning_questions', sa.Column('answer_history', sa.JSON(), nullable=False, server_default='[]'))


def downgrade():
    op.drop_column('planning_questions', 'answer_history')
