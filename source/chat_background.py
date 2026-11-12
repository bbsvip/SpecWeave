"""Durable Pi chat processing. Browser connections only observe persisted events.

Run directly from the existing review-worker process, never from the web request.
Multiple workers claim queued jobs with SKIP LOCKED on PostgreSQL. A crashed
worker does not blindly retry a potentially already-executed Pi prompt.
"""
import asyncio
import json
import os
import socket
import uuid
from contextlib import aclosing
from datetime import timedelta
from fastapi import HTTPException
from sqlalchemy import or_

from chat_coordination import clear_chat_continuation
from db import SessionLocal, AgentRun, ChatStreamEvent, utcnow

WORKER_ID = 'chat-'+socket.gethostname()[:30]+'-'+uuid.uuid4().hex[:10]
LEASE_SECONDS=180
CONCURRENCY=4


def record(db, run_id: str, event: dict):
    run=db.get(AgentRun,run_id,with_for_update=True)
    if not run or run.status!='running' or run.worker_owner!=WORKER_ID:
        return False
    # Store all actual Pi events for replay. Never store credentials or tool
    # capability headers here (these are not part of forwarded chat frames).
    db.add(ChatStreamEvent(run_id=run_id,event=event,created_at=utcnow()))
    run.lease_expires_at=utcnow()+timedelta(seconds=LEASE_SECONDS)
    db.commit()
    return True


def fail_run(db, run: AgentRun, message: str):
    run.status='error'
    run.error=str(message)[:500]
    run.finished_at=utcnow()
    run.worker_owner=None
    run.lease_expires_at=None
    clear_chat_continuation(db,run)
    db.add(ChatStreamEvent(run_id=run.id,event={
        'type':'error','run_id':run.id,'payload':{'message':str(message)[:500]}},created_at=utcnow()))
    db.commit()


def claim_next():
    with SessionLocal() as db:
        now=utcnow()
        # After a worker restart, fail a lease-expired run instead of retrying
        # a Pi prompt that may have completed inside the Pi Host already.
        stale=(db.query(AgentRun).filter(AgentRun.status=='running',
            AgentRun.worker_owner.isnot(None),AgentRun.lease_expires_at < now)
            .with_for_update(skip_locked=True).first())
        if stale:
            fail_run(db,stale,'Worker mất kết nối; phiên Pi có thể vẫn đang chạy. Kiểm tra trạng thái trước khi gửi lại.')
        row=(db.query(AgentRun).filter(AgentRun.status=='queued')
            # Requeued jobs back off until the prior Pi session releases its
            # advisory lock; they must not spin or be treated as failures.
            .filter(or_(AgentRun.lease_expires_at.is_(None),AgentRun.lease_expires_at<=now))
            .order_by(AgentRun.created_at,AgentRun.id)
            .with_for_update(skip_locked=True).first())
        if not row:
            db.commit()
            return None
        row.status='running'
        row.worker_owner=WORKER_ID
        row.lease_expires_at=now+timedelta(seconds=LEASE_SECONDS)
        db.commit()
        return row.id


async def process_queued_run(run_id: str):
    """Consume the existing Pi-native producer without an attached HTTP client."""
    # Import only inside worker. The web server never starts a model task.
    from app import _chat_stream_impl, acl
    from db import Project, User
    with SessionLocal() as db:
        run=db.get(AgentRun,run_id)
        if not run or run.status!='running' or run.worker_owner!=WORKER_ID:return
        project=db.get(Project,run.project_id)
        actor=db.get(User,run.requester_id) if run.requester_id else None
        if not project or not actor or not actor.enabled:
            fail_run(db,run,'Dự án hoặc tài khoản khởi tạo không còn hợp lệ')
            return
        try:acl(db,project.id,actor,write=True)
        except Exception:
            fail_run(db,run,'Quyền truy cập dự án đã thay đổi; Pi run không được phép tiếp tục')
            return
        if project.active_model_id!=(run.request_payload or {}).get('_active_model_id'):
            fail_run(db,run,'Model dự án đã thay đổi sau khi xếp hàng; vui lòng gửi lại')
            return
        try:
            response=await _chat_stream_impl(run.project_id,db,actor,project,
                run.request_payload or {},existing_run_id=run_id)
            async with aclosing(response.body_iterator) as frames:
                async for frame in frames:
                    event=json.loads(frame.removeprefix('data: '))
                    # The producer commits these with their run state. A
                    # subscriber must never see terminal state before the event.
                    if event['type'] in ('done','error'):
                        continue
                    with SessionLocal() as events_db:
                        if not record(events_db,run_id,event):
                            break
        except BaseException as exc:
            # Catch worker cancellation only to mark durable state, then let the
            # worker shutdown; don't silently re-run a possibly completed prompt.
            with SessionLocal() as err_db:
                current=err_db.get(AgentRun,run_id,with_for_update=True)
                if current and current.status=='running' and current.worker_owner==WORKER_ID:
                    detail=(exc.detail if isinstance(exc,HTTPException) else
                            'Pi background run interrupted ('+type(exc).__name__+')')
                    fail_run(err_db,current,detail)
            if isinstance(exc,asyncio.CancelledError):raise
            return
    with SessionLocal() as db:
        run=db.get(AgentRun,run_id,with_for_update=True)
        if run and run.status=='running' and run.worker_owner==WORKER_ID:
            fail_run(db,run,'Pi stream ended without a terminal result')


async def chat_worker_main():
    slots=max(1,min(int(os.getenv('SPECWEAVE_CHAT_CONCURRENCY',CONCURRENCY)),16))
    print(f'[chat-worker] running with {slots} chat slots',flush=True)
    await asyncio.gather(*(_chat_worker_slot() for _ in range(slots)))


async def _chat_worker_slot():
    while True:
        try:
            run_id=claim_next()
            if run_id:await process_queued_run(run_id)
            else:await asyncio.sleep(.65)
        except asyncio.CancelledError:raise
        except Exception as e:
            print('[chat-worker] '+type(e).__name__,flush=True)
            await asyncio.sleep(2)


async def run_once():
    run_id=claim_next()
    if not run_id:return None
    await process_queued_run(run_id)
    return run_id
