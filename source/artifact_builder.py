"""Deterministic, project-scoped BMAD-inspired handoff artifacts.

Never fabricates project facts, never marks a document approved and never overwrites
an approved document. Each substantive statement links to the corresponding record.
Use directly from source/app.py; no Python package installation required.
"""
from __future__ import annotations
import re
from collections import defaultdict
from fastapi import HTTPException
from db import (Project, ProjectGoal, Requirement, Decision, PlanPhase, ResearchItem,
                TraceLink, Document, PlanningQuestion, utcnow)
from storage import resolve, write_text, MAX_TEXT, enforce_project_quota

ARTIFACTS = {
    'product-brief': ('Product Brief', 'docs/drafts/PRODUCT-BRIEF.md'),
    'prd': ('Product Requirements Document', 'docs/drafts/PRD.md'),
    'architecture': ('Solution Architecture', 'docs/drafts/ARCHITECTURE.md'),
    'ux': ('UX Specification', 'docs/drafts/UX-SPEC.md'),
    'api': ('API & Data Contracts', 'docs/drafts/API-DATA-CONTRACTS.md'),
    'security': ('Security & Reliability', 'docs/drafts/SECURITY-RELIABILITY.md'),
    'roadmap': ('Epics & Implementation Roadmap', 'docs/drafts/ROADMAP.md'),
    'acceptance': ('Acceptance & Test Strategy', 'docs/drafts/TEST-STRATEGY.md'),
    'adr': ('Architecture Decision Records', 'docs/drafts/ADR-REGISTER.md'),
    'handoff': ('Implementation Handoff', 'docs/drafts/HANDOFF.md'),
}


def _clean(value, limit=2500):
    """Quote user-controlled text as data, not executable Markdown/HTML."""
    text = str(value or '').strip()[:limit]
    # Escape HTML and Markdown-sensitive prefix so quoted records cannot forge headings.
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return text.replace('\n', '\n> ') if '\n' in text else text


def _block(value, fallback='Chưa xác định — cần bổ sung và duyệt'):
    return _clean(value) or f'**[CẦN XÁC NHẬN]** {fallback}'


def build_artifacts(db, pid):
    pr = db.get(Project, pid)
    if not pr:
        raise HTTPException(404, 'Project not found')
    goals = db.query(ProjectGoal).filter_by(project_id=pid).order_by(ProjectGoal.created_at).all()
    reqs = db.query(Requirement).filter_by(project_id=pid).order_by(Requirement.code).all()
    decisions = db.query(Decision).filter_by(project_id=pid).order_by(Decision.created_at).all()
    phases = db.query(PlanPhase).filter_by(project_id=pid).order_by(PlanPhase.sort_order).all()
    research = db.query(ResearchItem).filter_by(project_id=pid).order_by(ResearchItem.created_at).limit(100).all()
    trace = db.query(TraceLink).filter_by(project_id=pid).all()
    questions = db.query(PlanningQuestion).filter_by(project_id=pid).order_by(PlanningQuestion.created_at).all()
    req_by_id = {r.id:r for r in reqs}
    sources = defaultdict(list)
    for edge in trace:
        sources[(edge.source_type,edge.source_id)].append((edge.target_type,edge.target_id,edge.relation))
    artifacts = {}
    header = (f'> SpecWeave generated draft · project {_clean(pr.id)} · NOT APPROVED\n'
              '> Generated from project records; unverified assumptions must be reviewed.\n\n')

    def refs(kind, uid):
        return ' · '.join(f'`{_clean(target_type)}/{_clean(target_id)}` ({_clean(relation)})'
                        for target_type,target_id,relation in sources.get((kind,uid),[])[:12]) or 'Chưa liên kết'

    def goal_list():
        if not goals:
            return '**[CẦN XÁC NHẬN]** Chưa có mục tiêu; thêm tại Planning Intelligence.\n'
        return '\n'.join(f'- **{_clean(g.title)}** [{g.status}] — Kết quả: {_block(g.outcome)}; Đo lường: {_block(g.metric)} (`goal/{g.id}`)' for g in goals)+'\n'

    def req_list():
        if not reqs:
            return '**[CẦN XÁC NHẬN]** Chưa có requirement; tạo tại Requirements.\n'
        return '\n'.join((f'### {r.code} — {_clean(r.title)}\n'
                          f'- Priority/status: `{r.priority}` / `{r.status}` · `requirement/{r.id}`\n'
                          f'- Mô tả: {_block(r.description)}\n'
                          f'- Acceptance criteria: {_block(r.criteria)}\n'
                          f'- Trace: {refs("requirement",r.id)}\n') for r in reqs)+'\n'

    def decision_list():
        if not decisions:
            return '_Chưa có decision._\n'
        return '\n'.join((f'### {_clean(d.title)} [{d.status}]\n'
                          f'- Context: {_block(d.description)}\n'
                          f'- Rationale: {_block(d.rationale)}\n'
                          f'- Reference: `decision/{d.id}`\n') for d in decisions)+'\n'

    def phase_list():
        if not phases:
            return '_Chưa có implementation phase; thêm tại Planning Intelligence._\n'
        return '\n'.join((f'### {index}. {_clean(p.title)} [{p.status}]\n'
                          f'- Summary: {_block(p.summary)}\n'
                          f'- Deliverables: {_block(p.deliverables)}\n'
                          f'- Acceptance: {_block(p.acceptance)}\n'
                          f'- Reference: `phase/{p.id}`; revision {p.revision}\n') for index,p in enumerate(phases,1))+'\n'

    product = (f'# Product Brief — {_clean(pr.name)}\n\n'
               f'## Product vision\n{_block(pr.description)}\n\n'
               f'## Target outcomes\n{goal_list()}\n'
               f'## Product profile\n{_clean(str(pr.profile or {}), 5500)}\n\n'
               f'## Assumptions and open questions\n'
               f'{len([q for q in questions if q.status=="pending"])} Guided Planning questions are pending.\n\n'
               f'## Research index\n'+ ('\n'.join(f'- [{_clean(r.title or r.url)}]({_clean(r.url)}) (`research/{r.id}`)' for r in research) if research else '_No research saved yet._')+'\n')
    artifacts['product-brief']=product
    artifacts['prd']=(f'# Product Requirements Document — {_clean(pr.name)}\n\n'
                      f'## Purpose and scope\n{_block(pr.description)}\n\n'
                      f'## Measurable goals\n{goal_list()}\n'
                      f'## Functional / non-functional requirements\n{req_list()}\n'
                      f'## Constraints & assumptions\n{_clean(str(pr.profile or {}), 4000)}\n\n'
                      f'## Explicitly unresolved\n{len([q for q in questions if q.status=="pending"])} pending planning questions.\n')
    artifacts['architecture']=(f'# Solution Architecture — {_clean(pr.name)}\n\n'
                                f'## Architecture drivers\n{goal_list()}\n'
                                f'## Accepted and proposed technical decisions\n{decision_list()}\n'
                                f'## Requirement traceability\n{req_list()}\n'
                                f'## System components / boundaries\n**[CẦN XÁC NHẬN]** Define services, trust boundaries and topology.\n\n'
                                f'## Failure modes, scaling and deployment\n**[CẦN XÁC NHẬN]** Add sequence diagrams, capacity assumptions and recovery.\n')
    artifacts['ux']=(f'# UX Specification — {_clean(pr.name)}\n\n'
                     f'## User journeys and target outcomes\n{goal_list()}\n'
                     f'## Requirements\n{req_list()}\n'
                     f'## Screens, navigation, states, accessibility\n'
                     f'**[CẦN XÁC NHẬN]** Add flows, edge states, responsive layouts and screen references.\n')
    artifacts['api']=(f'# API and Data Contracts — {_clean(pr.name)}\n\n'
                      f'## Requirement drivers\n{req_list()}\n'
                      f'## System of record, schemas and relationships\n'
                      f'**[CẦN XÁC NHẬN]** Define schema, PII classification, retention, migration and indexes.\n\n'
                      f'## API endpoints, errors, idempotency, auth\n'
                      f'**[CẦN XÁC NHẬN]** Define route contracts with concrete input/output examples.\n\n'
                      f'## Integration decisions\n{decision_list()}\n')
    artifacts['security']=(f'# Security & Reliability — {_clean(pr.name)}\n\n'
                           f'## Security requirements\n{req_list()}\n'
                           f'## Threat model\n**[CẦN XÁC NHẬN]** Assets, actors, entry points, trust boundaries, attack scenarios.\n\n'
                           f'## Access control, authentication, secrets\n**[CẦN XÁC NHẬN]** Define authorization matrix and audit policy.\n\n'
                           f'## Backup, restore, monitoring and DR\n**[CẦN XÁC NHẬN]** Define RPO, RTO and drills.\n')
    artifacts['roadmap']=(f'# Epics & Implementation Roadmap — {_clean(pr.name)}\n\n'
                          f'## Product goals\n{goal_list()}\n'
                          f'## Milestones / workstreams\n{phase_list()}\n'
                          f'## Backlog trace\n{req_list()}\n'
                          f'## Implementation approval gate\nDraft content does not authorize coding or production release.\n')
    artifacts['acceptance']=(f'# Acceptance & Test Strategy — {_clean(pr.name)}\n\n'
                             f'## User-facing acceptance criteria\n{req_list()}\n'
                             f'## Test levels and environments\n**[CẦN XÁC NHẬN]** Unit, integration, E2E, contract, accessibility, load and security tests.\n\n'
                             f'## Release, rollback and defect policy\n**[CẦN XÁC NHẬN]** Define measurable exit criteria.\n')
    artifacts['adr']=(f'# Architecture Decision Register — {_clean(pr.name)}\n\n'
                      f'## Decisions and rationale\n{decision_list()}\n'
                      f'## Open decision briefs\n'+ ('\n'.join(f'- {_clean(q.question)} (`planning-question/{q.id}`, status `{q.status}`)' for q in questions) if questions else '_None_')+'\n')
    artifacts['handoff']=(f'# Implementation Handoff — {_clean(pr.name)}\n\n'
                          f'## Project identity\nID: `{pr.id}` · Stage: `{pr.stage}`\n\n'
                          f'## Must-read documents\n'+ '\n'.join(f'- `{path}` — {label}' for label,path in ARTIFACTS.values())+'\n\n'
                          f'## Work plan\n{phase_list()}\n'
                          f'## Approval and known gaps\nThis bundle contains generated drafts. Review BMAD gates, findings, unresolved questions and acceptance before implementation.\n')
    answers=[]
    for q in questions:
        if q.status!='answered':continue
        a=q.answer or {}
        labels=a.get('selected_labels') or []
        answer=', '.join(str(x) for x in labels) if isinstance(labels,list) else str(labels)
        if a.get('custom_answer'):answer += ('; ' if answer else '')+str(a['custom_answer'])
        if a.get('note'):answer += '; Ghi chú: '+str(a['note'])
        answers.append(f'- **{_clean(q.question,450)}** (`planning-question/{q.id}`): {_block(answer)}')
    appendix=('\n\n## User-confirmed discovery answers (NOT formal approvals)\n'
              +'\n'.join(answers[:60])+'\n') if answers else ''
    return {key:{'title':ARTIFACTS[key][0], 'path':ARTIFACTS[key][1],
                 'content':(header+value+appendix)[:MAX_TEXT//2]}
            for key,value in artifacts.items()}


def publish_artifacts(db,pid,keys,overwrite=False):
    available=build_artifacts(db,pid)
    if not isinstance(keys,list) or not keys or len(keys)>len(available) or len(keys)!=len(set(keys)):
        raise HTTPException(422,'Invalid artifact selection')
    if any(k not in available for k in keys):
        raise HTTPException(422,'Unknown artifact')
    # Preflight ALL requested files before doing any writes.
    actions=[]
    for key in keys:
        draft=available[key]
        existing=db.query(Document).filter_by(project_id=pid,path=draft['path']).first()
        physical=resolve(pid,draft['path'])
        if existing and existing.status=='approved':
            raise HTTPException(409,f'Approved document cannot be overwritten: {draft["path"]}')
        if (existing or physical.exists()) and not overwrite:
            raise HTTPException(409,f'Document already exists: {draft["path"]}')
        if physical.exists() and physical.is_dir():
            raise HTTPException(409,f'Path is a directory: {draft["path"]}')
        actions.append((draft,existing))
    # Reserve batch totals before the first file mutation; includes new files and
    # one snapshot per overwritten file. Actual writes still enforce quota.
    reserve_bytes=0
    reserve_files=0
    for draft,_existing in actions:
        target=resolve(pid,draft['path'])
        reserve_bytes+=len(draft['content'].encode('utf-8'))
        reserve_files+=1
        if target.is_file():
            reserve_bytes+=target.stat().st_size
            reserve_files+=1
    enforce_project_quota(pid,extra_bytes=reserve_bytes,extra_files=reserve_files)
    result=[]
    for draft,existing in actions:
        write_text(pid,draft['path'],draft['content'])
        if existing:
            existing.revision+=1
            existing.updated_at=utcnow()
            existing.status='draft'
            record=existing
        else:
            record=Document(project_id=pid,path=draft['path'],title=draft['title'],status='draft',revision=1)
            db.add(record)
        db.flush()
        result.append({'id':record.id,'title':record.title,'path':draft['path'],'revision':record.revision,'status':'draft'})
    return result
