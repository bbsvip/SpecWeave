"""Deterministic, project-local planning intelligence and handoff checks.

A recommendation or a coverage score is not an approval. LLMs may discuss the
report, but only explicit owner actions can approve goals, phases and docs.
Run directly with the app's source directory on sys.path; no package install.
"""
from collections import Counter
from sqlalchemy.orm import Session
from fastapi import HTTPException
from db import (ProjectGoal, Requirement, Decision, Document, ResearchItem,
                PlanPhase, TraceLink, PhaseDependency, PlanningQuestion)

NODE_MODELS = {
    "goal": ProjectGoal, "requirement": Requirement,
    "decision": Decision, "document": Document, "phase": PlanPhase,
    "research": ResearchItem,
}
ALLOWED_LINKS = {
    ("goal", "requirement"), ("goal", "phase"),
    ("requirement", "decision"), ("requirement", "document"),
    ("requirement", "phase"), ("decision", "document"),
    ("decision", "phase"), ("phase", "document"),
    ("research", "requirement"), ("research", "decision"),
}


def project_node(db: Session, project_id: str, node_type: str, node_id: str):
    model = NODE_MODELS.get(node_type)
    if not model:
        raise HTTPException(422, "Loại đối tượng trace không hợp lệ")
    node = db.get(model, node_id)
    if node is None or node.project_id != project_id:
        raise HTTPException(404, "Không tìm thấy đối tượng trong dự án")
    return node


def link_nodes(db: Session, project_id: str, data: dict, actor_id: str):
    src_type = str(data.get('source_type', ''))
    dst_type = str(data.get('target_type', ''))
    if (src_type, dst_type) not in ALLOWED_LINKS:
        raise HTTPException(422, 'Kiểu liên kết không được hỗ trợ')
    source_id = str(data.get('source_id', ''))
    target_id = str(data.get('target_id', ''))
    project_node(db, project_id, src_type, source_id)
    project_node(db, project_id, dst_type, target_id)
    relation = str(data.get('relation', 'supports'))
    if relation not in ('supports', 'defines', 'implements', 'validates', 'informs'):
        raise HTTPException(422, 'Quan hệ không hợp lệ')
    found = db.query(TraceLink).filter_by(project_id=project_id, source_type=src_type,
        source_id=source_id, target_type=dst_type, target_id=target_id, relation=relation).first()
    if found:
        return found, False
    link = TraceLink(project_id=project_id, source_type=src_type, source_id=source_id,
                     target_type=dst_type, target_id=target_id, relation=relation,
                     note=str(data.get('note',''))[:1000], created_by=actor_id)
    db.add(link)
    db.flush()
    return link, True


def has_dependency_cycle(phase_ids, pairs):
    parents = {pid: set() for pid in phase_ids}
    for a, b in pairs:
        parents.setdefault(a, set()).add(b)
    visited, active = set(), set()
    def walk(node):
        if node in active:
            return True
        if node in visited:
            return False
        active.add(node)
        for parent in parents.get(node, ()):
            if walk(parent):
                return True
        active.remove(node)
        visited.add(node)
        return False
    return any(walk(x) for x in parents)


def add_phase_dependency(db: Session, project_id: str, phase_id: str, prerequisite_id: str):
    project_node(db, project_id, 'phase', phase_id)
    project_node(db, project_id, 'phase', prerequisite_id)
    if phase_id == prerequisite_id:
        raise HTTPException(422, 'Giai đoạn không thể phụ thuộc chính nó')
    existing = db.query(PhaseDependency).filter_by(project_id=project_id,
        phase_id=phase_id, prerequisite_id=prerequisite_id).first()
    if existing:
        return existing, False
    rows = db.query(PhaseDependency).filter_by(project_id=project_id).all()
    pairs = [(x.phase_id, x.prerequisite_id) for x in rows] + [(phase_id, prerequisite_id)]
    ids = [x.id for x in db.query(PlanPhase).filter_by(project_id=project_id).all()]
    if has_dependency_cycle(ids, pairs):
        raise HTTPException(409, 'Phụ thuộc tạo vòng lặp trong kế hoạch')
    dependency = PhaseDependency(project_id=project_id, phase_id=phase_id,
                                 prerequisite_id=prerequisite_id)
    db.add(dependency)
    db.flush()
    return dependency, True


def phase_review_blockers(db: Session, phase: PlanPhase):
    failures = []
    if not phase.summary.strip():
        failures.append('Thiếu mô tả giai đoạn')
    if not phase.deliverables.strip():
        failures.append('Thiếu deliverables')
    if not phase.acceptance.strip():
        failures.append('Thiếu tiêu chí nghiệm thu')
    prereqs = db.query(PhaseDependency).filter_by(project_id=phase.project_id, phase_id=phase.id).all()
    for dep in prereqs:
        prerequisite = project_node(db, phase.project_id, 'phase', dep.prerequisite_id)
        if prerequisite.status != 'approved':
            failures.append('Giai đoạn phụ thuộc chưa được duyệt: ' + prerequisite.title)
    links = db.query(TraceLink).filter_by(project_id=phase.project_id,
        source_type='requirement', target_type='phase', target_id=phase.id).all()
    if not links:
        failures.append('Chưa liên kết requirement nào với giai đoạn')
    return failures


def _label(node_type, item):
    if node_type == 'requirement':
        return f'{item.code} — {item.title}'
    if node_type == 'research':
        return item.title or item.url
    return item.title


def graph_payload(db: Session, project_id: str):
    nodes = []
    for node_type, model in NODE_MODELS.items():
        for item in db.query(model).filter_by(project_id=project_id).all():
            nodes.append({'id': item.id, 'type': node_type, 'label': _label(node_type, item),
                          'status': getattr(item, 'status', 'evidence'),
                          'code': getattr(item, 'code', None)})
    links = db.query(TraceLink).filter_by(project_id=project_id).all()
    dependencies = db.query(PhaseDependency).filter_by(project_id=project_id).all()
    edges = [{'id': link.id, 'source': link.source_id, 'target': link.target_id,
              'source_type': link.source_type, 'target_type': link.target_type,
              'relation': link.relation, 'note': link.note} for link in links]
    edges += [{'id': dep.id, 'source': dep.prerequisite_id, 'target': dep.phase_id,
               'source_type': 'phase', 'target_type': 'phase', 'relation': 'precedes',
               'note': ''} for dep in dependencies]
    return {'nodes': nodes, 'edges': edges}


def coverage_report(db: Session, project_id: str):
    goals = db.query(ProjectGoal).filter_by(project_id=project_id).all()
    reqs = db.query(Requirement).filter_by(project_id=project_id).all()
    decisions = db.query(Decision).filter_by(project_id=project_id).all()
    docs = db.query(Document).filter_by(project_id=project_id).all()
    phases = db.query(PlanPhase).filter_by(project_id=project_id).all()
    links = db.query(TraceLink).filter_by(project_id=project_id).all()
    deps = db.query(PhaseDependency).filter_by(project_id=project_id).all()
    questions = db.query(PlanningQuestion).filter_by(project_id=project_id, status='pending').all()
    issues = []

    def problem(code, severity, message, entity_type='project', entity_id=None, recommendation=''):
        issues.append({'code':code,'severity':severity,'message':message,
                       'entity_type':entity_type,'entity_id':entity_id,
                       'recommendation':recommendation})

    if not goals:
        problem('NO_GOALS','blocker','Chưa có mục tiêu sản phẩm cụ thể.',recommendation='Tạo mục tiêu và kết quả mong đợi trước khi viết spec.')
    elif not any(g.status == 'approved' for g in goals):
        problem('GOALS_UNAPPROVED','blocker','Chưa có mục tiêu nào được người phụ trách duyệt.',recommendation='Xác nhận mục tiêu ưu tiên.')
    for g in goals:
        if not g.outcome.strip():
            problem('GOAL_NO_OUTCOME','warning',f'Mục tiêu "{g.title}" thiếu kết quả mong muốn.','goal',g.id)
    if not reqs:
        problem('NO_REQUIREMENTS','blocker','Chưa có requirements.',recommendation='Phân rã mục tiêu thành yêu cầu và acceptance criteria.')
    for r in reqs:
        important = r.priority in ('must', 'should')
        if important and not r.criteria.strip():
            problem('REQ_NO_CRITERIA','blocker',f'{r.code} thiếu tiêu chí nghiệm thu.','requirement',r.id)
        if important and r.status != 'approved':
            problem('REQ_UNAPPROVED','blocker',f'{r.code} chưa được duyệt.','requirement',r.id)
        if not any(x.target_type == 'requirement' and x.target_id == r.id and x.source_type == 'goal' for x in links):
            problem('REQ_NO_GOAL','warning',f'{r.code} chưa được liên kết với mục tiêu.','requirement',r.id)
        if important and not any(x.source_type == 'requirement' and x.source_id == r.id and x.target_type == 'phase' for x in links):
            problem('REQ_NO_PHASE','blocker',f'{r.code} chưa được phân công vào phase triển khai.','requirement',r.id)
        if important and not any(x.source_type == 'requirement' and x.source_id == r.id and x.target_type in ('document','decision') for x in links):
            problem('REQ_NO_DESIGN','warning',f'{r.code} chưa có trace tới tài liệu hoặc ADR.','requirement',r.id)
    if not docs or not any(x.status == 'approved' for x in docs):
        problem('NO_APPROVED_DOCUMENT','blocker','Chưa có tài liệu đặc tả được duyệt.',recommendation='Soạn và duyệt PRD / architecture hoặc design spec.')
    if not phases:
        problem('NO_PHASES','blocker','Chưa có giai đoạn triển khai nào.',recommendation='Tạo roadmap theo các phase có deliverable và acceptance criteria.')
    for phase in phases:
        if phase.status != 'approved':
            problem('PHASE_UNAPPROVED','blocker',f'Phase "{phase.title}" chưa được duyệt.','phase',phase.id)
        for msg in phase_review_blockers(db,phase):
            problem('PHASE_REVIEW_GAP','blocker',f'{phase.title}: {msg}','phase',phase.id)
    if has_dependency_cycle([p.id for p in phases],[(d.phase_id,d.prerequisite_id) for d in deps]):
        problem('PHASE_CYCLE','blocker','Các phase có vòng lặp phụ thuộc.')
    if questions:
        problem('OPEN_QUESTIONS','blocker',f'Còn {len(questions)} câu hỏi thiết kế chưa giải quyết.',recommendation='Trả lời hoặc hoãn có lý do để đóng vòng discovery.')
    unreviewed = [d for d in decisions if d.status == 'proposed']
    if unreviewed:
        problem('OPEN_DECISIONS','warning',f'Còn {len(unreviewed)} đề xuất quyết định chưa được duyệt.')
    blockers = sum(i['severity']=='blocker' for i in issues)
    warnings = len(issues)-blockers
    totals = {'goals': len(goals), 'requirements': len(reqs), 'decisions': len(decisions),
              'documents': len(docs), 'phases': len(phases), 'links': len(links),
              'pending_questions': len(questions)}
    return {'ready': blockers==0, 'blockers':blockers, 'warnings':warnings,
            'issues': issues, 'totals':totals,
            'next_actions': issues[:8],
            'policy': 'deterministic-v1; ready is a handoff gate, not a claim of technical correctness'}


def report_markdown(report):
    lines = ['# Planning readiness review', '',
             f"**Trạng thái:** {'Ready for reviewed handoff' if report['ready'] else 'Not ready'}",
             f"**Blockers:** {report['blockers']} | **Warnings:** {report['warnings']}", '',
             'Kiểm tra theo quy tắc, không phải sự phê duyệt của AI hay đảm bảo đặc tả đúng tuyệt đối.', '',
             '## Issues', '']
    if not report['issues']:
        lines.append('Không phát hiện vấn đề theo các quy tắc hiện có.')
    for issue in report['issues']:
        lines.append(f"- **{issue['severity'].upper()} · {issue['code']}** — {issue['message']}")
        if issue['recommendation']:
            lines.append('  - Hướng xử lý: '+issue['recommendation'])
    return '\n'.join(lines)+'\n'
