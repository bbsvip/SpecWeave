import asyncio
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from test_app import client, auth, _make_discussion_with_model, SessionLocal, db
from chat_background import claim_next, process_queued_run, run_once
from model_runtime import ModelError, stream_turn
from storage import read_text, write_text


BRIEF = {
    'options':[
        {'label':'Phạm vi hẹp','description':'Ưu tiên MVP','pros':['Dễ kiểm thử','Phù hợp MVP'],'cons':'Ít tính năng'},
        {'label':'Phạm vi rộng','description':'Thêm tự động hóa','pros':'Đủ nghiệp vụ','cons':'Cần thêm thời gian'},
    ],
    'recommended_index':0,'recommendation_reason':'Phù hợp tiêu chí MVP và giảm rủi ro.',
}
PLAN = {'action':'draft','remaining_unknowns':[],
        'summary':'Đã thống nhất phạm vi nội bộ, dữ liệu do đơn vị quản lý, quyền thao tác theo vai trò và tiêu chí nghiệm thu.',
        'evidence':'Ba câu trả lời xác nhận phạm vi, quyền truy cập và điều kiện nghiệm thu.'}
DRAFT = '# Đặc tả dự án\n\n## Phạm vi\n' + (
    'Người dùng cần xem dữ liệu, kiểm tra quyền và xác nhận thao tác. '
    'Tiêu chí nghiệm thu kiểm thử đường thành công, xung đột và dữ liệu không hợp lệ.'
)


def frames(*events):
    wire = ''.join('data: '+json.dumps(event,ensure_ascii=False)+'\n\n' for event in events)
    return httpx.Response(200,headers={'content-type':'text/event-stream'},content=wire.encode('utf-8'))


def completion(text, reason='stop', output=7):
    return frames(
        {'choices':[{'delta':{'content':text},'finish_reason':None}]},
        {'choices':[{'delta':{},'finish_reason':reason}]},
        {'choices':[],'usage':{'prompt_tokens':20,'completion_tokens':output}},
    )


def install_provider(monkeypatch, handler):
    import harness
    import model_runtime
    def client_factory():
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(harness,'model_client',client_factory)
    monkeypatch.setattr(model_runtime,'model_client',client_factory)


def seed_answers(pid, did):
    from planning import answer_question, save_question
    with SessionLocal() as session:
        for number in range(3):
            question,_=save_question(session,pid,did,{**BRIEF,'question':f'Vấn đề cần quyết định thứ {number+1}?'})
            answer_question(session,question,{'selected_ids':['o1'],'note':f'Phạm vi xác nhận {number+1}'})


def test_discovery_rejects_premature_handoff_and_finishes_three_rounds(client, monkeypatch):
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'native-interview')
    with SessionLocal() as session:
        session.get(db.Project,pid).profile={'documents':['PRD','Architecture','UX / UI Design','Roadmap']}
        session.commit()
    responses=iter([
        (json.dumps(PLAN),'stop'),  # Must not hand off without three answers.
        *[(json.dumps({'action':'ask','question':{**BRIEF,'question':f'Câu hỏi phạm vi số {number}?'}}),'stop')
          for number in (1,2,3)],
        (json.dumps(PLAN),'stop'),
        ('# Tài liệu chưa đầy đủ','length'),
        *[(DRAFT,'stop')]*4,
    ])
    requests=[]

    def provider(request):
        payload=json.loads(request.content)
        assert request.url.path=='/v1/chat/completions'
        assert 'tools' not in payload
        requests.append(payload)
        text,reason=next(responses)
        return completion(text,reason)

    install_provider(monkeypatch,provider)
    job=client.post(f'/api/projects/{pid}/chat/start',headers=csrf,json={
        'discussion_id':did,'advance_planning':True,'mode':'discovery',
        '_resume_discovery':False,'_checkpoint':{'draft':{'path':'injected'}},
    }).json()
    for count in range(4):
        assert asyncio.run(run_once())==job['run_id']
        stream=client.get(job['events_url']).text
        with SessionLocal() as session:
            run=session.get(db.AgentRun,job['run_id'])
            assert run.status=='completed',run.error
            assert '_checkpoint' not in run.request_payload
            if count==3:
                assert '"type": "planning_complete"' in stream
                assert session.get(db.Project,pid).profile['_discovery']['status']=='draft_ready'
                docs=session.query(db.Document).filter_by(project_id=pid).all()
                assert len(docs)==10 and all(doc.revision==1 and doc.status=='draft' for doc in docs)
                assert session.query(db.Message).filter_by(discussion_id=did,role='user').count()==3
                break
            assert stream.count('"type": "planning_question"')==1
            assert session.query(db.Document).filter_by(project_id=pid).count()==0
            question=session.query(db.PlanningQuestion).filter_by(discussion_id=did,status='pending').one()
            assert question.options[0]['pros']=='Dễ kiểm thử · Phù hợp MVP'
            qid=question.id
        job=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer-start',headers=csrf,
                        json={'selected_ids':['o1']}).json()
    assert len(requests)==10
    assert 'không có công cụ gọi trực tiếp' in requests[0]['messages'][0]['content']
    assert 'Không hỏi lại thông tin đã có' in requests[0]['messages'][0]['content']
    assert 'không có công cụ gọi trực tiếp' in requests[-1]['messages'][0]['content']
    assert requests[6]['max_tokens']>=requests[5]['max_tokens']
    assert read_text(pid,'docs/drafts/PRD.md').strip()==DRAFT


def test_expired_worker_reconciles_written_file_without_second_model_call(client, monkeypatch):
    from harness import Harness, fact_digest, facts_for

    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'native-crash')
    seed_answers(pid,did)
    with SessionLocal() as session:
        session.get(db.Project,pid).profile={'documents':['PRD','Architecture']}
        session.commit()
    job=client.post(f'/api/projects/{pid}/chat/start',headers=csrf,json={
        'discussion_id':did,'advance_planning':True,'mode':'discovery'
    }).json()
    assert claim_next()==job['run_id']
    with SessionLocal() as session:
        project=session.get(db.Project,pid)
        run=session.get(db.AgentRun,job['run_id'])
        harness=Harness(session,project,session.get(db.Discussion,did),
                        session.get(db.Model,project.active_model_id),run,None)
        content=DRAFT+'\n'
        state={'digest':fact_digest(facts_for(session,project,did)),'plan':PLAN,
               'draft':{'path':'docs/drafts/PRD.md','text':content}}
        harness.save_checkpoint(state)
        # Crash boundary: file replacement completed and the transaction
        # that registers the document has not committed.
        write_text(pid,'docs/drafts/PRD.md',content,overwrite=False)
        write_text(pid,'docs/drafts/Architecture.md','# Human approved architecture\n')
        session.add(db.Document(project_id=pid,path='docs/drafts/Architecture.md',title='Architecture',
                                status='approved',revision=3))
        run.lease_expires_at=db.utcnow()-timedelta(seconds=1)
        session.commit()

    def unexpected(_request):
        raise AssertionError('Recovered and existing drafts require no new model call')

    install_provider(monkeypatch,unexpected)
    assert claim_next()==job['run_id']
    asyncio.run(process_queued_run(job['run_id']))
    with SessionLocal() as session:
        run=session.get(db.AgentRun,job['run_id'])
        assert run.status=='completed',run.error
        assert run.request_payload['_recovery_count']==1 and '_checkpoint' not in run.request_payload
        doc=session.query(db.Document).filter_by(project_id=pid,path='docs/drafts/PRD.md').one()
        assert doc.revision==1
        approved=session.query(db.Document).filter_by(project_id=pid,path='docs/drafts/Architecture.md').one()
        assert approved.status=='approved' and approved.revision==3
    assert read_text(pid,'docs/drafts/PRD.md')==content
    assert 'khôi phục bản nháp PRD.md' in client.get(job['events_url']).text


@pytest.mark.asyncio
async def test_transport_normalizes_responses_and_rejects_a_broken_stream():
    provider=SimpleNamespace(kind='openai-compatible',enabled=True,base_url='http://model.test/v1',encrypted_key='')
    model=SimpleNamespace(provider=provider,enabled=True,model_id='local',api='openai-responses',
                          compat={},sampling_params={},reasoning=False)
    sent=[]

    def response(request):
        sent.append(json.loads(request.content))
        assert request.url.path=='/v1/responses'
        return frames(
            {'type':'response.output_text.delta','delta':'Xin chào'},
            {'type':'response.completed','response':{'output':[],
             'usage':{'input_tokens':42,'output_tokens':3,'input_tokens_details':{'cached_tokens':9}}}},
        )
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as session:
        events=[event async for event in stream_turn(session,model,[{'role':'user','content':'Hi'}],
                                                     max_tokens=128,thinking='off')]
    assert events[-1][1].text=='Xin chào'
    assert events[-1][1].usage=={'input_tokens':42,'output_tokens':3,'cache_read_tokens':9,'source':'provider'}
    assert sent[0]['max_output_tokens']==128 and sent[0]['store'] is False

    def broken(_request):
        return frames({'type':'response.output_text.delta','delta':'Uncommitted'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as session:
        with pytest.raises(ModelError,match='trước sự kiện hoàn tất'):
            _=[event async for event in stream_turn(session,model,[],max_tokens=128,thinking='off')]


def test_no_pi_runtime_in_any_docker_variant():
    import yaml
    root=Path(__file__).resolve().parents[1]
    assert not (root/'source/pi_host.mjs').exists()
    assert '@earendil-works' not in (root/'Dockerfile').read_text(encoding='utf-8')
    for filename in ('compose.yaml','compose.external-db.yaml','compose.images.yaml','compose.external-db.images.yaml'):
        text=(root/filename).read_text(encoding='utf-8')
        assert 'PI_RUNNER' not in text
        assert 'openai_oauth_local:/auth-local' in text
        compose=yaml.safe_load(text)
        assert set(compose['services']) <= {'web','review-worker','db'}
        assert compose['services']['review-worker']['depends_on']['web']['condition']=='service_healthy'


def test_reanswer_refreshes_drafts_and_reconciles_revision_after_interrupted_write(client,monkeypatch):
    from harness import Harness, fact_digest, facts_for
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'reanswer-drafts')
    seed_answers(pid,did)
    with SessionLocal() as session:
        project=session.get(db.Project,pid)
        project.profile={'documents':['PRD','Architecture']}
        for path,status in (('docs/drafts/PRD.md','draft'),('docs/drafts/Architecture.md','approved')):
            write_text(pid,path,'# Original content\n')
            session.add(db.Document(project_id=pid,path=path,title=path,status=status,revision=1))
        session.commit()
        qid=session.query(db.PlanningQuestion).filter_by(project_id=pid).order_by(db.PlanningQuestion.created_at.desc()).first().id
    assert client.post(f'/api/projects/{pid}/planning/questions/{qid}/reopen',headers=csrf,json={}).status_code==200
    job=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer-start',headers=csrf,json={'selected_ids':['o2']}).json()
    assert claim_next()==job['run_id']
    with SessionLocal() as session:
        project=session.get(db.Project,pid)
        run=session.get(db.AgentRun,job['run_id'])
        harness=Harness(session,project,session.get(db.Discussion,did),session.get(db.Model,project.active_model_id),run,None)
        record=session.query(db.Document).filter_by(project_id=pid,path='docs/drafts/PRD.md').one()
        state=harness.prepare_draft({'digest':fact_digest(facts_for(session,project,did)),'plan':PLAN},record.path,DRAFT+'\n',record)
        harness.save_checkpoint(state)
        # The versioned file write finishes, then the worker loses its DB lease.
        write_text(pid,record.path,DRAFT+'\n')
        run.lease_expires_at=db.utcnow()-timedelta(seconds=1)
        session.commit()
    install_provider(monkeypatch,lambda request:pytest.fail('Recovered draft must not be generated again'))
    assert claim_next()==job['run_id']
    asyncio.run(process_queued_run(job['run_id']))
    with SessionLocal() as session:
        run=session.get(db.AgentRun,job['run_id'])
        assert run.status=='completed',run.error
        doc=session.query(db.Document).filter_by(project_id=pid,path='docs/drafts/PRD.md').one()
        assert doc.revision==2 and doc.status=='draft'
        approved=session.query(db.Document).filter_by(project_id=pid,path='docs/drafts/Architecture.md').one()
        assert approved.revision==1 and approved.status=='approved'
        assert session.get(db.Project,pid).profile['_discovery']['status']=='draft_ready'
    assert read_text(pid,'docs/drafts/PRD.md')==DRAFT+'\n'
    assert read_text(pid,'docs/drafts/Architecture.md')=='# Original content\n'


def test_model_review_uses_the_native_transport_without_tools(client,monkeypatch):
    csrf=auth(client)
    pid,_did=_make_discussion_with_model(client,csrf,'native-model-review')

    def provider(request):
        payload=json.loads(request.content)
        assert payload['stream'] and payload['max_tokens']==4000 and 'tools' not in payload
        _,_,body=payload['messages'][-1]['content'].partition('\n')
        assert json.loads(body)['project']['id']==pid
        return completion('{"summary":"Không phát hiện vấn đề có bằng chứng.","findings":[]}')

    install_provider(monkeypatch,provider)
    response=client.post(f'/api/projects/{pid}/quality/reviews/run',headers=csrf,json={'engine':'model'})
    assert response.status_code==200,response.text
    assert response.json()['findings']==0


def test_harness_executes_complete_tool_call_but_never_executes_cutoff(client,monkeypatch):
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'native-tool-round')
    write_text(pid,'inputs/qa-note.md','A fact belonging to this project.')
    calls=[]

    def provider(request):
        body=json.loads(request.content)
        calls.append(body)
        if len(calls)==1:
            return frames(
                {'choices':[{'delta':{'tool_calls':[{'index':0,'id':'read-1','function':{
                    'name':'read_project_file','arguments':'{"path":'}}]},'finish_reason':None}]},
                {'choices':[{'delta':{'tool_calls':[{'index':0,'function':{
                    'arguments':'"inputs/qa-note.md"}'}}]},'finish_reason':'tool_calls'}]},
            )
        if len(calls)==2:
            tool=body['messages'][-1]
            assert tool['role']=='tool' and tool['tool_call_id']=='read-1'
            assert 'A fact belonging to this project.' in tool['content']
            return completion('Đã đọc ghi chú trong dự án.')
        assert len(calls)==3
        return frames({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'cut-1','function':{
            'name':'propose_decision','arguments':'{"title":"Truncated","description":"No write"}'
        }}]},'finish_reason':'length'}]})

    install_provider(monkeypatch,provider)
    for mode in ('discuss','draft'):
        job=client.post(f'/api/projects/{pid}/chat/start',headers=csrf,
                        json={'discussion_id':did,'message':'Test tool round','mode':mode}).json()
        assert asyncio.run(run_once())==job['run_id']
        stream=client.get(job['events_url']).text
        if mode=='discuss':
            assert 'Đã đọc ghi chú trong dự án.' in stream
            assert '"status": "complete"' in stream
        else:
            assert 'Đầu ra bị cắt' in stream and '"type": "tool"' not in stream
    with SessionLocal() as session:
        assert session.query(db.Decision).filter_by(project_id=pid).count()==0
