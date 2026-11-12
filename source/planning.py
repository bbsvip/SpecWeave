"""Project-scoped, evidence-aware decision briefs for human-guided planning.

No framework package install is required: app.py imports this file directly.
"""
from sqlalchemy.orm import Session
from fastapi import HTTPException
from db import PlanningQuestion, Message, ResearchItem


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
        if not label or not str(entry.get('pros', '')).strip() or not str(entry.get('cons', '')).strip():
            raise ValueError('Mỗi phương án cần tên, ưu điểm và nhược điểm')
        options.append({
            'id': f'o{i+1}', 'label': label,
            'description': str(entry.get('description', '')).strip()[:700],
            'pros': str(entry.get('pros', '')).strip()[:600],
            'cons': str(entry.get('cons', '')).strip()[:600],
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
    }


def answer_question(db: Session, question: PlanningQuestion, data: dict, *, commit: bool = True):
    if question.status != 'pending':
        raise HTTPException(409, 'Câu hỏi này đã được xử lý')
    action = str(data.get('action', 'answer'))
    if action not in ('answer', 'defer'):
        raise HTTPException(422, 'Thao tác không hợp lệ')
    if action == 'defer':
        question.status = 'deferred'
        question.answer = {'note': str(data.get('note', ''))[:1000]}
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


QUESTION_JSON_INSTRUCTIONS = """Create ONE decision brief from the user's actual project.
Your output must be ONLY a JSON object with fields:
question, context, options (array of 2-4 objects with label,description,pros,cons),
recommended_index (0-based), recommendation_reason, impact, assumptions (array), sources (array of cited URLs from project research), allow_multiple (boolean).
You MUST examine confirmed context and recent questions; don't ask a duplicate.
Prefer the single highest-impact *unanswered* design decision.
Every option must be viable. Explain realistic benefits, costs and what changes the choice.
Keep the question and options concise. Keep option labels neutral; indicate the recommendation only through recommended_index and recommendation_reason.
Recommend exactly one suitable default, but do NOT claim certainty without evidence.
Use Vietnamese. Only cite research URLs previously provided in the project research context; otherwise use sources: []. Do not invent research citations or imply approval.
If information is insufficient, mark it explicitly as an assumption.
Do not ask the user to formulate all the approaches themselves."""
