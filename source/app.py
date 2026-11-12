"""SpecWeave web server. Run directly: python source/app.py (no editable install)."""
import asyncio
import time
import io
import json
import os
import re
import secrets
import shutil
import hashlib
import hmac
import zipfile
from contextlib import aclosing, asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from db import (get_db, init_db, SessionLocal, utcnow, User, AuthSession, Project, Membership,
                Preset, Provider, Model, Discussion, Message, AgentRun, ChatStreamEvent, Decision, Requirement,
                Document, ResearchItem, SystemSetting, Audit, PlanningQuestion,
                ProjectGoal, TraceLink, PlanPhase, PhaseDependency, PhaseReview,
                SpecReview, ReviewFinding, EvidenceAssessment, TraceSuggestion, WorkflowCheckpoint, ReviewJob, LoginAttempt)
from security import (hash_password, verify_password, new_session, current_auth, require_auth,
                      check_csrf, encrypt, decrypt, validate_encryption_key, SESSION_COOKIE)
from storage import (DATA_ROOT, PROJECTS_ROOT, project_root, resolve, init_project, list_entries,
                     read_text, write_text, remove, move, atomic_text, project_usage, enforce_project_quota, storage_limits)
from ai import pi_stream, tool_call, pi_tool_token, TOOLS, ROLES, MODES, plan_prompt
from openai_oauth_gateway import enabled as openai_oauth_enabled, require_access as openai_oauth_access, relay as openai_oauth_relay
from planning import save_question, question_payload, answer_question, QUESTION_JSON_INSTRUCTIONS
from intelligence import (coverage_report, graph_payload, report_markdown, link_nodes,
                          project_node, add_phase_dependency, phase_review_blockers)
from research import search_searxng, fetch_web
from planning_engine import (STEPS, SOURCE_KINDS, VERIFY_STATES, stage_current, stage_transition,
                             context_fingerprint, context_snapshot, rule_findings, model_review,
                             new_review, assess_evidence, propose_trace_links, resolve_suggestion,
                             quality_export, adversarial_review)
from review_queue import create_review_job, job_payload, TERMINAL
from provider_management import owned_provider, impact as provider_impact, delete_provider as perform_provider_deletion, delete_models as perform_model_deletion
from chat_coordination import discussion_lease, ChatBusy, ChatStopped, cancellation_requested, clear_chat_continuation
from chat_content import display_user_message, parse_model_response
from chat_events import with_heartbeat
from project_trash import (TRASH_RETENTION, TRASH_STATUSES, lock_trash_project, logger as trash_logger,
                           project_has_active_work, purge_project)

BASE = Path(__file__).parent
@asynccontextmanager
async def lifespan(_app):
    validate_encryption_key()
    DATA_ROOT.mkdir(parents=True,exist_ok=True)
    PROJECTS_ROOT.mkdir(parents=True,exist_ok=True)
    init_db()
    yield

app = FastAPI(title="SpecWeave", version="1.0.3", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static",StaticFiles(directory=BASE / "static"),name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.filters["date_short"] = lambda d: d.strftime("%d/%m/%Y %H:%M") if d else "—"
templates.env.filters["readable_chat"] = lambda v: parse_model_response(v)[0]
templates.env.filters["display_user_message"] = display_user_message
templates.env.filters["pretty_json"] = lambda v: json.dumps(v, indent=2, ensure_ascii=False, default=str)

def pretty_tool_value(value):
    """Unicode-safe display, never alters the actual Pi tool arguments."""
    def decode(obj):
        if isinstance(obj,str):
            return re.sub(r'\\u([0-9a-fA-F]{4})',lambda m:chr(int(m.group(1),16)),obj)
        if isinstance(obj,list):return [decode(item) for item in obj]
        if isinstance(obj,dict):return {decode(str(k)):decode(v) for k,v in obj.items()}
        return obj
    if value is None:return ''
    if isinstance(value,(dict,list)):
        return json.dumps(decode(value),ensure_ascii=False,indent=2)
    raw=str(value)
    try:
        obj=json.loads(raw)
        if isinstance(obj,str):
            try:obj=json.loads(obj)
            except (ValueError,TypeError):pass
        if isinstance(obj,(dict,list)):
            return json.dumps(decode(obj),ensure_ascii=False,indent=2)
        if isinstance(obj,str):raw=obj
    except (ValueError,TypeError):pass
    return decode(raw)

templates.env.filters['pretty_tool']=pretty_tool_value

STAGES = ["discovery","requirements","solution_design","ux_design","spec_review","approved"]
ROLE_LABELS = {"analyst":"Analyst","pm":"Product Manager","architect":"Architect","ux":"UX Designer","reviewer":"Critical Reviewer","planner":"Planning"}

@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = ("default-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
     "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
     "img-src 'self' data: https:; font-src 'self' data: https://cdn.jsdelivr.net; "
     "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'self'")
    return response

def page(request, name, *, db, user=None, session=None, **kw):
    if user is None:
        user, session = current_auth(db,request)
    return templates.TemplateResponse(request,name,{"user":user,"session":session,"csrf":session.csrf_token if session else "",
                                                   "role_labels":ROLE_LABELS, "stages":STAGES,
                                                   "assets_local":(BASE / "static/vendor/.ready").exists(),**kw})

def go(path): return RedirectResponse(path,status_code=303)

def auth_page(request, db):
    user, session = current_auth(db,request)
    if not user: return None, None, go("/login")
    if user.must_change_password and request.url.path != "/profile": return user, session, go("/profile")
    return user, session, None

def acl(db,project_id,user,write=False,owner=False):
    pr = db.get(Project,project_id)
    if not pr or pr.status in TRASH_STATUSES: raise HTTPException(404,"Không tìm thấy dự án")
    if pr.owner_id == user.id: return pr
    row = db.query(Membership).filter_by(project_id=project_id,user_id=user.id).first()
    if not row or (write and row.role not in ("editor",)) or owner:
        raise HTTPException(403,"Bạn không có quyền với dự án")
    return pr

def lock_project_write(db, pid):
    """Serialize critical project mutations on PostgreSQL transaction boundary.

    SQLite in single-worker development has no FOR UPDATE; Docker production uses
    PostgreSQL. Keep this guard short; never hold it across model/browser fetches.
    """
    row = db.query(Project.id).filter(Project.id==pid,Project.status.not_in(TRASH_STATUSES)).with_for_update().first()
    if row is None:
        raise HTTPException(404,'Project not found')

def api_guard(request,db,change=False):
    user,session = require_auth(db,request)
    if user.must_change_password: raise HTTPException(403,"Yêu cầu đổi mật khẩu")
    if change: check_csrf(request,session,request.headers.get("x-csrf-token"))
    return user,session

def audit(db,user,action,project=None,details=None):
    db.add(Audit(actor_id=user.id if user else None,project_id=project.id if project else None,action=action,details=details or {}))

def admin_only(user):
    if user.role != "admin": raise HTTPException(403,"Chỉ quản trị viên")

def get_projects(db,user):
    memberships = db.query(Membership.project_id).filter_by(user_id=user.id)
    return db.query(Project).filter(Project.status.not_in(TRASH_STATUSES),or_(Project.owner_id==user.id,Project.id.in_(memberships))).order_by(Project.updated_at.desc()).all()

def nonempty(data, key, default=""):
    return str(data.get(key,default)).strip()

@app.get("/health")
def health(db:Session=Depends(get_db)):
    from sqlalchemy import text
    db.execute(text("SELECT 1"))
    return {"status":"ok", "app":"SpecWeave", "version":app.version}

@app.get("/first-run",response_class=HTMLResponse)
def bootstrap_page(request:Request,db:Session=Depends(get_db)):
    if db.query(User).count(): return go("/login")
    return page(request,"bootstrap.html",db=db)

@app.post("/first-run")
def bootstrap(request:Request,username:str=Form(...),email:str=Form(...),password:str=Form(...),bootstrap_token:str=Form(""),db:Session=Depends(get_db)):
    if db.query(User).count(): raise HTTPException(403,"Instance đã khởi tạo")
    configured=os.environ.get("BOOTSTRAP_TOKEN","")
    if not configured or not secrets.compare_digest(bootstrap_token,configured):
        raise HTTPException(403,"Bootstrap token không hợp lệ")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{3,60}",username): raise HTTPException(400,"Username không hợp lệ")
    try: saved=hash_password(password)
    except ValueError as exc: raise HTTPException(400,str(exc)) from exc
    u=User(username=username.lower(),email=email.strip().lower(),display_name=username,password_hash=saved,role="admin")
    db.add(u);db.flush();audit(db,u,"bootstrap_admin");db.commit()
    return go("/login?created=1")

@app.get("/login",response_class=HTMLResponse)
def login_page(request:Request,db:Session=Depends(get_db)):
    if not db.query(User).count(): return go("/first-run")
    u,_ = current_auth(db,request)
    if u: return go("/")
    return page(request,"login.html",db=db,created=request.query_params.get("created"))

def _login_throttle_keys(request:Request, identifier:str):
    # Never persist user-submitted identifiers or raw IP addresses in throttle table.
    secret=os.environ.get('ENCRYPTION_KEY','').encode()
    client=request.client.host if request.client else 'unknown'
    return (hmac.new(secret,identifier.lower().strip().encode(),hashlib.sha256).hexdigest(),
            hmac.new(secret,client.encode(),hashlib.sha256).hexdigest())

@app.post("/login")
def login(request:Request,username:str=Form(...),password:str=Form(...),db:Session=Depends(get_db)):
    subject,client_hash=_login_throttle_keys(request,username)
    recent=utcnow()-timedelta(minutes=15)
    by_pair=db.query(LoginAttempt).filter(LoginAttempt.subject_hash==subject,
                    LoginAttempt.client_hash==client_hash,LoginAttempt.created_at>=recent).count()
    by_client=db.query(LoginAttempt).filter(LoginAttempt.client_hash==client_hash,
                   LoginAttempt.created_at>=recent).count()
    if by_pair>=8 or by_client>=50:
        raise HTTPException(429,"Quá nhiều lần thử đăng nhập; hãy thử lại sau")
    u=db.query(User).filter(or_(User.username==username.lower().strip(),User.email==username.lower().strip())).first()
    if not u or not u.enabled or not verify_password(u.password_hash,password):
        db.add(LoginAttempt(subject_hash=subject,client_hash=client_hash))
        db.commit()
        raise HTTPException(401,"Tài khoản hoặc mật khẩu không chính xác")
    db.query(LoginAttempt).filter_by(subject_hash=subject,client_hash=client_hash).delete(synchronize_session=False)
    # Periodic bounded cleanup avoids retaining indefinitely rejected login attempts.
    db.query(LoginAttempt).filter(LoginAttempt.created_at<utcnow()-timedelta(days=2)).delete(synchronize_session=False)
    token,session=new_session(db,u,request)
    audit(db,u,"login");db.commit()
    r=go("/profile" if u.must_change_password else "/")
    r.set_cookie(SESSION_COOKIE,token,httponly=True,secure=os.environ.get("COOKIE_SECURE","false").lower()=="true",
                 samesite="lax",max_age=7*86400,path="/")
    return r

@app.post("/logout")
async def logout(request:Request,db:Session=Depends(get_db)):
    u,s=require_auth(db,request)
    form=await request.form();check_csrf(request,s,form.get("csrf"))
    s.revoked_at=utcnow();audit(db,u,"logout");db.commit()
    r=go("/login");r.delete_cookie(SESSION_COOKIE);return r

@app.get("/profile",response_class=HTMLResponse)
def profile_page(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    sessions=db.query(AuthSession).filter_by(user_id=u.id,revoked_at=None).order_by(AuthSession.last_seen_at.desc()).all()
    return page(request,"profile.html",db=db,user=u,session=s,sessions=sessions)

@app.post("/profile/password")
def change_password(request:Request,current_password:str=Form(...),new_password:str=Form(...),csrf:str=Form(...),db:Session=Depends(get_db)):
    u,s=require_auth(db,request);check_csrf(request,s,csrf)
    if not verify_password(u.password_hash,current_password):raise HTTPException(400,"Mật khẩu hiện tại sai")
    try:u.password_hash=hash_password(new_password)
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    u.must_change_password=False
    db.query(AuthSession).filter(AuthSession.user_id==u.id,AuthSession.id!=s.id).update({"revoked_at":utcnow()})
    audit(db,u,"password_change");db.commit();return go("/profile?changed=1")

@app.post("/profile/logout-all")
def logout_all(request:Request,csrf:str=Form(...),db:Session=Depends(get_db)):
    u,s=require_auth(db,request);check_csrf(request,s,csrf)
    db.query(AuthSession).filter_by(user_id=u.id,revoked_at=None).update({"revoked_at":utcnow()})
    audit(db,u,"logout_all");db.commit()
    r=go("/login");r.delete_cookie(SESSION_COOKIE);return r

@app.get("/",response_class=HTMLResponse)
def dashboard(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    projects=get_projects(db,u)
    ids=[p.id for p in projects]
    stats={"total":len(projects),"approved":sum(p.stage=="approved" for p in projects),
           "decisions":db.query(Decision).filter(Decision.project_id.in_(ids)).count() if ids else 0,
           "documents":db.query(Document).filter(Document.project_id.in_(ids)).count() if ids else 0}
    return page(request,"dashboard.html",db=db,user=u,session=s,projects=projects,stats=stats,active="dashboard")

@app.get("/projects",response_class=HTMLResponse)
def projects_page(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    return page(request,"projects.html",db=db,user=u,session=s,projects=get_projects(db,u),active="projects")

@app.get("/projects/new",response_class=HTMLResponse)
def new_project_page(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    presets=db.query(Preset).filter_by(user_id=u.id).order_by(Preset.created_at.desc()).all()
    models=[m for m in db.query(Model).join(Provider).filter(Model.enabled==True,Provider.enabled==True).all()
            if m.provider.kind!='openai-oauth' or m.provider.owner_id==u.id]
    return page(request,"project_new.html",db=db,user=u,session=s,presets=presets,models=models,active="projects")

@app.post("/api/projects")
async def create_project(request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True)
    data=await request.json()
    name=nonempty(data,"name")
    if not 2 <= len(name) <= 200: raise HTTPException(422,"Tên dự án từ 2–200 ký tự")
    slug=re.sub(r"[^a-z0-9-]+","-",name.lower()).strip("-")[:90] or "project"
    profile=data.get("profile",{})
    if not isinstance(profile,dict):raise HTTPException(422,"Profile không hợp lệ")
    if len(json.dumps(profile,ensure_ascii=False).encode('utf-8'))>64*1024:
        raise HTTPException(413,'Project profile exceeds 64 KiB')
    chosen_model=data.get('model_id') or None
    if chosen_model:
        candidate=db.get(Model,chosen_model) if isinstance(chosen_model,str) else None
        if not candidate or not candidate.enabled or not candidate.provider.enabled:
            raise HTTPException(422,'Selected model is unavailable')
        if candidate.provider.kind=='openai-oauth' and candidate.provider.owner_id!=u.id:
            raise HTTPException(403,'OpenAI OAuth model belongs to another user')
    pr=Project(name=name,slug=slug,description=nonempty(data,"description")[:6000],owner_id=u.id,profile=profile,
               active_model_id=chosen_model,thinking_level=data.get("thinking", "medium"))
    if pr.thinking_level not in ("off","minimal","low","medium","high","xhigh","max"):pr.thinking_level="medium"
    db.add(pr);db.flush()
    try:
        init_project(pr)
        db.add(Discussion(project_id=pr.id,title="Khám phá dự án",mode="discovery"))
        audit(db,u,"project_create",pr);db.commit()
    except Exception:
        db.rollback();shutil.rmtree(project_root(pr.id),ignore_errors=True);raise
    return {"id":pr.id,"redirect":f"/projects/{pr.id}"}

@app.get('/projects/trash',response_class=HTMLResponse)
def projects_trash_page(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    trashed=db.query(Project).filter(Project.owner_id==u.id,Project.status.in_(TRASH_STATUSES)).order_by(Project.deleted_at.desc()).all()
    return page(request,'projects_trash.html',db=db,user=u,session=s,trashed=trashed,
                now=utcnow(),retention=TRASH_RETENTION,active='project_trash')


@app.get("/projects/{pid}",response_class=HTMLResponse)
def project_overview(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    discussions=db.query(Discussion).filter_by(project_id=pid).order_by(Discussion.created_at.desc()).all()
    decisions=db.query(Decision).filter_by(project_id=pid).order_by(Decision.created_at.desc()).limit(8).all()
    docs=db.query(Document).filter_by(project_id=pid).order_by(Document.updated_at.desc()).limit(8).all()
    reqs=db.query(Requirement).filter_by(project_id=pid).count()
    readiness=coverage_report(db,pid)
    return page(request,"project_overview.html",db=db,user=u,session=s,pr=pr,discussions=discussions,decisions=decisions,docs=docs,reqs=reqs,readiness=readiness,active="overview")

@app.get("/projects/{pid}/settings",response_class=HTMLResponse)
def project_settings(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u,write=True)
    models=[m for m in db.query(Model).join(Provider).filter(Model.enabled==True,Provider.enabled==True).all()
            if m.provider.kind!='openai-oauth' or (pr.owner_id==u.id and m.provider.owner_id==u.id)]
    members=db.query(Membership).filter_by(project_id=pid).all()
    users={x.id:x for x in db.query(User).all() if x.id in [m.user_id for m in members]}
    return page(request,"project_settings.html",db=db,user=u,session=s,pr=pr,models=models,members=members,users=users,workflow_active=db.query(WorkflowCheckpoint).filter_by(project_id=pid).count()>0,active="settings")

@app.post("/api/projects/{pid}/settings")
async def update_project(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json()
    if "name" in data: pr.name=str(data["name"]).strip()[:200]
    if "description" in data:pr.description=str(data["description"])[:6000]
    if "stage" in data and data["stage"] in STAGES:
        if db.query(WorkflowCheckpoint).filter_by(project_id=pid).count() and data['stage']!=pr.stage:
            raise HTTPException(409,'Stage được quản lý bởi BMAD Workflow; không đổi thủ công khi đã bắt đầu review')
        pr.stage=data["stage"]
    if "active_model_id" in data:
        mid=data["active_model_id"] or None
        if mid:
            candidate=db.get(Model,mid) if isinstance(mid,str) else None
            if not candidate or not candidate.enabled or not candidate.provider.enabled:
                raise HTTPException(422,'Selected model is unavailable')
            if candidate.provider.kind=='openai-oauth' and (pr.owner_id!=u.id or candidate.provider.owner_id!=u.id):
                raise HTTPException(403,'OpenAI OAuth model is private to its owner')
        pr.active_model_id=mid
    if "thinking_level" in data and data["thinking_level"] in ("off","minimal","low","medium","high","xhigh","max"):
        pr.thinking_level=data["thinking_level"]
    pr.agent_backend='pi'  # Pi SDK is the only available agent engine
    if "profile" in data:
        if not isinstance(data["profile"],dict):raise HTTPException(422,'Project profile must be an object')
        if len(json.dumps(data["profile"],ensure_ascii=False).encode('utf-8'))>64*1024:
            raise HTTPException(413,'Project profile exceeds 64 KiB')
        pr.profile=data["profile"]
    if 'quality_review_required' in data:
        if data['quality_review_required'] not in (True,False):raise HTTPException(422,'Invalid quality_review_required')
        p=dict(pr.profile or {});p['quality_review_required']=data['quality_review_required'];pr.profile=p
    if "ui_required" in data:
        if data['ui_required'] not in (True,False,None):raise HTTPException(422,'Invalid ui_required option')
        p=dict(pr.profile or {})
        if data['ui_required'] is None:p.pop('ui_required',None)
        else:p['ui_required']=data['ui_required']
        pr.profile=p
    pr.revision+=1;audit(db,u,"project_update",pr);db.commit();return {"ok":True}

@app.post("/api/projects/{pid}/archive")
def archive_project(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    db.refresh(pr)
    pr.status="archived" if pr.status!="archived" else "active"
    audit(db,u,"project_archive",pr);db.commit();return {"ok":True,"status":pr.status}

@app.post("/api/projects/{pid}/delete")
def delete_project(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    if project_has_active_work(db,pid):
        raise HTTPException(409,'Hãy dừng hoặc chờ các lượt Pi và review kết thúc trước khi chuyển vào thùng rác')
    pr.status='deleted'
    pr.deleted_at=utcnow()
    audit(db,u,'project_soft_delete',pr)
    db.commit()
    return {"ok":True}

@app.post("/api/presets")
async def create_preset(request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);data=await request.json()
    profile=data.get("profile",{})
    if not isinstance(profile,dict) or len(json.dumps(profile,ensure_ascii=False).encode('utf-8'))>30000:
        raise HTTPException(422,"Profile không hợp lệ")
    preset_name=str(data.get('name','')).strip()[:160]
    if not preset_name:raise HTTPException(422,'Preset name required')
    preset=Preset(user_id=u.id,name=preset_name,profile=profile)
    db.add(preset);db.commit();return {"id":preset.id}

@app.get("/api/presets")
def presets_api(request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db)
    return [{"id":p.id,"name":p.name,"profile":p.profile} for p in db.query(Preset).filter_by(user_id=u.id).all()]

@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);p=db.get(Preset,preset_id)
    if not p or p.user_id!=u.id:raise HTTPException(404,"Not found")
    db.delete(p);db.commit();return {"ok":True}

# --------------------------- File Manager ---------------------------
FILE_FOLDERS = {"inputs","docs","notes","research","decisions","memory"}
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB","40"))*1024*1024
MAX_UPLOAD_FILES = int(os.environ.get("MAX_UPLOAD_FILES","30"))

def managed_path(path, *, folder_ok=True):
    p=str(path or "").replace("\\","/").strip("/")
    if not p and folder_ok:return ""
    if p.split("/")[0] not in FILE_FOLDERS:raise HTTPException(403,"Folder được bảo vệ")
    return p

@app.get("/projects/{pid}/files",response_class=HTMLResponse)
def files_page(pid:str,request:Request,path:str="",db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    folder=managed_path(path)
    entries=list_entries(pid,folder)
    if not folder:entries=[x for x in entries if x["name"] in FILE_FOLDERS]
    crumbs=[];parts=folder.split("/") if folder else []
    for i,p in enumerate(parts):crumbs.append({"name":p,"path":"/".join(parts[:i+1])})
    member=db.query(Membership).filter_by(project_id=pid,user_id=u.id).first()
    editable=pr.owner_id==u.id or (member is not None and member.role=='editor')
    return page(request,"files.html",db=db,user=u,session=s,pr=pr,entries=entries,folder=folder,
                crumbs=crumbs,editable=editable,owner=(pr.owner_id==u.id),
                storage_usage=project_usage(pid),storage_quota=storage_limits()[0],active="files")

@app.post("/api/projects/{pid}/folders")
async def make_folder(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json();folder=managed_path(data.get("path",""),folder_ok=False)
    target=resolve(pid,folder)
    if target.exists():raise HTTPException(409,"Thư mục đã tồn tại")
    target.mkdir(parents=True,exist_ok=False)
    audit(db,u,"folder_create",pr,{"path":folder});db.commit()
    return {"ok":True,"path":folder}

@app.post("/api/projects/{pid}/files/upload")
async def upload_files(pid:str,request:Request,folder:str=Form("inputs"),files:list[UploadFile]=File(...),csrf:str=Form(...),db:Session=Depends(get_db)):
    u,s=require_auth(db,request);check_csrf(request,s,csrf);pr=acl(db,pid,u,write=True)
    folder=managed_path(folder,folder_ok=False)
    dest=resolve(pid,folder)
    if not dest.is_dir():raise HTTPException(404,"Không có thư mục upload")
    if not 1 <= len(files) <= MAX_UPLOAD_FILES:
        raise HTTPException(413, f'Tối đa {MAX_UPLOAD_FILES} file mỗi lượt')
    lock_project_write(db,pid)
    uploaded=[]
    pending=[]
    used_names=set()
    usage=project_usage(pid)
    max_bytes,max_files=storage_limits()
    planned_bytes=0
    try:
        for f in files:
            name=(f.filename or "").replace("\\", "/").split("/")[-1]
            if not name or name.startswith(".") or name.lower() in (".env","project.json") or "/" in name or "\\" in name:
                raise HTTPException(400,"Tên file không hợp lệ")
            if name in used_names:raise HTTPException(409,f'Tên file trùng: {name}')
            used_names.add(name)
            path=f"{folder}/{name}"
            target=resolve(pid,path)
            if target.exists():raise HTTPException(409,f"Đã tồn tại: {name}. Hãy đổi tên trước khi tải lên")
            if usage['files']+len(pending)+1 > max_files:
                raise HTTPException(413,'Project file count quota exceeded')
            total=0
            temp=target.with_name(".upload-"+secrets.token_hex(10))
            pending.append((temp,target,path))
            with open(temp,"xb") as out:
                while chunk:=await f.read(1024*1024):
                    total+=len(chunk)
                    if total>MAX_UPLOAD:raise HTTPException(413,f"File {name} quá {MAX_UPLOAD//1024//1024} MB")
                    if usage['bytes']+planned_bytes+total > max_bytes:
                        raise HTTPException(413,'Project storage quota exceeded')
                    out.write(chunk)
            planned_bytes+=total
        for temp,target,path in pending:
            if target.exists():raise HTTPException(409,'Target changed during upload')
            os.replace(temp,target)
            uploaded.append(path)
    except Exception:
        # Multi-file upload is atomic with respect to validation and quota failures.
        for temp,target,path in pending:
            if temp.exists():temp.unlink()
        for rel in uploaded:
            target=resolve(pid,rel)
            if target.exists():target.unlink()
        raise
    audit(db,u,"files_upload",pr,{"files":uploaded});db.commit()
    return {"ok":True,"files":uploaded}

@app.get("/api/projects/{pid}/files/content")
def file_content(pid:str,path:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);pr=acl(db,pid,u)
    path=managed_path(path,folder_ok=False)
    p=resolve(pid,path)
    return {"path":path,"content":read_text(pid,path),"mtime":p.stat().st_mtime}

@app.post("/api/projects/{pid}/files/content")
async def save_file(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    data=await request.json();path=managed_path(data.get("path",""),folder_ok=False)
    content=data.get("content")
    if not isinstance(content,str):raise HTTPException(400,"Content phải là văn bản")
    target=resolve(pid,path)
    if target.exists() and data.get("expected_mtime") is not None:
        if abs(target.stat().st_mtime-float(data["expected_mtime"]))>0.00001:
            raise HTTPException(409,"File đã thay đổi; tải lại trước khi lưu")
    write_text(pid,path,content)
    if path.startswith("docs/") and path.endswith(".md"):
        d=db.query(Document).filter_by(project_id=pid,path=path).first()
        if d:d.revision+=1;d.status="draft"
        else:db.add(Document(project_id=pid,path=path,title=Path(path).stem))
    audit(db,u,"file_save",pr,{"path":path});db.commit()
    return {"ok":True,"mtime":target.stat().st_mtime}

@app.post("/api/projects/{pid}/files/move")
async def move_file(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json()
    src=managed_path(data.get("source",""),folder_ok=False)
    if "/" not in src: raise HTTPException(403,"Không di chuyển thư mục hệ thống")
    dst=managed_path(data.get("destination",""),folder_ok=False)
    move(pid,src,dst)
    # Keep Document Studio references coherent for moved files and entire folders.
    tracked=db.query(Document).filter_by(project_id=pid).all()
    for doc in tracked:
        if doc.path==src or doc.path.startswith(src+'/'):
            doc.path=dst+doc.path[len(src):]
            doc.title=Path(doc.path).stem
            doc.revision+=1
            doc.status='draft'  # Moving an approved spec requires a new approval.
    audit(db,u,"file_move",pr,{"source":src,"destination":dst});db.commit()
    return {"ok":True}

@app.post("/api/projects/{pid}/files/delete")
async def delete_file(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json();path=managed_path(data.get("path",""),folder_ok=False)
    if "/" not in path: raise HTTPException(403,"Không xóa thư mục dữ liệu gốc")
    # Move to project-local trash; recoverable by operator.
    source=resolve(pid,path)
    if not source.exists():raise HTTPException(404,"Not found")
    trash=project_root(pid)/".system"/"trash"
    trash.mkdir(exist_ok=True)
    target=trash/(secrets.token_hex(12)+"-"+source.name)
    source.rename(target)
    # Delete tracked documents nested under trashed folders, not just exact path.
    for doc in db.query(Document).filter_by(project_id=pid).all():
        if doc.path==path or doc.path.startswith(path+'/'):
            db.delete(doc)
    audit(db,u,"file_trash",pr,{"path":path,"stored":target.name});db.commit()
    return {"ok":True}

def project_trash_entry(db, pid, audit_id):
    record=db.query(Audit).filter_by(id=audit_id,project_id=pid,action='file_trash').first()
    if not record:raise HTTPException(404,'Không tìm thấy mục đã xóa')
    meta=record.details or {}
    stored=str(meta.get('stored',''))
    if not re.fullmatch(r'[a-f0-9]{24}-[^/\\]+',stored):
        raise HTTPException(400,'Metadata thùng rác không hợp lệ')
    path=managed_path(str(meta.get('path','')),folder_ok=False)
    if '/' not in path:raise HTTPException(403,'Không được sửa thư mục dữ liệu gốc')
    trash=project_root(pid)/'.system'/'trash'
    target=trash/stored
    if target.is_symlink():raise HTTPException(400,'Symlink không được hỗ trợ')
    if not target.exists():raise HTTPException(404,'Đối tượng đã xóa hoặc phục hồi trước đó')
    return record,path,target

@app.get("/api/projects/{pid}/files/trash")
def list_project_trash(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    records=db.query(Audit).filter_by(project_id=pid,action='file_trash').order_by(Audit.created_at.desc()).limit(200).all()
    entries=[]
    for item in records:
        try:
            _,path,target=project_trash_entry(db,pid,item.id)
            entries.append({'id':item.id,'path':path,'type':'folder' if target.is_dir() else 'file',
                            'deleted_at':item.created_at.isoformat() if item.created_at else None})
        except HTTPException:continue
    return {'entries':entries}

@app.post("/api/projects/{pid}/files/trash/{tid}/restore")
async def restore_trashed_file(pid:str,tid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json()
    lock_project_write(db,pid)
    _,original,trashed=project_trash_entry(db,pid,tid)
    destination=managed_path(str(data.get('destination') or original),folder_ok=False)
    if '/' not in destination:raise HTTPException(403,'Không ghi đè thư mục gốc')
    target=resolve(pid,destination)
    if target.exists():raise HTTPException(409,'Đường dẫn đích đã tồn tại')
    # The user must restore below an existing managed folder, not create hidden paths.
    if not target.parent.is_dir():raise HTTPException(409,'Thư mục đích chưa tồn tại')
    trashed.rename(target)
    paths=([target] if target.is_file() else
           (x for x in target.rglob('*.md') if x.is_file() and not x.is_symlink()))
    for file in paths:
        rel=file.relative_to(project_root(pid)).as_posix()
        if rel.startswith('docs/') and rel.endswith('.md'):
            if not db.query(Document).filter_by(project_id=pid,path=rel).first():
                db.add(Document(project_id=pid,path=rel,title=file.stem,status='draft'))
    audit(db,u,'file_trash_restore',pr,{'from':original,'to':destination,'trash_id':tid})
    db.commit();return {'ok':True,'path':destination}

@app.delete("/api/projects/{pid}/files/trash/{tid}")
def purge_trashed_file(pid:str,tid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    _,path,target=project_trash_entry(db,pid,tid)
    if target.is_dir():shutil.rmtree(target)
    else:target.unlink()
    audit(db,u,'file_trash_purge',pr,{'original_path':path,'trash_id':tid})
    db.commit();return {'ok':True}

@app.get("/api/projects/{pid}/files/preview")
def preview_project_file(pid:str,path:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    file=resolve(pid,managed_path(path,folder_ok=False))
    if not file.is_file():raise HTTPException(404,'Không tìm thấy file')
    mime={'.pdf':'application/pdf','.png':'image/png','.jpg':'image/jpeg',
          '.jpeg':'image/jpeg','.webp':'image/webp','.gif':'image/gif'}.get(file.suffix.lower())
    if not mime:raise HTTPException(415,'Chỉ hỗ trợ xem nhanh PDF hoặc ảnh an toàn')
    return FileResponse(file,media_type=mime,headers={'Content-Security-Policy':"default-src 'none'; sandbox",
         'X-Content-Type-Options':'nosniff','Content-Disposition':'inline'})

@app.get("/api/projects/{pid}/files/download")
def download_file(pid:str,path:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);pr=acl(db,pid,u)
    target=resolve(pid,managed_path(path,folder_ok=False))
    if not target.is_file():raise HTTPException(404,"File not found")
    return FileResponse(target,filename=target.name,media_type="application/octet-stream",
                        headers={"X-Content-Type-Options":"nosniff","Content-Security-Policy":"default-src 'none'"})

@app.get("/api/projects/{pid}/files/versions")
def file_versions(pid:str,path:str,request:Request,db:Session=Depends(get_db)):
    import hashlib
    u,s=api_guard(request,db);acl(db,pid,u)
    path=managed_path(path,folder_ok=False)
    ver=project_root(pid)/"versions"/(hashlib.sha256(path.encode()).hexdigest()[:16])
    return {"versions":[p.name for p in sorted(ver.iterdir(),reverse=True)] if ver.exists() else []}

@app.post("/api/projects/{pid}/files/restore")
async def file_restore(pid:str,request:Request,db:Session=Depends(get_db)):
    import hashlib
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json();path=managed_path(data.get("path",""),folder_ok=False)
    name=str(data.get("version",""))
    if not re.fullmatch(r"\d{8}T\d{12}",name):raise HTTPException(400,"Invalid version")
    ver=project_root(pid)/"versions"/(hashlib.sha256(path.encode()).hexdigest()[:16])/name
    if not ver.is_file():raise HTTPException(404,"Version not found")
    write_text(pid,path,ver.read_text(encoding="utf-8"))
    audit(db,u,"file_restore",pr,{"path":path,"version":name});db.commit();return {"ok":True}

# ------------------------ Documents / Decisions / Requirements ------------------------
@app.get("/projects/{pid}/documents",response_class=HTMLResponse)
def documents_page(pid:str,request:Request,doc:str="",db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    docs=db.query(Document).filter_by(project_id=pid).order_by(Document.updated_at.desc()).all()
    selected=db.get(Document,doc) if doc else (docs[0] if docs else None)
    if selected and selected.project_id!=pid:raise HTTPException(404,"Doc not found")
    content=read_text(pid,selected.path) if selected else ""
    mtime=resolve(pid,selected.path).stat().st_mtime if selected else 0
    return page(request,"documents.html",db=db,user=u,session=s,pr=pr,docs=docs,selected=selected,content=content,mtime=mtime,active="documents")

@app.post("/api/projects/{pid}/documents")
async def create_document(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json()
    filename=re.sub(r"[^a-zA-Z0-9_.-]+","-",str(data.get("name","New Document"))).strip("-.")[:95]
    if not filename.endswith(".md"):filename+=".md"
    path="docs/drafts/"+filename
    if resolve(pid,path).exists():raise HTTPException(409,"Document already exists")
    write_text(pid,path,"# "+str(data.get("name","New Document"))+"\n\n")
    d=Document(project_id=pid,path=path,title=str(data.get("name","New Document"))[:200])
    db.add(d);audit(db,u,"document_create",pr,{"path":path});db.commit()
    return {"id":d.id,"redirect":f"/projects/{pid}/documents?doc={d.id}"}

@app.post("/api/projects/{pid}/documents/{did}/approve")
async def approve_document(pid:str,did:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    doc=db.get(Document,did)
    if not doc or doc.project_id!=pid:raise HTTPException(404,"Document not found")
    data=await request.json()
    if int(data.get("revision",-1))!=doc.revision:raise HTTPException(409,"Tài liệu đã thay đổi; không thể duyệt bản cũ")
    doc.status="approved";audit(db,u,"document_approve",pr,{"document":did,"revision":doc.revision});db.commit();return {"ok":True}

@app.get("/projects/{pid}/decisions",response_class=HTMLResponse)
def decisions_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    decisions=db.query(Decision).filter_by(project_id=pid).order_by(Decision.created_at.desc()).all()
    return page(request,"decisions.html",db=db,user=u,session=s,pr=pr,decisions=decisions,active="decisions")

@app.post("/api/projects/{pid}/decisions")
async def create_decision(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json();title=nonempty(data,"title")
    if not title:raise HTTPException(422,"Missing title")
    d=Decision(project_id=pid,title=title[:250],description=nonempty(data,"description")[:6000],
               rationale=nonempty(data,"rationale")[:4000],created_by=u.id)
    db.add(d);audit(db,u,"decision_propose",pr);db.commit();return {"id":d.id}

@app.post("/api/projects/{pid}/decisions/{did}/status")
async def decision_status(pid:str,did:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    d=db.get(Decision,did)
    if not d or d.project_id!=pid:raise HTTPException(404,"Not found")
    data=await request.json()
    if data.get("status") not in ("proposed","approved","rejected"):raise HTTPException(400,"Invalid status")
    d.status=data["status"];audit(db,u,"decision_status",pr,{"id":did,"status":d.status});db.commit();return {"ok":True}

@app.get("/projects/{pid}/requirements",response_class=HTMLResponse)
def requirements_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    reqs=db.query(Requirement).filter_by(project_id=pid).order_by(Requirement.code).all()
    return page(request,"requirements.html",db=db,user=u,session=s,pr=pr,reqs=reqs,active="requirements")

@app.post("/api/projects/{pid}/requirements")
async def create_requirement(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    data=await request.json();title=nonempty(data,"title")
    if not title:raise HTTPException(422,"Missing title")
    count=db.query(Requirement).filter_by(project_id=pid).count()
    code=f"REQ-{count+1:03d}"
    while db.query(Requirement).filter_by(project_id=pid,code=code).first():count+=1;code=f"REQ-{count+1:03d}"
    obj=Requirement(project_id=pid,code=code,title=title[:255],description=nonempty(data,"description")[:6000],
                    criteria=nonempty(data,"criteria")[:4000],priority=data.get("priority","should"))
    db.add(obj);audit(db,u,"requirement_create",pr);db.commit();return {"id":obj.id,"code":code}

@app.get("/api/projects/{pid}/requirements/{rid}")
def get_requirement(pid:str,rid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    obj=project_node(db,pid,'requirement',rid)
    return {'id':rid,'code':obj.code,'title':obj.title,'description':obj.description,
            'criteria':obj.criteria,'priority':obj.priority,'status':obj.status}

@app.post("/api/projects/{pid}/requirements/{rid}")
async def update_requirement(pid:str,rid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    obj=project_node(db,pid,'requirement',rid)
    data=await request.json();title=nonempty(data,'title')
    if not 1<=len(title)<=255:raise HTTPException(422,'Tiêu đề requirement không hợp lệ')
    priority=str(data.get('priority','should'))
    if priority not in ('must','should','could','wont'):
        raise HTTPException(422,'Priority không hợp lệ')
    obj.title=title;obj.description=nonempty(data,'description')[:6000]
    obj.criteria=nonempty(data,'criteria')[:4000];obj.priority=priority
    obj.status='draft'
    # A changed requirement requires reviewing phases that implement it.
    affected=db.query(TraceLink).filter_by(project_id=pid,source_type='requirement',
                                           source_id=rid,target_type='phase').all()
    for link in affected:
        phase=project_node(db,pid,'phase',link.target_id)
        if phase.status in ('approved','in_review'):
            phase.status='draft';phase.revision+=1;phase.approved_by=None;phase.approved_at=None
    audit(db,u,'requirement_update',pr,{'id':rid,'phase_reviews_invalidated':len(affected)})
    db.commit();return {'id':rid,'status':obj.status}

@app.post("/api/projects/{pid}/requirements/{rid}/status")
async def req_status(pid:str,rid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    obj=db.get(Requirement,rid)
    if not obj or obj.project_id!=pid:raise HTTPException(404,"Not found")
    data=await request.json()
    if data.get("status") not in ("draft","approved","needs_review"):raise HTTPException(400,"Invalid status")
    obj.status=data["status"];audit(db,u,"requirement_status",pr);db.commit();return {"ok":True}

# ------------------------ Planning Intelligence / Traceability / Human Review ------------------------
@app.get("/projects/{pid}/intelligence",response_class=HTMLResponse)
def intelligence_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    report=coverage_report(db,pid)
    goals=db.query(ProjectGoal).filter_by(project_id=pid).order_by(ProjectGoal.created_at).all()
    reqs=db.query(Requirement).filter_by(project_id=pid).order_by(Requirement.code).all()
    phases=db.query(PlanPhase).filter_by(project_id=pid).order_by(PlanPhase.sort_order,PlanPhase.created_at).all()
    docs=db.query(Document).filter_by(project_id=pid).all()
    decisions=db.query(Decision).filter_by(project_id=pid).all()
    evidence=db.query(ResearchItem).filter_by(project_id=pid).order_by(ResearchItem.created_at.desc()).limit(50).all()
    trace=db.query(TraceLink).filter_by(project_id=pid).all()
    dependencies=db.query(PhaseDependency).filter_by(project_id=pid).all()
    reviews=db.query(PhaseReview).filter_by(project_id=pid).order_by(PhaseReview.created_at.desc()).limit(40).all()
    membership=db.query(Membership).filter_by(project_id=pid,user_id=u.id).first()
    editable=pr.owner_id==u.id or bool(membership and membership.role=='editor')
    return page(request,"intelligence.html",db=db,user=u,session=s,pr=pr,editable=editable,report=report,goals=goals,
                reqs=reqs,phases=phases,docs=docs,decisions=decisions,evidence=evidence,
                trace=trace,dependencies=dependencies,reviews=reviews,active="intelligence")

@app.get("/api/projects/{pid}/intelligence/report")
def intelligence_report(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    return coverage_report(db,pid)

@app.get("/api/projects/{pid}/intelligence/graph")
def intelligence_graph(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    return graph_payload(db,pid)

@app.post("/api/projects/{pid}/intelligence/goals")
async def intelligence_create_goal(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json();title=nonempty(data,'title')
    if not 3<=len(title)<=255:raise HTTPException(422,"Tên mục tiêu cần 3–255 ký tự")
    goal=ProjectGoal(project_id=pid,title=title,outcome=nonempty(data,'outcome')[:5000],
                     metric=nonempty(data,'metric')[:350],created_by=u.id)
    db.add(goal);audit(db,u,'goal_create',pr,{'title':title});db.commit()
    return {'id':goal.id,'status':goal.status}

@app.get("/api/projects/{pid}/intelligence/goals/{gid}")
def intelligence_get_goal(pid:str,gid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    goal=project_node(db,pid,'goal',gid)
    return {'id':gid,'title':goal.title,'outcome':goal.outcome,
            'metric':goal.metric,'status':goal.status}

@app.post("/api/projects/{pid}/intelligence/goals/{gid}")
async def intelligence_update_goal(pid:str,gid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    goal=project_node(db,pid,'goal',gid);data=await request.json()
    title=nonempty(data,'title')
    if not 3<=len(title)<=255:raise HTTPException(422,'Tên mục tiêu cần 3–255 ký tự')
    goal.title=title;goal.outcome=nonempty(data,'outcome')[:5000]
    goal.metric=nonempty(data,'metric')[:350];goal.status='draft'
    # Goal changes invalidate directly linked, approved implementation phases.
    phase_ids={x.target_id for x in db.query(TraceLink).filter_by(project_id=pid,
        source_type='goal',source_id=gid,target_type='phase').all()}
    req_ids={x.target_id for x in db.query(TraceLink).filter_by(project_id=pid,
        source_type='goal',source_id=gid,target_type='requirement').all()}
    if req_ids:
        phase_ids.update(x.target_id for x in db.query(TraceLink).filter(
            TraceLink.project_id==pid,TraceLink.source_type=='requirement',
            TraceLink.source_id.in_(list(req_ids)),TraceLink.target_type=='phase').all())
    for phase_id in phase_ids:
        phase=project_node(db,pid,'phase',phase_id)
        if phase.status in ('approved','in_review'):
            phase.status='draft';phase.revision+=1;phase.approved_by=None;phase.approved_at=None
    audit(db,u,'goal_update',pr,{'goal_id':gid,'linked_phases':len(phase_ids)})
    db.commit();return {'id':gid,'status':goal.status}

@app.post("/api/projects/{pid}/intelligence/goals/{gid}/status")
async def intelligence_goal_status(pid:str,gid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    goal=project_node(db,pid,'goal',gid)
    data=await request.json()
    if data.get('status') not in ('draft','approved'):
        raise HTTPException(422,"Trạng thái mục tiêu không hợp lệ")
    if data['status']=='approved' and not goal.outcome.strip():
        raise HTTPException(409,'Cần mô tả kết quả mong muốn trước khi duyệt')
    goal.status=data['status'];audit(db,u,'goal_review',pr,{'id':gid,'status':goal.status});db.commit()
    return {'id':gid,'status':goal.status}

@app.post("/api/projects/{pid}/intelligence/links")
async def intelligence_create_link(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    data=await request.json();item,created=link_nodes(db,pid,data,u.id)
    if created and item.target_type=='phase':
        phase=project_node(db,pid,'phase',item.target_id)
        if phase.status in ('approved','in_review'):
            phase.status='draft';phase.revision+=1;phase.approved_at=None;phase.approved_by=None
    audit(db,u,'trace_link_create',pr,{'id':item.id,'created':created});db.commit()
    return {'id':item.id,'created':created}

@app.delete("/api/projects/{pid}/intelligence/links/{lid}")
def intelligence_delete_link(pid:str,lid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    item=db.get(TraceLink,lid)
    if not item or item.project_id!=pid:raise HTTPException(404,'Link not found')
    if item.target_type=='phase':
        phase=project_node(db,pid,'phase',item.target_id)
        if phase.status in ('approved','in_review'):
            phase.status='draft';phase.revision+=1;phase.approved_at=None;phase.approved_by=None
    db.delete(item);audit(db,u,'trace_link_remove',pr,{'id':lid});db.commit()
    return {'ok':True}

@app.post("/api/projects/{pid}/intelligence/phases")
async def intelligence_create_phase(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    data=await request.json();title=nonempty(data,'title')
    if not 3<=len(title)<=255:raise HTTPException(422,'Tên phase cần 3–255 ký tự')
    count=db.query(PlanPhase).filter_by(project_id=pid).count()
    item=PlanPhase(project_id=pid,title=title,summary=nonempty(data,'summary')[:6000],
                   deliverables=nonempty(data,'deliverables')[:6000],
                   acceptance=nonempty(data,'acceptance')[:6000],sort_order=count+1,created_by=u.id)
    db.add(item);audit(db,u,'phase_create',pr,{'title':title});db.commit()
    return {'id':item.id,'revision':item.revision,'status':item.status}

@app.get("/api/projects/{pid}/intelligence/phases/{phase_id}")
def intelligence_get_phase(pid:str,phase_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    item=project_node(db,pid,'phase',phase_id)
    return {'id':item.id,'title':item.title,'summary':item.summary,
            'deliverables':item.deliverables,'acceptance':item.acceptance,
            'revision':item.revision,'status':item.status}

@app.post("/api/projects/{pid}/intelligence/phases/{phase_id}")
async def intelligence_update_phase(pid:str,phase_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    item=project_node(db,pid,'phase',phase_id);data=await request.json()
    if data.get('revision')!=item.revision:
        raise HTTPException(409,'Phase thay đổi từ một phiên khác; tải lại trước khi sửa')
    title=nonempty(data,'title')
    if not 3<=len(title)<=255:raise HTTPException(422,'Tên phase cần 3–255 ký tự')
    for attr,limit in [('title',255),('summary',6000),('deliverables',6000),('acceptance',6000)]:
        setattr(item,attr,nonempty(data,attr)[:limit])
    item.revision+=1;item.status='draft';item.approved_by=None;item.approved_at=None
    audit(db,u,'phase_update',pr,{'phase_id':phase_id,'revision':item.revision});db.commit()
    return {'id':item.id,'revision':item.revision,'status':item.status}

@app.post("/api/projects/{pid}/intelligence/phases/{phase_id}/dependencies")
async def intelligence_add_dependency(pid:str,phase_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    data=await request.json();prerequisite_id=str(data.get('prerequisite_id',''))
    dep,created=add_phase_dependency(db,pid,phase_id,prerequisite_id)
    if created:
        phase=project_node(db,pid,'phase',phase_id)
        phase.revision+=1;phase.status='draft';phase.approved_by=None;phase.approved_at=None
    audit(db,u,'phase_dependency_add',pr,{'id':dep.id,'created':created});db.commit()
    return {'id':dep.id,'created':created}

@app.delete("/api/projects/{pid}/intelligence/phases/{phase_id}/dependencies/{dependency_id}")
def intelligence_remove_dependency(pid:str,phase_id:str,dependency_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    phase=project_node(db,pid,'phase',phase_id)
    dep=db.get(PhaseDependency,dependency_id)
    if not dep or dep.project_id!=pid or dep.phase_id!=phase_id:
        raise HTTPException(404,'Dependency not found')
    db.delete(dep);phase.revision+=1;phase.status='draft';phase.approved_by=None;phase.approved_at=None
    audit(db,u,'phase_dependency_remove',pr,{'id':dependency_id});db.commit()
    return {'ok':True}

@app.post("/api/projects/{pid}/intelligence/phases/{phase_id}/review")
async def intelligence_review_phase(pid:str,phase_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True)
    data=await request.json();action=str(data.get('action',''))
    if action not in ('submit','approve','request_changes'):
        raise HTTPException(422,'Review action không hợp lệ')
    pr=acl(db,pid,u,owner=(action!='submit'),write=(action=='submit'))
    lock_project_write(db,pid)
    phase=project_node(db,pid,'phase',phase_id)
    if data.get('revision')!=phase.revision:
        raise HTTPException(409,'Bản phase đã thay đổi; tải lại trước khi duyệt')
    if action=='submit':
        if phase.status not in ('draft','needs_changes'):
            raise HTTPException(409,'Chỉ phase draft/needs_changes mới được gửi review')
        errors=phase_review_blockers(db,phase)
        if errors:raise HTTPException(409,{'message':'Phase chưa đủ điều kiện review','issues':errors})
        phase.status='in_review'
    elif action=='approve':
        if phase.status!='in_review':raise HTTPException(409,'Phase phải được gửi review trước')
        errors=phase_review_blockers(db,phase)
        if errors:raise HTTPException(409,{'message':'Phase chưa đủ điều kiện duyệt','issues':errors})
        phase.status='approved';phase.approved_by=u.id;phase.approved_at=utcnow()
    else:
        if phase.status not in ('in_review','approved'):
            raise HTTPException(409,'Không thể yêu cầu sửa giai đoạn này')
        if not str(data.get('comment','')).strip():
            raise HTTPException(422,'Cần nêu lý do yêu cầu chỉnh sửa')
        phase.status='needs_changes';phase.approved_by=None;phase.approved_at=None
    db.add(PhaseReview(project_id=pid,phase_id=phase_id,reviewer_id=u.id,action=action,
                       revision=phase.revision,comment=str(data.get('comment',''))[:2000]))
    audit(db,u,'phase_review',pr,{'id':phase_id,'action':action,'revision':phase.revision})
    db.commit();return {'id':phase_id,'status':phase.status,'revision':phase.revision}

# ------------------------ Research ------------------------
@app.get("/projects/{pid}/research",response_class=HTMLResponse)
def research_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    entries=db.query(ResearchItem).filter_by(project_id=pid).order_by(ResearchItem.created_at.desc()).limit(100).all()
    return page(request,"research.html",db=db,user=u,session=s,pr=pr,entries=entries,active="research")

@app.post("/api/projects/{pid}/research/search")
async def research_search(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json();query=nonempty(data,"query")[:350]
    if not query:raise HTTPException(400,"Query required")
    cfg=db.get(SystemSetting,"search")
    result=await search_searxng(cfg.value if cfg else {},query)
    for x in result:db.add(ResearchItem(project_id=pid,title=x["title"],url=x["url"],snippet=x["snippet"],query=query))
    audit(db,u,"research_search",pr,{"query":query});db.commit();return {"results":result}

@app.post("/api/projects/{pid}/research/fetch")
async def research_fetch(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json()
    browser_setting=db.get(SystemSetting,"browser")
    proxy=decrypt(browser_setting.value.get("proxy_encrypted","")) if browser_setting and browser_setting.value.get("proxy_encrypted") else None
    out=await fetch_web(nonempty(data,"url"),browser=bool(data.get("browser",False)),proxy=proxy)
    from hashlib import sha256
    name=sha256(out["url"].encode()).hexdigest()[:15]
    write_text(pid,f"research/{name}.md",f"# {out['title']}\n\nSource: {out['url']}\n\n{out['text'][:40000]}")
    db.add(ResearchItem(project_id=pid,title=out["title"],url=out["url"],snippet=out["text"][:400],query="manual fetch"))
    audit(db,u,"research_fetch",pr,{"url":out["url"]});db.commit()
    return {"title":out["title"],"url":out["url"],"text":out["text"][:24000]}

# ------------------------ Export ------------------------
@app.get("/projects/{pid}/export",response_class=HTMLResponse)
def export_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    docs=db.query(Document).filter_by(project_id=pid).all()
    return page(request,"export.html",db=db,user=u,session=s,pr=pr,docs=docs,readiness=coverage_report(db,pid),quality=quality_export(db,pid),active="export")

@app.post("/api/projects/{pid}/export")
def export_zip(pid:str,request:Request,mode:str='draft',db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u)
    if mode not in ('draft','reviewed'):
        raise HTTPException(422,'Unsupported export mode')
    if mode=='reviewed' and pr.owner_id != u.id:
        raise HTTPException(403,'Chỉ chủ sở hữu được xuất bản bàn giao đã duyệt')
    readiness=coverage_report(db,pid)
    quality=quality_export(db,pid)
    if mode=='reviewed' and quality['quality_blockers']:
        raise HTTPException(409,{'message':'Quality review còn blocker đã xác nhận hoặc workflow chưa duyệt',
                                 'issues':[{'severity':'blocker','code':'QUALITY_GATE','message':'Kiểm tra Quality Review trước khi handoff'}]})
    if mode=='reviewed' and not readiness['ready']:
        raise HTTPException(409,{'message':'Chưa đủ điều kiện bàn giao','issues':readiness['issues'][:30]})
    root=project_root(pid)
    result=io.BytesIO()
    decisions=db.query(Decision).filter_by(project_id=pid).all()
    reqs=db.query(Requirement).filter_by(project_id=pid).all()
    docs=db.query(Document).filter_by(project_id=pid).all()
    planning_questions=db.query(PlanningQuestion).filter_by(project_id=pid).order_by(PlanningQuestion.created_at).all()
    manifest={"project_id":pid,"project_name":pr.name,"profile":pr.profile,"stage":pr.stage,
              "generated_at":utcnow().isoformat()+"Z","export_mode":mode,"readiness":{'ready':readiness['ready'],'blockers':readiness['blockers'],'warnings':readiness['warnings']},"documents":[{"path":d.path,"revision":d.revision,"status":d.status} for d in docs]}
    with zipfile.ZipFile(result,"w",zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        z.writestr("planning/readiness.json",json.dumps(readiness,ensure_ascii=False,indent=2))
        z.writestr("planning/quality-review.json",json.dumps(quality,ensure_ascii=False,indent=2,default=str))
        review_jobs=db.query(ReviewJob).filter_by(project_id=pid).order_by(ReviewJob.created_at.desc()).limit(100).all()
        z.writestr("planning/review-jobs.json",json.dumps(
            [job_payload(db,job) for job in review_jobs],ensure_ascii=False,indent=2,default=str))
        z.writestr("planning/evidence-assessments.json",json.dumps([{'research_id':x.research_id,
            'source_kind':x.source_kind,'verification':x.verification,'published_on':x.published_on,
            'note':x.note} for x in db.query(EvidenceAssessment).filter_by(project_id=pid).all()],ensure_ascii=False,indent=2))
        z.writestr("planning/readiness.md",report_markdown(readiness))
        z.writestr("planning/traceability-graph.json",json.dumps(graph_payload(db,pid),ensure_ascii=False,indent=2))
        z.writestr("planning/goals.json",json.dumps([{'id':g.id,'title':g.title,'outcome':g.outcome,'metric':g.metric,'status':g.status} for g in db.query(ProjectGoal).filter_by(project_id=pid).all()],ensure_ascii=False,indent=2))
        z.writestr("planning/phases.json",json.dumps([{'id':p.id,'title':p.title,'summary':p.summary,'deliverables':p.deliverables,'acceptance':p.acceptance,'status':p.status,'revision':p.revision} for p in db.query(PlanPhase).filter_by(project_id=pid).all()],ensure_ascii=False,indent=2))
        z.writestr("decisions.json",json.dumps([{"title":x.title,"description":x.description,"status":x.status,"rationale":x.rationale} for x in decisions],ensure_ascii=False,indent=2))
        z.writestr("requirements.json",json.dumps([{"code":x.code,"title":x.title,"description":x.description,"criteria":x.criteria,"status":x.status} for x in reqs],ensure_ascii=False,indent=2))
        z.writestr("planning/decision-briefs.json",json.dumps([question_payload(q) for q in planning_questions],ensure_ascii=False,indent=2))
        brief_lines=["# Lịch sử câu hỏi và phương án lập kế hoạch", "", "Các đề xuất AI không tự động trở thành quyết định được duyệt.", ""]
        for q in planning_questions:
            brief_lines.extend([f"## {q.question}", "", f"**Trạng thái:** {q.status}", "", q.context or "", ""])
            for opt in q.options:
                marker=" (AI đề xuất)" if opt['id']==q.recommended_id else ""
                brief_lines.extend([f"- **{opt['label']}**{marker}: {opt['description']}",f"  - Ưu: {opt['pros']}",f"  - Nhược: {opt['cons']}"])
            brief_lines.extend(["", f"**Lý do AI đề xuất:** {q.recommendation_reason}", "",f"**Trả lời người dùng:** {json.dumps(q.answer or {}, ensure_ascii=False)}", "", "---", ""])
        z.writestr("planning/decision-briefs.md", "\n".join(brief_lines))
        for folder in ("docs","research","decisions","notes","memory"):
            for p in (root/folder).rglob("*"):
                if p.is_file() and not p.is_symlink() and not any(part.startswith(".") for part in p.relative_to(root).parts):
                    if p.stat().st_size<3*1024*1024:z.write(p,p.relative_to(root).as_posix())
    audit(db,u,"project_export",pr,{"documents":len(docs),"mode":mode});db.commit()
    result.seek(0)
    return StreamingResponse(result,media_type="application/zip",headers={
        "Content-Disposition":f"attachment; filename=specweave-{pid[:8]}-{mode}.zip"})


# ------------------------ Guided planning & decision briefs ------------------------
@app.get("/api/projects/{pid}/planning/questions")
def get_planning_questions(pid:str, request:Request, discussion_id:str, db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    discussion=db.get(Discussion,discussion_id)
    if not discussion or discussion.project_id!=pid:raise HTTPException(404,"Discussion not found")
    questions=db.query(PlanningQuestion).filter_by(project_id=pid,discussion_id=discussion_id).order_by(PlanningQuestion.created_at.desc()).limit(30).all()
    return {"pending":next((question_payload(q) for q in questions if q.status=="pending"),None),
            "history":[question_payload(q) for q in questions if q.status!="pending"][:15]}

@app.get("/api/projects/{pid}/planning/discovery-progress")
def discovery_progress(pid:str,request:Request,discussion_id:str,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);pr=acl(db,pid,u)
    d=db.get(Discussion,discussion_id)
    if not d or d.project_id!=pid:raise HTTPException(404,'Discussion not found')
    questions=db.query(PlanningQuestion).filter_by(project_id=pid,discussion_id=discussion_id).all()
    paths=requested_draft_paths(pr) or [path for _,path in ARTIFACTS.values()]
    existing=named_draft_records(db,pid,paths)
    state=(pr.profile or {}).get('_discovery',{})
    return {'status':'draft_ready' if state.get('status')=='draft_ready' else 'interviewing',
            'answered':sum(q.status=='answered' for q in questions),
            'pending':sum(q.status=='pending' for q in questions),
            'deferred':sum(q.status=='deferred' for q in questions),
            'drafts':len(existing),
            'total_artifacts':len(paths),
            'summary':str(state.get('summary',''))[:1200] if state.get('status')=='draft_ready' else ''}

@app.post("/api/projects/{pid}/planning/questions/{qid}/answer")
async def submit_planning_answer(pid:str, qid:str, request:Request, db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    item=db.get(PlanningQuestion,qid)
    if not item or item.project_id!=pid:raise HTTPException(404,"Question not found")
    data=await request.json()
    answer_question(db,item,data)
    audit(db,u,"planning_answer",pr,{"question_id":qid,"status":item.status})
    db.commit()
    return {"ok":True,"question":question_payload(item)}

@app.post("/api/projects/{pid}/planning/questions/suggest")
async def suggest_planning_question(pid:str,request:Request,db:Session=Depends(get_db)):
    """Generate a decision card through a one-shot Pi SDK AgentSession.

    The output remains a proposal; only humans approve project decisions.
    """
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json()
    did=str(data.get("discussion_id", ""))
    discussion=db.get(Discussion,did)
    if not discussion or discussion.project_id!=pid:raise HTTPException(404,"Discussion not found")
    existing=db.query(PlanningQuestion).filter_by(project_id=pid,discussion_id=did,status="pending").first()
    if existing:return {"question":question_payload(existing),"created":False}
    model=db.get(Model,pr.active_model_id) if pr.active_model_id else None
    if not model or not model.enabled or not model.provider.enabled:
        raise HTTPException(409,"Hãy chọn model hoạt động trong Project Settings")
    openai_oauth_access(pr,model,u.id)
    history=db.query(Message).filter_by(discussion_id=did).order_by(Message.created_at.desc()).limit(10).all()[::-1]
    system=plan_prompt(pr,db,"analyst","discovery",discussion_id=did)+"\n"+QUESTION_JSON_INSTRUCTIONS
    messages=[{"role":"system","content":system}]
    messages += [{"role":m.role,"content":m.content[:2500]} for m in history]
    messages.append({"role":"user","content":"Đọc hồ sơ dự án và đề xuất câu hỏi QUAN TRỌNG NHẤT chưa được trả lời. Kèm 2-4 phương án có phân tích và một đề xuất mặc định. Chỉ trả JSON."})
    from pi_client import complete as pi_complete
    try:
        outcome=await pi_complete(pr,model,u.id,system_context=system,
            message='\n\n'.join(f"[{m['role'].upper()}] {m['content']}" for m in messages[1:]),
            discussion_id=did)
        content=outcome['text']
        if isinstance(content,list):
            content="".join(str(x.get("text", "")) for x in content if isinstance(x,dict))
        raw=str(content).strip()
        if raw.startswith("```"):
            raw=re.sub(r"^```(?:json)?\s*|\s*```$","",raw,flags=re.I).strip()
        question_data=json.loads(raw)
        item,created=save_question(db,pid,did,question_data)
        audit(db,u,"planning_question_proposed",pr,{"question_id":item.id})
        db.commit()
        return {"question":question_payload(item),"created":created}
    except (ValueError,KeyError,IndexError,TypeError,json.JSONDecodeError) as exc:
        raise HTTPException(502,"Không tạo được phương án có cấu trúc từ model. Hãy thử model khác hoặc tiếp tục chat.") from exc

# ------------------------ Discussions & Agent ------------------------
def find_active_chat_run(db, pid, did):
    return (db.query(AgentRun).filter_by(project_id=pid,discussion_id=did)
            .filter(AgentRun.status.in_(['queued','running']))
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).first())


@app.get("/projects/{pid}/discussion",response_class=HTMLResponse)
def discussion_page(pid:str,request:Request,did:str="",db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    discussions=db.query(Discussion).filter_by(project_id=pid).order_by(Discussion.created_at.asc()).all()
    selected=db.get(Discussion,did) if did else (discussions[0] if discussions else None)
    if selected and selected.project_id!=pid:raise HTTPException(404,"Discussion not found")
    active_run=find_active_chat_run(db,pid,selected.id) if selected else None
    messages=(db.query(Message).filter_by(discussion_id=selected.id)
              .order_by(Message.created_at.desc()).limit(120).all()[::-1]) if selected else []
    if active_run:
        # This run may finish between the two reads. Replay owns its assistant
        # response on this page, so do not render a second copy from history.
        messages=[m for m in messages if not (m.run_id==active_run.id and m.role=='assistant')]
    trace_runs={r.id:r for r in (db.query(AgentRun).filter(AgentRun.id.in_([m.run_id for m in messages if m.run_id])).all()
                                  if any(m.run_id for m in messages) else [])}
    models=db.query(Model).join(Provider).filter(Model.enabled==True,Provider.enabled==True).all()
    runs=(db.query(AgentRun).filter_by(project_id=pid,discussion_id=selected.id)
          .order_by(AgentRun.created_at.desc()).limit(7).all()) if selected else []
    response=page(request,"discussion.html",db=db,user=u,session=s,pr=pr,discussions=discussions,discussion=selected,messages=messages,
                models=models,runs=runs,trace_runs=trace_runs,active_run=active_run,
                modes=MODES,roles=ROLES,active="discussion")
    response.headers['Cache-Control']='no-store'
    return response

@app.post("/api/projects/{pid}/discussions")
async def new_discussion(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json();mode=data.get("mode","discovery")
    if mode not in MODES:mode="discovery"
    d=Discussion(project_id=pid,title=str(data.get("title","Thảo luận mới"))[:200],mode=mode)
    db.add(d);audit(db,u,"discussion_create",pr);db.commit()
    return {"id":d.id,"redirect":f"/projects/{pid}/discussion?did={d.id}"}

@app.post("/api/projects/{pid}/chat")
async def chat_stream(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    return await _chat_stream_impl(pid, db, u, pr, await request.json())


@app.post("/api/projects/{pid}/chat/start")
async def chat_start(pid:str,request:Request,db:Session=Depends(get_db)):
    """Start a durable Pi run. The tab is a subscriber, never the job owner."""
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json()
    if not isinstance(data,dict):raise HTTPException(422,'Invalid chat payload')
    return await _chat_stream_impl(pid,db,u,pr,data,queue_only=True)


@app.post("/api/projects/{pid}/planning/questions/{qid}/answer-start")
async def answer_and_start(pid:str,qid:str,request:Request,db:Session=Depends(get_db)):
    """Atomically accept one answer and enqueue its Pi continuation."""
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    payload=await request.json()
    if not isinstance(payload,dict) or payload.get('action','answer')!='answer':
        raise HTTPException(422,'Invalid decision answer')
    lock_project_write(db,pid)
    q=(db.query(PlanningQuestion).filter_by(id=qid,project_id=pid)
       .with_for_update().one_or_none())
    if not q or q.status!='pending':
        raise HTTPException(409,'Câu hỏi đã được trả lời hoặc không tồn tại')
    model=db.get(Model,pr.active_model_id) if pr.active_model_id else None
    if not model or not model.enabled or not model.provider.enabled:
        raise HTTPException(409,'Hãy chọn model trước khi trả lời')
    openai_oauth_access(pr,model,u.id)
    answer_question(db,q,payload,commit=False)
    audit(db,u,'planning_answer_background',pr,{'question_id':qid})
    return await _chat_stream_impl(pid,db,u,pr,{
        'discussion_id':q.discussion_id,'continue_question_id':qid,
        'role':payload.get('role','analyst'),'mode':'discovery',
        'auto_next':payload.get('auto_next') is True},queue_only=True)


@app.get("/api/projects/{pid}/discussions/{did}/active-run")
def active_discussion_run(pid:str,did:str,request:Request,response:Response,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    d=db.get(Discussion,did)
    if not d or d.project_id!=pid:raise HTTPException(404,'Discussion not found')
    run=find_active_chat_run(db,pid,did)
    response.headers['Cache-Control']='no-store'
    return {'run_id':run.id,'status':run.status} if run else {'run_id':None,'status':'idle'}


@app.get("/api/projects/{pid}/runs/{run_id}/events")
def detached_run_events(pid:str,run_id:str,request:Request,after:int=0,db:Session=Depends(get_db)):
    """Replayable SSE: persisted event IDs, auto-reconnect and no model lifecycle coupling."""
    u,s=api_guard(request,db);acl(db,pid,u)
    run=db.get(AgentRun,run_id)
    if not run or run.project_id!=pid:raise HTTPException(404,'Run not found')
    header=request.headers.get('last-event-id','')
    if len(header)<=10 and header.isascii() and header.isdigit():after=max(after,int(header))
    after=max(0,min(after,2147483647))
    # Revalidate access periodically even on a long-lived SSE connection.
    subscriber_id=u.id
    subscriber_session_id=s.id
    db.close()
    async def frames():
        cursor=after
        last_heartbeat=time.monotonic()
        last_acl=0.0
        while True:
            if await request.is_disconnected():break
            if time.monotonic()-last_acl>10:
                with SessionLocal() as authdb:
                    viewer=authdb.get(User,subscriber_id)
                    auth_session=authdb.get(AuthSession,subscriber_session_id)
                    target=authdb.get(Project,pid)
                    member=(authdb.query(Membership.id).filter_by(project_id=pid,user_id=subscriber_id).first())
                    allowed=(bool(viewer and viewer.enabled and not viewer.must_change_password and auth_session and
                        auth_session.revoked_at is None and auth_session.expires_at>=utcnow() and
                        target and target.status not in TRASH_STATUSES and
                        (target.owner_id==subscriber_id or member)))
                if not allowed:break
                last_acl=time.monotonic()
            with SessionLocal() as conn:
                # Read status first. A terminal status and its durable frame
                # are committed together, and the next query must see that frame.
                latest=conn.get(AgentRun,run_id)
                if not latest:return
                status=latest.status
                rows=(conn.query(ChatStreamEvent)
                    .filter(ChatStreamEvent.run_id==run_id,ChatStreamEvent.id>cursor)
                    .order_by(ChatStreamEvent.id).limit(150).all())
                entries=[(row.id,row.event) for row in rows]
            for seq,event in entries:
                cursor=seq
                yield 'id: '+str(seq)+'\ndata: '+json.dumps(event,ensure_ascii=False)+'\n\n'
                if event.get('type') in ('done','error'):return
            if entries:
                last_heartbeat=time.monotonic()
                continue
            if status in ('completed','cancelled','error'):
                # Catch legacy runs whose terminal event was not written by the worker.
                with SessionLocal() as conn:
                    tail=(conn.query(ChatStreamEvent).filter_by(run_id=run_id)
                          .order_by(ChatStreamEvent.id.desc()).first())
                    assistant=(conn.query(Message).filter_by(run_id=run_id,role='assistant').first()
                               if status=='completed' else None)
                if not tail or tail.event.get('type') not in ('done','error'):
                    if status=='completed':
                        if assistant:
                            final={'type':'final','run_id':run_id,'payload':{'text':assistant.content}}
                            yield 'id:\ndata: '+json.dumps(final,ensure_ascii=False)+'\n\n'
                        event={'type':'done','run_id':run_id,'payload':{'duration_ms':latest.duration_ms,
                               'first_token_ms':latest.first_token_ms,'usage':latest.usage or {}}}
                    else:
                        event={'type':'error','run_id':run_id,'payload':{'message':latest.error or status,'status':status}}
                    yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
                break
            if time.monotonic()-last_heartbeat>=12:
                yield ': keepalive\n\n'
                last_heartbeat=time.monotonic()
            await asyncio.sleep(.35)
    return StreamingResponse(frames(),media_type='text/event-stream',headers={
        'Cache-Control':'no-store, no-transform','X-Accel-Buffering':'no'})


@app.post("/api/projects/{pid}/planning/questions/{qid}/answer-stream")
async def answer_and_stream(pid:str,qid:str,request:Request,db:Session=Depends(get_db)):
    """One atomic user interaction: persist the choice AND start Pi SSE.

    Previously these were two independent frontend requests; a dropped second
    request left a user answer in chat with no assistant response.
    """
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    payload=await request.json()
    if not isinstance(payload,dict) or payload.get('action','answer')!='answer':
        raise HTTPException(422,'Chỉ câu trả lời được tiếp tục qua SSE')
    q=(db.query(PlanningQuestion).filter_by(id=qid,project_id=pid)
       .with_for_update().one_or_none())
    if not q or q.status!='pending':
        raise HTTPException(409,'Câu hỏi đã xử lý hoặc không tồn tại')
    model=db.get(Model,pr.active_model_id) if pr.active_model_id else None
    if not model or not model.enabled or not model.provider.enabled:
        raise HTTPException(409,'Hãy chọn model hoạt động trước khi trả lời')
    openai_oauth_access(pr,model,u.id)
    # The question already owns its discussion_id: never trust one from the browser.
    answer_question(db,q,payload)
    audit(db,u,'planning_answer_stream',pr,{'question_id':qid})
    db.commit()
    return await _chat_stream_impl(pid,db,u,pr,{
        'discussion_id':q.discussion_id,'continue_question_id':qid,
        'role':payload.get('role','analyst'), 'mode':'discovery'})


async def _chat_stream_impl(pid, db, u, pr, data, *, queue_only=False, existing_run_id=None):
    did=str(data.get("discussion_id",""))
    discussion=db.get(Discussion,did)
    if not discussion or discussion.project_id!=pid:raise HTTPException(404,"Discussion not found")
    model=db.get(Model,pr.active_model_id) if pr.active_model_id else None
    if not model or not model.enabled or not model.provider.enabled:
        raise HTTPException(409,"Chưa chọn model hoạt động. Vào Settings → Providers rồi chọn model cho dự án.")
    openai_oauth_access(pr,model,u.id)
    if model.provider.kind=='openai-oauth':
        status=await openai_oauth_relay('GET','/oauth/openai/status')
        if not status.get('connected'):raise HTTPException(409,'OpenAI chưa được kết nối trên Pi runtime')
    content=str(data.get("message","")).strip()
    continue_qid=str(data.get('continue_question_id') or '').strip()
    advance=bool(data.get('advance_planning',False))
    if advance and (continue_qid or content):
        raise HTTPException(422,'Automatic next step must not include a user message')
    if advance:
        if str(data.get('mode',discussion.mode))!='discovery':
            raise HTTPException(422,'Only discovery can advance automatically')
        pending=db.query(PlanningQuestion).filter_by(project_id=pid,discussion_id=did,status='pending').first()
        if pending:
            raise HTTPException(409,'Hãy trả lời câu hỏi đang chờ trước')
        if (pr.profile or {}).get('_discovery',{}).get('status')=='draft_ready':
            raise HTTPException(409,'Discovery đã hoàn tất; xem tài liệu draft')
    if not existing_run_id:
        lock_project_write(db,pid)
    if continue_qid:
        if content:
            raise HTTPException(422, 'Continuation cannot include a second user message')
        q=(db.query(PlanningQuestion).filter_by(id=continue_qid,project_id=pid,
                                               discussion_id=did).with_for_update().one_or_none())
        if not q or q.status!='answered':
            raise HTTPException(409,'Câu trả lời không còn ở trạng thái có thể tiếp tục')
        previous=(q.answer or {}).get('continuation_run_id')
        if previous and previous!=existing_run_id:
            raise HTTPException(409,'Câu trả lời này đã được gửi cho AI trước đó')
    elif not advance and not 1<=len(content)<=30000:
        raise HTTPException(422,"Nội dung phải từ 1 đến 30.000 ký tự")
    role=str(data.get("role","analyst"))
    mode=str(data.get("mode",discussion.mode))
    if role not in ROLES:role="analyst"
    if mode not in MODES:mode="discovery"
    if queue_only:
        # Serialize enqueue by project row. Only one pending/running Pi run per
        # discussion: prevents double clicks and cross-worker race conditions.
        busy=(db.query(AgentRun).filter_by(project_id=pid,discussion_id=did)
              .filter(AgentRun.status.in_(['queued','running'])).first())
        if busy:raise HTTPException(409,'Phiên đang có Pi run; hãy theo dõi run hiện tại')
    run=(db.get(AgentRun,existing_run_id) if existing_run_id else
         AgentRun(project_id=pid,discussion_id=did,model_name=model.model_id,
           thinking=pr.thinking_level,status='queued' if queue_only else 'running',
           requester_id=u.id,request_payload={**dict(data),'_active_model_id':model.id}))
    if not existing_run_id:db.add(run)
    db.flush()
    if continue_qid and not existing_run_id:
        # Persist the correlation so browser reconnect never resends the choice.
        q.answer={**(q.answer or {}),'continuation_run_id':run.id}
    if queue_only:
        if content:
            db.add(Message(discussion_id=did,role='user',content=content,run_id=run.id))
        db.add(ChatStreamEvent(run_id=run.id,event={'type':'status','run_id':run.id,
               'payload':{'phase':'queued','message':'Pi đã nhận công việc, đang đợi worker…'}},created_at=utcnow()))
    db.commit()
    run_id=run.id
    if queue_only:
        return {'run_id':run_id,'status':'queued','events_url':f'/api/projects/{pid}/runs/{run_id}/events'}
    backend='pi'
    actor_id=u.id
    worker_owner=run.worker_owner

    def locked_run(sdb):
        current=sdb.get(AgentRun,run_id,populate_existing=True,with_for_update=True)
        if current and current.status=='running' and current.worker_owner==worker_owner:
            return current
        return None

    def frame(kind,payload,**extra):
        return {'type':kind,'payload':payload,'run_id':run_id,**extra}

    def wire(event):
        return 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'

    def finish(sdb,current,event):
        if existing_run_id:
            current.worker_owner=None
            current.lease_expires_at=None
            sdb.add(ChatStreamEvent(run_id=run_id,event=event))
        sdb.commit()
        return wire(event)

    async def produce():
        with SessionLocal() as sdb:
            p=sdb.get(Project,pid);d=sdb.get(Discussion,did);m=sdb.get(Model,p.active_model_id)
            try:
                async with discussion_lease(did):
                    if content and not sdb.query(Message).filter_by(discussion_id=did,run_id=run_id,role='user').first():
                        sdb.add(Message(discussion_id=did,role="user",content=content,run_id=run_id))
                        sdb.commit()
                    async with aclosing(produce_locked(sdb,p,d,m)) as events:
                        async for event in events:
                            yield event
            except ChatBusy:
                # A previous Pi run may have committed its terminal status while
                # still holding the advisory lock. Do NOT fail the next run: the
                # user has already submitted it and the Pi prompt has not run.
                obj=locked_run(sdb)
                if not obj:
                    return
                if existing_run_id and not obj.cancel_requested:
                    obj.status='queued'
                    obj.worker_owner=None
                    obj.lease_expires_at=utcnow()+timedelta(seconds=3)
                    sdb.add(ChatStreamEvent(run_id=run_id,event=frame('status',{'phase':'waiting_lock',
                        'message':'Pi đang hoàn tất lượt trước; tự chờ để tiếp tục…'})))
                    sdb.commit()
                    return
                message='Đã hủy lượt Pi' if obj.cancel_requested else 'Một lượt thảo luận đang chạy'
                obj.status='cancelled' if obj.cancel_requested else 'error'
                obj.error=message;obj.finished_at=utcnow()
                clear_chat_continuation(sdb,obj)
                yield finish(sdb,obj,frame('error',{'message':message,'status':obj.status}))

    async def produce_locked(sdb,p,d,m):
        full=[]
        trace=[]
        clock=time.monotonic()
        first_token_ms=None
        usage={}
        thinking_trace=None
        document_output_limited=False
        def emit(event_type,payload=None,**extra):
            return wire(frame(event_type,payload or {},**extra))
        try:
            if cancellation_requested(run_id):
                raise ChatStopped()
            yield emit('start',{'model':m.model_id,'backend':backend,'context_window':m.context_window,
                                'continued':bool(continue_qid),'automatic':advance})
            yield emit('status',{'phase':'request','message':'Pi đang tiếp nhận ngữ cảnh dự án…'})
            advance_prompt=(
                'Tiếp tục discovery trong CÙNG session. Đọc câu trả lời mới nhất và lịch sử, '
                'đánh giá những vấn đề chưa rõ. Nếu chưa đủ thông tin, gọi tool '
                'propose_planning_question cho MỘT câu hỏi quan trọng tiếp theo, có các phương án cụ thể. '
                'Nếu thực sự đã đủ dữ liệu để soạn đặc tả có giá trị, sử dụng tool '
                'write_document, mỗi lệnh chỉ ghi một phần 400–800 ký tự. Nối phần sau bằng '
                'append_to_revision theo revision đã lưu. Khi đủ bản nháp, gọi finish_discovery. '
                'Không tự phê duyệt tài liệu. Nếu model không gọi được tool, hãy trả về '
                'DUY NHẤT một JSON object cho câu hỏi theo schema sau: '
                + QUESTION_JSON_INSTRUCTIONS
            )

            async def stream_attempt(prompt):
                nonlocal first_token_ms, thinking_trace, full, document_output_limited
                attempt_usage={}
                pi_kwargs={'role':role,'mode':mode,'run_id':run_id,'actor_id':actor_id}
                if prompt is not None:
                    pi_kwargs['prompt_override']=prompt
                stream=pi_stream(p,d,sdb,m,**pi_kwargs)
                async with aclosing(stream), aclosing(with_heartbeat(stream)) as events:
                    async for typ,payload in events:
                        if cancellation_requested(run_id):
                            raise ChatStopped()
                        if typ in ('delta','thinking'):
                            part=payload.get('text','')
                            if typ=='delta':
                                full.append(part)
                            elif isinstance(part,str) and part:
                                if thinking_trace is None and len(trace)<64:
                                    thinking_trace={'type':'thinking','text':'','at_ms':int((time.monotonic()-clock)*1000)}
                                    trace.append(thinking_trace)
                                if thinking_trace is not None:
                                    remaining=50_000-len(thinking_trace['text'])
                                    if remaining>0: thinking_trace['text']+=part[:remaining]
                            if first_token_ms is None and part:
                                first_token_ms=int((time.monotonic()-clock)*1000)
                                yield emit('timing',{'first_token_ms':first_token_ms})
                        elif typ=='final':
                            txt=payload.get('text','')
                            if txt:full=[txt]
                        elif typ=='usage':
                            for k,v in payload.items():
                                if v is not None:
                                    attempt_usage[k]=v
                        elif typ=='error':
                            raise RuntimeError(payload.get('message','Lỗi Pi Runner'))
                        if typ=='status' and payload.get('phase')=='document_output_limit':
                            document_output_limited=True
                        if typ in ('status','tool') and len(trace)<64:
                            item={'type':typ,'at_ms':int((time.monotonic()-clock)*1000)}
                            if typ=='status':item['message']=str(payload.get('message',''))[:220]
                            else:item.update({'name':str(payload.get('name',''))[:80],
                                              'id':str(payload.get('id',''))[:100],
                                              'status':str(payload.get('status',''))[:30],
                                              'summary':str(payload.get('summary',''))[:200],
                                              'arguments':str(payload.get('arguments',''))[:8000],
                                              'result':str(payload.get('result',''))[:8000]})
                            trace.append(item)
                        if typ != 'final':
                            yield emit(typ,payload)
                for key, value in attempt_usage.items():
                    if isinstance(value,(int,float)) and isinstance(usage.get(key),(int,float)):
                        usage[key]+=value
                    else:
                        usage[key]=value

            def resolve_planning_output():
                raw=''.join(full).strip()
                output, brief=parse_model_response(raw,interactive=bool(existing_run_id and mode=='discovery'))
                sdb.expire_all()
                pending=sdb.query(PlanningQuestion).filter_by(
                    project_id=pid,discussion_id=did,status='pending').order_by(PlanningQuestion.created_at.desc()).first()
                discovery=(sdb.get(Project,pid).profile or {}).get('_discovery',{})
                if brief and not pending and discovery.get('status')!='draft_ready':
                    try:
                        pending,_=save_question(sdb,pid,did,brief)
                    except (ValueError,TypeError,KeyError):
                        output=parse_model_response(raw)[0]
                return output, pending, discovery

            async with aclosing(stream_attempt(advance_prompt if advance else None)) as events:
                async for event in events:
                    yield event
            output,pending,discovery=resolve_planning_output()
            auto_next=bool(existing_run_id and data.get('auto_next') is True and mode=='discovery' and not advance)
            retry_document=bool(existing_run_id and advance and document_output_limited)
            if (auto_next or retry_document) and not pending and discovery.get('status')!='draft_ready':
                next_prompt=advance_prompt
                next_status='Pi đang chuẩn bị câu hỏi tiếp theo…'
                if document_output_limited:
                    paths=requested_draft_paths(sdb.get(Project,pid))
                    stored=named_draft_records(sdb,pid,paths)
                    missing=', '.join(path.rsplit('/',1)[-1] for path in paths if path.lower() not in stored)
                    next_prompt=(
                        'Lệnh bàn giao trước bị cắt do giới hạn đầu ra. Tiếp tục trong cùng phiên từ các kết quả đã lưu. '
                        f'Bản nháp được yêu cầu còn thiếu: {missing or "không có tên nào được liệt kê"}. '
                        'Gọi từng lệnh write_document với đúng một phần Markdown dài 400–800 ký tự, '
                        'sau đó dùng append_to_revision để nối phần tiếp theo. Giữ nội dung đã lưu. '
                        'Khi đủ bản nháp và không còn vấn đề quan trọng, gọi finish_discovery với tóm tắt và bằng chứng ngắn.'
                    )
                    next_status='Pi đang thử lại với phần tài liệu ngắn hơn…'
                # Keep the follow-up in this durable run and Pi session.
                first_output=output
                yield emit('final',{'text':first_output+'\n\n' if first_output else ''})
                yield emit('status',{'phase':'request','message':next_status})
                full=[]
                thinking_trace=None
                try:
                    async with aclosing(stream_attempt(next_prompt)) as events:
                        async for event in events:
                            yield event
                except ChatStopped:
                    raise
                except Exception:
                    # The first prompt already ran. Keep its result instead of
                    # making the saved answer retryable and repeating tool calls.
                    full=[]
                    yield emit('status',{'phase':'request','message':'Pi chưa thể tiếp tục Discovery.'})
                next_output,pending,discovery=resolve_planning_output()
                output='\n\n'.join(part for part in (first_output,next_output) if part)
            if not output:
                output=('Chọn một phương án để tiếp tục.' if pending else '(Không có nội dung phản hồi)')
            if (advance or auto_next) and not pending and discovery.get('status')!='draft_ready':
                yield emit('planning_needs_input')
            if pending:
                yield emit('planning_question',question_payload(pending))
            if discovery.get('status')=='draft_ready':
                yield emit('planning_complete',{'status':'draft_ready','summary':discovery.get('summary',''),
                                                'url':f'/projects/{pid}/artifacts'})
            yield emit('final', {'text':output})
            run=locked_run(sdb)
            if not run:
                return
            if run.cancel_requested:
                raise ChatStopped()
            sdb.add(Message(discussion_id=did,role='assistant',content=output,run_id=run_id))
            run.status='completed';run.finished_at=utcnow()
            run.usage=usage;run.events=trace;run.first_token_ms=first_token_ms;run.duration_ms=int((time.monotonic()-clock)*1000)
            seconds=max(0,(run.duration_ms-first_token_ms)/1000) if first_token_ms is not None else 0
            output_tps=round(usage['output_tokens']/seconds,2) if seconds>0 and isinstance(usage.get('output_tokens'),int) else None
            yield finish(sdb,run,frame('done',{'duration_ms':run.duration_ms,'first_token_ms':first_token_ms,
                                               'usage':usage,'output_tps':output_tps}))
        except ChatBusy:
            raise
        except (asyncio.CancelledError, GeneratorExit, Exception) as exc:
            sdb.rollback()
            run=locked_run(sdb)
            if run:
                stopped=run.cancel_requested or not existing_run_id and isinstance(exc,(asyncio.CancelledError,GeneratorExit))
                message=('Đã dừng lượt chạy' if stopped else
                         'Pi worker bị gián đoạn' if isinstance(exc,(asyncio.CancelledError,GeneratorExit))
                         else str(exc)[:500])
                run.status='cancelled' if stopped else 'error'
                run.events=trace;run.error=message;run.finished_at=utcnow()
                run.duration_ms=int((time.monotonic()-clock)*1000)
                clear_chat_continuation(sdb,run)
                result=finish(sdb,run,frame('error',{'message':message,'status':run.status},message=message))
            if isinstance(exc,(GeneratorExit,asyncio.CancelledError)):
                raise
            if run:
                yield result
    return StreamingResponse(produce(),media_type="text/event-stream",headers={"Cache-Control":"no-cache, no-transform","X-Accel-Buffering":"no"})


# Internal Pi bridge: explicit allowlist and per-run HMAC capability, never arbitrary FS/shell.
PI_TOOL_ALLOWLIST = {tool["function"]["name"] for tool in TOOLS}

@app.post("/api/internal/pi-tool",include_in_schema=False)
async def internal_pi_tool(request: Request, db: Session = Depends(get_db)):
    run_id=request.headers.get("x-specweave-run", "")
    token=request.headers.get("x-specweave-token", "")
    if not re.fullmatch(r"[0-9a-f-]{36}",run_id) or len(token)!=64:
        raise HTTPException(401, "Invalid Pi tool capability")
    run=db.get(AgentRun,run_id)
    if not run or run.status!="running" or not run.created_at or (utcnow()-run.created_at).total_seconds()>1800:
        raise HTTPException(401, "Pi run is not active")
    try:
        expected=pi_tool_token(run.id,run.project_id,run.discussion_id,run.created_at)
    except RuntimeError:
        raise HTTPException(503, "Pi tool bridge is not configured")
    if not hmac.compare_digest(token,expected):
        raise HTTPException(401, "Invalid Pi tool capability")
    project=db.get(Project,run.project_id)
    if not project or project.status!="active":
        raise HTTPException(409, "Project is not active")
    raw=await request.body()
    if len(raw)>100000:
        raise HTTPException(413, "Pi tool arguments too large")
    try:
        payload=json.loads(raw)
    except (ValueError,UnicodeError):
        raise HTTPException(400, "Invalid Pi tool payload")
    if not isinstance(payload,dict):
        raise HTTPException(422,"Invalid Pi tool payload")
    name=payload.get("name", "")
    args=payload.get("args", {})
    if name not in PI_TOOL_ALLOWLIST or not isinstance(args,dict):
        raise HTTPException(403, "Tool is not allowed")
    if name in ('write_document','finish_discovery'):
        lock_project_write(db,project.id)
    # Tool implementation enforces project-scoped paths, draft-only writes, and SSRF protections.
    try:
        result=await tool_call(name,args,project=project,db=db,discussion_id=run.discussion_id)
    except HTTPException as exc:
        raise HTTPException(exc.status_code,"Pi tool rejected the request")
    except Exception as exc:
        # Return exception category without internal server/provider details.
        raise HTTPException(502, f"Pi tool failed ({type(exc).__name__})")
    data=json.dumps(result,ensure_ascii=False,default=str)
    if len(data)>25000:
        return {"truncated":True,"content":data[:24500]}
    return result

@app.post("/api/projects/{pid}/runs/{run_id}/cancel")
async def cancel_run(pid:str,run_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);acl(db,pid,u,write=True)
    run=db.get(AgentRun,run_id,with_for_update=True)
    if not run or run.project_id!=pid:raise HTTPException(404,"Run not found")
    if run.status=='queued':
        run.cancel_requested=True
        run.status='cancelled';run.finished_at=utcnow()
        run.lease_expires_at=None
        clear_chat_continuation(db,run)
        db.add(ChatStreamEvent(run_id=run.id,event={'type':'error','run_id':run.id,
             'payload':{'message':'Đã hủy trước khi Pi bắt đầu','status':'cancelled'}},created_at=utcnow()))
        db.commit()
    elif run.status=='running':
        run.cancel_requested=True
        db.commit()
        from pi_client import abort as pi_abort
        await pi_abort(pid,run.discussion_id,run_id)
    return {"ok":True,"status":run.status,"cancel_requested":bool(run.cancel_requested)}

@app.get("/api/projects/{pid}/discussions/{did}/messages")
def message_history(pid:str,did:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    d=db.get(Discussion,did)
    if not d or d.project_id!=pid:raise HTTPException(404,"Not found")
    return [{"role":m.role,"content":m.content,"created_at":m.created_at.isoformat()} for m in db.query(Message).filter_by(discussion_id=did).order_by(Message.created_at).limit(150).all()]

# ------------------------ Provider & Global Settings ------------------------
@app.get("/settings/providers",response_class=HTMLResponse)
def providers_page(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    admin_only(u)
    providers=[p for p in db.query(Provider).order_by(Provider.created_at).all()
               if p.kind!='openai-oauth' or p.owner_id==u.id]
    allowed={p.id for p in providers}
    models=[m for m in db.query(Model).order_by(Model.name).all() if m.provider_id in allowed]
    return page(request,"providers.html",db=db,user=u,session=s,providers=providers,models=models,oauth_local=openai_oauth_enabled(),active="providers")

# Only OpenAI subscription OAuth is exposed. Other providers remain API-key based.
def _owner_openai_provider(db,u):
    found=db.query(Provider).filter_by(kind='openai-oauth').first()
    if found and found.owner_id!=u.id:
        raise HTTPException(403,'Local OpenAI OAuth connection belongs to another user')
    return found

def _oauth_admin(request,db,mutate=False):
    u,s=api_guard(request,db,mutate)
    admin_only(u)
    if not openai_oauth_enabled():
        raise HTTPException(503,'OpenAI OAuth is enabled only in owner-controlled LOCAL mode')
    return u

@app.get('/api/openai/oauth/status')
async def oauth_status(request:Request,db:Session=Depends(get_db)):
    u=_oauth_admin(request,db)
    provider=_owner_openai_provider(db,u)
    state=await openai_oauth_relay('GET','/oauth/openai/status')
    return {**state,'provider_id':provider.id if provider else None,
            'provider_enabled':provider.enabled if provider else False}

@app.get('/api/openai/oauth/diagnostics')
async def oauth_diagnostics(request:Request,db:Session=Depends(get_db)):
    _oauth_admin(request,db)
    return await openai_oauth_relay('GET','/oauth/openai/diagnostics',timeout=22)

@app.post('/api/openai/oauth/start')
async def oauth_start(request:Request,db:Session=Depends(get_db)):
    u=_oauth_admin(request,db,True)
    provider=_owner_openai_provider(db,u)
    result=await openai_oauth_relay('POST','/oauth/openai/start',{},timeout=20)
    if provider is None:
        provider=Provider(name='OpenAI (ChatGPT OAuth)',kind='openai-oauth',
                          base_url='pi://openai',encrypted_key='',owner_id=u.id,enabled=False)
        db.add(provider);db.flush()
    audit(db,u,'openai_oauth_login_started',details={'provider_id':provider.id})
    db.commit()
    return {**result,'provider_id':provider.id}

@app.get('/api/openai/oauth/login/{login_id}')
async def oauth_poll(login_id:str,request:Request,offset:int=0,db:Session=Depends(get_db)):
    u=_oauth_admin(request,db)
    _owner_openai_provider(db,u)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{20,80}',login_id):raise HTTPException(404,'Login not found')
    return await openai_oauth_relay('GET',f'/oauth/openai/login/{login_id}?offset={max(0,min(offset,10000))}')

@app.post('/api/openai/oauth/login/{login_id}/answer')
async def oauth_answer(login_id:str,request:Request,db:Session=Depends(get_db)):
    u=_oauth_admin(request,db,True)
    _owner_openai_provider(db,u)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{20,80}',login_id):raise HTTPException(404,'Login not found')
    body=await request.json()
    return await openai_oauth_relay('POST',f'/oauth/openai/login/{login_id}/answer',
                                   {'value':str(body.get('value',''))[:8192]})

@app.post('/api/openai/oauth/cancel')
async def oauth_cancel(request:Request,db:Session=Depends(get_db)):
    u=_oauth_admin(request,db,True)
    _owner_openai_provider(db,u)
    return await openai_oauth_relay('POST','/oauth/openai/cancel',{})

@app.post('/api/openai/oauth/activate')
async def oauth_activate(request:Request,db:Session=Depends(get_db)):
    u=_oauth_admin(request,db,True)
    provider=_owner_openai_provider(db,u)
    if not provider:raise HTTPException(404,'OpenAI OAuth provider not created')
    status=await openai_oauth_relay('GET','/oauth/openai/status')
    if not status.get('connected'):raise HTTPException(409,'OpenAI OAuth is not connected')
    provider.enabled=True
    audit(db,u,'openai_oauth_connected',details={'provider_id':provider.id})
    db.commit()
    return {'ok':True,'provider_id':provider.id}

@app.post('/api/openai/oauth/disconnect')
async def oauth_disconnect(request:Request,db:Session=Depends(get_db)):
    u=_oauth_admin(request,db,True)
    provider=_owner_openai_provider(db,u)
    result=await openai_oauth_relay('POST','/oauth/openai/disconnect',{})
    if provider:
        provider.enabled=False
        audit(db,u,'openai_oauth_disconnected',details={'provider_id':provider.id})
        db.commit()
    return result

@app.post("/api/providers")
async def add_provider(request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    data=await request.json()
    name=nonempty(data,"name")[:120];base=nonempty(data,"base_url")[:1024]
    if not name or not base.startswith(("http://","https://")):
        raise HTTPException(422,"Provider name hoặc base URL không hợp lệ")
    kind=data.get("kind","openai-compatible")
    if kind not in ("openai","openai-compatible"):raise HTTPException(422,"Unsupported provider protocol")
    p=Provider(name=name,base_url=base,kind=kind,encrypted_key=encrypt(str(data.get("api_key", ""))))
    db.add(p);audit(db,u,"provider_create");db.commit();return {"id":p.id}

@app.post("/api/providers/{provider_id}")
async def update_provider(provider_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    p=db.get(Provider,provider_id)
    if not p:raise HTTPException(404,"Provider not found")
    data=await request.json()
    if p.kind=='openai-oauth':
        if p.owner_id!=u.id:raise HTTPException(403,'Not your OpenAI connection')
        if any(k in data for k in ('api_key','clear_api_key','kind','base_url')):
            raise HTTPException(422,'OpenAI OAuth credentials and protocol are managed separately')
    if "name" in data:
        name=str(data["name"]).strip()[:120]
        if not name:raise HTTPException(422,"Tên provider không được trống")
        p.name=name
    if "base_url" in data:
        base=str(data["base_url"]).strip()
        if not base.startswith(("http://","https://")):raise HTTPException(400,"Invalid URL")
        p.base_url=base[:1024]
    if "api_key" in data and data["api_key"]:p.encrypted_key=encrypt(data["api_key"])
    if data.get('clear_api_key') is True:
        if data.get('api_key'):raise HTTPException(422,'Cannot set and clear API key together')
        p.encrypted_key=''
    if "kind" in data:
        if data['kind'] not in ('openai','openai-compatible'):raise HTTPException(422,'Unsupported protocol')
        p.kind=data['kind']
    if "enabled" in data:
        if not isinstance(data['enabled'],bool):raise HTTPException(422,'Invalid enabled flag')
        p.enabled=data['enabled']
    audit(db,u,"provider_update");db.commit();return {"ok":True}

@app.get('/api/providers/{provider_id}/delete-preview')
def provider_delete_preview(provider_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);admin_only(u)
    provider=owned_provider(db,provider_id,u)
    return provider_impact(db,provider)

@app.delete('/api/providers/{provider_id}')
async def remove_provider(provider_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    provider=owned_provider(db,provider_id,u)
    body=await request.json()
    if not isinstance(body,dict):raise HTTPException(422,'Invalid delete confirmation')
    # Local OAuth secrets are NOT stored in Provider. For OAuth connections,
    # disconnect first to guarantee no credential remains in the local vault.
    if provider.kind=='openai-oauth':
        state=await openai_oauth_relay('GET','/oauth/openai/status')
        if state.get('connected'):
            raise HTTPException(409,'Ngắt kết nối OpenAI OAuth trước khi xóa provider')
    result=perform_provider_deletion(db,provider,u,body.get('confirm_name',''))
    audit(db,u,'provider_delete',details={'provider_id':provider_id,**result})
    db.commit()
    return result

@app.get('/api/providers/{provider_id}/models/{mid}/delete-preview')
def model_delete_preview(provider_id:str,mid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);admin_only(u)
    provider=owned_provider(db,provider_id,u)
    model=db.get(Model,mid)
    if not model or model.provider_id!=provider_id:raise HTTPException(404,'Model not found')
    return {**provider_impact(db,provider,[model]),'model_name':model.model_id}

@app.delete('/api/providers/{provider_id}/models/{mid}')
async def remove_model(provider_id:str,mid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    provider=owned_provider(db,provider_id,u)
    model=db.get(Model,mid)
    if not model or model.provider_id!=provider_id:raise HTTPException(404,'Model not found')
    body=await request.json()
    if not isinstance(body,dict) or body.get('confirm_model')!=model.model_id:
        raise HTTPException(422,'Nhập chính xác Model ID để xác nhận xóa')
    result=perform_model_deletion(db,provider,[model])
    audit(db,u,'model_delete',details={'provider_id':provider.id,'model_id':mid,**result})
    db.commit()
    return result

@app.post("/api/providers/{provider_id}/models")
async def add_models(provider_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    p=db.get(Provider,provider_id)
    if not p:raise HTTPException(404,"Provider not found")
    data=await request.json();models=data.get("model_ids",[])
    if not isinstance(models,list) or len(models)>100:raise HTTPException(422,"Invalid model list")
    if p.kind=='openai-oauth':
        if p.owner_id!=u.id:raise HTTPException(403,'Not your OpenAI connection')
        catalog=await openai_oauth_relay('GET','/oauth/openai/models')
        known=set(catalog.get('models',[]))
        if not known or any(not isinstance(x,str) or x.strip() not in known for x in models):
            raise HTTPException(422,'Choose OpenAI OAuth models from the actual Pi catalog')
    created=[]
    for name in models:
        name=str(name).strip()[:300]
        if not name:continue
        if not db.query(Model).filter_by(provider_id=provider_id,model_id=name).first():
            m=Model(provider_id=provider_id,model_id=name,name=name,reasoning=bool(data.get("reasoning",False)),
                    context_window=int(data.get("context_window",32768)))
            db.add(m);created.append(name)
    audit(db,u,"models_create",details={"count":len(created)});db.commit();return {"created":created}

@app.get("/api/providers/{provider_id}/discover")
async def discover_models(provider_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);admin_only(u)
    p=db.get(Provider,provider_id)
    if not p:raise HTTPException(404,"Provider not found")
    if p.kind=='openai-oauth':
        if p.owner_id!=u.id:raise HTTPException(403,'Not your OpenAI connection')
        return await openai_oauth_relay('GET','/oauth/openai/models')
    base=p.base_url.rstrip("/")
    if base.endswith("/chat/completions"):base=base[:-len("/chat/completions")]
    url=base+"/models" if base.endswith("/v1") else base+"/v1/models"
    headers={}
    if p.encrypted_key:headers["Authorization"]="Bearer "+decrypt(p.encrypted_key)
    try:
        async with httpx.AsyncClient(timeout=18,trust_env=False) as client:
            resp=await client.get(url,headers=headers);resp.raise_for_status()
            data=resp.json()
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(502,"Không fetch được danh sách models (kiểm tra /v1/models hoặc API key)") from exc
    names=[x["id"] for x in data.get("data",[]) if isinstance(x,dict) and isinstance(x.get("id"),str)]
    return {"models":names[:300]}

@app.post("/api/providers/{provider_id}/test")
async def test_provider(provider_id:str,request:Request,db:Session=Depends(get_db)):
    return await discover_models(provider_id,request,db)

@app.get("/settings/integrations",response_class=HTMLResponse)
def integrations_page(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    admin_only(u)
    setting=db.get(SystemSetting,"search")
    search=setting.value if setting else {"url":os.environ.get("SEARXNG_URL", "")}
    browser_setting=db.get(SystemSetting,"browser")
    browser=browser_setting.value if browser_setting else {}
    return page(request,"integrations.html",db=db,user=u,session=s,search=search,
                browser_proxy_set=bool(browser.get("proxy_encrypted")),pi_runner=bool(os.environ.get("PI_RUNNER_URL")),active="integrations")

@app.post("/api/settings/search")
async def configure_search(request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    data=await request.json()
    url=str(data.get("url", "")).strip().rstrip("/")
    if url and not url.startswith(("http://","https://")):raise HTTPException(400,"Invalid URL")
    allowed={"url":url,"language":str(data.get("language","all"))[:20],
             "safesearch":min(2,max(0,int(data.get("safesearch",1)))),
             "engines":str(data.get("engines",""))[:200],"categories":str(data.get("categories",""))[:200]}
    obj=db.get(SystemSetting,"search")
    if not obj:obj=SystemSetting(key="search",value=allowed);db.add(obj)
    else:obj.value=allowed
    audit(db,u,"search_settings_update");db.commit();return {"ok":True}

@app.post("/api/settings/browser")
async def configure_browser(request:Request,db:Session=Depends(get_db)):
    from urllib.parse import urlsplit
    u,s=api_guard(request,db,True);admin_only(u)
    data=await request.json()
    obj=db.get(SystemSetting,"browser")
    if not obj:obj=SystemSetting(key="browser",value={});db.add(obj)
    config=dict(obj.value or {})
    if data.get("clear_proxy"):
        config.pop("proxy_encrypted",None)
    elif data.get("proxy"):
        proxy=str(data["proxy"]).strip()
        url=urlsplit(proxy)
        if url.scheme not in ("http","https","socks5") or not url.hostname or not url.port or len(proxy)>800:
            raise HTTPException(422,"Proxy phải là http(s)://host:port hoặc socks5://host:port")
        config["proxy_encrypted"]=encrypt(proxy)
    obj.value=config
    audit(db,u,"browser_settings_update");db.commit()
    return {"ok":True,"proxy_configured":bool(config.get("proxy_encrypted"))}

# ------------------------ Administration ------------------------
@app.get("/admin/users",response_class=HTMLResponse)
def admin_users(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    admin_only(u)
    users=db.query(User).order_by(User.created_at.desc()).all()
    return page(request,"admin_users.html",db=db,user=u,session=s,users=users,active="users")

@app.post("/api/admin/users")
async def admin_add_user(request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    data=await request.json()
    try:password_hash=hash_password(str(data.get("password","")))
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    username=nonempty(data,"username").lower()
    if not re.fullmatch(r"[a-z0-9_.-]{3,60}",username):raise HTTPException(400,"Invalid username")
    email=nonempty(data,"email").lower()
    if not email or "@" not in email:raise HTTPException(400,"Invalid email")
    if db.query(User).filter(or_(User.username==username,User.email==email)).first():raise HTTPException(409,"Tài khoản tồn tại")
    obj=User(username=username,email=email,password_hash=password_hash,display_name=nonempty(data,"display_name") or username,
             role="admin" if data.get("role")=="admin" else "member",must_change_password=True)
    db.add(obj);audit(db,u,"user_create");db.commit();return {"id":obj.id}

@app.post("/api/admin/users/{user_id}/update")
async def admin_update_user(user_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    target=db.get(User,user_id)
    if not target:raise HTTPException(404,"Not found")
    data=await request.json()
    if "enabled" in data:
        if target.id==u.id and not data["enabled"]:raise HTTPException(400,"Không thể khóa chính mình")
        target.enabled=bool(data["enabled"])
        if not target.enabled:db.query(AuthSession).filter_by(user_id=user_id,revoked_at=None).update({"revoked_at":utcnow()})
    if "role" in data:
        if target.id==u.id and data["role"]!="admin":raise HTTPException(400,"Không thể hạ vai trò chính mình")
        if data["role"] not in ("admin","member"):raise HTTPException(400,"Invalid role")
        target.role=data["role"]
    if data.get("password"):
        try:target.password_hash=hash_password(data["password"])
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        target.must_change_password=True
        db.query(AuthSession).filter_by(user_id=user_id,revoked_at=None).update({"revoked_at":utcnow()})
    audit(db,u,"user_update",details={"user_id":user_id});db.commit();return {"ok":True}

@app.get('/projects/{pid}/activity',response_class=HTMLResponse)
def project_activity(pid:str, request:Request, db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    events=db.query(Audit).filter_by(project_id=pid).order_by(Audit.created_at.desc()).limit(150).all()
    runs=db.query(AgentRun).filter_by(project_id=pid).order_by(AgentRun.created_at.desc()).limit(100).all()
    jobs=db.query(ReviewJob).filter_by(project_id=pid).order_by(ReviewJob.created_at.desc()).limit(100).all()
    return page(request,'activity.html',db=db,user=u,session=s,pr=pr,events=events,runs=runs,jobs=jobs,active='activity')

@app.get('/api/projects/{pid}/activity')
def project_activity_api(pid:str, request:Request, db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    events=db.query(Audit).filter_by(project_id=pid).order_by(Audit.created_at.desc()).limit(100).all()
    runs=db.query(AgentRun).filter_by(project_id=pid).order_by(AgentRun.created_at.desc()).limit(100).all()
    jobs=db.query(ReviewJob).filter_by(project_id=pid).order_by(ReviewJob.created_at.desc()).limit(100).all()
    return {
      'events':[{'action':e.action,'created_at':e.created_at.isoformat() if e.created_at else None,'actor_id':e.actor_id} for e in events],
      'runs':[{'id':r.id,'status':r.status,'model':r.model_name,'created_at':r.created_at.isoformat() if r.created_at else None} for r in runs],
      'jobs':[{'id':j.id,'status':j.status,'created_at':j.created_at.isoformat() if j.created_at else None} for j in jobs],
    }

@app.get("/admin/audit",response_class=HTMLResponse)
def audit_page(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    admin_only(u)
    entries=db.query(Audit).order_by(Audit.created_at.desc()).limit(250).all()
    return page(request,"audit.html",db=db,user=u,session=s,entries=entries,active="audit")

def operational_snapshot(db):
    """Aggregate counts only; never expose credentials, prompts or user content."""
    from sqlalchemy import func
    from datetime import timedelta
    since = utcnow() - timedelta(hours=24)
    statuses = dict(db.query(AgentRun.status, func.count(AgentRun.id)).group_by(AgentRun.status).all())
    jobs = dict(db.query(ReviewJob.status, func.count(ReviewJob.id)).group_by(ReviewJob.status).all())
    return {
        "users": db.query(User).count(), "projects": db.query(Project).filter(Project.status.not_in(TRASH_STATUSES)).count(),
        "providers": db.query(Provider).filter_by(enabled=True).count(),
        "models": db.query(Model).filter_by(enabled=True).count(),
        "chat_runs": statuses, "review_jobs": jobs,
        "chat_last_24h": db.query(AgentRun).filter(AgentRun.created_at >= since).count(),
        "failed_chat_last_24h": db.query(AgentRun).filter(AgentRun.created_at >= since, AgentRun.status == 'error').count(),
        "review_last_24h": db.query(ReviewJob).filter(ReviewJob.created_at >= since).count(),
        "audit_last_24h": db.query(Audit).filter(Audit.created_at >= since).count(),
        "generated_at": utcnow().isoformat() + 'Z',
    }

@app.get('/admin/operations', response_class=HTMLResponse)
def operations_page(request:Request, db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    admin_only(u)
    return page(request,'operations.html',db=db,user=u,session=s,stats=operational_snapshot(db),active='operations')

@app.get('/api/admin/operations')
def operations_api(request:Request, db:Session=Depends(get_db)):
    u,s=api_guard(request,db)
    admin_only(u)
    return operational_snapshot(db)

@app.post("/api/projects/{pid}/members")
async def add_member(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    data=await request.json();target=db.query(User).filter_by(username=str(data.get("username","")).lower()).first()
    if not target:raise HTTPException(404,"Không tìm thấy người dùng")
    if target.id==pr.owner_id:raise HTTPException(400,"Đã là chủ sở hữu")
    role=data.get("role","viewer")
    if role not in ("viewer","editor"):raise HTTPException(400,"Invalid role")
    existing=db.query(Membership).filter_by(project_id=pid,user_id=target.id).first()
    if existing:existing.role=role
    else:db.add(Membership(project_id=pid,user_id=target.id,role=role))
    audit(db,u,"project_member_add",pr,{"username":target.username,"role":role});db.commit();return {"ok":True}

# ------------------------ SpecWeave v0.4 Planning Quality / BMAD gates ------------------------
@app.get("/projects/{pid}/quality",response_class=HTMLResponse)
def quality_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    member=db.query(Membership).filter_by(project_id=pid,user_id=u.id).first()
    editable=pr.owner_id==u.id or bool(member and member.role=='editor')
    role_models=[m for m in db.query(Model).join(Provider).filter(Model.enabled==True,Provider.enabled==True).order_by(Provider.name,Model.name).all()
                 if m.provider.kind!='openai-oauth' or (pr.owner_id==u.id and m.provider.owner_id==u.id)]
    return page(request,'quality.html',db=db,user=u,session=s,pr=pr,editable=editable,
                role_models=role_models,owner=pr.owner_id==u.id,active='quality')

@app.get("/api/projects/{pid}/quality/overview")
def quality_overview(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    current=context_fingerprint(db,pid)
    reviews=db.query(SpecReview).filter_by(project_id=pid).order_by(SpecReview.created_at.desc()).limit(15).all()
    findings=db.query(ReviewFinding).filter_by(project_id=pid).order_by(ReviewFinding.created_at.desc()).limit(150).all()
    # Always expose unresolved critical blockers, even when older than 150 findings.
    blocking=db.query(ReviewFinding).filter_by(project_id=pid,status='confirmed',severity='high').all()
    included={f.id for f in findings}
    findings.extend(f for f in blocking if f.id not in included)
    items=db.query(ResearchItem).filter_by(project_id=pid).order_by(ResearchItem.created_at.desc()).limit(120).all()
    assessments={x.research_id:x for x in db.query(EvidenceAssessment).filter_by(project_id=pid).all()}
    suggestions=db.query(TraceSuggestion).filter_by(project_id=pid).order_by(TraceSuggestion.score.desc()).limit(150).all()
    def label(t,id):
        try:
            node=project_node(db,pid,t,id)
            return getattr(node,'title',None) or getattr(node,'code',None) or str(id)
        except HTTPException:return '[Đối tượng đã xóa]'
    return {'fingerprint':current,'readiness':coverage_report(db,pid),
       'reviews':[{'id':x.id,'engine':x.engine,'summary':x.summary,'stale':x.fingerprint!=current,
                   'created_at':x.created_at.isoformat()} for x in reviews],
       'findings':[{'id':x.id,'review_id':x.review_id,'severity':x.severity,'category':x.category,
           'description':x.description,'recommendation':x.recommendation,'evidence':x.evidence,
           'source_type':x.source_type,'source_id':x.source_id,'status':x.status}
                   for x in findings],
       'evidence':[{'id':x.id,'title':x.title,'url':x.url,'score':assess_evidence(x,assessments.get(x.id)),
                    'note':assessments.get(x.id).note if assessments.get(x.id) else ''} for x in items],
       'suggestions':[{'id':x.id,'source_type':x.source_type,'target_type':x.target_type,
           'source_id':x.source_id,'target_id':x.target_id,'from_label':label(x.source_type,x.source_id),
           'to_label':label(x.target_type,x.target_id),'score':x.score,'reason':x.reason,
           'status':x.status} for x in suggestions],
       'workflow':stage_current(db,pid), 'quality_gate':quality_export(db,pid)['quality_blockers']}

# Long-running AI reviews are durable DB jobs handled outside the web process.
@app.post("/api/projects/{pid}/quality/jobs")
async def quality_enqueue_job(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json()
    if data.get('engine','adversarial')!='adversarial':
        raise HTTPException(422,'Chỉ hỗ trợ background adversarial review')
    model=db.get(Model,pr.active_model_id) if pr.active_model_id else None
    if not model:raise HTTPException(409,'Chọn model trong Project Settings trước khi review AI')
    lock_project_write(db,pid)
    role_ids=(data.get('role_models') or {})
    if not isinstance(role_ids,dict):raise HTTPException(422,'Invalid role model map')
    for checked in [model]+[db.get(Model,mid) for mid in role_ids.values() if isinstance(mid,str)]:
        if checked:openai_oauth_access(pr,checked,u.id)
    job=create_review_job(db,pr,u.id,model,data.get('output_budget',12000),data.get('role_models'))
    audit(db,u,'review_job_queued',pr,{'job_id':job.id,'model_id':model.id})
    db.commit()
    return job_payload(db,job)

@app.get("/api/projects/{pid}/quality/jobs")
def quality_jobs(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    rows=db.query(ReviewJob).filter_by(project_id=pid).order_by(ReviewJob.created_at.desc()).limit(30).all()
    return {'jobs':[job_payload(db,row) for row in rows]}

@app.get("/api/projects/{pid}/quality/jobs/{jid}")
def quality_job_detail(pid:str,jid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    job=db.query(ReviewJob).filter_by(id=jid,project_id=pid).first()
    if not job:raise HTTPException(404,'Không thấy review job')
    return job_payload(db,job)

@app.post("/api/projects/{pid}/quality/jobs/{jid}/cancel")
def quality_cancel_job(pid:str,jid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    job=db.query(ReviewJob).filter_by(id=jid,project_id=pid).with_for_update().first()
    if not job:raise HTTPException(404,'Không thấy review job')
    if job.status in TERMINAL:return job_payload(db,job)
    job.cancel_requested=True
    if job.status=='queued':
        job.status='cancelled';job.finished_at=utcnow()
    audit(db,u,'review_job_cancel',pr,{'job_id':jid})
    db.commit()
    return job_payload(db,job)

@app.post("/api/projects/{pid}/quality/jobs/{jid}/retry")
def quality_retry_job(pid:str,jid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    job=db.query(ReviewJob).filter_by(id=jid,project_id=pid).with_for_update().first()
    if not job:raise HTTPException(404,'Không thấy review job')
    if job.status!='failed' or job.attempts>=20:
        raise HTTPException(409,'Chỉ retry được job lỗi chưa vượt quá giới hạn')
    if context_fingerprint(db,pid)!=job.fingerprint:
        raise HTTPException(409,'Spec đã đổi; tạo job mới thay vì retry')
    if db.query(ReviewJob).filter(ReviewJob.project_id==pid,ReviewJob.status.in_(('queued','running'))).count():
        raise HTTPException(409,'Đang có job khác chạy')
    job.status='queued';job.error='';job.cancel_requested=False
    job.lease_owner=None;job.lease_expires_at=None;job.finished_at=None
    audit(db,u,'review_job_retry',pr,{'job_id':jid})
    db.commit()
    return job_payload(db,job)

@app.post("/api/projects/{pid}/quality/reviews/run")
async def quality_run_review(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    data=await request.json();engine=str(data.get('engine','rules'))
    if engine not in ('rules','model','adversarial'):raise HTTPException(422,'Unknown review engine')
    summary='Phát hiện bằng quy tắc định sẵn; cần người dùng xác minh.'
    snapshot_before=context_fingerprint(db,pid)
    if engine=='rules': findings=rule_findings(db,pid)
    else:
        model=db.get(Model,pr.active_model_id) if pr.active_model_id else None
        if not model:raise HTTPException(409,'Hãy chọn model trong Project Settings trước khi review AI')
        openai_oauth_access(pr,model,u.id)
        summary, findings=await (adversarial_review(db,pr,model,actor_id=u.id) if engine=='adversarial' else model_review(db,pr,model,actor_id=u.id))
        db.expire_all()
        if context_fingerprint(db,pid)!=snapshot_before:
            raise HTTPException(409,'Spec đã thay đổi trong khi AI review; hãy chạy lại')
    lock_project_write(db,pid)
    db.expire_all()
    if context_fingerprint(db,pid)!=snapshot_before:
        raise HTTPException(409,'Spec thay đổi trong lúc review; hãy chạy lại')
    review=new_review(db,pid,u.id,engine=engine,
                      model_id=pr.active_model_id if engine in ('model','adversarial') else None,
                      summary=summary,items=findings)
    audit(db,u,'quality_review_run',pr,{'id':review.id,'engine':engine,'findings':len(findings)})
    db.commit()
    return {'id':review.id,'engine':engine,'findings':len(findings),'summary':summary}

@app.post("/api/projects/{pid}/quality/findings/{fid}/status")
async def quality_finding_status(pid:str,fid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    data=await request.json();status=data.get('status')
    if status not in ('confirmed','resolved','dismissed'):
        raise HTTPException(422,'Invalid finding status')
    finding=db.query(ReviewFinding).filter_by(project_id=pid,id=fid).with_for_update().first()
    if not finding:raise HTTPException(404,'Finding not found')
    review=db.query(SpecReview).filter_by(project_id=pid,id=finding.review_id).first()
    if not review:
        raise HTTPException(404,'Review not found')
    if review.fingerprint!=context_fingerprint(db,pid) and (
            status=='confirmed' or finding.status!='confirmed'):
        raise HTTPException(409,'Review đã cũ; chỉ có thể đóng finding đã xác nhận hoặc chạy review mới')
    if finding.status in ('resolved','dismissed'):
        raise HTTPException(409,'Finding đã kết thúc; cần review mới để mở lại')
    finding.status=status;finding.reviewed_by=u.id;finding.reviewed_at=utcnow()
    audit(db,u,'review_finding_status',pr,{'id':fid,'status':status});db.commit()
    return {'id':fid,'status':status}

@app.post("/api/projects/{pid}/quality/evidence/{rid}")
async def quality_evidence_edit(pid:str,rid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    source=db.query(ResearchItem).filter_by(project_id=pid,id=rid).first()
    if not source:raise HTTPException(404,'Research source not found')
    data=await request.json()
    item=db.query(EvidenceAssessment).filter_by(project_id=pid,research_id=rid).with_for_update().first()
    kind=data.get('source_kind',item.source_kind if item else 'unknown')
    previous_verification=item.verification if item else 'unverified'
    verification=data.get('verification',previous_verification)
    if kind not in SOURCE_KINDS or verification not in VERIFY_STATES:
        raise HTTPException(422,'Invalid evidence metadata')
    if pr.owner_id!=u.id and verification!=previous_verification:
        raise HTTPException(403,'Chỉ owner được đổi trạng thái xác minh nguồn')
    publication=data.get('published_on') or None
    if publication:
        from datetime import date
        try:date.fromisoformat(publication)
        except (ValueError,TypeError):raise HTTPException(422,'Ngày xuất bản cần YYYY-MM-DD')
    if not item:
        item=EvidenceAssessment(project_id=pid,research_id=rid);db.add(item)
    item.source_kind=kind;item.verification=verification;item.published_on=publication
    item.note=str(data.get('note',''))[:1500];item.updated_by=u.id
    audit(db,u,'evidence_assess',pr,{'research_id':rid,'verification':verification})
    db.commit();return {'research_id':rid,**assess_evidence(source,item)}

@app.post("/api/projects/{pid}/quality/trace/suggest")
async def quality_trace_suggest(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    data=await request.json();generated=propose_trace_links(db,pid,u.id,data.get('limit',35))
    audit(db,u,'trace_suggest',pr,{'count':len(generated)});db.commit()
    return {'created':len(generated),'ids':[x.id for x in generated]}

@app.post("/api/projects/{pid}/quality/trace/{sid}/decision")
async def quality_trace_decision(pid:str,sid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    data=await request.json()
    suggestion=resolve_suggestion(db,pid,sid,str(data.get('action','')),u.id)
    audit(db,u,'trace_suggestion_decision',pr,{'id':sid,'action':suggestion.status})
    db.commit();return {'id':sid,'status':suggestion.status}

@app.post("/api/projects/{pid}/quality/workflow/{stage}")
async def quality_workflow_action(pid:str,stage:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True)
    data=await request.json();action=str(data.get('action',''))
    if action not in ('submit','approve','request_changes'):
        raise HTTPException(422,'Invalid workflow action')
    pr=acl(db,pid,u,owner=(action!='submit'),write=(action=='submit'))
    lock_project_write(db,pid)
    checkpoint=stage_transition(db,pid,stage,action,u.id,str(data.get('notes','')))
    audit(db,u,'workflow_stage',pr,{'stage':stage,'action':action})
    db.commit();return {'stage':stage,'status':checkpoint.status,'project_stage':pr.stage}

# --------------------------- Product Completion ---------------------------
# The following modules use only the existing project-scoped ORM data and filesystem.
# All mutating JSON endpoints require the session CSRF header and enforce project ACL.
from artifact_builder import ARTIFACTS, build_artifacts, named_draft_records, publish_artifacts, requested_draft_paths

@app.get('/presets', response_class=HTMLResponse)
def preset_library_page(request:Request, db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    presets=db.query(Preset).filter_by(user_id=u.id).order_by(Preset.favorite.desc(),Preset.created_at.desc()).all()
    return page(request,'presets.html',db=db,user=u,session=s,presets=presets,active='presets')

@app.post('/api/presets/{preset_id}')
async def update_preset(preset_id:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True)
    preset=db.get(Preset,preset_id)
    if not preset or preset.user_id!=u.id:raise HTTPException(404,'Preset not found')
    data=await request.json()
    if 'name' in data:
        name=str(data['name']).strip()[:160]
        if not name:raise HTTPException(422,'Name required')
        preset.name=name
    if 'favorite' in data:
        if not isinstance(data['favorite'],bool):raise HTTPException(422,'Invalid favorite')
        preset.favorite=data['favorite']
    if 'profile' in data:
        if not isinstance(data['profile'],dict) or len(json.dumps(data['profile']))>30000:
            raise HTTPException(422,'Invalid profile')
        preset.profile=data['profile']
    audit(db,u,'preset_update',details={'id':preset_id});db.commit()
    return {'ok':True}

@app.post('/api/projects/{pid}/restore')
def restore_project(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True)
    pr=lock_trash_project(db,pid,owner_id=u.id,statuses=('deleted',))
    if not pr:raise HTTPException(404,'Project not found in trash')
    if pr.deleted_at and utcnow() >= pr.deleted_at + TRASH_RETENTION:
        raise HTTPException(410,'Dự án đã hết hạn khôi phục')
    if not project_root(pid).is_dir():raise HTTPException(409,'Project data directory missing; operator restore required')
    pr.status='active';pr.deleted_at=None
    audit(db,u,'project_restore',pr);db.commit()
    return {'ok':True,'redirect':f'/projects/{pid}'}


@app.delete('/api/projects/{pid}/purge')
def purge_trashed_project(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True)
    try:
        result=purge_project(db,pid,owner_id=u.id)
    except OSError:
        db.rollback()
        trash_logger.exception('Project volume purge failed for %s',pid)
        raise HTTPException(503,'Chưa xóa xong dữ liệu. Hệ thống sẽ tự thử lại.')
    if result=='missing':raise HTTPException(404,'Không tìm thấy dự án trong thùng rác')
    if result=='busy':raise HTTPException(409,'Dự án còn tác vụ đang xử lý. Hãy thử lại sau.')
    return {'ok':True}

@app.post('/api/projects/{pid}/members/{uid}')
async def update_member(pid:str,uid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    if uid==pr.owner_id:raise HTTPException(409,'Cannot modify the owner role')
    member=db.query(Membership).filter_by(project_id=pid,user_id=uid).with_for_update().first()
    if not member:raise HTTPException(404,'Membership not found')
    data=await request.json();role=data.get('role')
    if role not in ('viewer','editor'):raise HTTPException(422,'Invalid role')
    member.role=role
    audit(db,u,'member_role_change',pr,{'user_id':uid,'role':role});db.commit()
    return {'ok':True,'role':role}

@app.delete('/api/projects/{pid}/members/{uid}')
def remove_member(pid:str,uid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    lock_project_write(db,pid)
    if uid==pr.owner_id:raise HTTPException(409,'Cannot remove project owner')
    member=db.query(Membership).filter_by(project_id=pid,user_id=uid).with_for_update().first()
    if not member:raise HTTPException(404,'Membership not found')
    db.delete(member);audit(db,u,'member_remove',pr,{'user_id':uid});db.commit()
    return {'ok':True}

@app.post('/api/projects/{pid}/transfer-ownership')
async def transfer_project_owner(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,owner=True)
    data=await request.json()
    if data.get('confirm_name')!=pr.name:raise HTTPException(422,'Project name confirmation does not match')
    target=db.get(User,str(data.get('user_id','')))
    if not target or not target.enabled or target.id==u.id:raise HTTPException(422,'Invalid new owner')
    lock_project_write(db,pid)
    member=db.query(Membership).filter_by(project_id=pid,user_id=target.id).with_for_update().first()
    if not member:raise HTTPException(409,'New owner must already be a project member')
    db.delete(member)
    old=db.query(Membership).filter_by(project_id=pid,user_id=u.id).first()
    if old:old.role='editor'
    else:db.add(Membership(project_id=pid,user_id=u.id,role='editor'))
    old_model=db.get(Model,pr.active_model_id) if pr.active_model_id else None
    if old_model and old_model.provider.kind=='openai-oauth':
        pr.active_model_id=None
    pr.owner_id=target.id;pr.revision+=1
    audit(db,u,'project_owner_transfer',pr,{'new_owner_id':target.id,'old_owner_id':u.id})
    db.commit()
    return {'ok':True,'owner_id':target.id}

@app.post('/api/profile')
async def update_profile(request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True)
    data=await request.json()
    if 'display_name' in data:
        name=str(data['display_name']).strip()[:120]
        if not name:raise HTTPException(422,'Display name required')
        u.display_name=name
    if 'email' in data:
        email=str(data['email']).strip().lower()
        if len(email)>255 or not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+',email):
            raise HTTPException(422,'Email invalid')
        other=db.query(User).filter(User.email==email,User.id!=u.id).first()
        if other:raise HTTPException(409,'Email already registered')
        u.email=email
    audit(db,u,'profile_update');db.commit();return {'ok':True}

@app.delete('/api/profile/sessions/{sid}')
def revoke_user_session(sid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True)
    target=db.query(AuthSession).filter_by(id=sid,user_id=u.id).first()
    if not target:raise HTTPException(404,'Session not found')
    target.revoked_at=utcnow()
    audit(db,u,'session_revoke',details={'session_id':sid});db.commit()
    return {'ok':True,'current_session':sid==s.id}

@app.post('/api/providers/{provider_id}/models/{mid}')
async def update_model(provider_id:str,mid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);admin_only(u)
    model=db.get(Model,mid)
    if not model or model.provider_id!=provider_id:raise HTTPException(404,'Model not found')
    if model.provider.kind=='openai-oauth' and model.provider.owner_id!=u.id:
        raise HTTPException(403,'OpenAI OAuth account belongs to another user')
    data=await request.json()
    if 'enabled' in data:
        if not isinstance(data['enabled'],bool):raise HTTPException(422,'Invalid enabled flag')
        model.enabled=data['enabled']
    if 'reasoning' in data:
        if not isinstance(data['reasoning'],bool):raise HTTPException(422,'Invalid reasoning flag')
        model.reasoning=data['reasoning']
    if 'context_window' in data:
        try:size=int(data['context_window'])
        except (TypeError,ValueError):raise HTTPException(422,'Invalid context window')
        if not 1024<=size<=2097152:raise HTTPException(422,'Context window out of range')
        model.context_window=size
    audit(db,u,'model_update',details={'model_id':mid});db.commit()
    return {'ok':True}

@app.get('/settings/providers/{provider_id}',response_class=HTMLResponse)
def provider_detail_page(provider_id:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    admin_only(u)
    provider=db.get(Provider,provider_id)
    if not provider:raise HTTPException(404,'Provider not found')
    if provider.kind=='openai-oauth' and provider.owner_id!=u.id:
        raise HTTPException(403,'OpenAI OAuth account belongs to another user')
    models=db.query(Model).filter_by(provider_id=provider_id).order_by(Model.name).all()
    projects=db.query(Project).filter(Project.active_model_id.in_([m.id for m in models]),
                                     Project.status.not_in(TRASH_STATUSES)).all() if models else []
    return page(request,'provider_detail.html',db=db,user=u,session=s,
                provider=provider,models=models,affected_projects=projects,oauth_local=openai_oauth_enabled(),active='providers')

@app.get('/settings/appearance',response_class=HTMLResponse)
def appearance_page(request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    return page(request,'appearance.html',db=db,user=u,session=s,active='appearance')

@app.get('/projects/{pid}/architecture',response_class=HTMLResponse)
def architecture_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    data=graph_payload(db,pid)
    decisions=db.query(Decision).filter_by(project_id=pid).order_by(Decision.created_at.desc()).all()
    docs=db.query(Document).filter_by(project_id=pid).all()
    return page(request,'architecture.html',db=db,user=u,session=s,pr=pr,graph=data,
                decisions=decisions,docs=docs,active='architecture')

@app.get('/projects/{pid}/roadmap',response_class=HTMLResponse)
def roadmap_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    phases=db.query(PlanPhase).filter_by(project_id=pid).order_by(PlanPhase.sort_order).all()
    dependencies=db.query(PhaseDependency).filter_by(project_id=pid).all()
    return page(request,'roadmap.html',db=db,user=u,session=s,pr=pr,
                phases=phases,dependencies=dependencies,active='roadmap')

@app.get('/projects/{pid}/artifacts',response_class=HTMLResponse)
def artifacts_page(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s,redirect=auth_page(request,db)
    if redirect:return redirect
    pr=acl(db,pid,u)
    member=db.query(Membership).filter_by(project_id=pid,user_id=u.id).first()
    editable=u.id==pr.owner_id or bool(member and member.role=='editor')
    items=[]
    existing=named_draft_records(db,pid,[path for _,path in ARTIFACTS.values()])
    for key,(title,path) in ARTIFACTS.items():
        doc=existing.get(path.lower())
        items.append({'key':key,'title':title,'path':doc.path if doc else path,'exists':bool(doc),'status':doc.status if doc else 'missing',
                      'revision':doc.revision if doc else None})
    return page(request,'artifacts.html',db=db,user=u,session=s,pr=pr,items=items,editable=editable,active='artifacts')

@app.get('/api/projects/{pid}/artifacts')
def artifacts_preview(pid:str,request:Request,key:str='',db:Session=Depends(get_db)):
    u,s=api_guard(request,db);acl(db,pid,u)
    rendered=build_artifacts(db,pid)
    if key:
        if key not in rendered:raise HTTPException(404,'Artifact unknown')
        return rendered[key]
    return {'artifacts':[{'key':k,'title':v['title'],'path':v['path'],'bytes':len(v['content'].encode())}
                         for k,v in rendered.items()]}

@app.post('/api/projects/{pid}/artifacts/generate')
async def generate_artifacts(pid:str,request:Request,db:Session=Depends(get_db)):
    u,s=api_guard(request,db,True);pr=acl(db,pid,u,write=True)
    lock_project_write(db,pid)
    data=await request.json()
    keys=data.get('keys',list(ARTIFACTS))
    if not isinstance(data.get('overwrite',False),bool):raise HTTPException(422,'Invalid overwrite flag')
    records=publish_artifacts(db,pid,keys,data.get('overwrite',False))
    pr.revision+=1
    audit(db,u,'artifacts_generated',pr,{'keys':keys,'overwrite':data.get('overwrite',False)})
    db.commit()
    return {'documents':records,'count':len(records),'redirect':f'/projects/{pid}/documents'}

@app.exception_handler(HTTPException)
async def friendly_errors(request:Request,exc:HTTPException):
    if request.url.path.startswith("/api/") or request.url.path=="/health":
        body={"code":str(exc.status_code),"message":str(exc.detail)}
        if isinstance(exc.detail,dict):
            body.update({"message":str(exc.detail.get('message','Lỗi yêu cầu')),
                         "issues":exc.detail.get('issues',[])})
        return JSONResponse(status_code=exc.status_code,content=body)
    if request.method=="POST" and request.url.path in ("/login","/first-run"):
        return templates.TemplateResponse(request,"auth_error.html",{"message":str(exc.detail),"user":None,"csrf":""},status_code=exc.status_code)
    if exc.status_code==401:return go("/login")
    return templates.TemplateResponse(request,"error.html",{"message":str(exc.detail),"status":exc.status_code,"user":None},status_code=exc.status_code)

if __name__=="__main__":
    import uvicorn
    uvicorn.run("app:app",host="0.0.0.0",port=int(os.environ.get("PORT","8080")),workers=1)
