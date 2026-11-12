"""API/security smoke tests; DB and project data live in temp dirs outside source."""
import os
import sys
import json
import tempfile
import zipfile
import io
from pathlib import Path
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'source'))
DATA=Path(tempfile.mkdtemp(prefix='specweave-tests-'))
os.environ['DATA_ROOT']=str(DATA)
os.environ['DATABASE_URL']='sqlite:///'+str(DATA/'test.db')
os.environ['ENCRYPTION_KEY']=Fernet.generate_key().decode()
os.environ['BOOTSTRAP_TOKEN']='test-bootstrap-secrettoken-1234567890'
os.environ['COOKIE_SECURE']='false'

import pytest
from fastapi.testclient import TestClient
import db
from app import app
from db import SessionLocal, AuthSession, User, Document, Decision, Model, Project, Message
from security import decrypt
from storage import resolve, project_root
from ai import tool_call
from research import validate_public_url

@pytest.fixture()
def client():
    db.Base.metadata.drop_all(bind=db.engine)
    db.Base.metadata.create_all(bind=db.engine)
    for item in (DATA/'projects').glob('*'):
        import shutil
        shutil.rmtree(item)
    with TestClient(app) as c:
        yield c

def auth(client,username='admin',password='super-secure-password'):
    with SessionLocal() as existing_db:
        needs_bootstrap = existing_db.query(User).count() == 0
    if username=='admin' and needs_bootstrap:
        result=client.post('/first-run',data={'username':'admin','email':'admin@example.org','password':password,
                                              'bootstrap_token':os.environ['BOOTSTRAP_TOKEN']},follow_redirects=False)
        assert result.status_code==303
    res=client.post('/login',data={'username':username,'password':password},follow_redirects=False)
    assert res.status_code==303,res.text
    session_token=res.cookies['specweave_session']
    client.cookies.set('specweave_session',session_token)
    import hashlib
    with SessionLocal() as s:
        a=s.query(AuthSession).filter_by(token_hash=hashlib.sha256(session_token.encode()).hexdigest()).first()
        return {'X-CSRF-Token':a.csrf_token}

def create_project(client,csrf,name='Sample Project'):
    r=client.post('/api/projects',headers=csrf,json={'name':name,'description':'A sample app','profile':{
      'project_types':['Web App','Mobile App'],'platforms':['Linux','Windows'],'languages':['Python','Go']}})
    assert r.status_code==200,r.text
    return r.json()['id']

def test_bootstrap_login_and_project_wizard(client):
    assert client.get('/').status_code in (200,307,303)
    assert client.post('/first-run',data={'username':'fake','email':'f@x.com','password':'long-password-again','bootstrap_token':'wrong'}).status_code==403
    csrf=auth(client)
    assert client.get('/').status_code==200
    p=create_project(client,csrf)
    assert client.get('/projects/'+p).status_code==200
    assert client.get('/projects/'+p+'/files').status_code==200
    with SessionLocal() as dbs:
        row=dbs.get(Project,p)
        assert row.profile['languages']==['Python','Go']
        assert project_root(p).is_dir()
        assert (project_root(p)/'docs/drafts').is_dir()
    r=client.post('/api/projects/'+p+'/archive',json={},headers={'X-CSRF-Token':'bad'})
    assert r.status_code==403
    r=client.post('/api/projects/'+p+'/settings',json={'stage':'requirements','thinking_level':'high'},headers=csrf)
    assert r.status_code==200
    assert client.get('/projects/'+p+'/discussion').status_code==200


def test_provider_file_versions_documents_decisions_and_export(client):
    csrf=auth(client)
    provider=client.post('/api/providers',json={'name':'Local','base_url':'http://localhost:9999/v1','api_key':'secret'},headers=csrf)
    assert provider.status_code==200
    pid=provider.json()['id']
    with SessionLocal() as s:
        p=s.query(db.Provider).filter_by(id=pid).one()
        assert p.encrypted_key!='secret'
        assert decrypt(p.encrypted_key)=='secret'
    assert client.post('/api/providers/'+pid+'/models',json={'model_ids':['test-model']},headers=csrf).status_code==200
    project=create_project(client,csrf)
    r=client.post(f'/api/projects/{project}/folders',json={'path':'inputs/subdir'},headers=csrf)
    assert r.status_code==200
    r=client.post(f'/api/projects/{project}/files/upload',data={'folder':'inputs/subdir','csrf':csrf['X-CSRF-Token']},files=[('files',('hello.txt',b'hello world','text/plain'))])
    assert r.status_code==200,r.text
    assert resolve(project,'inputs/subdir/hello.txt').read_text()=='hello world'
    r=client.post(f'/api/projects/{project}/files/content',headers=csrf,json={'path':'inputs/subdir/hello.txt','content':'updated'})
    assert r.status_code==200
    assert client.get(f'/api/projects/{project}/files/versions',params={'path':'inputs/subdir/hello.txt'}).json()['versions']
    r=client.post(f'/api/projects/{project}/files/move',headers=csrf,json={'source':'inputs/subdir/hello.txt','destination':'inputs/renamed.txt'})
    assert r.status_code==200
    assert client.get(f'/api/projects/{project}/files/download',params={'path':'inputs/renamed.txt'}).status_code==200
    r=client.post(f'/api/projects/{project}/documents',headers=csrf,json={'name':'PRD'})
    assert r.status_code==200
    doc_id=r.json()['id']
    r=client.post(f'/api/projects/{project}/documents/{doc_id}/approve',headers=csrf,json={'revision':0})
    assert r.status_code==409
    r=client.post(f'/api/projects/{project}/documents/{doc_id}/approve',headers=csrf,json={'revision':1})
    assert r.status_code==200
    d=client.post(f'/api/projects/{project}/decisions',headers=csrf,json={'title':'Select PostgreSQL','description':'Use PostgreSQL for persistence'})
    assert d.status_code==200
    assert client.post(f'/api/projects/{project}/decisions/{d.json()["id"]}/status',headers=csrf,json={'status':'approved'}).status_code==200
    export=client.post(f'/api/projects/{project}/export',headers=csrf)
    assert export.status_code==200,export.text[:150]
    with zipfile.ZipFile(io.BytesIO(export.content)) as z:
        assert 'manifest.json' in z.namelist()
        assert 'docs/drafts/PRD.md' in z.namelist()
        assert json.loads(z.read('manifest.json'))['project_id']==project


def test_isolation_path_security_and_acl(client):
    csrf=auth(client)
    project=create_project(client,csrf)
    for bad in ['../other.txt','inputs/../../private','inputs/.env','secrets/data','inputs/.system/a']:
        r=client.post(f'/api/projects/{project}/files/content',headers=csrf,json={'path':bad,'content':'secret'})
        assert r.status_code in (400,403), (bad,r.text)
    # A symlink in project data must never expose the filesystem outside the project.
    (project_root(project)/'inputs'/'badlink').symlink_to(DATA/'test.db')
    assert client.get(f'/api/projects/{project}/files/content',params={'path':'inputs/badlink'}).status_code==400
    # Second user cannot read project files, even if they know UUID.
    client.post('/api/admin/users',headers=csrf,json={'username':'user2','email':'u2@example.org','password':'other-secure-password'})
    client.cookies.clear()
    csrf2=auth(client,'user2','other-secure-password')
    # First login must_change_password limits API access.
    assert client.get(f'/projects/{project}',follow_redirects=False).status_code==303
    assert client.get(f'/api/projects/{project}/files/content',params={'path':'inputs/x'}).status_code==403
    assert client.post(f'/api/projects/{project}/folders',json={'path':'inputs/hack'},headers=csrf2).status_code==403

@pytest.mark.asyncio
async def test_scoped_agent_tools(client):
    csrf=auth(client)
    pid=create_project(client,csrf)
    with SessionLocal() as s:
        pr=s.get(Project,pid)
        out=await tool_call('write_document',{'filename':'MY-PRD.md','content':'# PRD\nContent'},project=pr,db=s)
        assert out['status']=='draft'
        assert read_text_safe(pid,'docs/drafts/MY-PRD.md')=='# PRD\nContent'
        out=await tool_call('read_project_file',{'path':'memory/rolling_summary.md'},project=pr,db=s)
        assert 'error' in out
        out=await tool_call('propose_decision',{'title':'Use API','description':'Yes'},project=pr,db=s)
        assert out['status']=='proposed'
        assert s.query(Decision).filter_by(project_id=pid,status='approved').count()==0


def read_text_safe(pid,path):
    return resolve(pid,path).read_text(encoding='utf-8')

def test_ssrf_private_urls():
    from fastapi import HTTPException
    for url in ['http://127.0.0.1:8080/', 'http://169.254.169.254/latest/meta-data/',
                'file:///etc/passwd','https://localhost/private','http://[::1]/']:
        with pytest.raises(HTTPException):validate_public_url(url)

def test_all_user_facing_pages_render(client):
    """Catch template regressions across global and project pages."""
    csrf = auth(client)
    pid = create_project(client, csrf, name='Full UI smoke project')
    paths = [
        '/', '/projects', '/projects/new', '/profile', '/presets', '/projects/trash', '/settings/appearance',
        '/settings/providers', '/settings/integrations', '/admin/users', '/admin/audit',
        f'/projects/{pid}', f'/projects/{pid}/settings', f'/projects/{pid}/discussion',
        f'/projects/{pid}/files', f'/projects/{pid}/files?path=inputs',
        f'/projects/{pid}/artifacts', f'/projects/{pid}/architecture', f'/projects/{pid}/roadmap',
        f'/projects/{pid}/documents', f'/projects/{pid}/decisions',
        f'/projects/{pid}/requirements', f'/projects/{pid}/research', f'/projects/{pid}/export',
    ]
    for path in paths:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 200, f'{path}: {response.status_code} {response.text[:400]}'
        assert 'SpecWeave' in response.text, path

def test_browser_proxy_settings_encryption_and_access(client):
    csrf = auth(client)
    value = 'http://demo:private-pass@proxy.example.org:8080'
    response = client.post('/api/settings/browser', headers=csrf, json={'proxy':value})
    assert response.status_code == 200, response.text
    assert response.json()['proxy_configured'] is True
    with SessionLocal() as session:
        setting = session.get(db.SystemSetting, 'browser')
        assert value not in json.dumps(setting.value)
        assert decrypt(setting.value['proxy_encrypted']) == value
    html = client.get('/settings/integrations').text
    assert 'Configured (encrypted)' in html and 'private-pass' not in html
    assert client.post('/api/settings/browser',headers=csrf,json={'proxy':'file:///etc/passwd'}).status_code == 422
    assert client.post('/api/settings/browser',headers=csrf,json={'clear_proxy':True}).json()['proxy_configured'] is False


def test_streaming_chat_persists_response(client, monkeypatch):
    import importlib
    application=importlib.import_module('app')
    csrf=auth(client)
    provider=client.post('/api/providers',headers=csrf,json={'name':'Mock','base_url':'http://model.internal/v1','api_key':'test'}).json()['id']
    client.post(f'/api/providers/{provider}/models',headers=csrf,json={'model_ids':['demo']})
    with SessionLocal() as s:
        model=s.query(Model).filter_by(model_id='demo').one()
        mid=model.id
    pid=create_project(client,csrf)
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,json={'active_model_id':mid}).status_code==200
    with SessionLocal() as s:
        discussion=s.query(db.Discussion).filter_by(project_id=pid).first()
        did=discussion.id
    async def stub_compatible(project, discussion, db, model, *, role, mode, run_id=None, actor_id=None):
        yield 'delta', {'text':'Spec'}
        yield 'delta', {'text':' confirmed'}
        yield 'final', {'text':'Spec confirmed'}
    monkeypatch.setattr(application,'pi_stream',stub_compatible)
    response=client.post(f'/api/projects/{pid}/chat',headers=csrf,json={'discussion_id':did,'message':'Please interview me','role':'analyst','mode':'discovery'})
    assert response.status_code==200,response.text[:500]
    assert '"type": "done"' in response.text
    history=client.get(f'/api/projects/{pid}/discussions/{did}/messages').json()
    assert [m['role'] for m in history]==['user','assistant']
    assert history[-1]['content']=='Spec confirmed'


def test_guided_planning_decision_briefs_answers_and_project_isolation(client):
    from db import PlanningQuestion
    csrf=auth(client)
    pid=create_project(client,csrf,'Planning One')
    other=create_project(client,csrf,'Planning Two')
    with SessionLocal() as s:
        did=s.query(db.Discussion).filter_by(project_id=pid).first().id
        pr=s.get(Project,pid)
        proposed={
          'question':'How should the application process background work?',
          'context':'Python web app with Docker, scheduled jobs expected',
          'options':[
             {'label':'PostgreSQL-backed worker','description':'One separate worker', 'pros':'Simple operational topology','cons':'Implement safe job leases'},
             {'label':'Redis + Celery','description':'Dedicated queue','pros':'Scale workers','cons':'Extra service'},
             {'label':'Inline only','description':'No queue','pros':'Minimal infrastructure','cons':'Bad for long jobs'},
          ],
          'recommended_index':0, 'recommendation_reason':'Small MVP, already needs PostgreSQL',
          'impact':'Restart safety','assumptions':['Initial load is low'], 'allow_multiple':False,
        }
        from planning import save_question
        item,created=save_question(s,pid,did,proposed)
        assert created
        repeated,created=save_question(s,pid,did,dict(proposed,question='This should not overwrite pending'))
        assert not created and repeated.id==item.id
        qid=item.id
        assert s.query(Decision).filter_by(project_id=pid).count()==0
    got=client.get(f'/api/projects/{pid}/planning/questions',params={'discussion_id':did})
    assert got.status_code==200 and got.json()['pending']['recommended_id']=='o1'
    assert got.json()['pending']['options'][1]['cons']=='Extra service'
    assert client.get(f'/api/projects/{other}/planning/questions',params={'discussion_id':did}).status_code==404
    assert client.post(f'/api/projects/{other}/planning/questions/{qid}/answer',headers=csrf,json={'selected_ids':['o1']}).status_code==404
    assert client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer',headers=csrf,json={'selected_ids':['o55']}).status_code==422
    assert client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer',headers=csrf,json={'selected_ids':['o1','o2']}).status_code==422
    approved=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer',headers=csrf,
                         json={'selected_ids':['o1'],'custom_answer':'Use SKIP LOCKED and retries','note':'Reevaluate at 500 jobs/day'})
    assert approved.status_code==200,approved.text
    assert approved.json()['question']['status']=='answered'
    assert client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer',headers=csrf,json={'selected_ids':['o2']}).status_code==409
    with SessionLocal() as s:
        assert s.query(Decision).filter_by(project_id=pid,status='approved').count()==0
        assert s.query(Message).filter_by(discussion_id=did,role='user').count()==1
        assert 'SKIP LOCKED' in s.query(Message).filter_by(discussion_id=did,role='user').one().content
        item,_=save_question(s,pid,did,dict(proposed,question='How should the deployment be secured?'))
        new_id=item.id
    result=client.post(f'/api/projects/{pid}/planning/questions/{new_id}/answer',headers=csrf,json={'action':'defer'})
    assert result.status_code==200 and result.json()['question']['status']=='deferred'
    assert client.get(f'/projects/{pid}/discussion').status_code==200
    assert 'planningQuestion' in client.get(f'/projects/{pid}/discussion').text


def test_proactive_question_generation_without_pi_tool_calls(client,monkeypatch):
    import importlib
    module=importlib.import_module('app')
    csrf=auth(client)
    provider=client.post('/api/providers',headers=csrf,json={'name':'Mock','base_url':'http://model.internal/v1','api_key':'test'}).json()['id']
    client.post(f'/api/providers/{provider}/models',headers=csrf,json={'model_ids':['planner-demo']})
    with SessionLocal() as s:
        mid=s.query(Model).filter_by(model_id='planner-demo').one().id
    pid=create_project(client,csrf)
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,json={'active_model_id':mid,'agent_backend':'pi'}).status_code==200
    with SessionLocal() as s:
        did=s.query(db.Discussion).filter_by(project_id=pid).one().id
    output={
        'question':'Should the MVP run as a single service or separate workers?',
        'context':'The MVP will use Docker',
        'options':[{'label':'One service','description':'Simpler','pros':'Simple deployment','cons':'Difficult scaling'},
                   {'label':'Separate worker','description':'Two containers','pros':'Isolation','cons':'More setup'}],
        'recommended_index':0,'recommendation_reason':'Lower complexity for MVP',
        'assumptions':['Light initial traffic']
    }
    import pi_client
    async def fake_complete(project,model,actor_id,**kwargs):
        assert 'JSON' in kwargs['system_context']
        return {'text':json.dumps(output),'usage':{}}
    monkeypatch.setattr(pi_client,'complete',fake_complete)
    first=client.post(f'/api/projects/{pid}/planning/questions/suggest',headers=csrf,json={'discussion_id':did})
    assert first.status_code==200,first.text
    assert first.json()['created'] and first.json()['question']['recommended_id']=='o1'
    second=client.post(f'/api/projects/{pid}/planning/questions/suggest',headers=csrf,json={'discussion_id':did})
    assert second.status_code==200 and not second.json()['created']
    assert second.json()['question']['id']==first.json()['question']['id']


def test_planning_research_sources_export_and_validation(client):
    from planning import save_question, normalize_question
    csrf=auth(client)
    pid=create_project(client,csrf)
    source_url='https://example.org/architecture-note'
    with SessionLocal() as s:
        did=s.query(db.Discussion).filter_by(project_id=pid).one().id
        s.add(db.ResearchItem(project_id=pid,title='Architecture reference',url=source_url,snippet='Comparison of deployment patterns'))
        s.commit()
        proposal={
            'question':'Which deploy model should be used for MVP production?',
            'context':'Docker environment',
            'options':[
                {'label':'Single service','description':'One app','pros':'Easier to deploy','cons':'Reduced isolation'},
                {'label':'Separate services','description':'Split app/worker','pros':'Scaling flexibility','cons':'More ops'},
            ],
            'recommended_index':1,
            'recommendation_reason':'Worker isolation aligns with long tasks',
            'sources':[source_url,'https://fake.example.net/invented'],
        }
        item,_=save_question(s,pid,did,proposal)
        assert item.sources==[{'title':'Architecture reference','url':source_url}]
        assert len(item.assumptions)==0
        with pytest.raises(ValueError):normalize_question(dict(proposal,recommended_index=9))
        with pytest.raises(ValueError):normalize_question(dict(proposal,recommendation_reason=''))
        with pytest.raises(ValueError):normalize_question(dict(proposal,options=[{'label':'single'}]*2))
    response=client.post(f'/api/projects/{pid}/export',headers=csrf)
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        assert 'planning/decision-briefs.json' in z.namelist()
        assert 'planning/decision-briefs.md' in z.namelist()
        exported=json.loads(z.read('planning/decision-briefs.json'))
        assert exported[0]['sources'][0]['url']==source_url
        assert 'fake.example.net' not in z.read('planning/decision-briefs.md').decode()

# ---------------- SpecWeave 0.3 Planning Intelligence ----------------
def test_intelligence_goal_trace_graph_and_project_isolation(client):
    csrf=auth(client)
    p1=create_project(client,csrf,'Coverage Project')
    p2=create_project(client,csrf,'Other Coverage Project')
    first=client.get(f'/api/projects/{p1}/intelligence/report')
    assert first.status_code==200
    assert not first.json()['ready'] and first.json()['blockers']>=3
    assert client.get(f'/projects/{p1}/intelligence').status_code==200
    assert 'Planning Intelligence' in client.get(f'/projects/{p1}/intelligence').text
    g=client.post(f'/api/projects/{p1}/intelligence/goals',headers=csrf,json={
        'title':'Reduce planning time','outcome':'Track accepted requirements and reviews',
        'metric':'Requirements with acceptance criteria / total requirements'})
    assert g.status_code==200,g.text
    gid=g.json()['id']
    assert client.post(f'/api/projects/{p1}/intelligence/goals/{gid}/status',headers=csrf,
        json={'status':'approved'}).status_code==200
    r=client.post(f'/api/projects/{p1}/requirements',headers=csrf,json={
        'title':'Gather acceptance criteria','criteria':'Every approved requirement has verifiable criteria',
        'priority':'must'})
    assert r.status_code==200,r.text
    rid=r.json()['id']
    wrong=client.post(f'/api/projects/{p2}/intelligence/links',headers=csrf,json={
        'source_type':'goal','source_id':gid,'target_type':'requirement','target_id':rid})
    assert wrong.status_code==404
    link_data={'source_type':'goal','source_id':gid,'target_type':'requirement','target_id':rid,
               'relation':'supports'}
    result=client.post(f'/api/projects/{p1}/intelligence/links',headers=csrf,json=link_data)
    assert result.status_code==200 and result.json()['created']
    second=client.post(f'/api/projects/{p1}/intelligence/links',headers=csrf,json=link_data)
    assert second.status_code==200 and second.json()['created'] is False
    assert client.post(f'/api/projects/{p1}/intelligence/links',headers=csrf,json={
        'source_type':'goal','source_id':gid,'target_type':'research','target_id':rid}).status_code==422
    graph=client.get(f'/api/projects/{p1}/intelligence/graph').json()
    assert {x['type'] for x in graph['nodes']} >= {'goal','requirement'}
    assert any(e['source']==gid and e['target']==rid for e in graph['edges'])
    assert not client.get(f'/api/projects/{p2}/intelligence/graph').json()['nodes']
    assert client.delete(f'/api/projects/{p1}/intelligence/links/{result.json()["id"]}',headers=csrf).status_code==200
    assert not client.get(f'/api/projects/{p1}/intelligence/graph').json()['edges']


def test_intelligence_phase_dependency_cycle_and_human_review(client):
    csrf=auth(client)
    pid=create_project(client,csrf)
    rid=client.post(f'/api/projects/{pid}/requirements',headers=csrf,json={
        'title':'Have integration tests','criteria':'Tests pass and report coverage', 'priority':'must'}).json()['id']
    phases=[]
    for i in (1,2):
        response=client.post(f'/api/projects/{pid}/intelligence/phases',headers=csrf,json={
            'title':f'Phase {i}', 'summary':'Design and validation',
            'deliverables':f'Specification version {i}', 'acceptance':'Tests pass'})
        assert response.status_code==200,response.text
        phases.append(response.json()['id'])
    a,b=phases
    for phase in phases:
        assert client.post(f'/api/projects/{pid}/intelligence/links',headers=csrf,json={
            'source_type':'requirement','source_id':rid,'target_type':'phase','target_id':phase}).status_code==200
    dep=client.post(f'/api/projects/{pid}/intelligence/phases/{b}/dependencies',headers=csrf,
                    json={'prerequisite_id':a})
    assert dep.status_code==200,dep.text
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{a}/dependencies',headers=csrf,
                       json={'prerequisite_id':b}).status_code==409
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{a}/dependencies',headers=csrf,
                       json={'prerequisite_id':a}).status_code==422
    av=client.get(f'/api/projects/{pid}/intelligence/phases/{a}').json()['revision']
    bv=client.get(f'/api/projects/{pid}/intelligence/phases/{b}').json()['revision']
    premature=client.post(f'/api/projects/{pid}/intelligence/phases/{b}/review',headers=csrf,
        json={'action':'submit','revision':bv})
    assert premature.status_code==409
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{a}/review',headers=csrf,
        json={'action':'approve','revision':av}).status_code==409  # submit first
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{a}/review',headers=csrf,
        json={'action':'submit','revision':av}).json()['status']=='in_review'
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{a}/review',headers=csrf,
        json={'action':'approve','revision':av}).json()['status']=='approved'
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{b}/review',headers=csrf,
        json={'action':'submit','revision':bv}).json()['status']=='in_review'
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{b}/review',headers=csrf,
        json={'action':'request_changes','revision':bv,'comment':'Need retry design'}).json()['status']=='needs_changes'
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{b}/review',headers=csrf,
        json={'action':'submit','revision':bv}).json()['status']=='in_review'
    assert client.post(f'/api/projects/{pid}/intelligence/phases/{b}/review',headers=csrf,
        json={'action':'approve','revision':bv}).json()['status']=='approved'
    outdated=client.post(f'/api/projects/{pid}/intelligence/phases/{a}',headers=csrf,json={
        'revision':av-1,'title':'Stale','summary':'Old','deliverables':'Old','acceptance':'Old'})
    assert outdated.status_code==409
    changed=client.post(f'/api/projects/{pid}/intelligence/phases/{a}',headers=csrf,json={
        'revision':av,'title':'Updated Phase','summary':'New summary','deliverables':'New deliverable','acceptance':'New criterion'})
    assert changed.status_code==200 and changed.json()['status']=='draft'
    report=client.get(f'/api/projects/{pid}/intelligence/report').json()
    assert any(issue['code']=='PHASE_REVIEW_GAP' and issue['entity_id']==b for issue in report['issues'])
    assert client.get(f'/api/projects/{pid}/intelligence/graph').status_code==200


def test_reviewed_export_gate_and_handoff_bundle(client):
    csrf=auth(client)
    pid=create_project(client,csrf)
    blocked=client.post(f'/api/projects/{pid}/export?mode=reviewed',headers=csrf)
    assert blocked.status_code==409
    assert blocked.json()['issues']
    goal=client.post(f'/api/projects/{pid}/intelligence/goals',headers=csrf,json={
        'title':'Deliver audited specification','outcome':'Produce approved spec and trace map'}).json()['id']
    client.post(f'/api/projects/{pid}/intelligence/goals/{goal}/status',headers=csrf,json={'status':'approved'})
    req=client.post(f'/api/projects/{pid}/requirements',headers=csrf,json={
        'title':'User can review plan','criteria':'Owner approves each phase','priority':'must'}).json()['id']
    assert client.post(f'/api/projects/{pid}/requirements/{req}/status',headers=csrf,
                       json={'status':'approved'}).status_code==200
    phase=client.post(f'/api/projects/{pid}/intelligence/phases',headers=csrf,json={
        'title':'Requirements and review','summary':'Review specification',
        'deliverables':'Approved PRD','acceptance':'Owner has approved'}).json()['id']
    for src_type,src_id,target_type,target_id in [
        ('goal',goal,'requirement',req),('requirement',req,'phase',phase)]:
        link=client.post(f'/api/projects/{pid}/intelligence/links',headers=csrf,json={
            'source_type':src_type,'source_id':src_id,'target_type':target_type,'target_id':target_id})
        assert link.status_code==200,link.text
    doc=client.post(f'/api/projects/{pid}/documents',headers=csrf,json={'name':'Approved PRD'}).json()['id']
    assert client.post(f'/api/projects/{pid}/documents/{doc}/approve',headers=csrf,json={'revision':1}).status_code==200
    revision=client.get(f'/api/projects/{pid}/intelligence/phases/{phase}').json()['revision']
    client.post(f'/api/projects/{pid}/intelligence/phases/{phase}/review',headers=csrf,json={'action':'submit','revision':revision})
    approved=client.post(f'/api/projects/{pid}/intelligence/phases/{phase}/review',headers=csrf,json={'action':'approve','revision':revision})
    assert approved.status_code==200,approved.text
    readiness=client.get(f'/api/projects/{pid}/intelligence/report').json()
    assert readiness['ready'] and readiness['blockers']==0,readiness['issues']
    export=client.post(f'/api/projects/{pid}/export?mode=reviewed',headers=csrf)
    assert export.status_code==200,export.text[:200]
    with zipfile.ZipFile(io.BytesIO(export.content)) as z:
        assert {'planning/readiness.json','planning/readiness.md','planning/traceability-graph.json',
                'planning/goals.json','planning/phases.json'} <= set(z.namelist())
        manifest=json.loads(z.read('manifest.json'))
        assert manifest['export_mode']=='reviewed' and manifest['readiness']['ready']
        assert json.loads(z.read('planning/readiness.json'))['blockers']==0
    # Changing an approved phase's traceability revokes the approval immediately.
    link=client.post(f'/api/projects/{pid}/intelligence/links',headers=csrf,json={
        'source_type':'goal','source_id':goal,'target_type':'phase','target_id':phase})
    assert link.status_code==200
    assert client.get(f'/api/projects/{pid}/intelligence/phases/{phase}').json()['status']=='draft'
    assert client.post(f'/api/projects/{pid}/export?mode=reviewed',headers=csrf).status_code==409
    assert client.post(f'/api/projects/{pid}/export?mode=draft',headers=csrf).status_code==200


def test_intelligence_viewer_read_editor_write_and_owner_only_approval(client):
    csrf_admin=auth(client)
    pid=create_project(client,csrf_admin)
    created=client.post('/api/admin/users',headers=csrf_admin,json={
        'username':'reviewer','email':'reviewer@example.org','password':'another-secure-password'});
    assert created.status_code==200,created.text
    with SessionLocal() as session:
        reviewer=session.query(User).filter_by(username='reviewer').one()
        reviewer.must_change_password=False
        session.commit()
    assert client.post(f'/api/projects/{pid}/members',headers=csrf_admin,
                       json={'username':'reviewer','role':'viewer'}).status_code==200
    client.cookies.clear()
    csrf_viewer=auth(client,'reviewer','another-secure-password')
    page=client.get(f'/projects/{pid}/intelligence')
    assert page.status_code==200 and 'piGoalForm' not in page.text
    assert client.get(f'/api/projects/{pid}/intelligence/graph').status_code==200
    assert client.post(f'/api/projects/{pid}/intelligence/goals',headers=csrf_viewer,
                       json={'title':'Forbidden viewer goal'}).status_code==403
    client.cookies.clear()
    csrf_admin=auth(client)
    assert client.post(f'/api/projects/{pid}/members',headers=csrf_admin,
                       json={'username':'reviewer','role':'editor'}).status_code==200
    client.cookies.clear()
    csrf_editor=auth(client,'reviewer','another-secure-password')
    goal=client.post(f'/api/projects/{pid}/intelligence/goals',headers=csrf_editor,
                     json={'title':'Editable goal','outcome':'Valid measurable result'})
    assert goal.status_code==200
    assert client.post(f'/api/projects/{pid}/intelligence/goals/{goal.json()["id"]}/status',
                       headers=csrf_editor,json={'status':'approved'}).status_code==403


def test_edit_requirement_and_goal_invalidate_approved_phases(client):
    csrf=auth(client)
    pid=create_project(client,csrf)
    goal=client.post(f'/api/projects/{pid}/intelligence/goals',headers=csrf,json={
        'title':'Goal A','outcome':'Produce a validated specification'}).json()['id']
    req=client.post(f'/api/projects/{pid}/requirements',headers=csrf,json={
        'title':'Requirement A','description':'Implement spec tracking',
        'criteria':'Every requirement traces to a phase','priority':'must'}).json()['id']
    phase=client.post(f'/api/projects/{pid}/intelligence/phases',headers=csrf,json={
        'title':'Phase A','summary':'Implement acceptance review','deliverables':'Approved docs',
        'acceptance':'Document and test pass'}).json()['id']
    for a,aid,b,bid in [('goal',goal,'requirement',req),('requirement',req,'phase',phase)]:
        assert client.post(f'/api/projects/{pid}/intelligence/links',headers=csrf,json={
            'source_type':a,'source_id':aid,'target_type':b,'target_id':bid}).status_code==200
    def approve_phase():
        rev=client.get(f'/api/projects/{pid}/intelligence/phases/{phase}').json()['revision']
        assert client.post(f'/api/projects/{pid}/intelligence/phases/{phase}/review',headers=csrf,
                           json={'action':'submit','revision':rev}).status_code==200
        assert client.post(f'/api/projects/{pid}/intelligence/phases/{phase}/review',headers=csrf,
                           json={'action':'approve','revision':rev}).status_code==200
    approve_phase()
    result=client.post(f'/api/projects/{pid}/requirements/{req}',headers=csrf,json={
        'title':'Requirement B','description':'New acceptance scope',
        'criteria':'Requirements have precise measurable outcomes','priority':'must'})
    assert result.status_code==200 and result.json()['status']=='draft'
    assert client.get(f'/api/projects/{pid}/intelligence/phases/{phase}').json()['status']=='draft'
    approve_phase()
    changed=client.post(f'/api/projects/{pid}/intelligence/goals/{goal}',headers=csrf,json={
        'title':'Goal Updated','outcome':'Changed product success outcome','metric':'All phases reviewed'})
    assert changed.status_code==200 and changed.json()['status']=='draft'
    assert client.get(f'/api/projects/{pid}/intelligence/phases/{phase}').json()['status']=='draft'

# ------------------------ v0.4 Planning Quality regression tests ------------------------

def test_quality_rules_review_human_confirmation_and_export_gate(client):
    from db import Requirement, ProjectGoal, PlanPhase, TraceLink, SpecReview, ReviewFinding
    csrf=auth(client);pid=create_project(client,csrf)
    assert client.get(f'/projects/{pid}/quality').status_code==200
    with SessionLocal() as s:
        r=Requirement(project_id=pid,code='FR-01',title='Login',description='Authenticate users',
                      criteria='x',priority='must',status='draft')
        s.add(r);s.commit()
    response=client.post(f'/api/projects/{pid}/quality/reviews/run',headers=csrf,json={'engine':'rules'})
    assert response.status_code==200,response.text
    summary=client.get(f'/api/projects/{pid}/quality/overview').json()
    assert summary['reviews'][0]['engine']=='rules'
    assert summary['findings']
    f=summary['findings'][0]
    result=client.post(f'/api/projects/{pid}/quality/findings/{f["id"]}/status',headers=csrf,
                       json={'status':'confirmed'})
    assert result.status_code==200,result.text
    summary=client.get(f'/api/projects/{pid}/quality/overview').json()
    assert summary['quality_gate']>=1
    # Existing deterministic blockers still apply. Quality also blocks reviewed export.
    reviewed=client.post(f'/api/projects/{pid}/export?mode=reviewed',headers=csrf)
    assert reviewed.status_code==409 and 'Quality' in reviewed.text
    assert client.post(f'/api/projects/{pid}/quality/findings/{f["id"]}/status',headers=csrf,
                       json={'status':'resolved'}).status_code==200
    assert client.get(f'/api/projects/{pid}/quality/overview').json()['quality_gate']==0
    with zipfile.ZipFile(io.BytesIO(client.post(f'/api/projects/{pid}/export?mode=draft',headers=csrf).content)) as z:
        assert 'planning/quality-review.json' in z.namelist()
        assert 'planning/evidence-assessments.json' in z.namelist()
    # Changing spec makes old findings stale, prevents confirming old findings.
    with SessionLocal() as s:
        r=s.query(Requirement).filter_by(project_id=pid).first();r.description='updated';s.commit()
    summary=client.get(f'/api/projects/{pid}/quality/overview').json()
    assert summary['reviews'][0]['stale'] is True


def test_evidence_provenance_is_scoped_and_cannot_be_auto_verified(client):
    from db import ResearchItem
    csrf=auth(client);p1=create_project(client,csrf,'Evidence Alpha');p2=create_project(client,csrf,'Evidence Beta')
    with SessionLocal() as s:
        r=ResearchItem(project_id=p1,title='Product documentation',url='https://example.org/documentation',
                       snippet='Useful external reference. '*5,query='docs')
        s.add(r);s.commit();rid=r.id
    endpoint=f'/api/projects/{p1}/quality/evidence/{rid}'
    assert client.post(endpoint,headers=csrf,json={'source_kind':'official_docs',
            'verification':'checked','published_on':'2026-01-01','note':'Reviewed by owner'}).status_code==200
    detail=client.get(f'/api/projects/{p1}/quality/overview').json()['evidence'][0]
    assert detail['score']['verification']=='checked' and detail['score']['score']<100
    assert client.post(f'/api/projects/{p2}/quality/evidence/{rid}',headers=csrf,json={}).status_code==404
    assert client.post(endpoint,headers=csrf,json={'source_kind':'invented'}).status_code==422
    assert client.post(endpoint,headers=csrf,json={'published_on':'30/13/2026'}).status_code==422


def test_trace_suggestions_are_reviewed_and_project_isolated(client):
    from db import ProjectGoal, Requirement, TraceLink, TraceSuggestion
    csrf=auth(client);p1=create_project(client,csrf,'Trace One');p2=create_project(client,csrf,'Trace Two')
    with SessionLocal() as s:
        goal=ProjectGoal(project_id=p1,title='Customer authentication email password',
                         outcome='Enable customer authentication email password')
        req=Requirement(project_id=p1,code='FR-1',title='Customer authentication email password',
                        description='Implement customer authentication email password')
        external=Requirement(project_id=p2,code='FR-2',title='Customer authentication email password')
        s.add_all([goal,req,external]);s.commit();gid=goal.id;rid=req.id;eid=external.id
    r=client.post(f'/api/projects/{p1}/quality/trace/suggest',headers=csrf,json={'limit':10})
    assert r.status_code==200,r.text
    overview=client.get(f'/api/projects/{p1}/quality/overview').json()
    suggestion=next(x for x in overview['suggestions'] if x['source_id']==gid and x['target_id']==rid)
    assert all(x['target_id']!=eid for x in overview['suggestions'])
    r=client.post(f'/api/projects/{p1}/quality/trace/{suggestion["id"]}/decision',headers=csrf,
                  json={'action':'accept'})
    assert r.status_code==200,r.text
    with SessionLocal() as s:
        assert s.query(TraceLink).filter_by(project_id=p1,source_id=gid,target_id=rid).count()==1
        assert s.query(TraceLink).filter_by(project_id=p2).count()==0
    assert client.post(f'/api/projects/{p1}/quality/trace/{suggestion["id"]}/decision',headers=csrf,
                       json={'action':'accept'}).status_code==409
    assert client.post(f'/api/projects/{p2}/quality/trace/{suggestion["id"]}/decision',headers=csrf,
                       json={'action':'accept'}).status_code==404


def test_bmad_workflow_requires_human_gate_and_invalidates_on_change(client):
    from db import ProjectGoal, WorkflowCheckpoint
    csrf=auth(client);pid=create_project(client,csrf)
    url=f'/api/projects/{pid}/quality/workflow/discovery'
    assert client.post(url,headers=csrf,json={'action':'submit'}).status_code==409
    with SessionLocal() as s:
        g=ProjectGoal(project_id=pid,title='Market discovery',outcome='A validated MVP',status='approved')
        s.add(g);s.commit();gid=g.id
    assert client.post(url,headers=csrf,json={'action':'approve'}).status_code==409
    first_submit=client.post(url,headers=csrf,json={'action':'submit'})
    assert first_submit.status_code==200,first_submit.text
    assert client.post(url,headers=csrf,json={'action':'approve'}).status_code==200
    report=client.get(f'/api/projects/{pid}/quality/overview').json()
    assert report['workflow'][0]['status']=='approved' and report['workflow'][0]['stale'] is False
    assert report['quality_gate']>=1  # Once workflow is initiated, incomplete stages block handoff.
    with SessionLocal() as s:
        g=s.get(ProjectGoal,gid);g.outcome='Different validated outcome';s.commit()
    report=client.get(f'/api/projects/{pid}/quality/overview').json()
    assert report['workflow'][0]['stale'] is True
    # Resubmitting an approved-but-stale checkpoint is necessary to proceed.
    r=client.post(url,headers=csrf,json={'action':'submit'})
    assert r.status_code==200,r.text
    assert client.post(url,headers=csrf,json={'action':'approve'}).status_code==200


def test_model_review_json_validation_and_no_cross_project_references(client,monkeypatch):
    from db import ProjectGoal
    from planning_engine import parse_model_review
    csrf=auth(client);p1=create_project(client,csrf);p2=create_project(client,csrf)
    with SessionLocal() as s:
        g=ProjectGoal(project_id=p2,title='Private project goal',outcome='Keep isolated')
        s.add(g);s.commit();foreign=g.id
        raw=json.dumps({'summary':'Review complete','findings':[
            {'severity':'high','category':'risk','description':'Should not reference another project',
             'source_type':'goal','source_id':foreign},
            {'severity':'warning','category':'ambiguity','description':'Unclear actor for use case',
             'recommendation':'Clarify which actor'}]})
        summary,parsed=parse_model_review(raw,s,p1)
        assert summary=='Review complete'
        assert len(parsed)==1 and parsed[0]['source_id'] is None
    assert client.post(f'/api/projects/{p1}/quality/reviews/run',headers=csrf,
                       json={'engine':'model'}).status_code==409

def test_confirmed_critical_findings_survive_spec_changes(client):
    from db import Requirement
    csrf=auth(client);pid=create_project(client,csrf)
    with SessionLocal() as s:
        req=Requirement(project_id=pid,code='SEC-001',title='Prevent insecure authentication',
                        description='Password login',criteria='x',priority='must')
        s.add(req);s.commit();rid=req.id
    client.post(f'/api/projects/{pid}/quality/reviews/run',headers=csrf,json={'engine':'rules'})
    item=client.get(f'/api/projects/{pid}/quality/overview').json()['findings'][0]
    fid=item['id']
    assert client.post(f'/api/projects/{pid}/quality/findings/{fid}/status',headers=csrf,json={'status':'confirmed'}).status_code==200
    with SessionLocal() as s:
        req=s.get(Requirement,rid);req.description='Password login with timeout';s.commit()
    overview=client.get(f'/api/projects/{pid}/quality/overview').json()
    assert overview['reviews'][0]['stale'] is True
    assert overview['quality_gate']>=1
    # We do not allow confirming a stale proposal, but owner may close already-confirmed issues.
    assert client.post(f'/api/projects/{pid}/quality/findings/{fid}/status',headers=csrf,json={'status':'resolved'}).status_code==200
    assert client.get(f'/api/projects/{pid}/quality/overview').json()['quality_gate']==0

def test_editor_cannot_downgrade_owner_verified_source(client):
    from db import ResearchItem, User, Membership
    csrf=auth(client);pid=create_project(client,csrf)
    with SessionLocal() as s:
        src=ResearchItem(project_id=pid,title='Source',url='https://example.org/guide',snippet='Verified')
        user=User(username='editor',email='editor@example.org',password_hash=db.User.password_hash.default.arg if False else '')
        # TestClient login needs a valid password hash.
        from security import hash_password
        user.password_hash=hash_password('other-secure-password')
        s.add_all([src,user]);s.flush()
        s.add(Membership(project_id=pid,user_id=user.id,role='editor'))
        s.commit();rid=src.id
    endpoint=f'/api/projects/{pid}/quality/evidence/{rid}'
    assert client.post(endpoint,headers=csrf,json={'source_kind':'primary','verification':'checked'}).status_code==200
    editor_csrf=auth(client,username='editor',password='other-secure-password')
    assert client.post(endpoint,headers=editor_csrf,json={'source_kind':'community','verification':'unverified'}).status_code==403
    r=client.post(endpoint,headers=editor_csrf,json={'source_kind':'community','note':'new details'})
    assert r.status_code==200,r.text
    assert r.json()['verification']=='checked'

def test_workflow_stage_cannot_be_manually_overridden_and_ui_flag_is_editable(client):
    from db import ProjectGoal
    csrf=auth(client);pid=create_project(client,csrf)
    with SessionLocal() as s:
        s.add(ProjectGoal(project_id=pid,title='API-only system',outcome='Stable API MVP',status='approved'))
        s.commit()
    r=client.post(f'/api/projects/{pid}/settings',headers=csrf,json={'ui_required':False})
    assert r.status_code==200,r.text
    from planning_engine import stage_checklist
    with SessionLocal() as s:
        assert all(x['passed'] for x in stage_checklist(s,pid,'ux_design'))
    url=f'/api/projects/{pid}/quality/workflow/discovery'
    assert client.post(url,headers=csrf,json={'action':'submit'}).status_code==200
    assert client.post(url,headers=csrf,json={'action':'approve'}).status_code==200
    r=client.post(f'/api/projects/{pid}/settings',headers=csrf,json={'stage':'approved'})
    assert r.status_code==409,r.text
    assert client.get(f'/projects/{pid}/settings').status_code==200

def test_optional_current_quality_review_required_gate(client):
    from db import Requirement
    csrf=auth(client);pid=create_project(client,csrf)
    setting=client.post(f'/api/projects/{pid}/settings',headers=csrf,json={'quality_review_required':True})
    assert setting.status_code==200,setting.text
    report=client.get(f'/api/projects/{pid}/quality/overview').json()
    assert report['quality_gate']>=1
    assert client.post(f'/api/projects/{pid}/quality/reviews/run',headers=csrf,json={'engine':'rules'}).status_code==200
    assert client.get(f'/api/projects/{pid}/quality/overview').json()['quality_gate']==0
    with SessionLocal() as s:
        s.add(Requirement(project_id=pid,code='FR-3',title='Customer login',criteria='Login accepted'))
        s.commit()
    assert client.get(f'/api/projects/{pid}/quality/overview').json()['quality_gate']>=1

@pytest.mark.asyncio
async def test_adversarial_review_merges_roles(monkeypatch):
    import planning_engine
    calls=[]
    async def fake(db_session, project, model, lens=None):
        calls.append(lens)
        return lens, [{'severity':'warning' if lens!='qa' else 'high',
                       'category':'risk','description':'Requirement lacks an explicit failure recovery plan',
                       'recommendation':'Add a recovery strategy', 'source_type':None,'source_id':None}]
    monkeypatch.setattr(planning_engine,'model_review',fake)
    summary, findings=await planning_engine.adversarial_review(None,None,None)
    assert calls==['analyst','architect','qa']
    assert len(findings)==1
    assert findings[0]['severity']=='high'
    assert '[analyst, architect, qa]' in findings[0]['recommendation']
    assert 'architect:' in summary


def test_adversarial_review_requires_model(client):
    csrf=auth(client)
    pid=create_project(client,csrf)
    result=client.post(f'/api/projects/{pid}/quality/reviews/run',
                       json={'engine':'adversarial'},headers=csrf)
    assert result.status_code==409

# ---------------- SpecWeave v0.6 durable reviews and File Manager recovery ----------------
def configured_review_project(client, csrf):
    """Real provider/model rows, fake inference only (no external network)."""
    from db import Provider
    provider=client.post('/api/providers',headers=csrf,json={
        'name':'Review mock','base_url':'http://localhost:19999/v1','api_key':'dummy'})
    assert provider.status_code==200,provider.text
    added=client.post(f'/api/providers/{provider.json()["id"]}/models',headers=csrf,
                      json={'model_ids':['review-mock']})
    assert added.status_code==200,added.text
    pid=create_project(client,csrf)
    with SessionLocal() as s:
        model=s.query(Model).filter_by(provider_id=provider.json()['id']).first()
        pr=s.get(Project,pid);pr.active_model_id=model.id
        s.commit()
    return pid


@pytest.mark.asyncio
async def test_background_review_four_roles_and_provenance(client,monkeypatch):
    import review_queue
    from db import ReviewJob, ReviewJobStep, SpecReview, ReviewFinding
    csrf=auth(client)
    pid=configured_review_project(client,csrf)
    url=f'/api/projects/{pid}/quality/jobs'
    assert client.post(url,headers=csrf,json={'output_budget':10}).status_code==422
    r=client.post(url,headers=csrf,json={'output_budget':12000})
    assert r.status_code==200,r.text
    jid=r.json()['id']
    assert client.post(url,headers=csrf,json={}).status_code==409
    calls=[]
    async def model_fake(session, project, model, *, lens=None, prior_reviews=None,
                         max_output_tokens=None, include_usage=False, release_db_before_http=False, actor_id=None):
        calls.append((lens,len(prior_reviews or []),release_db_before_http))
        return lens+' summary',[{'severity':'high' if lens=='qa' else 'warning',
               'category':'risk','description':'No tested recovery behavior for external API failures',
               'recommendation':'Document retries, timeouts and recovery cases',
               'source_type':None,'source_id':None}],12,False
    monkeypatch.setattr(review_queue,'model_review',model_fake)
    for progress in range(1,5):
        assert await review_queue.run_job_once('test-worker')
        response=client.get(f'{url}/{jid}')
        assert response.status_code==200
        info=response.json()
        assert info['progress']==progress
    assert info['status']=='succeeded'
    assert info['output_used']==48
    assert [x[0] for x in calls]==['analyst','architect','qa','synthesis']
    assert calls[-1][1]==3 and all(x[2] for x in calls)
    with SessionLocal() as s:
        row=s.get(ReviewJob,jid)
        assert row.review_id
        assert s.query(ReviewJobStep).filter_by(job_id=jid).count()==4
        review=s.get(SpecReview,row.review_id)
        assert review.engine=='adversarial'
        findings=s.query(ReviewFinding).filter_by(review_id=review.id).all()
        assert len(findings)==1
        assert findings[0].severity=='high' and findings[0].status=='proposed'
    assert await review_queue.run_job_once('idle-worker') is False


@pytest.mark.asyncio
async def test_review_jobs_cancel_stale_and_acl(client,monkeypatch):
    import review_queue
    from db import Requirement, ReviewJob, User
    csrf=auth(client)
    pid=configured_review_project(client,csrf)
    other=create_project(client,csrf)
    url=f'/api/projects/{pid}/quality/jobs'
    jid=client.post(url,headers=csrf,json={}).json()['id']
    assert client.get(f'/api/projects/{other}/quality/jobs/{jid}').status_code==404
    assert client.post(f'/api/projects/{other}/quality/jobs/{jid}/cancel',headers=csrf).status_code==404
    assert client.post(f'{url}/{jid}/cancel',headers=csrf).json()['status']=='cancelled'
    assert await review_queue.run_job_once('tester') is False
    jid=client.post(url,headers=csrf,json={}).json()['id']
    async def fake(*args,**kwargs):
        return 'summary', [], 8, True
    monkeypatch.setattr(review_queue,'model_review',fake)
    assert await review_queue.run_job_once('tester')
    with SessionLocal() as s:
        s.add(Requirement(project_id=pid,code='REQ-900',title='Changed after review started',
                          criteria='System returns a response'))
        s.commit()
    assert await review_queue.run_job_once('tester')
    detail=client.get(f'{url}/{jid}').json()
    assert detail['status']=='stale' and detail['progress']==1 and not detail['review_id']
    assert client.post(f'{url}/{jid}/retry',headers=csrf).status_code==409
    with SessionLocal() as s:
        s.add(User(username='outsider',email='outsider@example.org',
                   password_hash=__import__('security').hash_password('outside-strong-password')))
        s.commit()
    client.cookies.clear()
    outsider=auth(client,'outsider','outside-strong-password')
    assert client.get(url).status_code==403
    assert client.post(url,headers=outsider,json={}).status_code==403


@pytest.mark.asyncio
async def test_review_failed_pass_retry_preserves_completed_steps(client,monkeypatch):
    import review_queue
    csrf=auth(client)
    pid=configured_review_project(client,csrf)
    url=f'/api/projects/{pid}/quality/jobs'
    jid=client.post(url,headers=csrf,json={}).json()['id']
    calls=[]
    async def fake(*args,lens=None,**kwargs):
        calls.append(lens)
        if lens=='architect' and calls.count('architect')==1:
            raise ValueError('upstream mock error')
        return 'summary',[],5,True
    monkeypatch.setattr(review_queue,'model_review',fake)
    assert await review_queue.run_job_once('tester')
    assert await review_queue.run_job_once('tester')
    assert client.get(f'{url}/{jid}').json()['status']=='failed'
    assert client.get(f'{url}/{jid}').json()['progress']==1
    assert client.post(f'{url}/{jid}/retry',headers=csrf).status_code==200
    for _ in range(3):assert await review_queue.run_job_once('tester')
    assert client.get(f'{url}/{jid}').json()['status']=='succeeded'
    assert calls==['analyst','architect','architect','qa','synthesis']


def test_trash_restore_directory_docs_preview_and_permanent_delete(client):
    from db import Document
    csrf=auth(client)
    pid=create_project(client,csrf)
    other=create_project(client,csrf)
    client.post(f'/api/projects/{pid}/folders',headers=csrf,json={'path':'docs/drafts/removed'})
    file='docs/drafts/removed/design.md'
    assert client.post(f'/api/projects/{pid}/files/content',headers=csrf,
        json={'path':file,'content':'# Initial design'}).status_code==200
    with SessionLocal() as s:
        doc=s.query(Document).filter_by(project_id=pid,path=file).first()
        assert doc
        doc.status='approved';s.commit()
    assert client.post(f'/api/projects/{pid}/files/delete',headers=csrf,
                       json={'path':'docs/drafts/removed'}).status_code==200
    with SessionLocal() as s:
        assert s.query(Document).filter_by(project_id=pid,path=file).count()==0
    trash=client.get(f'/api/projects/{pid}/files/trash')
    assert trash.status_code==200
    item=trash.json()['entries'][0]
    assert item['type']=='folder'
    assert client.post(f'/api/projects/{other}/files/trash/{item["id"]}/restore',headers=csrf,json={}).status_code==404
    restored=client.post(f'/api/projects/{pid}/files/trash/{item["id"]}/restore',headers=csrf,json={})
    assert restored.status_code==200,restored.text
    assert resolve(pid,file).read_text()=='# Initial design'
    with SessionLocal() as s:
        doc=s.query(Document).filter_by(project_id=pid,path=file).first()
        assert doc and doc.status=='draft'
    assert not client.get(f'/api/projects/{pid}/files/trash').json()['entries']
    # Preview whitelist: SVG/HTML never served inline.
    assert client.post(f'/api/projects/{pid}/files/upload',headers=csrf,
        data={'folder':'inputs','csrf':csrf['X-CSRF-Token']},
        files=[('files',('preview.pdf',b'%PDF-1.4\n%%EOF','application/pdf'))]).status_code==200
    preview=client.get(f'/api/projects/{pid}/files/preview',params={'path':'inputs/preview.pdf'})
    assert preview.status_code==200 and preview.headers['content-type'].startswith('application/pdf')
    assert client.get(f'/api/projects/{pid}/files/preview',params={'path':file}).status_code==415
    assert client.post(f'/api/projects/{pid}/files/delete',headers=csrf,json={'path':'inputs/preview.pdf'}).status_code==200
    item=client.get(f'/api/projects/{pid}/files/trash').json()['entries'][0]
    purged=client.delete(f'/api/projects/{pid}/files/trash/{item["id"]}',headers=csrf)
    assert purged.status_code==200,purged.text
    assert client.post(f'/api/projects/{pid}/files/trash/{item["id"]}/restore',headers=csrf,json={}).status_code==404


def test_document_folder_move_updates_paths_and_revokes_approval(client):
    from db import Document
    csrf=auth(client);pid=create_project(client,csrf)
    client.post(f'/api/projects/{pid}/folders',headers=csrf,json={'path':'docs/drafts/old'})
    f='docs/drafts/old/architecture.md'
    client.post(f'/api/projects/{pid}/files/content',headers=csrf,json={'path':f,'content':'# Design'})
    with SessionLocal() as s:
        doc=s.query(Document).filter_by(project_id=pid,path=f).one()
        doc.status='approved';s.commit()
    move=client.post(f'/api/projects/{pid}/files/move',headers=csrf,
                     json={'source':'docs/drafts/old','destination':'docs/drafts/new'})
    assert move.status_code==200,move.text
    with SessionLocal() as s:
        doc=s.query(Document).filter_by(project_id=pid,path='docs/drafts/new/architecture.md').first()
        assert doc and doc.status=='draft' and doc.revision==2


def test_login_throttle_is_db_backed_and_does_not_store_usernames(client):
    from db import LoginAttempt
    csrf=auth(client)
    client.cookies.clear()
    for _ in range(8):
        resp=client.post('/login',data={'username':'admin','password':'wrong-password'},follow_redirects=False)
        assert resp.status_code==401
    locked=client.post('/login',data={'username':'admin','password':'super-secure-password'},
                       follow_redirects=False)
    assert locked.status_code==429
    with SessionLocal() as s:
        records=s.query(LoginAttempt).all()
        assert len(records)==8
        assert all(len(row.subject_hash)==64 and len(row.client_hash)==64 for row in records)
        assert not any('admin' in (row.subject_hash+row.client_hash) for row in records)
        s.query(LoginAttempt).delete()
        s.commit()
    assert client.post('/login',data={'username':'admin','password':'super-secure-password'},
                       follow_redirects=False).status_code==303


@pytest.mark.asyncio
async def test_model_review_usage_and_cross_role_context(client,monkeypatch):
    import planning_engine
    import pi_client
    from db import Requirement
    csrf=auth(client)
    pid=configured_review_project(client,csrf)
    with SessionLocal() as s:
        req=Requirement(project_id=pid,code='REQ-101',title='Recover on failure',
                        criteria='After API timeout show retry state')
        s.add(req);s.commit();rid=req.id
    seen=[]
    async def fake_complete(project,model,actor_id,**kwargs):
        seen.append(kwargs)
        assert 'PRIOR_REVIEW_OBSERVATIONS' in kwargs['message']
        return {'text':json.dumps({'summary':'Sources were checked','findings':[
            {'severity':'warning','category':'risk','description':'Retry policy is not fully specified',
            'source_type':'requirement','source_id':rid}]}),
            'usage':{'output_tokens':66}}
    monkeypatch.setattr(pi_client,'complete',fake_complete)
    with SessionLocal() as s:
        project=s.get(Project,pid)
        model=s.get(Model,project.active_model_id)
        summary,items,used,estimated=await planning_engine.model_review(s,project,model,lens='synthesis',
                 prior_reviews=[{'role':'analyst','summary':'Missing retry handling'}],
                 include_usage=True,release_db_before_http=True,max_output_tokens=1500)
        assert summary=='Sources were checked'
        assert used==66 and not estimated
        assert len(items)==1 and items[0]['source_id']==rid
        assert 'PROJECT_DATA' in seen[0]['message']


def test_0003_migration_creates_review_queue_without_deleting_old_data(tmp_path,monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text, inspect
    import db
    old=tmp_path/'upgrade.db'
    url='sqlite:///'+str(old)
    eng=create_engine(url)
    # v0.5 database before ReviewJobs + DB throttle tables.
    missing={'review_jobs','review_job_steps','login_attempts'}
    for table in db.Base.metadata.sorted_tables:
        if table.name not in missing:table.create(eng,checkfirst=True)
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE agent_runs DROP COLUMN cancel_requested"))
        conn.execute(text("INSERT INTO users (id,username,email,display_name,password_hash,role,enabled,must_change_password) "
                          "VALUES ('01234567-89ab-cdef-0123-456789abcdef','legacy','legacy@example.org','Legacy','hash','member',1,0)"))
    cfg=Config(str(ROOT/'alembic.ini'))
    cfg.set_main_option('script_location',str(ROOT/'migrations'))
    monkeypatch.setenv('DATABASE_URL',url)
    command.stamp(cfg,'0002_quality')
    command.upgrade(cfg,'head')
    assert set(inspect(eng).get_table_names())>=missing
    with eng.connect() as conn:
        assert conn.execute(text("SELECT username FROM users WHERE username='legacy'")).scalar()=='legacy'
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0010_detached_chat'
        assert 'cancel_requested' in [x['name'] for x in inspect(eng).get_columns('agent_runs')]
    eng.dispose()

@pytest.mark.asyncio
async def test_pi_tool_bridge_caps_and_project_scope(client, monkeypatch):
    """Pi can only call allowlisted tools for an active project run: no shell or cross-project access."""
    from ai import pi_tool_token
    monkeypatch.setenv("PI_RUNNER_KEY", "bridge-test-key-must-be-at-least-32-characters-long")
    csrf=auth(client)
    p1=create_project(client,csrf,"Trusted project")
    p2=create_project(client,csrf,"Different project")
    with SessionLocal() as session:
        discussion=db.Discussion(project_id=p1,title="Pi session")
        session.add(discussion);session.flush()
        run=db.AgentRun(project_id=p1,discussion_id=discussion.id,model_name="test-model")
        session.add(run);session.commit()
        rid=run.id
        did=discussion.id
        created=run.created_at
        token=pi_tool_token(rid,p1,did,created)
    target="/api/internal/pi-tool"
    h={"X-Specweave-Run":rid,"X-Specweave-Token":token}
    assert client.post(target,headers={**h,"X-Specweave-Token":"bad"},json={"name":"list_project_files","args":{}}).status_code==401
    assert client.post(target,headers=h,json={"name":"bash","args":{"command":"whoami"}}).status_code==403
    result=client.post(target,headers=h,json={"name":"list_project_files","args":{"folder":"inputs"}})
    assert result.status_code==200,result.text
    result=client.post(target,headers=h,json={"name":"propose_decision","args":{"title":"Use SQLite for prototype","description":"Discussion proposal"}})
    assert result.status_code==200,result.text
    assert result.json()["status"]=="proposed"
    with SessionLocal() as session:
        assert session.query(Decision).filter_by(project_id=p1).count()==1
        assert session.query(Decision).filter_by(project_id=p2).count()==0
        assert session.query(Decision).filter_by(project_id=p1,status="approved").count()==0
        run=session.get(db.AgentRun,rid)
        run.status="cancelled"
        session.commit()
    assert client.post(target,headers=h,json={"name":"list_project_files","args":{}}).status_code==401


def test_pi_runner_has_custom_only_allowlist_and_project_restricted_env():
    """Pi SDK is the only agent; no built-in shell or independent completion loop."""
    host=(ROOT/'source/pi_host.mjs').read_text(encoding='utf-8')
    tools=(ROOT/'source/pi_host_tools.mjs').read_text()
    ai=(ROOT/'source/ai.py').read_text()
    assert 'createAgentSession(' in host and 'SessionManager.continueRecent' in host
    assert "noTools:withTools?'builtin':'all'" in host
    assert 'tools:[]' not in host and 'customTools:withTools?createScopedTools(active):[]' in host
    assert 'X-Specweave-Token' in tools and 'X-Specweave-Run' in tools
    assert 'propose_planning_question' in tools and 'tool_token' in ai
    assert 'compatible_stream' not in ai
    assert 'pi --mode rpc' not in (ROOT/'Dockerfile').read_text(encoding='utf-8')


@pytest.mark.asyncio
async def test_chat_coordination_lease_and_cancel_persistence(client):
    import chat_coordination as cc
    from db import AgentRun
    from datetime import datetime
    csrf=auth(client)
    project=create_project(client,csrf)
    provider=client.post('/api/providers',json={'name':'Lease Local','base_url':'http://localhost:9999/v1','api_key':'secret'},headers=csrf).json()['id']
    client.post(f'/api/providers/{provider}/models',json={'model_ids':['lease-model']},headers=csrf)
    with SessionLocal() as session:
        model=session.query(Model).filter_by(model_id='lease-model').first()
        run=AgentRun(project_id=project,discussion_id=db.uid(),model_name='lease-model',thinking='low')
        session.add(run);session.commit()
        run_id=run.id
    assert cc.lock_key('a')==cc.lock_key('a')
    assert cc.lock_key('a')!=cc.lock_key('b')
    async with cc.discussion_lease('test-lease'):
        with pytest.raises(cc.ChatBusy):
            async with cc.discussion_lease('test-lease'):
                pass
    with SessionLocal() as session:
        session.get(AgentRun,run_id).cancel_requested=True
        session.commit()
    assert cc.cancellation_requested(run_id)
    result=client.post(f'/api/projects/{project}/runs/{run_id}/cancel',headers=csrf)
    assert result.status_code==200 and result.json()['cancel_requested']


def test_operations_dashboard_admin_only(client):
    csrf=auth(client)
    create_project(client,csrf)
    page=client.get('/admin/operations')
    assert page.status_code==200 and 'Operations' in page.text
    data=client.get('/api/admin/operations')
    assert data.status_code==200
    assert data.json()['projects']==1
    assert 'chat_runs' in data.json()
    assert 'encrypted_key' not in data.text


def test_project_activity_is_project_scoped(client):
    csrf=auth(client)
    p1=create_project(client,csrf,'Activity A')
    p2=create_project(client,csrf,'Activity B')
    page=client.get(f'/projects/{p1}/activity')
    assert page.status_code==200
    data=client.get(f'/api/projects/{p1}/activity')
    assert data.status_code==200
    assert all(x['action'] for x in data.json()['events'])
    assert not any(x.get('project_id')==p2 for x in data.json()['events'])


def test_0004_fresh_database_migration_is_idempotent(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    database=tmp_path/'fresh.db'
    url='sqlite:///'+str(database)
    cfg=Config(str(ROOT/'alembic.ini'))
    cfg.set_main_option('script_location',str(ROOT/'migrations'))
    monkeypatch.setenv('DATABASE_URL',url)
    command.upgrade(cfg,'head')
    eng=create_engine(url)
    with eng.connect() as conn:
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0010_detached_chat'
        assert any(col['name']=='cancel_requested' for col in __import__('sqlalchemy').inspect(eng).get_columns('agent_runs'))
    eng.dispose()

# v0.8 product-completion behavior tests

def test_artifact_generator_preview_selection_and_approval_guard(client):
    csrf=auth(client)
    pid=create_project(client,csrf,name='Product Handoff')
    assert client.get(f'/projects/{pid}/artifacts').status_code==200
    assert client.get(f'/projects/{pid}/architecture').status_code==200
    assert client.get(f'/projects/{pid}/roadmap').status_code==200
    response=client.get(f'/api/projects/{pid}/artifacts',params={'key':'prd'})
    assert response.status_code==200
    assert 'Product Handoff' in response.json()['content']
    assert 'CẦN XÁC NHẬN' in response.json()['content']
    assert client.get(f'/api/projects/{pid}/artifacts',params={'key':'bad'}).status_code==404
    assert client.post(f'/api/projects/{pid}/artifacts/generate',json={'keys':['prd']}).status_code==403
    r=client.post(f'/api/projects/{pid}/artifacts/generate',headers=csrf,
        json={'keys':['product-brief','prd','architecture','roadmap']})
    assert r.status_code==200,r.text
    assert r.json()['count']==4
    with SessionLocal() as s:
        doc=s.query(Document).filter_by(project_id=pid,path='docs/drafts/PRD.md').one()
        assert doc.status=='draft' and doc.revision==1
        assert resolve(pid,'docs/drafts/PRD.md').is_file()
        did=doc.id
    assert client.post(f'/api/projects/{pid}/artifacts/generate',headers=csrf,
        json={'keys':['prd']}).status_code==409
    assert client.post(f'/api/projects/{pid}/artifacts/generate',headers=csrf,
        json={'keys':['prd','bad'], 'overwrite':True}).status_code==422
    r=client.post(f'/api/projects/{pid}/artifacts/generate',headers=csrf,
        json={'keys':['prd'], 'overwrite':True})
    assert r.status_code==200,r.text
    assert r.json()['documents'][0]['revision']==2
    assert client.post(f'/api/projects/{pid}/documents/{did}/approve',headers=csrf,
        json={'revision':2}).status_code==200
    assert client.post(f'/api/projects/{pid}/artifacts/generate',headers=csrf,
        json={'keys':['prd'],'overwrite':True}).status_code==409
    # Preflight: an approved doc must prevent all updates in a mixed batch.
    before=resolve(pid,'docs/drafts/ROADMAP.md').read_text()
    denied=client.post(f'/api/projects/{pid}/artifacts/generate',headers=csrf,
        json={'keys':['roadmap','prd'],'overwrite':True})
    assert denied.status_code==409
    assert resolve(pid,'docs/drafts/ROADMAP.md').read_text()==before
    zip_response=client.post(f'/api/projects/{pid}/export',headers=csrf)
    assert zip_response.status_code==200
    with zipfile.ZipFile(io.BytesIO(zip_response.content)) as z:
        assert 'docs/drafts/PRD.md' in z.namelist()
        assert 'docs/drafts/ARCHITECTURE.md' in z.namelist()


def test_preset_library_profile_and_session_revocation(client):
    csrf=auth(client)
    assert client.get('/presets').status_code==200
    assert client.get('/settings/appearance').status_code==200
    assert client.get('/projects/trash').status_code==200
    assert client.post('/api/presets',headers=csrf,json={'name':'','profile':{}}).status_code==422
    assert client.post('/api/presets',headers=csrf,json={'name':'Too big','profile':{'text':'A'*31000}}).status_code==422
    result=client.post('/api/presets',headers=csrf,json={'name':'Custom','profile':{'languages':['Python']}})
    assert result.status_code==200
    preset_id=result.json()['id']
    assert client.post('/api/presets/'+preset_id,headers=csrf,json={'name':'Updated','favorite':False,'profile':{'languages':['Go']}}).status_code==200
    assert client.get('/api/presets').json()[0]['name']=='Updated'
    assert client.post('/api/profile',headers=csrf,json={'email':'admin-new@example.org','display_name':'Admin Updated'}).status_code==200
    assert client.get('/profile').status_code==200
    with SessionLocal() as s:
        session=s.query(AuthSession).filter(AuthSession.revoked_at.is_(None)).order_by(AuthSession.last_seen_at.desc()).first()
        sid=session.id
    assert client.delete('/api/profile/sessions/nonexistent',headers=csrf).status_code==404
    response=client.delete('/api/profile/sessions/'+sid,headers=csrf)
    assert response.status_code==200 and response.json()['current_session']
    assert client.get('/').status_code in (200,303,307)  # browser redirects to login


def test_team_member_lifecycle_and_owner_transfer(client):
    csrf=auth(client)
    pid=create_project(client,csrf,'Team Workspace')
    for user in ['collaborator','observer']:
        r=client.post('/api/admin/users',headers=csrf,json={'username':user,'email':user+'@example.org',
                                         'password':'test-strong-password','role':'member'})
        assert r.status_code==200
        with SessionLocal() as s:
            row=s.query(User).filter_by(username=user).one()
            row.must_change_password=False
            s.commit()
    for name in ['collaborator','observer']:
        assert client.post(f'/api/projects/{pid}/members',headers=csrf,
                           json={'username':name,'role':'viewer'}).status_code==200
    with SessionLocal() as s:
        coll=s.query(User).filter_by(username='collaborator').one()
        obs=s.query(User).filter_by(username='observer').one()
        coll_id,obs_id=coll.id,obs.id
    assert client.post(f'/api/projects/{pid}/members/{coll_id}',headers=csrf,
                       json={'role':'editor'}).status_code==200
    assert client.delete(f'/api/projects/{pid}/members/{obs_id}',headers=csrf).status_code==200
    assert client.post(f'/api/projects/{pid}/transfer-ownership',headers=csrf,
                       json={'user_id':obs_id,'confirm_name':'Team Workspace'}).status_code==409
    assert client.post(f'/api/projects/{pid}/transfer-ownership',headers=csrf,
                       json={'user_id':coll_id,'confirm_name':'wrong'}).status_code==422
    r=client.post(f'/api/projects/{pid}/transfer-ownership',headers=csrf,
                  json={'user_id':coll_id,'confirm_name':'Team Workspace'})
    assert r.status_code==200,r.text
    assert client.post(f'/api/projects/{pid}/delete',headers=csrf).status_code==403
    assert client.get(f'/projects/{pid}/settings').status_code==200
    client.cookies.clear()
    csrf_coll=auth(client,'collaborator','test-strong-password')
    assert client.get(f'/projects/{pid}/settings').status_code==200
    assert client.post(f'/api/projects/{pid}/delete',headers=csrf_coll).status_code==200
    assert client.get(f'/projects/{pid}',follow_redirects=False).status_code==404
    assert client.post(f'/api/projects/{pid}/restore',headers=csrf_coll).status_code==200
    assert client.get(f'/projects/{pid}').status_code==200


def test_project_trash_scope_provider_model_management(client):
    csrf=auth(client)
    pid=create_project(client,csrf)
    provider=client.post('/api/providers',headers=csrf,
                         json={'name':'TestProvider','base_url':'http://localhost:8888/v1'})
    assert provider.status_code==200
    prov_id=provider.json()['id']
    assert client.post(f'/api/providers/{prov_id}/models',headers=csrf,
                       json={'model_ids':['bench-model']}).status_code==200
    with SessionLocal() as s:
        model=s.query(Model).filter_by(provider_id=prov_id).one()
        mid=model.id
    assert client.post(f'/api/providers/{prov_id}/models/{mid}',headers=csrf,
                       json={'enabled':False,'context_window':64000,'reasoning':True}).status_code==200
    with SessionLocal() as s:
        model=s.get(Model,mid)
        assert not model.enabled and model.context_window==64000 and model.reasoning
    assert client.post(f'/api/providers/{prov_id}',headers=csrf,json={'enabled':False}).status_code==200
    assert client.post(f'/api/projects/{pid}/delete',headers=csrf).status_code==200
    assert 'Sample Project' in client.get('/projects/trash').text
    assert client.post(f'/api/projects/{pid}/restore',json={}).status_code==403
    assert client.post(f'/api/projects/{pid}/restore',headers=csrf).status_code==200
    assert client.get(f'/projects/{pid}').status_code==200


def test_artifact_generator_isolation_between_users(client):
    csrf=auth(client)
    pid=create_project(client,csrf)
    client.post('/api/admin/users',headers=csrf,json={'username':'stranger','email':'stranger@example.org',
                'password':'valid-other-password'})
    with SessionLocal() as s:
        outsider=s.query(User).filter_by(username='stranger').one()
        outsider.must_change_password=False
        s.commit()
    client.cookies.clear()
    csrf_stranger=auth(client,'stranger','valid-other-password')
    assert client.get(f'/projects/{pid}/artifacts').status_code==403
    assert client.get(f'/api/projects/{pid}/artifacts',params={'key':'prd'}).status_code==403
    assert client.post(f'/api/projects/{pid}/artifacts/generate',headers=csrf_stranger,
                       json={'keys':['prd']}).status_code==403


def test_upload_atomicity_and_project_quota(client,monkeypatch):
    csrf=auth(client)
    pid=create_project(client,csrf)
    monkeypatch.setenv('MAX_PROJECT_STORAGE_MB','1')
    response=client.post(f'/api/projects/{pid}/files/upload',
        data={'folder':'inputs','csrf':csrf['X-CSRF-Token']},
        files=[('files',('good.txt',b'ok','text/plain')),
               ('files',('oversized.bin',b'x'*(1024*1024+1),'application/octet-stream'))])
    assert response.status_code==413,response.text
    assert not resolve(pid,'inputs/good.txt').exists()
    assert not resolve(pid,'inputs/oversized.bin').exists()
    # Can still store within limits, and usage can be calculated without following symlinks.
    assert client.post(f'/api/projects/{pid}/files/upload',
        data={'folder':'inputs','csrf':csrf['X-CSRF-Token']},
        files=[('files',('small.txt',b'hello','text/plain'))]).status_code==200
    from storage import project_usage
    assert project_usage(pid)['bytes']>=5


@pytest.mark.asyncio
async def test_multi_model_review_selection_provenance_export_and_validation(client,monkeypatch):
    """Each role can use its own enabled model, recorded in durable review and export."""
    import review_queue
    from db import ReviewJobStep
    csrf=auth(client)
    pid=configured_review_project(client,csrf)
    with SessionLocal() as dbs:
        main=dbs.get(Project,pid).active_model_id
        provider_id=dbs.get(Model,main).provider_id
    added=client.post(f'/api/providers/{provider_id}/models',headers=csrf,
                      json={'model_ids':['secondary-model','third-model']})
    assert added.status_code==200,added.text
    with SessionLocal() as dbs:
        models={m.model_id:m.id for m in dbs.query(Model).filter_by(provider_id=provider_id).all()}
    url=f'/api/projects/{pid}/quality/jobs'
    bad=client.post(url,headers=csrf,json={'role_models':{'architect':'not-a-model'}})
    assert bad.status_code==422
    bad=client.post(url,headers=csrf,json={'role_models':{'shell':'not-a-model'}})
    assert bad.status_code==422
    roles={'architect':models['secondary-model'],'qa':models['third-model']}
    r=client.post(url,headers=csrf,json={'role_models':roles})
    assert r.status_code==200,r.text
    job=r.json()
    assert job['role_models']==roles
    calls=[]
    async def fake_review(db_session,project,model,*,lens=None,prior_reviews=None,
                          max_output_tokens=None,include_usage=False,release_db_before_http=False,actor_id=None):
        calls.append((lens,model.id))
        return f'Checked by {model.model_id}',[],16,False
    monkeypatch.setattr(review_queue,'model_review',fake_review)
    for _ in range(4):
        assert await review_queue.run_job_once('multi-model-test')
    data=client.get(f"{url}/{job['id']}").json()
    assert data['status']=='succeeded'
    assert data['progress']==4
    assert [r for r,_ in calls]==['analyst','architect','qa','synthesis']
    assert [m for _,m in calls]==[main,roles['architect'],roles['qa'],main]
    assert [s['model_id'] for s in data['steps']]==[main,roles['architect'],roles['qa'],main]
    assert '[model=' in data['summary']
    with SessionLocal() as session:
        steps=session.query(ReviewJobStep).filter_by(job_id=job['id']).all()
        assert len(steps)==4 and {x.role:x.model_id for x in steps}['qa']==roles['qa']
    export=client.post(f'/api/projects/{pid}/export',headers=csrf)
    assert export.status_code==200
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        saved=json.loads(archive.read('planning/review-jobs.json'))
        assert saved[0]['id']==job['id']
        assert saved[0]['steps'][2]['model_id']==roles['qa']


def test_project_profile_and_model_guard(client):
    csrf=auth(client)
    assert client.post('/api/projects',headers=csrf,json={'name':'Big Project','profile':{'x':'a'*70000}}).status_code==413
    assert client.post('/api/projects',headers=csrf,json={'name':'Unknown Model','model_id':'bad','profile':{}}).status_code==422
    pid=create_project(client,csrf)
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,
                       json={'active_model_id':'missing-model'}).status_code==422
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,
                       json={'profile':{'notes':'y'*70000}}).status_code==413
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,
                       json={'profile':['wrong-type']}).status_code==422
    assert 'Dung lượng:' in client.get(f'/projects/{pid}/files').text

# v0.9 OpenAI-only, owner-scoped OAuth: no token enters PostgreSQL or the browser.

def test_openai_oauth_disabled_by_default(client,monkeypatch):
    monkeypatch.delenv('OPENAI_OAUTH_LOCAL_MODE',raising=False)
    csrf=auth(client)
    assert client.get('/api/openai/oauth/status').status_code==503
    assert client.post('/api/openai/oauth/start',headers=csrf,json={}).status_code==503
    assert client.post('/api/providers',headers=csrf,json={
        'name':'Try OAuth through generic endpoint','base_url':'pi://openai',
        'kind':'openai-oauth','api_key':'untrusted'}).status_code==422


def test_openai_oauth_login_owner_scoped_and_no_token_db(client,monkeypatch):
    import importlib
    web=importlib.import_module('app')
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_MODE','true')
    calls=[]
    async def fake_relay(method,path,body=None,timeout=30):
        calls.append((method,path,body))
        if path=='/oauth/openai/start':return {'id':'login-ticket-example-1234567890','status':'running'}
        if path=='/oauth/openai/status':return {'connected':True,'provider':'openai','storage':'local-pi-vault','shared':False}
        if path=='/oauth/openai/models':return {'models':['model-from-pi-catalog']}
        if path=='/oauth/openai/disconnect':return {'ok':True,'revoked_remotely':False}
        if path.startswith('/oauth/openai/login/'):
            return {'status':'done','events':[],'next_offset':0,'connected':True}
        return {'ok':True}
    monkeypatch.setattr(web,'openai_oauth_relay',fake_relay)
    csrf=auth(client)
    begun=client.post('/api/openai/oauth/start',headers=csrf,json={})
    assert begun.status_code==200,begun.text
    provider_id=begun.json()['provider_id']
    with SessionLocal() as dbs:
        provider=dbs.get(db.Provider,provider_id)
        assert provider.kind=='openai-oauth'
        assert provider.base_url=='pi://openai'
        assert provider.encrypted_key==''
        assert not provider.enabled
        assert provider.owner_id==dbs.query(User).filter_by(username='admin').first().id
    assert client.post('/api/openai/oauth/activate',headers=csrf,json={}).status_code==200
    discovered=client.get(f'/api/providers/{provider_id}/discover')
    assert discovered.json()=={'models':['model-from-pi-catalog']}
    assert client.post(f'/api/providers/{provider_id}',headers=csrf,json={
        'kind':'openai','base_url':'https://api.openai.com/v1'}).status_code==422
    assert client.post(f'/api/providers/{provider_id}',headers=csrf,json={'api_key':'steal'}).status_code==422
    assert client.post(f'/api/providers/{provider_id}/models',headers=csrf,json={
        'model_ids':['model-from-pi-catalog'],'reasoning':True}).status_code==200
    assert client.get('/settings/providers').status_code==200
    assert client.get(f'/settings/providers/{provider_id}').status_code==200
    assert client.post('/api/openai/oauth/disconnect',headers=csrf,json={}).json()['ok']
    with SessionLocal() as dbs:
        assert not dbs.get(db.Provider,provider_id).enabled
    assert all('token' not in str(c).lower() for c in calls)


def test_openai_oauth_project_owner_only(client,monkeypatch):
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_MODE','true')
    csrf=auth(client)
    with SessionLocal() as session:
        admin=session.query(User).filter_by(username='admin').first()
        provider=db.Provider(name='OpenAI private',kind='openai-oauth',base_url='pi://openai',
                            encrypted_key='',owner_id=admin.id,enabled=True)
        session.add(provider);session.flush()
        m=Model(provider_id=provider.id,model_id='private-model',name='Private Model',enabled=True)
        session.add(m);session.commit();model_id=m.id
    pid=client.post('/api/projects',headers=csrf,json={'name':'Owned OAuth Project',
                    'model_id':model_id,'profile':{}}).json()['id']
    with SessionLocal() as session:
        from openai_oauth_gateway import require_access
        project=session.get(Project,pid)
        model=session.get(Model,model_id)
        require_access(project,model,project.owner_id)
        with pytest.raises(Exception):require_access(project,model,'different-user')
    client.post('/api/admin/users',headers=csrf,json={'username':'member2',
            'email':'m2@example.org','password':'another-strong-password'})
    with SessionLocal() as session:
        member=session.query(User).filter_by(username='member2').one()
        member.must_change_password=False;session.commit()
    client.cookies.clear()
    member_csrf=auth(client,'member2','another-strong-password')
    res=client.post('/api/projects',headers=member_csrf,json={'name':'Private Model Escalation',
            'model_id':model_id,'profile':{}})
    assert res.status_code==403,res.text


@pytest.mark.asyncio
async def test_openai_oauth_local_vault_status_disconnection(tmp_path,monkeypatch):
    import openai_oauth_runtime as runtime
    auth_file=tmp_path/'.pi'/'agent'/'auth.json'
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text(json.dumps({'openai':{'type':'oauth','access':'SECRET_ACCESS',
                         'refresh':'SECRET_REFRESH'},'another-provider':{'type':'api_key','key':'KEEP'}}))
    monkeypatch.setattr(runtime,'AUTH_PATH',auth_file)
    monkeypatch.setattr(runtime,'LOCAL_HOME',tmp_path)
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_MODE','true')
    status=runtime.public_status()
    assert status['connected'] and 'access' not in json.dumps(status)
    assert await runtime.disconnect()=={'ok':True,'revoked_remotely':False}
    stored=json.loads(auth_file.read_text())
    assert 'openai' not in stored and stored['another-provider']['key']=='KEEP'
    assert not runtime.public_status()['connected']
    assert auth_file.stat().st_mode & 0o777==0o600


def test_openai_oauth_event_whitelist_and_redaction():
    import openai_oauth_runtime as runtime
    assert runtime.safe_event({'type':'auth_url','url':'https://evil.example/steal'})['type']=='error'
    assert runtime.safe_event({'type':'auth_url','url':'http://auth.openai.com/code'})['type']=='error'
    valid=runtime.safe_event({'type':'auth_url','url':'https://auth.openai.com/oauth/authorize?state=abc','access_token':'HIDDEN'})
    assert valid['type']=='auth_url' and 'access_token' not in valid
    assert runtime.safe_event({'type':'error','message':'secret token abc'})=={
        'type':'error','code':'OAUTH_RUNTIME_FAILURE',
        'message':runtime.ERROR_DESCRIPTIONS['OAUTH_RUNTIME_FAILURE']}


@pytest.mark.asyncio
async def test_openai_oauth_completion_never_relays_credentials(monkeypatch):
    from types import SimpleNamespace
    import pi_client
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_MODE','true')
    project=SimpleNamespace(id='project-one',owner_id='owner-one',thinking_level='medium')
    provider=SimpleNamespace(kind='openai-oauth',owner_id='owner-one',enabled=True,
                             encrypted_key='DO_NOT_SEND',base_url='pi://openai')
    model=SimpleNamespace(provider=provider,enabled=True,model_id='known-openai-model',
                          reasoning=True,context_window=32768)
    payload=pi_client.model_payload(project,model,'owner-one')
    assert payload['auth_mode']=='openai-oauth'
    assert payload['provider_key']=='' and 'DO_NOT_SEND' not in json.dumps(payload)
    with pytest.raises(Exception):pi_client.model_payload(project,model,'other-user')


def test_openai_oauth_model_catalog_is_enforced(client, monkeypatch):
    import importlib
    application=importlib.import_module('app')
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_MODE','true')
    async def relay(method,path,body=None,timeout=30):
        assert path=='/oauth/openai/models'
        return {'models':['catalog-available-model']}
    monkeypatch.setattr(application,'openai_oauth_relay',relay)
    csrf=auth(client)
    with SessionLocal() as session:
        uid=session.query(User).filter_by(username='admin').first().id
        provider=db.Provider(name='Owner OAuth catalog',kind='openai-oauth',base_url='pi://openai',
                             encrypted_key='',owner_id=uid,enabled=True)
        session.add(provider);session.commit();provider_id=provider.id
    uri=f'/api/providers/{provider_id}/models'
    assert client.post(uri,headers=csrf,json={'model_ids':['invented-model']}).status_code==422
    valid=client.post(uri,headers=csrf,json={'model_ids':['catalog-available-model']})
    assert valid.status_code==200 and valid.json()['created']==['catalog-available-model']
    with SessionLocal() as session:
        assert session.query(Model).filter_by(provider_id=provider_id).count()==1


def test_openai_oauth_owner_transfer_clears_private_model(client,monkeypatch):
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_MODE','true')
    csrf=auth(client)
    with SessionLocal() as session:
        owner=session.query(User).filter_by(username='admin').first()
        provider=db.Provider(name="Owner OAuth",kind='openai-oauth',base_url='pi://openai',
                            encrypted_key='',owner_id=owner.id,enabled=True)
        session.add(provider);session.flush()
        model=Model(provider_id=provider.id,model_id='private-for-owner',
                    name='Private',enabled=True)
        session.add(model);session.commit();model_id=model.id
    project=create_project(client,csrf,'Transfer private project')
    assert client.post(f'/api/projects/{project}/settings',headers=csrf,
                       json={'active_model_id':model_id}).status_code==200
    res=client.post('/api/admin/users',headers=csrf,json={'username':'newowner',
         'email':'owner@example.org','password':'another-strong-password'})
    assert res.status_code==200,res.text
    with SessionLocal() as session:
        target=session.query(User).filter_by(username='newowner').first()
        target.must_change_password=False
        session.commit();uid=target.id
    assert client.post(f'/api/projects/{project}/members',headers=csrf,json={
        'username':'newowner','role':'editor'}).status_code==200
    r=client.post(f'/api/projects/{project}/transfer-ownership',headers=csrf,
                  json={'user_id':uid,'confirm_name':'Transfer private project'})
    assert r.status_code==200,r.text
    with SessionLocal() as session:
        pr=session.get(Project,project)
        assert pr.owner_id==uid and pr.active_model_id is None
    client.cookies.clear()
    newer=auth(client,'newowner','another-strong-password')
    attempt=client.post(f'/api/projects/{project}/settings',headers=newer,
                        json={'active_model_id':model_id})
    assert attempt.status_code==403


@pytest.mark.asyncio
async def test_openai_oauth_vault_lock_prevents_disconnect(tmp_path,monkeypatch):
    from fastapi import HTTPException
    import openai_oauth_runtime as runtime
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_MODE','true')
    monkeypatch.setattr(runtime,'AUTH_PATH',tmp_path/'auth.json')
    monkeypatch.setattr(runtime,'AUTH_USE_LOCK',__import__('asyncio').Lock())
    async with runtime.AUTH_USE_LOCK:
        with pytest.raises(HTTPException) as exc:
            await runtime.disconnect()
        assert exc.value.status_code==409

# v0.9.6 ThinkRail-inspired conversation continuity and observable SSE

def test_chat_continues_answer_once_with_usage_and_no_duplicate_message(client, monkeypatch):
    import importlib
    from planning import save_question
    from db import AgentRun, PlanningQuestion
    application=importlib.import_module('app')
    csrf=auth(client)
    provider=client.post('/api/providers',headers=csrf,json={'name':'MockChat','base_url':'http://model.test/v1','api_key':'x'}).json()['id']
    assert client.post(f'/api/providers/{provider}/models',headers=csrf,json={'model_ids':['mock-continuation']}).status_code==200
    with SessionLocal() as s:
        mid=s.query(Model).filter_by(model_id='mock-continuation').one().id
    pid=create_project(client,csrf)
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,json={'active_model_id':mid}).status_code==200
    with SessionLocal() as s:
        did=s.query(db.Discussion).filter_by(project_id=pid).one().id
        item,_=save_question(s,pid,did,{
            'question':'How should user accounts be isolated in this system?',
            'options':[{'label':'Per-user accounts','pros':'Privacy','cons':'Permissions design'},
                       {'label':'Shared access','pros':'Simpler','cons':'Leaks across users'}],
            'recommended_index':0,'recommendation_reason':'User-owned trading accounts require isolation'})
        qid=item.id
    res=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer',headers=csrf,
                    json={'selected_ids':['o1'],'custom_answer':'Each person supplies their own trading credentials'})
    assert res.status_code==200
    assert 'answered' in res.text

    async def simulated(project, discussion, db, model, *, role, mode, run_id=None, actor_id=None):
        assert db.query(Message).filter_by(discussion_id=did,role='user').count()==1
        yield 'status',{'phase':'reasoning','message':'Đang phân tích…'}
        yield 'delta',{'text':'Đã hiểu. Mỗi người có tài khoản riêng.'}
        yield 'usage',{'input_tokens':123,'output_tokens':27,'cache_read_tokens':10,'source':'provider'}
        yield 'final',{'text':'Đã hiểu. Mỗi người có tài khoản riêng.'}
    monkeypatch.setattr(application,'pi_stream',simulated)
    url=f'/api/projects/{pid}/chat'
    answer=client.post(url,headers=csrf,json={'discussion_id':did,'continue_question_id':qid,'message':'','role':'analyst','mode':'discovery'})
    assert answer.status_code==200,answer.text
    assert '"type": "usage"' in answer.text
    assert '"input_tokens": 123' in answer.text
    assert '"type": "timing"' in answer.text
    assert '"type": "done"' in answer.text
    with SessionLocal() as s:
        messages=s.query(Message).filter_by(discussion_id=did).order_by(Message.created_at).all()
        assert [m.role for m in messages]==['user','assistant']
        run=s.query(AgentRun).filter_by(discussion_id=did).one()
        assert run.status=='completed' and run.usage['output_tokens']==27
        assert isinstance(run.duration_ms,int)
        assert s.get(PlanningQuestion,qid).answer['continuation_run_id']==run.id
        assert run.events and any(e.get('phase','')=='reasoning' or 'Đang phân tích' in e.get('message','') for e in run.events)
        assert messages[-1].run_id==run.id
    restored=client.get(f'/projects/{pid}/discussion?did={did}')
    assert restored.status_code==200
    assert 'Run history · phiên này' in restored.text
    assert 'Đang phân tích' in restored.text
    assert '123' in restored.text
    repeat=client.post(url,headers=csrf,json={'discussion_id':did,'continue_question_id':qid,'message':''})
    assert repeat.status_code==409
    assert client.post(url,headers=csrf,json={'discussion_id':did,'continue_question_id':qid,'message':'duplicate'}).status_code==422


def test_usage_normalization_distinguishes_provider_from_estimates():
    from chat_events import normalize_usage, accumulate_usage
    assert normalize_usage({},source='provider')=={}
    assert normalize_usage({'prompt_tokens':0,'completion_tokens':12,
                            'prompt_tokens_details':{'cached_tokens':7}},source='provider')=={
        'source':'provider','input_tokens':0,'output_tokens':12,'cache_read_tokens':7}
    p=normalize_usage({'input':123,'output':21,'cacheRead':11,'cacheWrite':4,'cost':{'total':0.08}},source='pi')
    assert p['input_tokens']==123 and p['output_tokens']==21 and p['cost_usd']==0.08
    assert accumulate_usage({'input_tokens':5},p)['input_tokens']==128
    assert normalize_usage({'input':'estimated','output':True},source='pi')=={}


@pytest.mark.asyncio
@pytest.mark.parametrize('include_final',[True,False])
async def test_pi_sdk_stream_forwards_native_events(client,monkeypatch,include_final):
    import pi_client
    import httpx
    from fastapi import HTTPException
    csrf=auth(client)
    provider=client.post('/api/providers',headers=csrf,json={
        'name':'Native Pi Stream','base_url':'http://model.internal/v1','api_key':'x'}).json()['id']
    client.post(f'/api/providers/{provider}/models',headers=csrf,json={'model_ids':['pi-model']})
    pid=create_project(client,csrf)
    requests=[]
    def handler(request):
        body=json.loads(request.content);requests.append(body)
        assert request.url.path=='/run'
        assert body['auth_mode']=='api_key' and body['model_id']=='pi-model'
        packets=[{'type':'thinking','payload':{'text':'Available model reasoning'}},
                 {'type':'tool','payload':{'name':'search_web','status':'complete'}},
                 {'type':'delta','payload':{'text':'Hello'}},
                 {'type':'usage','payload':{'source':'pi','input_tokens':99,'output_tokens':7}}]
        if include_final:
            packets.append({'type':'final','payload':{'text':'Hello'}})
        data=''.join('data: '+json.dumps(x)+'\n\n' for x in packets)
        return httpx.Response(200,content=data,headers={'Content-Type':'text/event-stream'})
    real=httpx.AsyncClient
    monkeypatch.setattr(pi_client,'runner_config',lambda:('http://pi-runner:8090',{'X-Runner-Key':'x'*32}))
    monkeypatch.setattr(pi_client.httpx,'AsyncClient',lambda **kw:real(transport=httpx.MockTransport(handler)))
    with SessionLocal() as db_session:
        project=db_session.get(Project,pid)
        model=db_session.query(Model).filter_by(model_id='pi-model').one()
        if not include_final:
            with pytest.raises(HTTPException,match='ended before a final event'):
                _=[x async for x in pi_client.stream(project,model,None,{'message':'Hi','run_id':'run'})]
            return
        events=[x async for x in pi_client.stream(project,model,None,{'message':'Hi','run_id':'run'})]
    assert ('delta',{'text':'Hello'}) in events
    assert events[-1]==('final',{'text':'Hello'})
    assert any(k=='usage' and v['output_tokens']==7 for k,v in events)
    assert requests and requests[0]['provider_base']=='http://model.internal/v1'


@pytest.mark.asyncio
async def test_sse_heartbeat_does_not_cancel_waiting_model():
    import asyncio
    from chat_events import with_heartbeat
    completed=[]
    async def slow_model():
        await asyncio.sleep(.025)
        completed.append(True)
        yield 'delta',{'text':'ok'}
    events=[]
    async for name,payload in with_heartbeat(slow_model(),interval_seconds=.005):
        events.append((name,payload))
    assert completed==[True]
    assert any(name=='status' and payload['phase']=='waiting' for name,payload in events)
    assert events[-1]==('delta',{'text':'ok'})

# v1.0.2: same-session Guided Discovery, atomic answer + Pi SSE, document handoff.

def _make_discussion_with_model(client,csrf,label='guided-flow-model'):
    provider=client.post('/api/providers',headers=csrf,json={
        'name':'Local-Guided-Flow','base_url':'http://local.test/v1','api_key':'local-test'}).json()['id']
    assert client.post(f'/api/providers/{provider}/models',headers=csrf,json={'model_ids':[label]}).status_code==200
    with SessionLocal() as s:
        mid=s.query(Model).filter_by(model_id=label).one().id
    pid=create_project(client,csrf,'Guided Flow')
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,
                       json={'active_model_id':mid}).status_code==200
    with SessionLocal() as s:
        did=s.query(db.Discussion).filter_by(project_id=pid).one().id
    return pid,did


def test_answer_stream_atomic_continues_pi_and_surfaces_question(client,monkeypatch):
    from planning import save_question
    from db import PlanningQuestion
    import importlib
    module=importlib.import_module('app')
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf)
    with SessionLocal() as s:
        q,_=save_question(s,pid,did,{
            'question':'How should credentials be isolated?',
            'options':[{'label':'Per account','pros':'Secure','cons':'More implementation'},
                       {'label':'Shared','pros':'Simple','cons':'Risk of leaking'}],
            'recommended_index':0,'recommendation_reason':'Data isolation is critical.'})
        qid=q.id
    async def fake_pi(project, discussion, dbs, model, **kw):
        assert kw['run_id']
        text=dbs.query(Message).filter_by(discussion_id=did,role='user').all()
        assert len(text)==1 and 'Phương án người dùng chọn' in text[0].content
        assert 'prompt_override' not in kw
        yield 'status',{'phase':'thinking','message':'Pi nhận câu trả lời'}
        yield 'thinking',{'text':'Assessing isolation tradeoffs'}
        yield 'delta',{'text':'Đã xác nhận tài khoản riêng.'}
        yield 'usage',{'input_tokens':140,'output_tokens':32}
        # Real Pi tools transact via a separate gateway DB session.
        with SessionLocal() as external:
            save_question(external,pid,did,{
                'question':'How will trades be audited?',
                'options':[{'label':'Append-only ledger','pros':'Traceable','cons':'Storage'},
                           {'label':'Event log','pros':'Lightweight','cons':'Less structured'}],
                'recommended_index':0,'recommendation_reason':'Auditability matters.'})
    monkeypatch.setattr(module,'pi_stream',fake_pi)
    r=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer-stream',headers=csrf,json={
        'selected_ids':['o1'],'custom_answer':'Separate accounts per user','note':'No shared credentials'})
    assert r.status_code==200,r.text
    assert '"type": "thinking"' in r.text
    assert '"type": "delta"' in r.text
    assert '"type": "planning_question"' in r.text
    assert '"type": "done"' in r.text
    with SessionLocal() as s:
        q=s.get(PlanningQuestion,qid)
        assert q.status=='answered' and q.answer.get('continuation_run_id')
        msgs=s.query(Message).filter_by(discussion_id=did).all()
        assert len(msgs)==2 and sorted(x.role for x in msgs)==['assistant','user']
        assert s.query(PlanningQuestion).filter_by(discussion_id=did,status='pending').count()==1
    retry=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer-stream',headers=csrf,json={
        'selected_ids':['o1']})
    assert retry.status_code==409


def test_auto_advance_stays_in_same_pi_session_and_never_saves_fake_user(client,monkeypatch):
    import importlib
    module=importlib.import_module('app')
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'advance-flow')
    calls=[]
    async def fake_pi(project,discussion,dbs,model,**kw):
        calls.append(kw)
        assert 'prompt_override' in kw
        assert 'CÙNG session' in kw['prompt_override']
        yield 'status',{'phase':'planning','message':'Pi đang lập câu hỏi'}
        yield 'delta',{'text':'Đang rà những thông tin chưa rõ.'}
    monkeypatch.setattr(module,'pi_stream',fake_pi)
    r=client.post(f'/api/projects/{pid}/chat',headers=csrf,
                  json={'discussion_id':did,'mode':'discovery','advance_planning':True})
    assert r.status_code==200,r.text
    assert '"type": "done"' in r.text
    assert len(calls)==1
    with SessionLocal() as s:
        assert s.query(Message).filter_by(discussion_id=did,role='user').count()==0
    assert client.post(f'/api/projects/{pid}/chat',headers=csrf,json={
        'discussion_id':did,'mode':'discuss','advance_planning':True}).status_code==422
    assert client.post(f'/api/projects/{pid}/chat',headers=csrf,json={
        'discussion_id':did,'mode':'discovery','advance_planning':True,'message':'spoof'}).status_code==422


@pytest.mark.asyncio
async def test_finish_discovery_creates_unapproved_drafts_with_answers(client):
    from planning import save_question, answer_question
    from artifact_builder import ARTIFACTS
    csrf=auth(client)
    pid=create_project(client,csrf,'Draft Completion')
    with SessionLocal() as s:
        pr=s.get(Project,pid)
        did=s.query(db.Discussion).filter_by(project_id=pid).one().id
        for i in range(3):
            q,_=save_question(s,pid,did,{
                'question':f'Important scope question {i}?',
                'options':[{'label':f'Selected choice {i}','pros':'Benefit','cons':'Cost'},
                           {'label':'Alternative','pros':'Other benefit','cons':'Risk'}],
                'recommended_index':0,'recommendation_reason':'Best fit for project scope'})
            answer_question(s,q,{'selected_ids':['o1']})
        invalid=await tool_call('finish_discovery',{'summary':'short','evidence':'yes','remaining_unknowns':[]},
                                project=pr,db=s,discussion_id=did)
        assert invalid['status']=='needs_discovery'
        valid=await tool_call('finish_discovery',{
            'summary':'The scope includes independent users, transactions, an auditable dashboard, separated credentials, and secure reconciliation. All critical cases and boundaries were discussed with the project owner.',
            'evidence':'Three user-confirmed decisions are recorded in this discussion; open risks are documented in the drafts.',
            'remaining_unknowns':[]},project=pr,db=s,discussion_id=did)
        assert valid['status']=='draft_ready' and len(valid['documents_created'])==len(ARTIFACTS)
        docs=s.query(Document).filter_by(project_id=pid).all()
        assert len(docs)==len(ARTIFACTS) and all(x.status=='draft' for x in docs)
        assert s.get(Project,pid).profile['_discovery']['status']=='draft_ready'
        from storage import read_text
        brief=read_text(pid,'docs/drafts/PRODUCT-BRIEF.md')
        assert 'Selected choice 0' in brief and 'Selected choice 2' in brief
        # Repeating completion does not overwrite or duplicate drafts.
        again=await tool_call('finish_discovery',{
            'summary':'The scope includes independent users, transactions, an auditable dashboard, separated credentials, and secure reconciliation. All critical cases and boundaries were discussed with the project owner.',
            'evidence':'Three user decisions recorded.', 'remaining_unknowns':[]},
            project=pr,db=s,discussion_id=did)
        assert again['status']=='draft_ready' and not again['documents_created']
        document=s.query(Document).filter_by(project_id=pid,path='docs/drafts/PRODUCT-BRIEF.md').one()
        document.status='approved';s.commit()
        protected=await tool_call('write_document',{'filename':'PRODUCT-BRIEF.md','content':'No overwrite'},
                                  project=pr,db=s,discussion_id=did)
        assert 'error' in protected
        assert read_text(pid,'docs/drafts/PRODUCT-BRIEF.md')==brief


def test_answer_stream_failure_allows_retry_and_is_scoped(client,monkeypatch):
    from planning import save_question
    from db import PlanningQuestion
    import importlib
    application=importlib.import_module('app')
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'retry-flow')
    other=create_project(client,csrf,'Not The Same Project')
    with SessionLocal() as s:
        q,_=save_question(s,pid,did,{
            'question':'How should the system protect customer data?',
            'options':[{'label':'Isolated storage','pros':'Privacy','cons':'Extra design'},
                       {'label':'Shared storage','pros':'Simple','cons':'Risk'}],
            'recommended_index':0,'recommendation_reason':'Data privacy is essential.'})
        qid=q.id
    answer={'selected_ids':['o1']}
    assert client.post(f'/api/projects/{other}/planning/questions/{qid}/answer-stream',headers=csrf,json=answer).status_code==409
    async def broken_pi(*args,**kwargs):
        yield 'status',{'phase':'model','message':'Pi start'}
        raise RuntimeError('Simulated transport interruption')
    monkeypatch.setattr(application,'pi_stream',broken_pi)
    url=f'/api/projects/{pid}/planning/questions/{qid}/answer-stream'
    failure=client.post(url,headers=csrf,json=answer)
    assert failure.status_code==200
    assert '"type": "error"' in failure.text
    with SessionLocal() as s:
        q=s.get(PlanningQuestion,qid)
        assert q.status=='answered' and 'continuation_run_id' not in q.answer
        assert s.query(Message).filter_by(discussion_id=did,role='user').count()==1
    async def recovered_pi(*args,**kwargs):
        yield 'delta',{'text':'Recovered without storing another user message.'}
    monkeypatch.setattr(application,'pi_stream',recovered_pi)
    retry=client.post(f'/api/projects/{pid}/chat',headers=csrf,json={
        'discussion_id':did,'continue_question_id':qid,'mode':'discovery'})
    assert retry.status_code==200 and '"type": "done"' in retry.text
    with SessionLocal() as s:
        assert s.query(Message).filter_by(discussion_id=did,role='user').count()==1
        assert s.query(Message).filter_by(discussion_id=did,role='assistant').count()==1


def test_discovery_progress_and_ui_contract(client):
    csrf=auth(client)
    pid=create_project(client,csrf,'Progress')
    with SessionLocal() as s:
        did=s.query(db.Discussion).filter_by(project_id=pid).one().id
    r=client.get(f'/api/projects/{pid}/planning/discovery-progress',params={'discussion_id':did})
    assert r.status_code==200
    assert r.json()['status']=='interviewing' and r.json()['answered']==0
    assert r.json()['total_artifacts']==10
    html=client.get(f'/projects/{pid}/discussion?did={did}').text
    assert 'discoveryProgress' in html and 'data-discovery-ready="false"' in html
    code=(ROOT/'source/static/js/discussion.js').read_text(encoding='utf-8')
    assert '/answer-start' in code and 'advance_planning:true' in code and 'EventSource' in code
    assert 'planning_complete' in code and 'planning_question' in code
    assert '/planning/questions/suggest' not in code


def test_three_round_interview_reaches_draft_handoff_via_pi_tools(client,monkeypatch):
    """Simulate three sequential user decisions, each followed by Pi's next step."""
    import importlib
    from planning import save_question
    from db import PlanningQuestion
    from artifact_builder import ARTIFACTS
    application=importlib.import_module('app')
    csrf=auth(client)
    pid,did=_make_discussion_with_model(client,csrf,'multi-round-interview')
    def create_q(number):
        with SessionLocal() as s:
            question,_=save_question(s,pid,did,{
                'question':f'What is the scope decision number {number}?',
                'options':[{'label':f'Option {number} A','pros':'Fits MVP','cons':'Work'},
                           {'label':f'Option {number} B','pros':'Simple','cons':'Risk'}],
                'recommended_index':0,'recommendation_reason':'Fits the project constraints.'})
            return question.id
    qid=create_q(1)
    seen=[]
    async def simulated_pi(project, discussion, dbs, model, **kw):
        assert discussion.id==did
        answered=dbs.query(PlanningQuestion).filter_by(project_id=pid,discussion_id=did,status='answered').count()
        seen.append((answered,kw['run_id']))
        yield 'status',{'phase':'thinking','message':'Pi assessing previous answer'}
        yield 'delta',{'text':f'Đã phân tích quyết định thứ {answered}.'}
        if answered<3:
            create_q(answered+1)
        else:
            with SessionLocal() as gateway:
                result=await tool_call('finish_discovery',{
                    'summary':'The stakeholders have defined the user-facing scope, account isolation, trade reconciliation, audit trails and operational constraints. The planning record now contains three confirmed decisions and documented risks.',
                    'evidence':'The owner answered each of the three planning questions and verified the MVP boundary.',
                    'remaining_unknowns':[]},project=gateway.get(Project,pid),db=gateway,discussion_id=did)
                assert result['status']=='draft_ready'
    monkeypatch.setattr(application,'pi_stream',simulated_pi)
    for step in range(1,4):
        r=client.post(f'/api/projects/{pid}/planning/questions/{qid}/answer-stream',headers=csrf,
                      json={'selected_ids':['o1']})
        assert r.status_code==200,r.text
        assert '"type": "done"' in r.text
        if step<3:
            assert '"type": "planning_question"' in r.text
            with SessionLocal() as s:
                q=s.query(PlanningQuestion).filter_by(project_id=pid,discussion_id=did,status='pending').one()
                qid=q.id
        else:
            assert '"type": "planning_complete"' in r.text
    assert [answered for answered,_run in seen]==[1,2,3]
    assert len({run for _,run in seen})==3
    r=client.get(f'/api/projects/{pid}/planning/discovery-progress',params={'discussion_id':did})
    assert r.json()['status']=='draft_ready'
    assert r.json()['answered']==3
    assert r.json()['pending']==0
    assert r.json()['drafts']==len(ARTIFACTS)
    # User may still chat, but automatic discovery must no longer create questions.
    after=client.post(f'/api/projects/{pid}/chat',headers=csrf,json={
        'discussion_id':did,'mode':'discovery','advance_planning':True})
    assert after.status_code==409
