"""Project-scoped, evidence-aware decision briefs for human-guided planning.

No framework package install is required: app.py imports this file directly.
"""
from sqlalchemy.orm import Session
from fastapi import HTTPException
from db import PlanningQuestion, Message, ResearchItem, AgentRun, ChatStreamEvent, ReviewJob, utcnow


def tradeoff_text(value):
    if isinstance(value,str):
        return value.strip()[:600]
    if isinstance(value,list):
        return ' · '.join(item.strip() for item in value[:4] if isinstance(item,str) and item.strip())[:600]
    return ''


def normalize_question(data):
    """Constrain the model's proposed decision to a predictable, safe JSON contract."""
    if not isinstance(data, dict):
        raise ValueError('Question must be an object')
    question = str(data.get('question', '')).strip()[:600]
    context = str(data.get('context', '')).strip()[:2200]
    why = str(data.get('recommendation_reason', '')).strip()[:2000]
    impact = str(data.get('impact', '')).strip()[:1100]
    raw = data.get('options', [])
    if not (10 <= len(question) and isinstance(raw, list) and 2 <= len(raw) <= 4) or len(why) < 10:
        raise ValueError('Cần một câu hỏi rõ ràng và 2–4 phương án')
    options = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError('Invalid option')
        label = str(entry.get('label', '')).strip()[:110]
        pros, cons = tradeoff_text(entry.get('pros')), tradeoff_text(entry.get('cons'))
        if not label or not pros or not cons:
            raise ValueError('Mỗi phương án cần tên, ưu điểm và nhược điểm')
        options.append({
            'id': f'o{i+1}', 'label': label,
            'description': str(entry.get('description', '')).strip()[:700],
            'pros': pros,
            'cons': cons,
        })
    try:
        recommended = int(data['recommended_index'])
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError('Cần chỉ rõ phương án khuyến nghị') from exc
    if not 0 <= recommended < len(options):
        raise ValueError('Phương án khuyến nghị không hợp lệ')
    assumptions = data.get('assumptions', [])
    if not isinstance(assumptions, list): assumptions = []
    assumptions = [str(x).strip()[:300] for x in assumptions[:6] if str(x).strip()]
    return {
        'question': question, 'context': context, 'options': options,
        'recommended_id': options[recommended]['id'],
        'recommendation_reason': why,
        'impact': impact, 'assumptions': assumptions,
        'sources': [str(u).strip()[:1800] for u in data.get('sources', [])[:8]] if isinstance(data.get('sources', []),list) else [],
        'allow_multiple': bool(data.get('allow_multiple', False)),
    }


def save_question(db: Session, project_id: str, discussion_id: str, raw: dict):
    """One open question per discussion: never flood user with a questionnaire."""
    pending = db.query(PlanningQuestion).filter_by(
        project_id=project_id, discussion_id=discussion_id, status='pending'
    ).order_by(PlanningQuestion.created_at.desc()).first()
    if pending:
        return pending, False
    parsed = normalize_question(raw)
    candidate_urls = [u for u in parsed['sources'] if u.startswith(('https://','http://'))]
    verified = db.query(ResearchItem).filter(ResearchItem.project_id==project_id,ResearchItem.url.in_(candidate_urls)).all() if candidate_urls else []
    parsed['sources'] = [{'title':r.title[:200], 'url':r.url} for r in verified][:6]
    item = PlanningQuestion(project_id=project_id, discussion_id=discussion_id, **parsed)
    db.add(item)
    db.commit()
    return item, True


def question_payload(item):
    return {
        'id': item.id, 'project_id': item.project_id, 'discussion_id': item.discussion_id,
        'question': item.question, 'context': item.context, 'options': item.options,
        'recommended_id': item.recommended_id,
        'recommendation_reason': item.recommendation_reason, 'impact': item.impact,
        'assumptions': item.assumptions or [], 'sources': item.sources or [], 'allow_multiple': item.allow_multiple,
        'status': item.status, 'answer': item.answer or {},
        'answer_history': item.answer_history or [], 'created_at': item.created_at.isoformat(),
    }


def question_threads(db, project_id, discussion_id):
    """Join saved briefs to their original chat turn and answer messages for display."""
    questions = db.query(PlanningQuestion).filter_by(
        project_id=project_id, discussion_id=discussion_id
    ).order_by(PlanningQuestion.created_at, PlanningQuestion.id).all()
    messages = db.query(Message).filter_by(discussion_id=discussion_id).order_by(Message.created_at, Message.id).all()
    events = db.query(ChatStreamEvent).join(AgentRun).filter(
        AgentRun.project_id==project_id, AgentRun.discussion_id==discussion_id,
        ChatStreamEvent.event['type'].as_string()=='planning_question'
    ).order_by(ChatStreamEvent.id).all()
    payloads = {question.id:{**question_payload(question), 'message_ids':[], 'run_id':None,
                            'timeline_at':question.created_at.isoformat()} for question in questions}
    for event in events:
        question_id = (event.event.get('payload') or {}).get('id')
        if question_id in payloads and payloads[question_id]['run_id'] is None:
            payloads[question_id]['run_id'] = event.run_id
    for message in messages:
        if message.role=='assistant':
            for payload in payloads.values():
                if payload['run_id'] and payload['run_id']==message.run_id:
                    payload['message_ids'].append(message.id)
                    payload['timeline_at'] = message.created_at.isoformat()
        elif message.role=='user' and message.content.startswith('[TRẢ LỜI CÂU HỎI LẬP KẾ HOẠCH]\nCâu hỏi: '):
            candidates = [question for question in questions if question.created_at<=message.created_at
                          and message.content.startswith(f'[TRẢ LỜI CÂU HỎI LẬP KẾ HOẠCH]\nCâu hỏi: {question.question}\nPhương án người dùng chọn: ')]
            if candidates:
                payloads[candidates[-1].id]['message_ids'].append(message.id)
    return list(payloads.values())


def answer_question(db: Session, question: PlanningQuestion, data: dict, *, commit: bool = True):
    if question.status != 'pending':
        raise HTTPException(409, 'Câu hỏi này đã được xử lý')
    action = str(data.get('action', 'answer'))
    if action not in ('answer', 'defer'):
        raise HTTPException(422, 'Thao tác không hợp lệ')
    if action == 'defer':
        question.status = 'deferred'
        question.answer = {'note': str(data.get('note', ''))[:1000]}
        record_answer(question)
        if commit:db.commit()
        return question
    selected = data.get('selected_ids', [])
    if not isinstance(selected, list) or len(selected) > 4:
        raise HTTPException(422, 'Danh sách lựa chọn không hợp lệ')
    selected = list(dict.fromkeys(str(s) for s in selected))
    valid = {o['id'] for o in question.options}
    if any(s not in valid for s in selected):
        raise HTTPException(422, 'Phương án không thuộc câu hỏi')
    if not question.allow_multiple and len(selected) > 1:
        raise HTTPException(422, 'Câu hỏi chỉ cho phép chọn một phương án')
    custom = str(data.get('custom_answer', '')).strip()[:2000]
    note = str(data.get('note', '')).strip()[:2000]
    if not selected and not custom:
        raise HTTPException(422, 'Hãy chọn phương án hoặc nhập câu trả lời riêng')
    labels = [o['label'] for o in question.options if o['id'] in selected]
    question.answer = {'selected_ids': selected, 'selected_labels': labels, 'custom_answer': custom, 'note': note}
    question.status = 'answered'
    record_answer(question)
    # An explicit user choice is captured as conversation context, NOT a spec approval.
    answer_text = ', '.join(labels) if labels else custom
    details = f"\nĐiều chỉnh: {custom}" if selected and custom else ''
    details += f"\nGhi chú: {note}" if note else ''
    db.add(Message(discussion_id=question.discussion_id, role='user', content=(
        f'[TRẢ LỜI CÂU HỎI LẬP KẾ HOẠCH]\nCâu hỏi: {question.question}\n'
        f'Phương án người dùng chọn: {answer_text}{details}\n'
        'Đây là câu trả lời xác nhận trong trao đổi, KHÔNG phải quyết định đã được duyệt trong Decision Center.'
    )))
    if commit:db.commit()
    return question


def record_answer(question):
    question.answer_history = [*(question.answer_history or []), {
        'status': question.status, 'answer': dict(question.answer or {}),
        'recorded_at': utcnow().isoformat(),
    }]


def reopen_question(db, project, question, actor_id):
    """Caller holds the project write lock. Preserve the prior branch for review."""
    if question.status not in ('answered', 'deferred'):
        raise HTTPException(409, 'Chỉ có thể quay lại câu hỏi đã trả lời hoặc tạm hoãn')
    if db.query(AgentRun.id).filter(AgentRun.project_id==project.id,
            AgentRun.status.in_(('queued', 'running'))).first():
        raise HTTPException(409, 'Hãy đợi lượt AI hiện tại hoàn thành hoặc bấm Dừng trước khi quay lại')
    if db.query(ReviewJob.id).filter(ReviewJob.project_id==project.id,
            ReviewJob.status.in_(('queued', 'running'))).first():
        raise HTTPException(409, 'Hãy đợi review hiện tại hoàn thành trước khi quay lại')
    branch = db.query(PlanningQuestion).filter_by(
        project_id=project.id, discussion_id=question.discussion_id
    ).order_by(PlanningQuestion.created_at, PlanningQuestion.id).all()
    index = next(i for i, item in enumerate(branch) if item.id==question.id)
    for item in branch[index:]:
        if item.status=='superseded':
            continue
        # Legacy answers are captured lazily, without changing old messages.
        if item.status in ('answered', 'deferred') and not item.answer_history:
            record_answer(item)
        item.answer_history = [*(item.answer_history or []), {
            'status': 'reopened' if item.id==question.id else 'superseded',
            'recorded_at': utcnow().isoformat(), 'actor_id': actor_id,
        }]
        item.status = 'pending' if item.id==question.id else 'superseded'
        item.answer = {}
    state = dict((project.profile or {}).get('_discovery') or {})
    state.update(status='interviewing', reopened_question_id=question.id,
                 branch_started_at=utcnow().isoformat(), review_required=True)
    project.profile = {**(project.profile or {}), '_discovery': state}
    db.flush()
    return question


QUESTION_JSON_INSTRUCTIONS = """Create ONE decision brief from the user's actual project.
Your output must be ONLY a JSON object with fields:
question, context, options (2-4 objects with string fields label, description, pros, cons),
recommended_index (0-based), recommendation_reason, impact, assumptions (array), sources (array of cited URLs from project research), allow_multiple (boolean).
You MUST examine confirmed context and recent questions; don't ask a duplicate.
Prefer the single highest-impact *unanswered* design decision.
Every option must be viable. Explain realistic benefits, costs and what changes the choice.
Keep the question and options concise. Keep option labels neutral; indicate the recommendation only through recommended_index and recommendation_reason.
Recommend exactly one suitable default, but do NOT claim certainty without evidence.
Use Vietnamese. Only cite research URLs previously provided in the project research context; otherwise use sources: []. Do not invent research citations or imply approval.
If information is insufficient, mark it explicitly as an assumption.
Do not ask the user to formulate all the approaches themselves."""
