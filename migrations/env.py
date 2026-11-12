"""Database migrations for the standalone source layout (not a Python package)."""
import os
import sys
from pathlib import Path
from logging.config import fileConfig
from alembic import context
from sqlalchemy import create_engine, pool
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'source'))
from db import Base  # noqa: E402

config=context.config
# Our short alembic.ini intentionally has no [loggers] section.
if config.config_file_name and config.get_section('loggers'):
    fileConfig(config.config_file_name)
url=os.environ.get('DATABASE_URL',config.get_main_option('sqlalchemy.url'))
if url.startswith('postgresql://'):
    url='postgresql+psycopg://'+url[len('postgresql://'):]
config.set_main_option('sqlalchemy.url',url.replace('%','%%'))

def run_migrations_offline():
    context.configure(url=url,target_metadata=Base.metadata,literal_binds=True,
                      compare_type=True,dialect_opts={'paramstyle':'named'})
    with context.begin_transaction(): context.run_migrations()

def run_migrations_online():
    engine=create_engine(url,poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=Base.metadata,compare_type=True)
        with context.begin_transaction():context.run_migrations()
    engine.dispose()

if context.is_offline_mode():run_migrations_offline()
else:run_migrations_online()
