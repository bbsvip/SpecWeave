"""SQLAlchemy models for SpecWeave; deliberately importable without package install."""
import os
import uuid
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, String, Integer, Boolean, DateTime, ForeignKey, Text, JSON, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

def uid():
    return str(uuid.uuid4())

def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id = Column(String(36), primary_key=True, default=uid)
    username = Column(String(90), unique=True, nullable=False)
    email = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(120), nullable=False, default="")
    password_hash = Column(Text, nullable=False)
    role = Column(String(20), nullable=False, default="member")
    enabled = Column(Boolean, default=True, nullable=False)
    must_change_password = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=utcnow)

class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False)
    csrf_token = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, default=utcnow)
    revoked_at = Column(DateTime, nullable=True)
    user_agent = Column(String(250), default="")
    user = relationship("User")

class LoginAttempt(Base):
    """HMAC-derived login/IP identifiers, bounded-window multi-worker throttling."""
    __tablename__ = "login_attempts"
    id = Column(String(36), primary_key=True, default=uid)
    subject_hash = Column(String(64), nullable=False, index=True)
    client_hash = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)

class Project(Base):
    __tablename__ = "projects"
    id = Column(String(36), primary_key=True, default=uid)
    slug = Column(String(120), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(30), default="active")
    stage = Column(String(40), default="discovery")
    profile = Column(JSON, default=dict, nullable=False)
    active_model_id = Column(String(36), nullable=True)
    thinking_level = Column(String(20), default="medium")
    agent_backend = Column(String(20), default="pi")
    revision = Column(Integer, default=1)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    deleted_at = Column(DateTime, index=True)
    owner = relationship("User")

class Membership(Base):
    __tablename__ = "memberships"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    role = Column(String(20), default="viewer")
    __table_args__ = (UniqueConstraint("project_id", "user_id"),)

class Preset(Base):
    __tablename__ = "presets"
    id = Column(String(36), primary_key=True, default=uid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    name = Column(String(160), nullable=False)
    profile = Column(JSON, default=dict)
    favorite = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

class Provider(Base):
    __tablename__ = "providers"
    id = Column(String(36), primary_key=True, default=uid)
    name = Column(String(120), nullable=False)
    kind = Column(String(40), default="openai-compatible")
    base_url = Column(String(1024), nullable=False)
    encrypted_key = Column(Text, nullable=False, default="")
    owner_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)  # OpenAI OAuth only
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utcnow)

class Model(Base):
    __tablename__ = "models"
    id = Column(String(36), primary_key=True, default=uid)
    provider_id = Column(String(36), ForeignKey("providers.id"), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    model_id = Column(String(300), nullable=False)
    reasoning = Column(Boolean, default=False)
    context_window = Column(Integer, default=32768)
    enabled = Column(Boolean, default=True)
    __table_args__ = (UniqueConstraint("provider_id", "model_id"),)
    provider = relationship("Provider")

class Discussion(Base):
    __tablename__ = "discussions"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(200), default="Thảo luận chính")
    mode = Column(String(30), default="discovery")
    created_at = Column(DateTime, default=utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(String(36), primary_key=True, default=uid)
    discussion_id = Column(String(36), ForeignKey("discussions.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    run_id = Column(String(36), ForeignKey('agent_runs.id'), nullable=True, index=True)
    created_at = Column(DateTime, default=utcnow)

class PlanningQuestion(Base):
    __tablename__ = "planning_questions"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    discussion_id = Column(String(36), ForeignKey("discussions.id"), nullable=False, index=True)
    question = Column(Text, nullable=False)
    context = Column(Text, default="")
    options = Column(JSON, nullable=False, default=list)
    recommended_id = Column(String(20), nullable=False)
    recommendation_reason = Column(Text, default="")
    impact = Column(Text, default="")
    assumptions = Column(JSON, nullable=False, default=list)
    sources = Column(JSON, nullable=False, default=list)
    allow_multiple = Column(Boolean, default=False, nullable=False)
    status = Column(String(20), default="pending", nullable=False)
    answer = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, default=utcnow)

class AgentRun(Base):
    __tablename__ = "agent_runs"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    discussion_id = Column(String(36), ForeignKey("discussions.id"), nullable=False, index=True)
    status = Column(String(30), default="running")
    model_name = Column(String(300), default="")
    thinking = Column(String(20), default="")
    cancel_requested = Column(Boolean, nullable=False, default=False)
    error = Column(Text, default="")
    usage = Column(JSON, nullable=False, default=dict)
    events = Column(JSON, nullable=False, default=list)
    first_token_ms = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    request_payload = Column(JSON, nullable=False, default=dict)
    requester_id = Column(String(36), nullable=True)
    worker_owner = Column(String(100), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    finished_at = Column(DateTime, nullable=True)

class ChatStreamEvent(Base):
    """Append-only, cursor-replayable Pi events. A browser is never the job owner."""
    __tablename__ = 'chat_stream_events'
    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(36), ForeignKey('agent_runs.id'), nullable=False, index=True)
    event = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

class Decision(Base):
    __tablename__ = "decisions"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(250), nullable=False)
    description = Column(Text, default="")
    rationale = Column(Text, default="")
    status = Column(String(25), default="proposed")
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=utcnow)

class Requirement(Base):
    __tablename__ = "requirements"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    code = Column(String(20), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, default="")
    criteria = Column(Text, default="")
    priority = Column(String(20), default="should")
    status = Column(String(20), default="draft")
    __table_args__ = (UniqueConstraint("project_id", "code"),)

class Document(Base):
    __tablename__ = "documents"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    path = Column(String(550), nullable=False)
    title = Column(String(200), nullable=False)
    status = Column(String(25), default="draft")
    revision = Column(Integer, default=1)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "path"),)

class ResearchItem(Base):
    __tablename__ = "research_items"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(500), default="")
    url = Column(Text, nullable=False)
    snippet = Column(Text, default="")
    query = Column(String(500), default="")
    created_at = Column(DateTime, default=utcnow)

# Planning Intelligence 0.3: scoped traceability and human-reviewed implementation phases.
class ProjectGoal(Base):
    __tablename__ = "project_goals"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    outcome = Column(Text, default="")
    metric = Column(String(350), default="")
    status = Column(String(24), nullable=False, default="draft")
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=utcnow)

class TraceLink(Base):
    __tablename__ = "trace_links"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    source_type = Column(String(24), nullable=False)
    source_id = Column(String(36), nullable=False)
    target_type = Column(String(24), nullable=False)
    target_id = Column(String(36), nullable=False)
    relation = Column(String(30), nullable=False, default="supports")
    note = Column(String(1000), default="")
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "source_type", "source_id", "target_type", "target_id", "relation"),)

class PlanPhase(Base):
    __tablename__ = "plan_phases"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    summary = Column(Text, default="")
    deliverables = Column(Text, default="")
    acceptance = Column(Text, default="")
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String(24), nullable=False, default="draft")
    revision = Column(Integer, nullable=False, default=1)
    created_by = Column(String(36), nullable=True)
    approved_by = Column(String(36), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)

class PhaseDependency(Base):
    __tablename__ = "phase_dependencies"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    phase_id = Column(String(36), ForeignKey("plan_phases.id"), nullable=False)
    prerequisite_id = Column(String(36), ForeignKey("plan_phases.id"), nullable=False)
    __table_args__ = (UniqueConstraint("project_id", "phase_id", "prerequisite_id"),)

class PhaseReview(Base):
    __tablename__ = "phase_reviews"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    phase_id = Column(String(36), ForeignKey("plan_phases.id"), nullable=False)
    reviewer_id = Column(String(36), nullable=True)
    action = Column(String(24), nullable=False)
    revision = Column(Integer, nullable=False)
    comment = Column(Text, default="")
    created_at = Column(DateTime, default=utcnow)

# Planning Review 0.4: AI suggestions are never user approvals. Every row scoped to one project.
class SpecReview(Base):
    __tablename__ = "spec_reviews"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    model_id = Column(String(36), nullable=True)
    model_snapshot = Column(JSON, nullable=False, default=dict)
    engine = Column(String(20), nullable=False, default="rules")
    summary = Column(Text, default="")
    fingerprint = Column(String(64), nullable=False)
    created_by = Column(String(36), nullable=True)
    created_at = Column(DateTime, default=utcnow)

class ReviewFinding(Base):
    __tablename__ = "review_findings"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    review_id = Column(String(36), ForeignKey("spec_reviews.id"), nullable=False, index=True)
    severity = Column(String(16), nullable=False, default="warning")
    category = Column(String(32), nullable=False, default="quality")
    description = Column(Text, nullable=False)
    recommendation = Column(Text, default="")
    source_type = Column(String(24), nullable=True)
    source_id = Column(String(36), nullable=True)
    evidence = Column(Text, default="")
    status = Column(String(20), nullable=False, default="proposed")
    reviewed_by = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

class EvidenceAssessment(Base):
    __tablename__ = "evidence_assessments"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    research_id = Column(String(36), ForeignKey("research_items.id"), nullable=False)
    source_kind = Column(String(24), nullable=False, default="unknown")
    verification = Column(String(20), nullable=False, default="unverified")
    published_on = Column(String(10), nullable=True)
    note = Column(Text, default="")
    updated_by = Column(String(36), nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "research_id"),)

class TraceSuggestion(Base):
    __tablename__ = "trace_suggestions"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    source_type = Column(String(24), nullable=False)
    source_id = Column(String(36), nullable=False)
    target_type = Column(String(24), nullable=False)
    target_id = Column(String(36), nullable=False)
    relation = Column(String(30), nullable=False, default="supports")
    score = Column(Integer, nullable=False, default=0)
    reason = Column(Text, default="")
    status = Column(String(20), nullable=False, default="pending")
    created_by = Column(String(36), nullable=True)
    decided_by = Column(String(36), nullable=True)
    decided_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "source_type", "source_id", "target_type", "target_id", "relation"),)

class WorkflowCheckpoint(Base):
    __tablename__ = "workflow_checkpoints"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    stage = Column(String(30), nullable=False)
    role = Column(String(30), nullable=False)
    status = Column(String(24), nullable=False, default="draft")
    fingerprint = Column(String(64), nullable=False)
    checklist = Column(JSON, default=list, nullable=False)
    notes = Column(Text, default="")
    submitted_by = Column(String(36), nullable=True)
    reviewed_by = Column(String(36), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "stage"),)

# Durable, project-scoped review work queue. A separate worker executes long model calls.
# No credentials or complete project snapshots are stored in these records.
class ReviewJob(Base):
    __tablename__ = "review_jobs"
    id = Column(String(36), primary_key=True, default=uid)
    project_id = Column(String(36), ForeignKey("projects.id"), nullable=False, index=True)
    requester_id = Column(String(36), ForeignKey("users.id"), nullable=False)
    model_id = Column(String(36), ForeignKey("models.id"), nullable=True)
    model_snapshot = Column(JSON, nullable=False, default=dict)
    status = Column(String(24), nullable=False, default="queued", index=True)
    fingerprint = Column(String(64), nullable=False)
    step_index = Column(Integer, nullable=False, default=0)
    # This is an approximate output-token budget, not a billing or total-token counter.
    output_budget = Column(Integer, nullable=False, default=12000)
    role_models = Column(JSON, nullable=False, default=dict)
    output_used = Column(Integer, nullable=False, default=0)
    usage_estimated = Column(Boolean, nullable=False, default=False)
    cancel_requested = Column(Boolean, nullable=False, default=False)
    attempts = Column(Integer, nullable=False, default=0)
    lease_owner = Column(String(100), nullable=True)
    lease_expires_at = Column(DateTime, nullable=True)
    summary = Column(Text, default="")
    error = Column(String(600), default="")
    review_id = Column(String(36), ForeignKey("spec_reviews.id"), nullable=True)
    created_at = Column(DateTime, default=utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (UniqueConstraint("project_id", "id"),)

class ReviewJobStep(Base):
    __tablename__ = "review_job_steps"
    id = Column(String(36), primary_key=True, default=uid)
    job_id = Column(String(36), ForeignKey("review_jobs.id"), nullable=False, index=True)
    role = Column(String(24), nullable=False)
    model_id = Column(String(36), nullable=True)
    summary = Column(Text, default="")
    findings = Column(JSON, nullable=False, default=list)
    output_tokens = Column(Integer, nullable=False, default=0)
    usage_estimated = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("job_id", "role"),)

class SystemSetting(Base):
    __tablename__ = "system_settings"
    key = Column(String(100), primary_key=True)
    value = Column(JSON, nullable=False, default=dict)

class Audit(Base):
    __tablename__ = "audit_events"
    id = Column(String(36), primary_key=True, default=uid)
    actor_id = Column(String(36), nullable=True)
    project_id = Column(String(36), nullable=True)
    action = Column(String(100), nullable=False)
    details = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utcnow)

db_url = os.environ.get("DATABASE_URL", "sqlite:///./data/specweave.db")
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)
connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
engine = create_engine(db_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

def init_db():
    # Docker runs Alembic before starting the app. Direct developer runs still
    # use create_all for the simple SQLite smoke-test workflow.
    if os.environ.get('SPECWEAVE_MIGRATIONS_MANAGED','false').lower() != 'true':
        Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
