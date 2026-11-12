"""Detached Pi conversation regression: browser closure must never own the agent."""
import asyncio
import json

from test_app import client, auth, _make_discussion_with_model, SessionLocal, db
from chat_background import claim_next, process_queued_run, run_once


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


def test_turn_without_reply_reports_the_outcome_live_and_on_reload(client, monkeypatch):
    import app as application
    from chat_content import EMPTY_REPLY

    csrf = auth(client)
    pid, did = _make_discussion_with_model(client, csrf, 'turn-outcomes')
    cases = iter([
        [('tool', {'name':'write_document', 'status':'complete'}), ('final', {'text':''})],
        [('thinking', {'text':'Thinking only'}), ('final', {'text':'', 'stop_reason':'length'})],
        [('final', {'text':'<tool_call>\n<function'})],
        [('thinking', {'text':'Thinking only'}), ('final', {'text':'', 'stop_reason':'stop'})],
    ])

    async def pi_stub(*args, **kwargs):
        for event in next(cases):
            yield event

    monkeypatch.setattr(application, 'pi_stream', pi_stub)
    labels = ('Có hoạt động công cụ', 'Đầu ra bị cắt', 'Lệnh công cụ chưa hoàn chỉnh', 'Chưa có câu trả lời')
    for index, label in enumerate(labels, start=1):
        result = client.post(f'/api/projects/{pid}/chat/start', headers=csrf, json={
            'discussion_id':did, 'message':f'Test {index}', 'mode':'discuss',
        })
        assert result.status_code == 200, result.text
        job = result.json()
        assert asyncio.run(run_once()) == job['run_id']
        events = [json.loads(line[6:]) for line in client.get(job['events_url']).text.splitlines()
                  if line.startswith('data: ')]
        assert events[-1]['type'] == 'done'
        assert events[-1]['payload']['notice']['label'] == label
        assert next(event for event in events if event['type']=='final')['payload']['text'] == ''
        with SessionLocal() as sess:
            reply = sess.query(db.Message).filter_by(run_id=job['run_id'], role='assistant').one()
            assert reply.content == ''
            if index == 1:
                # Old runs could reach the trace cap before tool execution.
                reply.content = EMPTY_REPLY
                sess.get(db.AgentRun,job['run_id']).events = []
                sess.commit()
            if index == 3:
                reply.content = '<tool_call>\n<function'
                sess.get(db.AgentRun,job['run_id']).events = []
                sess.commit()
        html = client.get(f'/projects/{pid}/discussion?did={did}').text
        assert label in html
        assert EMPTY_REPLY not in html and '&lt;tool_call&gt;' not in html
        assert html.count('class="turn-history-metrics"') == index


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


def test_discovery_fallback_runs_on_worker_once_per_saved_answer(client, monkeypatch):
    import app as application
    from planning import save_question

    csrf = auth(client)
    pid, did = _make_discussion_with_model(client, csrf, 'durable-discovery')
    brief = {
        'question': 'Phương án lưu trữ nào phù hợp?',
        'options': [{'label':'PostgreSQL','pros':'Nhất quán','cons':'Cần vận hành'},
                    {'label':'Tệp cục bộ','pros':'Dễ triển khai','cons':'Khó truy vấn'}],
        'recommended_index':0, 'recommendation_reason':'Cần đối soát và kiểm toán',
    }
    with SessionLocal() as sess:
        first, _ = save_question(sess, pid, did, brief)
        question_id = first.id

    attempts = []

    async def pi_stub(project, discussion, sess, model, **kwargs):
        attempts.append((discussion.id, kwargs['run_id'], 'prompt_override' in kwargs))
        with SessionLocal() as current:
            answered = current.query(db.PlanningQuestion).filter_by(discussion_id=did, status='answered').count()
        if 'prompt_override' not in kwargs:
            yield 'delta', {'text':'Đã ghi nhận lựa chọn.'}
        else:
            next_brief = {**brief, 'question':f'Câu hỏi kế tiếp về giới hạn số {answered}?'}
            yield 'final', {'text':'```json\n'+json.dumps(next_brief, ensure_ascii=False)+'\n```'}
        yield 'usage', {'input_tokens':10, 'output_tokens':5}

    monkeypatch.setattr(application, 'pi_stream', pi_stub)
    for answered in (1, 2):
        response = client.post(f'/api/projects/{pid}/planning/questions/{question_id}/answer-start',
                               headers=csrf, json={'selected_ids':['o1'], 'auto_next':True})
        assert response.status_code == 200, response.text
        job = response.json()
        run_id = job['run_id']
        assert asyncio.run(run_once()) == run_id  # No browser or SSE subscriber.
        assert attempts[-2:] == [(did, run_id, False), (did, run_id, True)]
        replay = client.get(job['events_url']).text
        assert replay.count('"type": "planning_question"') == 1
        assert '"type": "done"' in replay
        with SessionLocal() as sess:
            pending = sess.query(db.PlanningQuestion).filter_by(discussion_id=did, status='pending').one()
            assert pending.question.endswith(f'số {answered}?')
            assert sess.query(db.Message).filter_by(discussion_id=did, role='user').count() == answered
            assert sess.query(db.Message).filter_by(run_id=run_id, role='assistant').one().content == 'Đã ghi nhận lựa chọn.'
            assert sess.get(db.AgentRun, run_id).usage == {'input_tokens':20, 'output_tokens':10}
            question_id = pending.id


def test_advance_retries_truncated_document_once_on_the_worker(client, monkeypatch):
    import app as application

    csrf = auth(client)
    pid, did = _make_discussion_with_model(client, csrf, 'truncated-document')
    with SessionLocal() as sess:
        sess.get(db.Project,pid).profile={'documents':['Roadmap','UX / UI Design']}
        sess.add(db.Document(project_id=pid,path='docs/drafts/ROADMAP.md',title='Roadmap'))
        sess.commit()
    attempts = []

    async def pi_stub(project, discussion, sess, model, **kwargs):
        attempts.append((discussion.id, kwargs['run_id'], kwargs['prompt_override']))
        yield 'status', {'phase':'document_output_limit','message':'Tool output limit'}

    monkeypatch.setattr(application, 'pi_stream', pi_stub)
    result = client.post(f'/api/projects/{pid}/chat/start',headers=csrf,json={
        'discussion_id':did,'mode':'discovery','advance_planning':True})
    assert result.status_code==200,result.text
    job = result.json()
    assert asyncio.run(run_once())==job['run_id']
    assert [(discussion,run_id) for discussion,run_id,_ in attempts]==[(did,job['run_id'])]*2
    retry_prompt = attempts[1][2]
    assert 'UX-SPEC.md' in retry_prompt and 'ROADMAP.md' not in retry_prompt
    assert '400–800' in retry_prompt and 'append_to_revision' in retry_prompt
    replay = client.get(job['events_url']).text
    assert '"type": "planning_needs_input"' in replay and '"type": "done"' in replay
    with SessionLocal() as sess:
        assert sess.query(db.Message).filter_by(discussion_id=did,role='user').count()==0


def test_page_keeps_the_run_id_when_it_finishes_before_subscription(client, monkeypatch):
    import app as application
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'reload-during-chat')
    async def pi_stub(*args, **kwargs):
        yield 'thinking',{'text':'Thinking already streamed'}
        yield 'delta',{'text':'Durable response'}
    monkeypatch.setattr(application,'pi_stream',pi_stub)
    job=client.post(f'/api/projects/{pid}/chat/start',headers=csrf,json={
        'discussion_id':did,'message':'Original prompt'}).json()
    run_id=job['run_id']
    assert claim_next()==run_id
    page=client.get(f'/projects/{pid}/discussion?did={did}')
    assert f'data-active-run="{run_id}"' in page.text
    assert 'id="piRunPlaceholder"' in page.text
    active=f'/api/projects/{pid}/discussions/{did}/active-run'
    assert client.get(active).json()=={'run_id':run_id,'status':'running'}
    assert client.get(active).headers['cache-control']=='no-store'

    asyncio.run(process_queued_run(run_id))
    assert client.get(active).json()=={'run_id':None,'status':'idle'}
    assert 'Durable response' in client.get(f'/projects/{pid}/discussion?did={did}').text
    response=client.get(job['events_url'])
    ids=[int(line[4:]) for line in response.text.splitlines() if line.startswith('id: ')]
    assert len(ids)>=5
    resumed=client.get(job['events_url'],headers={'Last-Event-ID':str(ids[-2])})
    assert resumed.text.count('data: ')==1
    assert '"type": "done"' in resumed.text
    assert client.get(job['events_url'],headers={'Last-Event-ID':str(ids[-1])}).text==''


def test_workers_overlap_between_discussions_and_retry_same_id_after_lock(client, monkeypatch):
    import app as application
    from chat_background import chat_worker_main
    from chat_coordination import discussion_lease
    from datetime import timedelta
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'parallel-chat')
    other=client.post(f'/api/projects/{pid}/discussions',headers=csrf,json={'title':'Other'}).json()['id']
    first,second=[client.post(f'/api/projects/{pid}/chat/start',headers=csrf,json={
        'discussion_id':discussion,'message':discussion}).json()['run_id'] for discussion in (did,other)]

    async def exercise():
        entered=set()
        overlap=asyncio.Event()
        async def pi_stub(project,discussion,sess,model,**kwargs):
            assert kwargs['run_id'] not in entered
            entered.add(kwargs['run_id'])
            if entered=={first,second}:
                overlap.set()
            await asyncio.wait_for(overlap.wait(),5)
            yield 'delta',{'text':discussion.id}
        monkeypatch.setattr(application,'pi_stream',pi_stub)
        async with discussion_lease(did):
            assert await run_once()==first
            assert entered==set()
        with SessionLocal() as sess:
            waiting=sess.get(db.AgentRun,first)
            assert waiting.status=='queued' and waiting.worker_owner is None
            waiting.lease_expires_at=db.utcnow()-timedelta(seconds=1)
            sess.commit()
        worker=asyncio.create_task(chat_worker_main())
        try:
            await asyncio.wait_for(overlap.wait(),5)
            for _ in range(100):
                with SessionLocal() as sess:
                    if all(sess.get(db.AgentRun,rid).status=='completed' for rid in (first,second)):
                        break
                await asyncio.sleep(.02)
            else:
                raise AssertionError('Both discussions must reach a durable terminal state')
        finally:
            worker.cancel()
            await asyncio.gather(worker,return_exceptions=True)
        with SessionLocal() as sess:
            for rid,discussion in ((first,did),(second,other)):
                assert sess.get(db.AgentRun,rid).worker_owner is None
                assert sess.query(db.Message).filter_by(run_id=rid,role='assistant').one().content==discussion
    asyncio.run(exercise())


def test_prompt_includes_only_questions_from_its_discussion(client):
    from ai import plan_prompt
    from planning import save_question
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'scoped-planning-context')
    other=client.post(f'/api/projects/{pid}/discussions',headers=csrf,json={'title':'Other'}).json()['id']
    with SessionLocal() as sess:
        for discussion,question in ((did,'Question specific to Alpha'),(other,'Question specific to Bravo')):
            save_question(sess,pid,discussion,{
                'question':question,
                'options':[{'label':'First approach','pros':'Simple','cons':'Basic'},
                           {'label':'Second approach','pros':'Flexible','cons':'Complex'}],
                'recommended_index':0,'recommendation_reason':'First is enough for now'})
        project=sess.get(db.Project,pid)
        alpha=plan_prompt(project,sess,'analyst','discovery',discussion_id=did)
        bravo=plan_prompt(project,sess,'analyst','discovery',discussion_id=other)
        assert 'Question specific to Alpha' in alpha and 'Question specific to Bravo' not in alpha
        assert 'Question specific to Bravo' in bravo and 'Question specific to Alpha' not in bravo
