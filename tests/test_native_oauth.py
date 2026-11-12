import asyncio
import json
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest
from fastapi import HTTPException
from cryptography.hazmat.primitives.asymmetric import rsa

from test_app import client, auth, create_project, SessionLocal, db
from test_native_harness import frames, install_provider
from chat_background import run_once
import openai_oauth


def test_native_oauth_login_refresh_and_chat_without_pi(client,monkeypatch,tmp_path):
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_HOME',str(tmp_path/'vault'))
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_MODE','true')
    csrf=auth(client)
    assert 'Continue with ChatGPT' in client.get('/settings/providers').text
    assert client.post('/api/openai/oauth/start',json={}).status_code==403
    started=client.post('/api/openai/oauth/start',headers=csrf,json={})
    assert started.status_code==200,started.text
    login_id,provider_id=started.json()['id'],started.json()['provider_id']
    state=client.get(f'/api/openai/oauth/login/{login_id}').json()
    query=parse_qs(urlsplit(state['url']).query)
    assert query['client_id']==['dynamic_agent_client']
    assert query['code_challenge_method']==['S256'] and 'verifier' not in state
    assert client.post('/api/openai/oauth/start',headers=csrf,json={}).status_code==409
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    jwk=json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid='test-key',alg='RS256',use='sig')
    signed=jwt.encode({'iss':openai_oauth.AUTH,'aud':'oaiapp_native_fixture','sub':'test-account',
                      'exp':int(time.time())+3600,'nonce':query['nonce'][0],'email':'qa@example.org'},
                     key,algorithm='RS256',headers={'kid':'test-key'})
    grants=[]

    def provider(request):
        if request.url.path=='/.well-known/jwks.json':return httpx.Response(200,json={'keys':[jwk]})
        if request.url.path=='/api/accounts/oauth/token':
            fields=parse_qs(request.content.decode());grants.append(fields['grant_type'][0])
            if grants[-1]=='authorization_code':
                assert fields['client_id']==['oaiapp_native_fixture']
                assert fields['redirect_uri']==query['redirect_uri']
                return httpx.Response(200,json={'access_token':'private-access-1','refresh_token':'private-refresh-1',
                                               'id_token':signed,'scope':openai_oauth.SCOPES,'expires_in':3600})
            assert fields['refresh_token']==['private-refresh-1']
            return httpx.Response(200,json={'access_token':'private-access-2','refresh_token':'private-refresh-2',
                                           'expires_in':3600,'scope':openai_oauth.SCOPES})
        assert request.url.host=='api.openai.com'
        assert request.url.path=='/v1/responses'
        assert request.headers['Authorization']=='Bearer private-access-2'
        body=json.loads(request.content)
        assert body['stream'] and body['store'] is False and 'tools' in body
        return frames({'type':'response.output_text.delta','delta':'Đã kết nối qua OAuth native.'},
                      {'type':'response.completed','response':{'output':[], 'usage':{'input_tokens':25,'output_tokens':8}}})

    monkeypatch.setattr(openai_oauth,'client',lambda:httpx.AsyncClient(transport=httpx.MockTransport(provider)))
    install_provider(monkeypatch,provider)
    callback=query['redirect_uri'][0]+'?'+urlencode({'code':'test-code','state':query['state'][0],'client_id':'oaiapp_native_fixture'})
    wrong=client.post(f'/api/openai/oauth/login/{login_id}/answer',headers=csrf,json={'value':callback.replace(query['state'][0],'wrong-state')})
    assert wrong.status_code==403 and not grants
    completed=client.post(f'/api/openai/oauth/login/{login_id}/answer',headers=csrf,json={'value':callback})
    assert completed.status_code==200,completed.text
    assert 'private-access' not in completed.text
    status=client.get('/api/openai/oauth/status')
    assert status.json()['connected'] and 'private-access' not in status.text
    assert 'private-access' not in openai_oauth.vault_path(provider_id).read_text()
    vault=openai_oauth.read_vault(provider_id)
    vault['token']['expires_at']=time.time()-5
    openai_oauth.write_vault(provider_id,vault)
    with SessionLocal() as session:
        owner=session.get(db.Provider,provider_id).owner_id
    for _ in range(2):
        assert asyncio.run(openai_oauth.authorization(provider_id,owner))['Authorization']=='Bearer private-access-2'
    assert grants==['authorization_code','refresh_token']
    assert openai_oauth.read_vault(provider_id)['token']['refresh_token']=='private-refresh-2'
    assert client.post(f'/api/providers/{provider_id}/models',headers=csrf,json={'model_ids':['native-fixture']}).status_code==200
    with SessionLocal() as session:
        mid=session.query(db.Model).filter_by(provider_id=provider_id).one().id
    pid=create_project(client,csrf,'OAuth native project')
    assert client.post(f'/api/projects/{pid}/settings',headers=csrf,json={'active_model_id':mid}).status_code==200
    with SessionLocal() as session:
        did=session.query(db.Discussion).filter_by(project_id=pid).one().id
    queued=client.post(f'/api/projects/{pid}/chat/start',headers=csrf,json={'discussion_id':did,'message':'Xin chào','mode':'discuss'}).json()
    assert asyncio.run(run_once())==queued['run_id']
    with SessionLocal() as session:
        run=session.get(db.AgentRun,queued['run_id'])
        assert run.status=='completed',run.error
        assert 'private-access' not in json.dumps(run.request_payload)
    html=client.get(f'/projects/{pid}/discussion').text
    assert 'Đã kết nối qua OAuth native.' in html and 'private-access' not in html
    assert client.post('/api/openai/oauth/disconnect',headers=csrf,json={}).status_code==200
    assert not client.get('/api/openai/oauth/status').json()['connected']
    with pytest.raises(HTTPException):asyncio.run(openai_oauth.authorization(provider_id,owner))


def test_invalid_signed_identity_is_rejected_and_login_can_be_cancelled(client,monkeypatch,tmp_path):
    monkeypatch.setenv('OPENAI_OAUTH_LOCAL_HOME',str(tmp_path/'vault'))
    csrf=auth(client)
    started=client.post('/api/openai/oauth/start',headers=csrf,json={}).json()
    login_id=started['id'];provider_id=started['provider_id']
    state=client.get(f'/api/openai/oauth/login/{login_id}').json()
    query=parse_qs(urlsplit(state['url']).query)
    def provider(request):
        return httpx.Response(200,json={'access_token':'unverified-access','id_token':'bad-signature','expires_in':3600,'scope':openai_oauth.SCOPES})
    monkeypatch.setattr(openai_oauth,'client',lambda:httpx.AsyncClient(transport=httpx.MockTransport(provider)))
    callback=query['redirect_uri'][0]+'?'+urlencode({'code':'test','state':query['state'][0],'client_id':'oaiapp_test'})
    result=client.post(f'/api/openai/oauth/login/{login_id}/answer',headers=csrf,json={'value':callback})
    assert result.status_code==403
    assert 'token' not in openai_oauth.read_vault(provider_id)
    assert client.post('/api/openai/oauth/cancel',headers=csrf,json={}).status_code==200
    assert 'login' not in openai_oauth.read_vault(provider_id)
    assert client.post('/api/openai/oauth/start',headers=csrf,json={}).status_code==200
