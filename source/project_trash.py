"""Owner-scoped project deletion and restart-safe retention cleanup."""
import asyncio
import logging
import shutil
import stat
from datetime import timedelta

from sqlalchemy import select

from db import (AgentRun, Audit, Base, ChatStreamEvent, Discussion, Message, Project,
                ReviewJob, ReviewJobStep, SessionLocal, utcnow)
from storage import project_root

TRASH_STATUSES = ('deleted', 'purging')
TRASH_RETENTION = timedelta(days=30)
logger = logging.getLogger(__name__)


def project_has_active_work(db, pid):
    return any(db.query(model.id).filter(model.project_id == pid,
               model.status.in_(('queued', 'running'))).first()
               for model in (AgentRun, ReviewJob))


def lock_trash_project(db, pid, *, owner_id=None, statuses=TRASH_STATUSES):
    if db.bind.dialect.name == 'sqlite':
        # SQLite ignores FOR UPDATE. Acquire its write lock before reading state.
        table = Project.__table__
        db.execute(table.update().where(table.c.id == pid).values(
            status=table.c.status, updated_at=table.c.updated_at))
    query = db.query(Project).filter(Project.id == pid, Project.status.in_(statuses))
    if owner_id is not None:
        query = query.filter(Project.owner_id == owner_id)
    return query.with_for_update().populate_existing().one_or_none()


def purge_project(db, pid, *, owner_id=None, cutoff=None):
    """Recheck ownership/state under lock before crossing the point of no return."""
    project = lock_trash_project(db, pid, owner_id=owner_id)
    if project is None:
        return 'missing'
    if cutoff is not None and project.status == 'deleted' and (
        project.deleted_at is None or project.deleted_at > cutoff
    ):
        return 'retained'
    if project_has_active_work(db, pid):
        return 'busy'
    if project.status == 'deleted':
        # Persist the intent before touching the volume. If interrupted, neither
        # a restore nor a second worker can expose a partially removed project.
        project.status = 'purging'
        db.add(Audit(project_id=pid, actor_id=owner_id, action='project_purge_started'))
        db.commit()
        project = lock_trash_project(db, pid, owner_id=owner_id, statuses=('purging',))
        if project is None:
            return 'purged'  # Another worker finished after the intent committed.

    initiated = db.query(Audit).filter_by(project_id=pid, action='project_purge_started').first()
    actor_id = initiated.actor_id if initiated else owner_id
    _delete_project_rows(db, pid)
    db.delete(project)
    db.add(Audit(project_id=pid, actor_id=actor_id, action='project_purge'))
    db.flush()
    _remove_project_volume(pid)
    db.commit()
    return 'purged'


def _delete_project_rows(db, pid):
    # These three tables are scoped through a parent rather than project_id.
    for table, foreign_key, parent in (
        (ChatStreamEvent, ChatStreamEvent.run_id, AgentRun),
        (Message, Message.discussion_id, Discussion),
        (ReviewJobStep, ReviewJobStep.job_id, ReviewJob),
    ):
        db.execute(table.__table__.delete().where(foreign_key.in_(
            select(parent.id).where(parent.project_id == pid))))

    for table in reversed(Base.metadata.sorted_tables):
        if any(key.column.table is Project.__table__ for key in table.foreign_keys):
            db.execute(table.delete().where(table.c.project_id == pid))

    db.query(Audit).filter_by(project_id=pid).delete()


def _remove_project_volume(pid):
    root = project_root(pid)
    if not root.parent.is_dir() or root.parent.resolve() != root.parent:
        raise OSError('Projects volume is unavailable or is not a regular directory')
    try:
        info = root.lstat()
    except FileNotFoundError:
        return  # Already removed by an interrupted purge.
    if (not stat.S_ISDIR(info.st_mode) or root.resolve() != root or
        getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
        raise OSError('Project volume path is not a regular project directory')
    shutil.rmtree(root)


def clean_project_trash(*, now=None):
    cutoff = (now or utcnow()) - TRASH_RETENTION
    with SessionLocal() as db:
        ids = db.scalars(select(Project.id).where(
            (Project.status == 'purging') |
            ((Project.status == 'deleted') & (Project.deleted_at <= cutoff))
        ).order_by(Project.deleted_at, Project.id)).all()
    for pid in ids:
        try:
            with SessionLocal() as db:
                purge_project(db, pid, cutoff=cutoff)
        except Exception:
            logger.exception('Project trash cleanup failed for %s', pid)


async def project_trash_worker():
    while True:
        try:
            await asyncio.to_thread(clean_project_trash)
        except Exception:
            logger.exception('Project trash scan failed')
        await asyncio.sleep(300)
