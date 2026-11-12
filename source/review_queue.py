"""Durable, project-isolated quality review jobs; run directly, not a package.

Production uses PostgreSQL SELECT FOR UPDATE SKIP LOCKED. A job is claimed for
one bounded model call at a time and the result is committed before proceeding.
Workers never hold database transactions open while waiting for the model.
"""
import asyncio
import os
import socket
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from db import (SessionLocal, ReviewJob, ReviewJobStep, Project, Model, utcnow)
from planning_engine import (context_fingerprint, model_review, new_review,
                             consolidate_review_findings)

ROLES = ("analyst", "architect", "qa", "synthesis")
TERMINAL = frozenset(("succeeded", "failed", "cancelled", "stale"))
ACTIVE = ("queued", "running")
LEASE_SECONDS = 210  # Model HTTP timeout is 120 s; extra time for DB scheduling.
WORKER_ID = (socket.gethostname()[:40] + "-" + uuid.uuid4().hex[:12])


def create_review_job(db: Session, project: Project, requester_id: str, model: Model,
                      output_budget: int = 12000, role_models: dict | None = None) -> ReviewJob:
    """Caller must check ACL, CSRF and serialize project mutations."""
    if not model or not model.enabled or not model.provider.enabled:
        raise HTTPException(409, "Model đã tắt hoặc chưa cấu hình")
    if type(output_budget) is not int or not 2048 <= output_budget <= 24000:
        raise HTTPException(422, "Output budget phải từ 2048 đến 24000 tokens")
    role_models = role_models or {}
    if not isinstance(role_models,dict) or not set(role_models).issubset(ROLES):
        raise HTTPException(422,'Invalid role model map')
    checked = {}
    for role, mid in role_models.items():
        if not isinstance(mid,str):raise HTTPException(422,'Invalid role model ID')
        candidate=db.get(Model,mid)
        if not candidate or not candidate.enabled or not candidate.provider.enabled:
            raise HTTPException(422,f'Role model unavailable: {role}')
        checked[role]=candidate.id
    running = db.query(ReviewJob).filter(ReviewJob.project_id == project.id,
               ReviewJob.status.in_(ACTIVE)).first()
    if running:
        raise HTTPException(409, "Dự án đã có AI review đang chờ hoặc đang chạy")
    job = ReviewJob(project_id=project.id, requester_id=requester_id,
                    model_id=model.id, fingerprint=context_fingerprint(db,project.id),
                    output_budget=output_budget, role_models=checked, status="queued")
    db.add(job)
    db.flush()
    return job


def job_payload(db: Session, job: ReviewJob) -> dict:
    steps = db.query(ReviewJobStep).filter_by(job_id=job.id).all()
    by_role = {step.role: step for step in steps}
    return {
        "id": job.id, "project_id": job.project_id,
        "status": job.status, "progress": len(by_role), "total": len(ROLES),
        "cancel_requested": bool(job.cancel_requested),
        "model_id": job.model_id, "model_snapshot": job.model_snapshot or {}, "review_id": job.review_id,
        "output_budget": job.output_budget, "output_used": job.output_used,
        "role_models": job.role_models or {},
        "usage_estimated": bool(job.usage_estimated),
        "error": job.error or "", "summary": job.summary or "",
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "steps": [{"role":role, "status":"completed" if role in by_role else "pending",
                   "summary":(by_role[role].summary or "")[:1500] if role in by_role else "",
                   "findings":len(by_role[role].findings or []) if role in by_role else 0,
                   "model_id":by_role[role].model_id if role in by_role else (job.role_models or {}).get(role,job.model_id),
                   "output_tokens":by_role[role].output_tokens if role in by_role else 0,
                   "usage_estimated":by_role[role].usage_estimated if role in by_role else False}
                  for role in ROLES],
    }


def _terminal(job, status, reason=""):
    job.status=status
    job.error=reason[:600]
    job.finished_at=utcnow()
    job.lease_owner=None
    job.lease_expires_at=None


def claim_next_job(db: Session, worker_id: str = WORKER_ID):
    """Atomic DB claim for multi-worker PostgreSQL; best-effort SQLite for local testing."""
    now=utcnow()
    # Clear expired cancellation requests to avoid stuck runs after a crashed worker.
    cancelled = db.query(ReviewJob).filter(ReviewJob.status=="running",
                 ReviewJob.cancel_requested.is_(True),
                 ReviewJob.lease_expires_at < now).with_for_update(skip_locked=True).all()
    for job in cancelled:
        _terminal(job,"cancelled")
    query=db.query(ReviewJob).filter(
        ReviewJob.cancel_requested.is_(False),
        or_(ReviewJob.status=="queued",
            (ReviewJob.status=="running") & (ReviewJob.lease_expires_at < now))
    ).order_by(ReviewJob.created_at,ReviewJob.id).with_for_update(skip_locked=True)
    job=query.first()
    if job is None:
        db.commit()
        return None
    if job.attempts >= 20:
        _terminal(job,"failed","Worker claim retry limit exceeded")
        db.commit()
        return None
    job.status="running"
    job.lease_owner=worker_id
    job.lease_expires_at=now+timedelta(seconds=LEASE_SECONDS)
    job.attempts+=1
    if not job.started_at:job.started_at=now
    db.commit()
    return job.id


def _check_current(db: Session, job: ReviewJob):
    if job.cancel_requested:
        _terminal(job,"cancelled")
        return False
    project=db.get(Project,job.project_id)
    if not project or project.status=="deleted":
        _terminal(job,"stale","Dự án không còn hoạt động")
        return False
    if context_fingerprint(db,job.project_id)!=job.fingerprint:
        _terminal(job,"stale","Spec đã thay đổi; hãy tạo một review mới")
        return False
    return True


def _finish(db: Session, job: ReviewJob):
    steps={step.role:step for step in db.query(ReviewJobStep).filter_by(job_id=job.id).all()}
    if not all(role in steps for role in ROLES):
        raise RuntimeError("Cannot finish incomplete review")
    passes=[(role,steps[role].findings or []) for role in ROLES]
    notes="\n".join(f"{role.upper()} [model={steps[role].model_id}]: {steps[role].summary}" for role in ROLES)
    review=new_review(db,job.project_id,job.requester_id,engine="adversarial",
                      model_id=job.model_id,summary=notes,items=consolidate_review_findings(passes))
    job.review_id=review.id
    job.summary=notes[:2000]
    _terminal(job,"succeeded")


def _record_failure(job: ReviewJob, exc: Exception):
    # Never return provider URLs, key, prompt fragments, or full HTTP responses.
    if isinstance(exc,HTTPException):
        reason=f"Model review failed (HTTP {exc.status_code})"
    else:
        reason=f"Review worker error ({type(exc).__name__})"
    _terminal(job,"failed",reason)


async def process_claimed_job(job_id: str, worker_id: str = WORKER_ID) -> bool:
    """Execute at most one model pass; durable state is committed around it."""
    role=None
    with SessionLocal() as db:
        job=db.query(ReviewJob).filter_by(id=job_id).with_for_update().first()
        if not job or job.status!="running" or job.lease_owner!=worker_id:
            return False
        if not _check_current(db,job):
            db.commit()
            return True
        existing={x.role:x for x in db.query(ReviewJobStep).filter_by(job_id=job_id).all()}
        role=next((name for name in ROLES if name not in existing),None)
        if role is None:
            _finish(db,job)
            db.commit()
            return True
        job_model_id=(job.role_models or {}).get(role,job.model_id)
        model=db.get(Model,job_model_id)
        project=db.get(Project,job.project_id)
        job_project_id=job.project_id
        if model and model.provider.kind=='openai-oauth':
            from openai_oauth_gateway import require_access
            try: require_access(project,model,job.requester_id)
            except HTTPException:
                _terminal(job,'failed','OpenAI OAuth account cannot be shared or authorization was revoked')
                db.commit();return True
        if not model or not model.enabled or not model.provider.enabled:
            _terminal(job,"failed","Configured model is no longer available")
            db.commit()
            return True
        prior=[{"role":r,"model_id":existing[r].model_id,"summary":existing[r].summary,"findings":existing[r].findings[:12]}
               for r in ROLES if r in existing]
        remaining_roles=len(ROLES)-len(existing)
        remaining=job.output_budget-job.output_used
        if remaining<256*remaining_roles:
            _terminal(job,"failed","Output budget exhausted before completing review")
            db.commit()
            return True
        max_tokens=max(256,min(4000,remaining//remaining_roles))
        # ORM objects detach with expire_on_commit=False; don't carry a session across model HTTP.
    try:
        with SessionLocal() as model_db:
            model_project=model_db.get(Project,job_project_id)
            model_config=model_db.get(Model,job_model_id)
            summary, findings, output_tokens, estimated = await model_review(
                model_db, model_project, model_config, lens=role,
                prior_reviews=prior if role=="synthesis" else None,
                max_output_tokens=max_tokens, include_usage=True,
                release_db_before_http=True, **({"actor_id":job.requester_id} if model.provider.kind=="openai-oauth" else {}))
    except Exception as exc:
        with SessionLocal() as db:
            job=db.query(ReviewJob).filter_by(id=job_id).with_for_update().first()
            if job and job.status=="running" and job.lease_owner==worker_id:
                if job.cancel_requested:_terminal(job,"cancelled")
                else:_record_failure(job,exc)
                db.commit()
        return True
    with SessionLocal() as db:
        job=db.query(ReviewJob).filter_by(id=job_id).with_for_update().first()
        if not job or job.status!="running" or job.lease_owner!=worker_id:
            return False
        if not _check_current(db,job):
            db.commit()
            return True
        if db.query(ReviewJobStep).filter_by(job_id=job_id,role=role).first():
            return False
        db.add(ReviewJobStep(job_id=job_id,role=role,model_id=job_model_id,summary=summary[:2000],
              findings=findings[:20],output_tokens=output_tokens,usage_estimated=estimated))
        job.output_used+=output_tokens
        job.usage_estimated=job.usage_estimated or estimated
        job.step_index+=1
        job.lease_owner=None
        job.lease_expires_at=None
        job.status="queued"
        if job.step_index>=len(ROLES):
            db.flush()
            _finish(db,job)
        db.commit()
    return True



async def run_job_once(worker_id: str = WORKER_ID):
    with SessionLocal() as db:
        job_id=claim_next_job(db,worker_id)
    if not job_id:
        return False
    await process_claimed_job(job_id,worker_id)
    return True


async def worker_main():
    from db import init_db
    init_db()
    print(f"[review-worker] started {WORKER_ID}",flush=True)
    while True:
        try:
            worked=await run_job_once()
            if not worked:await asyncio.sleep(2)
        except Exception as exc:
            print(f"[review-worker] loop failure: {type(exc).__name__}",flush=True)
            await asyncio.sleep(3)

if __name__=="__main__":
    asyncio.run(worker_main())
