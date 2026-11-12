"""Provider/model CRUD and historical integrity regression tests for v0.9.5."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'source'))
from test_app import client as client_fixture, auth, create_project
client=client_fixture
from db import (Provider, Model, Project, SessionLocal, ReviewJob, ReviewJobStep,
                SpecReview, utcnow)


def add_provider(client, csrf, name='Local 3090'):
    r=client.post('/api/providers',headers=csrf,json={'name':name,'base_url':'http://host.docker.internal:8008/v1', 'api_key':'hidden'})
    assert r.status_code==200,r.text
    pid=r.json()['id']
    r=client.post(f'/api/providers/{pid}/models',headers=csrf,json={'model_ids':['qwen27b','qwen4b']})
    assert r.status_code==200,r.text
    with SessionLocal() as s:
        models={m.model_id:m.id for m in s.query(Model).filter_by(provider_id=pid).all()}
    return pid, models


def test_provider_edit_and_details_visible(client):
    csrf=auth(client)
    pid,models=add_provider(client,csrf)
    assert client.get('/settings/providers').status_code==200
    assert 'provider-delete' in client.get('/settings/providers').text
    assert 'Sửa' in client.get('/settings/providers').text
    res=client.post(f'/api/providers/{pid}',headers=csrf,json={'name':'Updated Local 3090', 'base_url':'https://models.example.test/v1','kind':'openai-compatible','api_key':'replacement','enabled':False})
    assert res.status_code==200,res.text
    with SessionLocal() as s:
        p=s.get(Provider,pid)
        from security import decrypt
        assert p.name=='Updated Local 3090'
        assert p.base_url=='https://models.example.test/v1'
        assert not p.enabled
        assert decrypt(p.encrypted_key)=='replacement'
    assert 'providerEditName' in client.get(f'/settings/providers/{pid}').text
    assert 'model-delete' in client.get(f'/settings/providers/{pid}').text
    assert client.post(f'/api/providers/{pid}',headers=csrf,json={'name':'   '}).status_code==422


def test_custom_model_limits_and_sampling_reach_runtime_and_validate(client):
    from model_runtime import request_body

    csrf=auth(client)
    provider,models=add_provider(client,csrf)
    mid=models['qwen27b']
    path=f'/api/providers/{provider}/models/{mid}'
    settings={'context_window':262144,'max_output_tokens':32768,'reasoning':True,
              'api':'openai-completions','compat':{'maxTokensField':'max_tokens','thinkingFormat':'qwen'},
              'sampling_params':{'temperature':0.7,'top_p':0.95,'top_k':40,'seed':12}}
    result=client.post(path,headers=csrf,json=settings)
    assert result.status_code==200,result.text
    project=create_project(client,csrf)
    with SessionLocal() as session:
        model=session.get(Model,mid)
        resource,payload=request_body(model,[],max_tokens=model.max_output_tokens,tools=[],thinking='off')
        assert resource=='chat/completions'
        assert model.context_window==262144 and payload['max_tokens']==32768
        assert payload['enable_thinking'] is False
        for key,value in settings['sampling_params'].items():
            assert payload[key]==value
    invalid=[{'max_output_tokens':262144},{'max_output_tokens':True},{'context_window':3.5},
             {'sampling_params':{'max_tokens':1}},{'sampling_params':{'temperature':9}},
             {'compat':{'supportsDeveloperRole':'false'}},{'compat':{'headers':{}}},
             {'api':'openai-responses'}]
    for patch in invalid:
        response=client.post(path,headers=csrf,json=patch)
        assert response.status_code==422,(patch,response.text)
    with SessionLocal() as session:
        model=session.get(Model,mid)
        assert model.max_output_tokens==32768 and model.context_window==262144
    assert client.post(path,headers=csrf,json={'api':'openai-responses','compat':{}}).status_code==200
    html=client.get(f'/settings/providers/{provider}').text
    assert 'max_output_tokens' in html and 'sampling_params' in html
    with SessionLocal() as session:
        owner=session.get(Project,project).owner_id
        row=session.get(Provider,provider)
        row.kind='openai-oauth';row.owner_id=owner;session.commit()
    assert client.post(path,headers=csrf,json={'max_output_tokens':16384}).status_code==410
    assert client.get(f'/settings/providers/{provider}').status_code==410


def test_new_custom_model_uses_validated_runtime_settings(client):
    csrf=auth(client)
    provider,_=add_provider(client,csrf)
    path=f'/api/providers/{provider}/models'
    bad=client.post(path,headers=csrf,json={'model_ids':['bad-limits'],'context_window':32768,'max_output_tokens':65536})
    assert bad.status_code==422
    result=client.post(path,headers=csrf,json={'model_ids':['long-context'],'context_window':262144,
                                              'max_output_tokens':65536,'sampling_params':{'top_p':0.9}})
    assert result.status_code==200,result.text
    with SessionLocal() as session:
        row=session.query(Model).filter_by(provider_id=provider,model_id='long-context').one()
        assert row.context_window==262144 and row.max_output_tokens==65536
        assert row.sampling_params=={'top_p':0.9}
        assert session.query(Model).filter_by(provider_id=provider,model_id='bad-limits').count()==0


def test_delete_provider_preserves_reviews_and_resets_projects(client):
    csrf=auth(client)
    pid,model_ids=add_provider(client,csrf)
    project=create_project(client,csrf)
    with SessionLocal() as s:
        p=s.get(Project,project)
        p.active_model_id=model_ids['qwen27b']
        uid=p.owner_id
        r=SpecReview(project_id=project,model_id=model_ids['qwen27b'],engine='model',fingerprint='abc',created_by=uid,summary='Original finding')
        s.add(r);s.flush()
        j=ReviewJob(project_id=project,requester_id=uid,model_id=model_ids['qwen27b'],
                    role_models={'qa':model_ids['qwen4b']},status='succeeded',fingerprint='abc',review_id=r.id)
        s.add(j);s.flush()
        j_id=j.id;r_id=r.id
        s.add(ReviewJobStep(job_id=j_id,role='qa',model_id=model_ids['qwen4b'],summary='QA finding'))
        s.commit()
    prev=client.get(f'/api/providers/{pid}/delete-preview')
    assert prev.status_code==200,prev.text
    assert prev.json()['models']==2
    assert prev.json()['projects']==1
    assert prev.json()['review_jobs']==1
    assert client.request('DELETE',f'/api/providers/{pid}',headers={'X-CSRF-Token':'wrong'},json={'confirm_name':'Local 3090'}).status_code==403
    assert client.request('DELETE',f'/api/providers/{pid}',headers=csrf,json={'confirm_name':'typo'}).status_code==422
    res=client.request('DELETE',f'/api/providers/{pid}',headers=csrf,json={'confirm_name':'Local 3090'})
    assert res.status_code==200,res.text
    assert res.json()['models_deleted']==2
    assert res.json()['review_jobs_preserved']==1
    assert client.get(f'/settings/providers/{pid}').status_code==404
    with SessionLocal() as s:
        assert s.get(Provider,pid) is None
        for model_id in model_ids.values():assert s.get(Model,model_id) is None
        assert s.get(Project,project).active_model_id is None
        job=s.get(ReviewJob,j_id)
        assert job.model_id is None
        assert job.model_snapshot['deleted_models'][model_ids['qwen27b']]['model_id']=='qwen27b'
        assert job.model_snapshot['deleted_models'][model_ids['qwen4b']]['model_id']=='qwen4b'
        review=s.get(SpecReview,r_id)
        assert review.summary=='Original finding'
        assert review.model_snapshot['model_id']=='qwen27b'


def test_delete_rejects_queued_review_and_allows_single_model(client):
    csrf=auth(client)
    pid,ids=add_provider(client,csrf)
    project=create_project(client,csrf)
    with SessionLocal() as s:
        owner=s.get(Project,project).owner_id
        job=ReviewJob(project_id=project,requester_id=owner,model_id=ids['qwen4b'],
                      role_models={'architect':ids['qwen27b']},status='queued',fingerprint='test')
        s.add(job);s.commit();job_id=job.id
    url=f'/api/providers/{pid}/models/{ids["qwen27b"]}'
    assert client.get(url+'/delete-preview').json()['active_reviews']==1
    response=client.request('DELETE',url,headers=csrf,json={'confirm_model':'qwen27b'})
    assert response.status_code==409
    with SessionLocal() as s:
        job=s.get(ReviewJob,job_id);job.status='cancelled';job.finished_at=utcnow();s.commit()
    assert client.request('DELETE',url,headers=csrf,json={'confirm_model':'not-qwen27b'}).status_code==422
    response=client.request('DELETE',url,headers=csrf,json={'confirm_model':'qwen27b'})
    assert response.status_code==200,response.text
    with SessionLocal() as s:
        assert s.get(Model,ids['qwen27b']) is None
        assert s.get(Model,ids['qwen4b']) is not None
        assert s.get(Provider,pid) is not None
        job=s.get(ReviewJob,job_id)
        assert job.model_id==ids['qwen4b']
        assert ids['qwen27b'] in job.model_snapshot['deleted_models']


def test_no_deletion_when_active_chat(client):
    from db import AgentRun, Discussion
    csrf=auth(client)
    pid,ids=add_provider(client,csrf)
    project=create_project(client,csrf)
    with SessionLocal() as s:
        pr=s.get(Project,project)
        pr.active_model_id=ids['qwen27b']
        discussion=s.query(Discussion).filter_by(project_id=project).first()
        assert discussion is not None
        run=AgentRun(project_id=project,discussion_id=discussion.id,status='running',model_name='qwen27b')
        s.add(run);s.commit()
    r=client.request('DELETE',f'/api/providers/{pid}',headers=csrf,json={'confirm_name':'Local 3090'})
    assert r.status_code==409
    with SessionLocal() as s:
        assert s.get(Provider,pid) is not None
