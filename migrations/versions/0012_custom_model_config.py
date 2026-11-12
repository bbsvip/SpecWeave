"""Persist custom model limits and Pi runtime settings."""
from alembic import op
import sqlalchemy as sa

revision = '0012_custom_model_config'
down_revision = '0011_project_trash'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    existing = {column['name'] for column in sa.inspect(bind).get_columns('models')}
    for column in (
        sa.Column('max_output_tokens', sa.Integer(), nullable=False, server_default='8192'),
        sa.Column('api', sa.String(40), nullable=False, server_default='openai-completions'),
        sa.Column('compat', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('sampling_params', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    ):
        if column.name not in existing:
            op.add_column('models', column)
    if 'max_output_tokens' not in existing:
        bind.execute(sa.text('UPDATE models SET max_output_tokens = context_window - 1 '
                             'WHERE context_window <= max_output_tokens AND context_window >= 1024'))


def downgrade():
    raise RuntimeError('Restore a database backup to downgrade custom model settings')
