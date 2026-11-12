"""Track project trash retention independently of unrelated updates."""
from alembic import op
import sqlalchemy as sa

revision = '0011_project_trash'
down_revision = '0010_detached_chat'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'deleted_at' not in {column['name'] for column in inspector.get_columns('projects')}:
        op.add_column('projects', sa.Column('deleted_at', sa.DateTime(), nullable=True))
    if 'ix_projects_deleted_at' not in {index['name'] for index in inspector.get_indexes('projects')}:
        op.create_index('ix_projects_deleted_at', 'projects', ['deleted_at'])
    bind.execute(sa.text("""
        UPDATE projects SET deleted_at = COALESCE(
            (SELECT MAX(created_at) FROM audit_events
             WHERE project_id = projects.id AND action = 'project_soft_delete'),
            updated_at, created_at, CURRENT_TIMESTAMP)
        WHERE status = 'deleted' AND deleted_at IS NULL
    """))


def downgrade():
    raise RuntimeError('Restore a database backup to downgrade project retention')
