"""Create the original project schema on a clean database.

Revision ID: 0001_baseline
Revises:

IMPORTANT: The ORM is shared by every migration, so Base.metadata contains
future-revision tables too. Never create all tables except 0002's quality
set here: review_jobs (0003) has a foreign key to spec_reviews (0002).
PostgreSQL rejects that FK until 0002 has run.
"""
from alembic import op
from db import Base

revision = '0001_baseline'
down_revision = None
branch_labels = None
depends_on = None

# Frozen table membership for the original schema. New tables belong in their
# own later revision, even when they appear in current ORM metadata.
BASELINE_TABLES = frozenset({
    'audit_events', 'system_settings', 'users', 'auth_sessions',
    'presets', 'projects', 'providers', 'decisions', 'discussions',
    'documents', 'memberships', 'models', 'plan_phases',
    'project_goals', 'requirements', 'research_items',
    'trace_links', 'agent_runs', 'messages', 'phase_dependencies',
    'phase_reviews', 'planning_questions',
})


def upgrade():
    bind = op.get_bind()
    missing = BASELINE_TABLES - Base.metadata.tables.keys()
    if missing:
        raise RuntimeError(f'Baseline ORM tables missing: {sorted(missing)}')
    # SQLAlchemy orders tables by their FK dependencies. Future revision tables
    # are excluded, so the order can be executed safely on empty PostgreSQL.
    for table in Base.metadata.sorted_tables:
        if table.name in BASELINE_TABLES:
            table.create(bind=bind, checkfirst=True)


def downgrade():
    raise RuntimeError('Non-destructive baseline: restore a database backup to downgrade')
