"""SpecWeave chat v0.9.8: structured answer rendering, thinking and usage stream."""
import json

from test_app import client, auth, create_project, SessionLocal, Model, Message, db
from chat_content import parse_model_response
from chat_events import safe_tool_preview


def test_model_json_options_are_readable_markdown():
    raw='''Về tài khoản giao dịch, nên tách quyền sở hữu. Các lựa chọn: [{"id":"A","label":"Sổ cái nội bộ","description":"Quản lý số dư ở PostgreSQL","pros_cons":"Ưu: nhất quán, nhược: phải đối soát"},{"id":"B","label":"Điều phối","description":"Số dư ở đối tác","pros_cons":"Ưu: MVP gọn, nhược: lệ thuộc API"}]'''
    cleaned, brief=parse_model_response(raw)
    assert brief is None
    assert '### Các phương án' in cleaned and '**Sổ cái nội bộ**' in cleaned
    assert '"pros_cons"' not in cleaned and 'PostgreSQL' in cleaned


def test_valid_brief_recognized_but_code_sample_untouched():
    doc={'question':'Các tài khoản giao dịch sẽ được phân quyền như thế nào?',
         'options':[{'label':'Mỗi người một tài khoản','pros':'Cô lập','cons':'Thêm ACL'},
                    {'label':'Dùng chung','pros':'Đơn giản','cons':'Không an toàn'}],
         'recommended_index':0,'recommendation_reason':'Yêu cầu người dùng phải tự quản lý tài khoản riêng'}
    result,brief=parse_model_response('Phương án cụ thể: '+json.dumps(doc,ensure_ascii=False))
    assert brief==doc and '**Mỗi người một tài khoản**' in result
    sample='Ví dụ:\n```json\n'+json.dumps(doc,ensure_ascii=False)+'\n```'
    assert parse_model_response(sample)==(sample,None)


def test_tool_arguments_redact_credential_fields():
    preview=safe_tool_preview({'query':'design', 'api_key':'never-expose','nested':{'refresh_token':'also-private'}})
    assert 'never-expose' not in preview and 'also-private' not in preview
    assert '[redacted]' in preview


def test_stream_persists_thinking_tool_usage_and_normalized_answer(client,monkeypatch):
    import app as application
    csrf=auth(client)
    provider=client.post('/api/providers',headers=csrf,json={
        'name':'MockStream098','base_url':'http://model.test/v1','api_key':'secret'}).json()['id']
    client.post(f'/api/providers/{provider}/models',headers=csrf,json={'model_ids':['stream-098']})
    with SessionLocal() as session:
        model_id=session.query(Model).filter_by(model_id='stream-098').one().id
    pid=create_project(client,csrf)
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,json={'active_model_id':model_id}).status_code==200
    with SessionLocal() as session:
        did=session.query(db.Discussion).filter_by(project_id=pid).one().id

    async def simulated(project,discussion,session,model,*,role,mode,run_id=None,actor_id=None):
        yield 'status',{'phase':'reasoning','message':'Model đang phân tích'}
        yield 'thinking',{'text':'Cân nhắc các giới hạn của từng phương án.'}
        yield 'tool',{'id':'t1','name':'search_web','status':'running','arguments':'{"query":"risk"}'}
        yield 'tool',{'id':'t1','name':'search_web','status':'complete','result':'{"results":[]}'}
        answer='Đây là các phương án: '+json.dumps([
            {'id':'A','label':'Phương án A','description':'Tách quyền sở hữu'},
            {'id':'B','label':'Phương án B','description':'Chia sẻ'}],ensure_ascii=False)
        yield 'delta',{'text':answer}
        yield 'usage',{'source':'provider','input_tokens':198,'output_tokens':45}
        yield 'final',{'text':answer}
    monkeypatch.setattr(application,'pi_stream',simulated)
    response=client.post(f'/api/projects/{pid}/chat',headers=csrf,
             json={'discussion_id':did,'message':'Hãy phân tích các phương án','role':'analyst','mode':'discovery'})
    assert response.status_code==200,response.text
    assert '"type": "thinking"' in response.text
    assert '"type": "tool"' in response.text
    assert '"output_tps"' in response.text
    assert '"type": "final"' in response.text
    with SessionLocal() as session:
        message=session.query(Message).filter_by(discussion_id=did,role='assistant').one()
        assert '### Các phương án' in message.content
        run=session.get(db.AgentRun,message.run_id)
        assert run.usage['output_tokens']==45
        assert any(e['type']=='thinking' and 'Cân nhắc' in e['text'] for e in run.events)
        assert any(e['type']=='tool' and e['name']=='search_web' for e in run.events)
    history=client.get(f'/projects/{pid}/discussion?did={did}')
    assert history.status_code==200,history.text
    assert 'Thinking · model đã cung cấp' in history.text
    assert 'Output' in history.text
    assert 'Phương án A' in history.text
    assert '<div class="message-avatar">A</div>' in history.text
