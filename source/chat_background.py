"""Durable Pi chat processing. Browser connections only observe persisted events.

Run directly from the existing review-worker process, never from the web request.
Multiple workers claim queued jobs with SKIP LOCKED on PostgreSQL. A crashed
worker does not blindly retry a potentially already-executed Pi prompt.
"""
import asyncio
import json
import socket
import uuid
from datetime import timedelta

from db import SessionLocal, AgentRun, ChatStreamEvent, PlanningQuestion, utcnow

WORKER_ID = 'chat-'+socket.gethostname()[:30]+'-'+uuid.uuid4().hex[:10]
LEASE_SECONDS=180
TERMINAL={'completed','error','cancelled'}


def record(db, run_id: str, event: dict):
    if not isinstance(event,dict):
        return
    # Store all actual Pi events for replay. Never store credentials or tool
    # capability headers here (these are not part of forwarded chat frames).
    db.add(ChatStreamEvent(run_id=run_id,event=event,created_at=utcnow()))
    run=db.get(AgentRun,run_id)
    if run and run.status=='running':
        run.lease_expires_at=utcnow()+timedelta(seconds=LEASE_SECONDS)
    db.commit()


def fail_run(db, run: AgentRun, message: str):
    run.status='error'
    run.error=str(message)[:500]
    run.finished_at=utcnow()
    run.worker_owner=None
    run.lease_expires_at=None
    request=run.request_payload or {}
    question_id=request.get('continue_question_id')
    if question_id:
        q=db.get(PlanningQuestion,question_id)
        if q and (q.answer or {}).get('continuation_run_id')==run.id:
            q.answer={k:v for k,v in q.answer.items() if k!='continuation_run_id'}
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
            .with_for_update(skip_locked=True).limit(10).all())
        for run in stale:
            fail_run(db,run,'Worker mất kết nối; phiên Pi có thể vẫn đang chạy. Kiểm tra trạng thái trước khi gửi lại.')
        row=(db.query(AgentRun).filter(AgentRun.status=='queued')
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
        if not run or run.status!='running':return
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
            async for chunk in response.body_iterator:
                if isinstance(chunk,bytes):chunk=chunk.decode('utf-8','replace')
                for line in str(chunk).splitlines():
                    if not line.startswith('data: '):continue
                    try:event=json.loads(line[6:])
                    except ValueError:continue
                    with SessionLocal() as events_db:
                        record(events_db,run_id,event)
        except BaseException as exc:
            # Catch worker cancellation only to mark durable state, then let the
            # worker shutdown; don't silently re-run a possibly completed prompt.
            with SessionLocal() as err_db:
                current=err_db.get(AgentRun,run_id)
                if current and current.status not in TERMINAL:
                    fail_run(err_db,current,'Pi background run interrupted ('+type(exc).__name__+')')
            if isinstance(exc,asyncio.CancelledError):raise
            return
    with SessionLocal() as db:
        run=db.get(AgentRun,run_id)
        if run and run.status not in TERMINAL:
            fail_run(db,run,'Pi stream ended without a terminal result')
        elif run:
            run.worker_owner=None
            run.lease_expires_at=None
            db.commit()


async def chat_worker_main():
    print('[chat-worker] running with durable PostgreSQL queue',flush=True)
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
