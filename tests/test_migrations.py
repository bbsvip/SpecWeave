"""Regression tests for a brand-new DB and a partially pre-created DB.

Use SQLite for fast self-contained migration tests and statically verify that
foreign keys never point to tables scheduled for later Alembic revisions.
PostgreSQL is checked by the operator during deployment, not by these tests.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'source'))
from db import Base  # noqa: E402

# Freeze expected table membership for regression safety. Later migration
# changes should update the test and the corresponding migration separately.
TABLES_0002 = {
    'spec_reviews', 'review_findings', 'evidence_assessments',
    'trace_suggestions', 'workflow_checkpoints',
}
TABLES_0003 = {'review_jobs', 'review_job_steps', 'login_attempts'}


def _baseline_names():
    # Read the migration's allowlist directly without importing its dependencies.
    from importlib.util import spec_from_file_location, module_from_spec
    path = ROOT / 'migrations/versions/0001_baseline.py'
    spec = spec_from_file_location('baseline_under_test', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.BASELINE_TABLES)


def test_migrations_create_tables_in_fk_dependency_order():
    baseline = _baseline_names()
    assert baseline & TABLES_0002 == set()
    assert baseline & TABLES_0003 == set()
    created = set()
    for stage in (baseline, TABLES_0002, TABLES_0003):
        # The ORM graph can grow in later versions, but migrations must not
        # try creating a table before any of its referenced tables exist.
        for table in Base.metadata.sorted_tables:
            if table.name not in stage:
                continue
            refs = {fk.column.table.name for fk in table.foreign_keys}
            assert refs <= (created | stage), (table.name, refs - (created | stage))
        created.update(stage)
    assert created == set(Base.metadata.tables.keys())


def _alembic(env):
    proc = subprocess.run(
        [sys.executable, '-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'],
        cwd=ROOT, env=env, text=True, capture_output=True, timeout=90,
    )
    assert proc.returncode == 0, proc.stdout + '\n' + proc.stderr


def test_fresh_database_migrates_to_head():
    with tempfile.TemporaryDirectory(prefix='specweave-migration-') as tmp:
        db_path = Path(tmp) / 'fresh.db'
        url = f'sqlite:///{db_path}'
        env = {**os.environ, 'DATABASE_URL': url}
        _alembic(env)
        engine = sa.create_engine(url)
        with engine.connect() as conn:
            names = set(sa.inspect(conn).get_table_names())
            assert set(Base.metadata.tables) <= names
            assert conn.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() == '0006_openai_oauth_owner'
            # Confirm that a real FK references the previously missing table.
            fks = sa.inspect(conn).get_foreign_keys('review_jobs')
            assert any(fk['referred_table'] == 'spec_reviews' for fk in fks)
        engine.dispose()
        # Re-running migration must not corrupt schema or fail.
        _alembic(env)


def test_legacy_schema_without_alembic_version_is_adopted():
    with tempfile.TemporaryDirectory(prefix='specweave-legacy-migration-') as tmp:
        db_path = Path(tmp) / 'legacy.db'
        url = f'sqlite:///{db_path}'
        engine = sa.create_engine(url)
        # Simulate a legacy version that called Base.metadata.create_all()
        # before Alembic was introduced, and holds existing user data.
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("INSERT INTO system_settings (key, value) VALUES ('test', '{}')"))
        engine.dispose()
        _alembic({**os.environ, 'DATABASE_URL': url})
        with sa.create_engine(url).connect() as conn:
            assert conn.execute(sa.text('SELECT count(*) FROM system_settings')).scalar_one() == 1
            assert conn.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() == '0006_openai_oauth_owner'
