"""Switch projects to the SpecWeave harness and sanitize queued legacy payloads."""
from alembic import op
import sqlalchemy as sa


revision = '0013_native_harness'
down_revision = '0012_custom_model_config'
branch_labels = None
depends_on = None

PUBLIC_FIELDS = {
    'discussion_id', 'message', 'role', 'mode', 'continue_question_id',
    'advance_planning', 'auto_next', '_active_model_id',
}


def upgrade():
    db = op.get_bind()
    db.execute(sa.text("UPDATE projects SET agent_backend = 'native'"))
    runs = sa.table('agent_runs', sa.column('id',sa.String), sa.column('status',sa.String),
                    sa.column('request_payload',sa.JSON))
    rows = db.execute(sa.select(runs.c.id,runs.c.request_payload).where(
        runs.c.status.in_(('queued','running'))
    )).all()
    for run_id,payload in rows:
        clean = {key:value for key,value in (payload or {}).items() if key in PUBLIC_FIELDS}
        db.execute(runs.update().where(runs.c.id==run_id).values(request_payload=clean))


def downgrade():
    op.execute("UPDATE projects SET agent_backend = 'pi'")
