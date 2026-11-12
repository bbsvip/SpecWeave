"""Safe hard-delete of provider plus models, keeping completed review history.

Only called after authenticated administrator + CSRF guard. Other provider owners
cannot delete owner-bound OAuth credentials. Does not delete OAuth vault credentials.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session
from db import Provider, Model, Project, ReviewJob, SpecReview, AgentRun, utcnow

ACTIVE_REVIEW=('queued','running')

def owned_provider(db:Session, provider_id:str, actor):
    provider=db.query(Provider).filter(Provider.id==provider_id).with_for_update().first()
    if provider is None:
        raise HTTPException(404,'Provider not found')
    if provider.kind=='openai-oauth' and provider.owner_id!=actor.id:
        raise HTTPException(403,'OpenAI OAuth account belongs to another user')
    return provider


def impact(db:Session, provider:Provider, models=None):
    if models is None:models=db.query(Model).filter_by(provider_id=provider.id).all()
    ids={m.id for m in models}
    # Query all jobs rather than just job.model_id: role_models stores optional
    # per-role model IDs and must be taken into account as well.
    jobs=[j for j in db.query(ReviewJob).all() if j.model_id in ids or any(v in ids for v in (j.role_models or {}).values())]
    projects=db.query(Project).filter(Project.active_model_id.in_(ids)).all() if ids else []
    live_projects={p.id for p in projects}
    active_runs=db.query(AgentRun).filter(AgentRun.project_id.in_(live_projects),AgentRun.status=='running').count() if live_projects else 0
    return {'provider_id':provider.id,'name':provider.name,'kind':provider.kind,
            'models':len(ids),'model_ids':sorted(ids),'projects':len(projects),
            'project_names':[p.name for p in projects][:15],
            'review_jobs':len(jobs),
            'active_reviews':sum(j.status in ACTIVE_REVIEW for j in jobs),
            'active_chats':active_runs}


def delete_models(db:Session,provider:Provider,models:list[Model]):
    """One DB transaction, no partial deletion. Caller commits together with audit."""
    ids={m.id for m in models}
    # The default model is an FK; role overrides are stored as JSON UUIDs.
    # Look at both, including historical completed reviews.
    jobs=(db.query(ReviewJob).with_for_update().all() if ids else [])
    affected=[j for j in jobs if j.model_id in ids or any(v in ids for v in (j.role_models or {}).values())]
    active=[j for j in affected if j.status in ACTIVE_REVIEW]
    if active:
        raise HTTPException(409,f'{len(active)} review job đang chạy/chờ; hủy hoặc hoàn thành trước khi xóa model')
    projects=db.query(Project).filter(Project.active_model_id.in_(ids)).with_for_update().all() if ids else []
    affected_ids={p.id for p in projects}
    active_chats=(db.query(AgentRun).filter(AgentRun.project_id.in_(affected_ids),AgentRun.status=='running').count() if affected_ids else 0)
    if active_chats:
        raise HTTPException(409,'Có chat đang chạy bằng model của provider; hãy dừng chat trước khi xóa')
    model_names={m.id:{'provider':provider.name,'name':m.name,'model_id':m.model_id} for m in models}
    for job in affected:
        snap=dict(job.model_snapshot or {})
        deleted=dict(snap.get('deleted_models') or {})
        for mid in ids:
            if mid==job.model_id or mid in (job.role_models or {}).values():
                deleted[mid]=model_names[mid]
        snap['deleted_models']=deleted
        job.model_snapshot=snap
        if job.model_id in ids:job.model_id=None
    for project in projects:
        project.active_model_id=None
        project.revision=(project.revision or 0)+1
        project.updated_at=utcnow()
    reviews=db.query(SpecReview).filter(SpecReview.model_id.in_(ids)).all() if ids else []
    for review in reviews:
        review.model_snapshot=model_names[review.model_id]
    for model in models:db.delete(model)
    db.flush()
    return {'ok':True,'models_deleted':len(models),'projects_reset':len(projects),
            'review_jobs_preserved':len(affected)}


def delete_provider(db:Session,provider:Provider,actor,expected_name:str):
    if expected_name != provider.name:
        raise HTTPException(422,'Nhập chính xác tên provider để xác nhận xóa')
    models=db.query(Model).filter_by(provider_id=provider.id).with_for_update().all()
    result=delete_models(db,provider,models)
    db.delete(provider)
    db.flush()
    return result
