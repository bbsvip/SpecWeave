"""Detached Pi conversation regression: browser closure must never own the agent."""
import asyncio
import json

from test_app import client, auth, _make_discussion_with_model, SessionLocal, db
from chat_background import run_once


def test_detached_run_completes_without_any_open_sse_subscriber(client, monkeypatch):
    import app as application
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'detached-run')
    seen=[]
    async def pi_stub(project,discussion,sess,model,**kwargs):
        seen.append(kwargs['run_id'])
        yield 'status',{'phase':'reasoning','message':'Pi đang phân tích'}
        yield 'thinking',{'text':'Đang xem phương án'}
        yield 'tool',{'name':'search_web','id':'demo','status':'running',
                      'arguments':r'{"query":"t\\u1eadp trung"}'}
        yield 'delta',{'text':'Một câu trả lời tiếng Việt hoàn chỉnh.'}
        yield 'usage',{'input_tokens':234,'output_tokens':52}
    monkeypatch.setattr(application,'pi_stream',pi_stub)
    result=client.post(f'/api/projects/{pid}/chat/start',headers=csrf,json={
        'discussion_id':did,'message':'Phân tích rủi ro', 'role':'analyst','mode':'discovery'})
    assert result.status_code==200,result.text
    job=result.json();run_id=job['run_id']
    with SessionLocal() as sess:
        assert sess.get(db.AgentRun,run_id).status=='queued'
        assert sess.query(db.Message).filter_by(discussion_id=did,role='user').count()==1
    # No browser connection at all. The dedicated worker runs Pi independently.
    assert asyncio.run(run_once())==run_id
    assert seen==[run_id]
    with SessionLocal() as sess:
        run=sess.get(db.AgentRun,run_id)
        assert run.status=='completed' and run.usage.get('output_tokens')==52
        events=sess.query(db.ChatStreamEvent).filter_by(run_id=run_id).order_by(db.ChatStreamEvent.id).all()
        assert events[0].event['payload']['phase']=='queued'
        assert any(x.event['type']=='thinking' for x in events)
        assert any(x.event['type']=='tool' for x in events)
        assert events[-1].event['type']=='done'
        assert sess.query(db.Message).filter_by(discussion_id=did,role='user').count()==1
        assert sess.query(db.Message).filter_by(discussion_id=did,role='assistant').count()==1
    # A user can reconnect after completion and replay the exact persisted frames.
    replay=client.get(job['events_url'])
    assert replay.status_code==200,replay.text
    assert 'id: ' in replay.text
    assert '"type": "done"' in replay.text
    assert 'Một câu trả lời tiếng Việt' in replay.text
    other=client.get(f'/api/projects/00000000-0000-0000-0000-000000000000/runs/{run_id}/events')
    assert other.status_code in (403,404)


def test_cancel_queued_run_prevents_any_pi_execution(client,monkeypatch):
    import app as application
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'queued-cancel')
    async def pi_nope(*args,**kwargs):
        raise AssertionError('Cancelled queue must not invoke Pi')
        yield 'delta',{'text':'never'}
    monkeypatch.setattr(application,'pi_stream',pi_nope)
    r=client.post(f'/api/projects/{pid}/chat/start',headers=csrf,json={
        'discussion_id':did,'message':'Xin chào'})
    assert r.status_code==200
    run_id=r.json()['run_id']
    cancelled=client.post(f'/api/projects/{pid}/runs/{run_id}/cancel',headers=csrf)
    assert cancelled.status_code==200
    assert asyncio.run(run_once()) is None
    replay=client.get(r.json()['events_url'])
    assert 'Đã hủy trước khi Pi bắt đầu' in replay.text


def test_second_message_cannot_queue_in_busy_discussion(client):
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'busy-discussion')
    path=f'/api/projects/{pid}/chat/start'
    first=client.post(path,headers=csrf,json={'discussion_id':did,'message':'Hi'})
    assert first.status_code==200
    second=client.post(path,headers=csrf,json={'discussion_id':did,'message':'Again'})
    assert second.status_code==409
    with SessionLocal() as sess:
        assert sess.query(db.AgentRun).filter_by(discussion_id=did,status='queued').count()==1
        assert sess.query(db.Message).filter_by(discussion_id=did,role='user').count()==1


def test_unicode_tool_display_does_not_mutate_payload():
    from app import pretty_tool_value
    payload='{"query":"dashboard t\\u1eadp trung","description":"Kh\\u00e1m ph\\u00e1"}'
    assert 'tập trung' in pretty_tool_value(payload)
    assert 'Khám phá' in pretty_tool_value(payload)
    assert '\\u1eadp' in payload  # input still untouched


def test_decision_answer_enqueues_same_pi_session_once(client,monkeypatch):
    import app as application
    from planning import save_question
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'decision-detached')
    with SessionLocal() as sess:
        q,_=save_question(sess,pid,did,{
            'question':'Lưu trạng thái giao dịch bằng cách nào?',
            'options':[{'label':'PostgreSQL','pros':'Nhất quán','cons':'Thêm nghiệp vụ'},
                       {'label':'Đối tác','pros':'MVP nhanh','cons':'Phụ thuộc API'}],
            'recommended_index':0,'recommendation_reason':'Dễ kiểm toán và đối soát giao dịch'})
        qid=q.id
    async def pi_stub(project,discussion,sess,model,**kwargs):
        assert discussion.id==did
        with SessionLocal() as current:
            assert current.get(db.PlanningQuestion,qid).status=='answered'
        yield 'delta',{'text':'Đã hiểu lựa chọn của anh, tiếp tục làm rõ phạm vi.'}
    monkeypatch.setattr(application,'pi_stream',pi_stub)
    answer=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer-start',headers=csrf,
                       json={'selected_ids':['o1'],'custom_answer':'','note':'Cần audit'})
    assert answer.status_code==200,answer.text
    rid=answer.json()['run_id']
    with SessionLocal() as sess:
        question=sess.get(db.PlanningQuestion,qid)
        assert question.status=='answered'
        assert question.answer['continuation_run_id']==rid
        assert sess.query(db.Message).filter_by(discussion_id=did,role='user').count()==1
    assert asyncio.run(run_once())==rid
    with SessionLocal() as sess:
        assert sess.get(db.AgentRun,rid).status=='completed'
        assert sess.query(db.Message).filter_by(discussion_id=did,role='user').count()==1
        assert sess.query(db.Message).filter_by(discussion_id=did,role='assistant').count()==1
    again=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer-start',headers=csrf,
                      json={'selected_ids':['o1']})
    assert again.status_code==409


def test_reconnect_uses_eventsource_without_issuing_new_prompt():
    from pathlib import Path
    code=(Path(__file__).resolve().parents[1]/'source/static/js/discussion.js').read_text(encoding='utf-8')
    assert 'new EventSource(url,{withCredentials:true})' in code
    assert '/active-run' in code
    assert '/answer-start' in code
    assert '/chat/start' in code
    assert 'source.onerror' in code
    # Tab navigation does not call /runs/.../cancel; only explicit Stop does.
    assert 'window.addEventListener(\'beforeunload\'' not in code
