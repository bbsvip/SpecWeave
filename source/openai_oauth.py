"""Owner-scoped native ChatGPT OAuth. Credentials stay in an encrypted local vault."""
import base64
import asyncio
import hashlib
import json
import os
import secrets
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID, uuid4

import httpx
import jwt
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool

from db import Provider, SessionLocal
from security import decrypt, encrypt
from storage import DATA_ROOT

AUTH = 'https://auth.openai.com'
API = 'https://api.openai.com/v1'
SCOPES = 'openid profile email offline_access resource.invoke chatgpt.tokens.use.direct'


def enabled():
    return os.getenv('OPENAI_OAUTH_LOCAL_MODE','true').lower() in ('1','true','yes')


def client():
    return httpx.AsyncClient(timeout=20,trust_env=False,follow_redirects=False)


def require_access(project, model, actor_id):
    if model.provider.kind!='openai-oauth':
        return
    if not enabled():
        raise HTTPException(503,'Đăng nhập ChatGPT đang tắt trong cấu hình máy chủ')
    if not actor_id or model.provider.owner_id!=actor_id or (project and project.owner_id!=actor_id):
        raise HTTPException(403,'Kết nối ChatGPT chỉ dùng cho dự án của chủ tài khoản')


def vault_path(provider_id):
    name=str(UUID(provider_id))
    root=Path(os.getenv('OPENAI_OAUTH_LOCAL_HOME',str(DATA_ROOT/'.oauth')))/'native'
    return root/(name+'.enc')


def read_vault(provider_id):
    try:
        return json.loads(decrypt(vault_path(provider_id).read_text(encoding='utf-8')))
    except FileNotFoundError:
        return {}
    except (OSError,ValueError,RuntimeError) as exc:
        raise HTTPException(503,'Không đọc được kho OAuth, kiểm tra volume và ENCRYPTION_KEY') from exc


def write_vault(provider_id, data):
    path=vault_path(provider_id)
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor,temporary=tempfile.mkstemp(dir=path.parent,prefix='.oauth-')
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as output:
            output.write(encrypt(json.dumps(data)))
            output.flush();os.fsync(output.fileno())
        os.chmod(temporary,0o600)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def public_status(provider):
    data=read_vault(provider.id) if provider else {}
    token=data.get('token') or {}
    login=data.get('login') or {}
    return {'connected':bool(token.get('access_token')),
            'provider_id':provider.id if provider else None,
            'provider_enabled':bool(provider and provider.enabled),
            'email':data.get('email',''),'storage':'encrypted-local-vault',
            'login_id':login.get('id') if login.get('expires_at',0)>time.time() else None,
            'needs_reconnect':bool(provider and not token.get('access_token'))}


def start_login(provider):
    data=read_vault(provider.id)
    current=data.get('login') or {}
    if current.get('expires_at',0)>time.time():
        raise HTTPException(409,'Đã có phiên đăng nhập, hãy hoàn tất hoặc hủy trước')
    verifier=secrets.token_urlsafe(48)
    redirect=os.getenv('OPENAI_OAUTH_REDIRECT_URI','http://127.0.0.1:1455/auth/callback')
    parsed=urlsplit(redirect)
    if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or parsed.path!='/auth/callback' or parsed.query or parsed.fragment or parsed.username:
        raise HTTPException(503,'OAuth redirect phải là HTTP loopback 127.0.0.1 với đường dẫn /auth/callback')
    registration=data.get('registration') or {}
    host_id=data.get('host_id') or 'urn:uuid:'+str(uuid4())
    login={'id':secrets.token_urlsafe(24),'state':secrets.token_urlsafe(32),
           'nonce':secrets.token_urlsafe(32),'verifier':verifier,'redirect_uri':redirect,
           'client_id':registration.get('client_id','dynamic_agent_client'),
           'expires_at':time.time()+600}
    params={'client_id':login['client_id'],'response_type':'code','redirect_uri':redirect,
            'scope':SCOPES,'resource':API,'state':login['state'],'nonce':login['nonce'],
            'code_challenge_method':'S256','ext_agent_host_id':host_id,
            'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')}
    if login['client_id']=='dynamic_agent_client':params['agent_name_hint']='SpecWeave'
    elif data.get('email'):params['login_hint']=data['email']
    # Returning sign-in URLs may contain an ID-token hint. Send only to the owner UI.
    login['url']=AUTH+'/api/accounts/authorize?'+urlencode(params)
    write_vault(provider.id,{**data,'host_id':host_id,'login':login})
    return {'id':login['id'],'provider_id':provider.id}


def login_state(provider, login_id):
    data=read_vault(provider.id)
    login=data.get('login') or {}
    if not secrets.compare_digest(str(login.get('id','')),login_id):
        raise HTTPException(404,'Phiên đăng nhập không tồn tại')
    if login.get('expires_at',0)<time.time():
        raise HTTPException(409,'Phiên đăng nhập đã hết hạn, hãy kết nối lại')
    return data,login


async def exchange(http, fields):
    try:
        response=await http.post(AUTH+'/api/accounts/oauth/token',data=fields)
        response.raise_for_status()
        data=response.json()
        if not isinstance(data,dict) or not isinstance(data.get('access_token'),str) or not data['access_token']:
            raise ValueError('Missing token')
        return data
    except (httpx.HTTPError,ValueError) as exc:
        raise HTTPException(502,'OpenAI chưa chấp nhận xác thực. Hãy bắt đầu đăng nhập lại.') from exc


async def validate_identity(http, token, audience, nonce):
    try:
        header=jwt.get_unverified_header(token)
        response=await http.get(AUTH+'/.well-known/jwks.json');response.raise_for_status()
        key=next(item for item in response.json()['keys'] if item.get('kid')==header.get('kid') and item.get('kty')=='RSA')
        claims=jwt.decode(token,jwt.PyJWK.from_dict(key).key,algorithms=['RS256'],
                          audience=audience,issuer=AUTH,options={'require':['sub','exp','aud','iss','nonce']})
        if not secrets.compare_digest(str(claims['nonce']),nonce):raise ValueError('Invalid nonce')
        return claims
    except (httpx.HTTPError,jwt.PyJWTError,ValueError,KeyError,TypeError,StopIteration) as exc:
        raise HTTPException(403,'Không xác minh được danh tính ChatGPT của phiên đăng nhập này') from exc


async def finish_login(provider, login_id, callback):
    data,login=login_state(provider,login_id)
    if not isinstance(callback,str) or len(callback)>8192:
        raise HTTPException(422,'URL chuyển hướng không hợp lệ')
    parsed=urlsplit(callback.strip())
    if parsed._replace(query='',fragment='').geturl()!=login['redirect_uri'] or parsed.fragment:
        raise HTTPException(422,'Hãy dán đầy đủ URL chuyển hướng loopback của phiên này')
    query=parse_qs(parsed.query)
    if not secrets.compare_digest(query.get('state',[''])[0],login['state']):
        raise HTTPException(403,'URL không thuộc phiên đăng nhập này')
    if query.get('error'):
        raise HTTPException(409,'Đăng nhập chưa được chấp thuận. Hủy phiên này để thử lại.')
    code=query.get('code',[''])[0]
    issued=query.get('client_id',[login['client_id']])[0]
    if not code or issued=='dynamic_agent_client' or (login['client_id']!='dynamic_agent_client' and issued!=login['client_id']):
        raise HTTPException(422,'Thiếu mã xác thực hoặc client ID đã được cấp')
    async with client() as http:
        token=await exchange(http,{'grant_type':'authorization_code','client_id':issued,'code':code,
                                  'code_verifier':login['verifier'],'redirect_uri':login['redirect_uri'],'resource':API})
        identity=await validate_identity(http,token.get('id_token',''),issued,login['nonce'])
    registration=data.get('registration') or {}
    if registration.get('subject') and identity['sub']!=registration['subject']:
        raise HTTPException(403,'Tài khoản khác với kết nối đang chọn. Ngắt kết nối trước khi đổi tài khoản.')
    scopes=token.get('scope','').split()
    if 'chatgpt.tokens.use.direct' not in scopes:
        raise HTTPException(403,'Tài khoản chưa cấp quyền sử dụng model từ gói ChatGPT')
    token['expires_at']=time.time()+int(token.get('expires_in',3600))
    write_vault(provider.id,{'host_id':data['host_id'],'email':identity.get('email',''),
                             'registration':{'client_id':issued,'subject':identity['sub'],'id_token':token['id_token']},
                             'token':token})
    provider.base_url=API;provider.enabled=True
    return {'ok':True,'provider_id':provider.id}


async def authorization(provider_id, owner_id):
    # Keep the entire row-locked refresh independent of the web event loop.
    # A second synchronous DB writer must not prevent the lock owner from finishing.
    return await run_in_threadpool(lambda:asyncio.run(_authorization(provider_id,owner_id)))


async def _authorization(provider_id, owner_id):
    """Serialize token rotation with the provider row across web and worker processes."""
    with SessionLocal() as db:
        provider=db.get(Provider,provider_id,with_for_update=True)
        if not provider or provider.kind!='openai-oauth' or provider.owner_id!=owner_id or not provider.enabled or not enabled():
            raise HTTPException(403,'Kết nối ChatGPT đã đổi quyền hoặc bị vô hiệu hóa')
        data=read_vault(provider.id);token=data.get('token') or {}
        if not token.get('access_token'):raise HTTPException(409,'Hãy đăng nhập lại ChatGPT trong Providers')
        if token.get('expires_at',0)<time.time()+60:
            if not token.get('refresh_token'):raise HTTPException(409,'Phiên ChatGPT đã hết hạn, hãy đăng nhập lại')
            async with client() as http:
                updated=await exchange(http,{'grant_type':'refresh_token','client_id':data['registration']['client_id'],
                                             'refresh_token':token['refresh_token'],'resource':API})
            updated={**token,**updated,'expires_at':time.time()+int(updated.get('expires_in',3600))}
            if 'scope' in updated and 'chatgpt.tokens.use.direct' not in updated['scope'].split():
                raise HTTPException(403,'Quyền sử dụng gói ChatGPT đã thay đổi, hãy đăng nhập lại')
            write_vault(provider.id,{**data,'token':updated})
            token=updated
        db.commit()
        return {'Authorization':'Bearer '+token['access_token']}
