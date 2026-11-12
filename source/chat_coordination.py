"""Cross-worker chat coordination using PostgreSQL session advisory locks.

Run directly from source/. SQLite uses a local lock for development tests only.
"""
import asyncio
import hashlib
from contextlib import asynccontextmanager
from sqlalchemy import text
from fastapi import HTTPException
from db import engine, SessionLocal, AgentRun, PlanningQuestion, Project
from project_trash import TRASH_STATUSES


def lock_project_write(db, project_id):
    row = db.query(Project.id).filter(Project.id==project_id,Project.status.not_in(TRASH_STATUSES)).with_for_update().first()
    if row is None:
        raise HTTPException(404,'Project not found')

LOCAL_LOCKS = {}

def lock_key(discussion_id: str) -> int:
    return int.from_bytes(hashlib.sha256(('specweave-chat:' + discussion_id).encode()).digest()[:8], 'big', signed=True)

class ChatBusy(Exception):
    pass

class ChatStopped(Exception):
    pass

@asynccontextmanager
async def discussion_lease(discussion_id: str):
    """Keep a dedicated DB connection alive until the stream ends.

    PostgreSQL advisory locks are connection-scoped, and therefore automatically
    released if the worker crashes or the database connection is lost.
    """
    if engine.dialect.name != 'postgresql':
        lock = LOCAL_LOCKS.setdefault(discussion_id, asyncio.Lock())
        if lock.locked():
            raise ChatBusy()
        async with lock:
            yield
        return
    # Connection is deliberately held throughout model streaming; do not share it
    # with an ORM session or return it to the pool before releasing the lock.
    conn = engine.connect()
    key = lock_key(discussion_id)
    acquired = False
    try:
        acquired = bool(conn.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': key}).scalar())
        conn.commit()
        if not acquired:
            raise ChatBusy()
        yield
    finally:
        if acquired:
            try:
                conn.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': key})
                conn.commit()
            except Exception:
                conn.invalidate()
        conn.close()

def cancellation_requested(run_id: str, *, claim=None) -> bool:
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        return (run is None or bool(run.cancel_requested) or run.status != 'running'
                or (claim is not None and run.worker_owner!=claim))


def clear_chat_continuation(db, run):
    question_id=(run.request_payload or {}).get('continue_question_id')
    if question_id:
        question=db.get(PlanningQuestion,question_id)
        if question and (question.answer or {}).get('continuation_run_id')==run.id:
            question.answer={k:v for k,v in question.answer.items() if k!='continuation_run_id'}
