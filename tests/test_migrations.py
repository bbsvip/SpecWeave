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
TABLES_0010 = {'chat_stream_events'}


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
    assert baseline & TABLES_0010 == set()
    created = set()
    for stage in (baseline, TABLES_0002, TABLES_0003, TABLES_0010):
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
            assert conn.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() == '0014_planning_history'
            # Confirm that a real FK references the previously missing table.
            fks = sa.inspect(conn).get_foreign_keys('review_jobs')
            assert any(fk['referred_table'] == 'spec_reviews' for fk in fks)
        engine.dispose()
        # Re-running migration must not corrupt schema or fail.
        _alembic(env)


def test_planning_history_upgrade_keeps_existing_answer(tmp_path):
    url=f'sqlite:///{tmp_path / "planning-history.db"}'
    env={**os.environ,'DATABASE_URL':url}
    _alembic(env)
    engine=sa.create_engine(url)
    questions=Base.metadata.tables['planning_questions']
    with engine.begin() as conn:
        conn.execute(questions.insert(),{
            'id':'saved-question','project_id':'qa','discussion_id':'qa-discussion',
            'question':'Which option?','recommended_id':'o1','options':[{'id':'o1','label':'Original'}],
            'status':'answered','answer':{'selected_ids':['o1'],'note':'Keep this answer'},
        })
        conn.execute(sa.text('ALTER TABLE planning_questions DROP COLUMN answer_history'))
        conn.execute(sa.text("UPDATE alembic_version SET version_num='0013_native_harness'"))
    _alembic(env)
    with engine.connect() as conn:
        row=conn.execute(sa.select(questions.c.answer,questions.c.answer_history)).one()
        assert row.answer=={'selected_ids':['o1'],'note':'Keep this answer'}
        assert row.answer_history==[]
    engine.dispose()

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
        with engine.connect() as conn:
            assert conn.execute(sa.text('SELECT count(*) FROM system_settings')).scalar_one() == 1
            assert conn.execute(sa.text('SELECT version_num FROM alembic_version')).scalar_one() == '0014_planning_history'
        engine.dispose()


def test_existing_trash_uses_last_soft_delete_timestamp(tmp_path):
    url = f'sqlite:///{tmp_path / "trash.db"}'
    env = {**os.environ, 'DATABASE_URL':url}
    _alembic(env)
    engine = sa.create_engine(url)
    projects = Base.metadata.tables['projects']
    with engine.begin() as conn:
        conn.execute(projects.insert(), {'id':'trash','name':'Trash','slug':'trash','owner_id':'owner','status':'deleted'})
        conn.execute(sa.text("INSERT INTO audit_events (id, project_id, action, created_at) VALUES "
                             "('1','trash','project_soft_delete','2026-09-01 12:00:00'), "
                             "('2','trash','project_soft_delete','2026-10-01 12:00:00')"))
        conn.execute(sa.text('DROP INDEX ix_projects_deleted_at'))
        conn.execute(sa.text('ALTER TABLE projects DROP COLUMN deleted_at'))
        conn.execute(sa.text("UPDATE alembic_version SET version_num = '0010_detached_chat'"))
    _alembic(env)
    with engine.connect() as conn:
        assert conn.execute(sa.select(projects.c.deleted_at)).scalar_one().isoformat() == '2026-10-01T12:00:00'
    engine.dispose()


def test_custom_model_upgrade_preserves_existing_limits_and_credentials(tmp_path):
    url = f'sqlite:///{tmp_path / "models.db"}'
    env = {**os.environ, 'DATABASE_URL': url}
    _alembic(env)
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        # Reproduce the deployed 0011 schema rather than relying on create_all.
        for column in ('max_output_tokens', 'api', 'compat', 'sampling_params'):
            conn.execute(sa.text(f'ALTER TABLE models DROP COLUMN {column}'))
        conn.execute(sa.text("INSERT INTO providers (id,name,base_url,encrypted_key,kind,created_at) "
                             "VALUES ('provider','Custom','https://example.invalid/v1','encrypted-test-key','custom',CURRENT_TIMESTAMP)"))
        conn.execute(sa.text("INSERT INTO models (id,provider_id,name,model_id,context_window,reasoning,enabled) "
                             "VALUES ('long','provider','Long','long',262144,1,1), "
                             "('small','provider','Small','small',4096,0,1)"))
        conn.execute(sa.text("UPDATE alembic_version SET version_num = '0011_project_trash'"))
    _alembic(env)
    with engine.connect() as conn:
        rows = conn.execute(sa.text('SELECT id,context_window,max_output_tokens,api,compat,sampling_params FROM models ORDER BY id')).all()
        assert rows == [('long',262144,8192,'openai-completions','{}','{}'),
                        ('small',4096,4095,'openai-completions','{}','{}')]
        assert conn.execute(sa.text('SELECT encrypted_key FROM providers')).scalar_one() == 'encrypted-test-key'
    engine.dispose()


def test_native_upgrade_preserves_history_and_strips_untrusted_checkpoint(tmp_path):
    url = f'sqlite:///{tmp_path / "native.db"}'
    env = {**os.environ,'DATABASE_URL':url}
    _alembic(env)
    engine = sa.create_engine(url)
    projects, discussions, runs, messages = (
        Base.metadata.tables[name] for name in ('projects','discussions','agent_runs','messages')
    )
    with engine.begin() as conn:
        conn.execute(projects.insert(),{'id':'p','name':'Legacy','slug':'legacy','owner_id':'owner','agent_backend':'pi'})
        conn.execute(discussions.insert(),{'id':'d','project_id':'p'})
        conn.execute(runs.insert(),{'id':'r','project_id':'p','discussion_id':'d','status':'queued',
                    'request_payload':{'message':'Continue', '_active_model_id':'model',
                    '_checkpoint':{'draft':{'path':'injected'}},'_resume_discovery':True}})
        conn.execute(messages.insert(),{'id':'m','discussion_id':'d','role':'assistant','content':'Saved reply'})
        conn.execute(sa.text("UPDATE alembic_version SET version_num = '0012_custom_model_config'"))
    _alembic(env)
    with engine.connect() as conn:
        assert conn.execute(sa.select(projects.c.agent_backend)).scalar_one()=='native'
        assert conn.execute(sa.select(messages.c.content)).scalar_one()=='Saved reply'
        assert conn.execute(sa.select(runs.c.request_payload)).scalar_one()=={
            'message':'Continue','_active_model_id':'model'
        }
    engine.dispose()
