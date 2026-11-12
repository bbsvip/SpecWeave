"""Add v0.4 project-scoped quality and workflow records.

Revision ID: 0002_quality
Revises: 0001_baseline
"""
from alembic import op
from db import Base
revision='0002_quality'
down_revision='0001_baseline'
branch_labels=None
depends_on=None
NEW={'spec_reviews','review_findings','evidence_assessments','trace_suggestions','workflow_checkpoints'}

def upgrade():
    bind=op.get_bind()
    for table in Base.metadata.sorted_tables:
        if table.name in NEW:
            table.create(bind=bind,checkfirst=True)

def downgrade():
    raise RuntimeError('Non-destructive migrations: restore a database backup to downgrade')
