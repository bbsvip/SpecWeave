"""Durable review queue and per-role provenance, no destructive schema changes."""
from alembic import op
from db import Base
revision = "0003_review_jobs"
down_revision = "0002_quality"
branch_labels = None
depends_on = None

def upgrade():
    bind = op.get_bind()
    for name in ("review_jobs", "review_job_steps", "login_attempts"):
        Base.metadata.tables[name].create(bind=bind, checkfirst=True)

def downgrade():
    raise RuntimeError("Non-destructive migrations: restore a database backup to downgrade")
