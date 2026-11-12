"""Baseline v0.3 tables for new installs; adopt existing v0.3 without dropping data.

Revision ID: 0001_baseline
Revises:
"""
from alembic import op
from db import Base
revision='0001_baseline'
down_revision=None
branch_labels=None
depends_on=None
NEW={'spec_reviews','review_findings','evidence_assessments','trace_suggestions','workflow_checkpoints'}

def upgrade():
    bind=op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.name not in NEW:
            table.create(bind=bind,checkfirst=True)

def downgrade():
    raise RuntimeError('Non-destructive baseline: restore a database backup to downgrade')
