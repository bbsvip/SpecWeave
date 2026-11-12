"""OpenAI-only OAuth broker running in locally controlled Pi runner. Not a generic auth proxy.

All externally visible events omit credentials. Only one owner-owned local vault exists.
There is deliberately NO token or authorization-code persistence in PostgreSQL.
"""
import asyncio
import json
import logging
import os
import re
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

LOCAL_HOME = Path(os.getenv('OPENAI_OAUTH_LOCAL_HOME', '/auth-local'))
LOG = logging.getLogger("specweave.openai_oauth")
AUTH_PATH = LOCAL_HOME / '.pi' / 'agent' / 'auth.json'
LOGIN = None
LOCK = asyncio.Lock()
# One local Pi vault belongs to one owner. Serialize login, refresh-capable model calls
# and disconnect inside the single-process Pi runner. Never share this vault across runners.
AUTH_USE_LOCK = asyncio.Lock()


def require_local():
    if os.getenv('OPENAI_OAUTH_LOCAL_MODE', '').strip().lower() not in ('1', 'true', 'yes'):
        raise HTTPException(503, 'OpenAI OAuth requires explicitly enabled owner-controlled LOCAL mode')


def public_status():
    require_local()
    try:
        record=json.loads(AUTH_PATH.read_text(encoding='utf8')).get('openai',{})
        return {'connected':isinstance(record,dict) and record.get('type')=='oauth',
                'provider':'openai','storage':'local-pi-vault','shared':False}
    except (OSError, ValueError, TypeError):
        return {'connected':False,'provider':'openai','storage':'local-pi-vault','shared':False}


# Only allow static, reviewed messages to reach the browser, not arbitrary
# provider/Node exception strings which might include auth codes or tokens.
ERROR_DESCRIPTIONS = {
    'OAUTH_MODULE_ERROR':'Thư viện Pi OAuth không nạp được. Kiểm tra bản Pi trong image runner.',
    'OAUTH_VAULT_PERMISSION':'Pi không đọc/ghi được credential volume /auth-local.',
    'OAUTH_NETWORK':'Pi runner không kết nối được dịch vụ xác thực OpenAI.',
    'OAUTH_REJECTED':'OpenAI từ chối hoặc mã đăng nhập đã hết hạn. Hãy tạo phiên mới.',
    'OAUTH_TIMEOUT':'Phiên đăng nhập hết thời gian chờ hoặc đã bị hủy.',
    'OAUTH_UNSUPPORTED':'Phiên bản Pi đang cài không hỗ trợ OpenAI OAuth theo giao thức này.',
    'OAUTH_RUNTIME_FAILURE':'Đăng nhập OpenAI thất bại trong Pi runner; xem mã lỗi trong log Pi.',
    'OAUTH_PROCESS_EXIT':'Tiến trình OAuth kết thúc trước khi xác thực hoàn tất.',
    'OAUTH_PROCESS_TIMEOUT':'Tiến trình OAuth bị ngắt hoặc quá thời gian chờ.',
}

def _oauth_error(code):
    if code not in ERROR_DESCRIPTIONS:
        code='OAUTH_RUNTIME_FAILURE'
    return {'type':'error','code':code,'message':ERROR_DESCRIPTIONS[code]}


def safe_event(event):
    """Never echo arbitrary fields from Pi/OAuth provider to the browser."""
    kind=event.get('type')
    if kind=='auth_url':
        url=str(event.get('url',''))
        parsed=urlsplit(url)
        if parsed.scheme!='https' or parsed.hostname not in ('auth.openai.com','chatgpt.com','auth.chatgpt.com'):
            return {'type':'error','message':'Unexpected authorization host'}
        return {'type':'auth_url','url':url[:5000]}
    if kind=='device_code':
        uri=str(event.get('verification_uri',''))
        parsed=urlsplit(uri)
        if parsed.scheme!='https' or parsed.hostname not in ('auth.openai.com','chatgpt.com','auth.chatgpt.com'):
            return {'type':'error','message':'Unexpected verification host'}
        return {'type':'device_code','url':uri[:2000],'user_code':str(event.get('user_code',''))[:120]}
    if kind=='prompt':
        return {'type':'prompt','message':str(event.get('message',''))[:600]}
    if kind=='done': return {'type':'done','connected':True}
    if kind=='error':
        result=_oauth_error(str(event.get('code','OAUTH_RUNTIME_FAILURE')))
        phase=str(event.get('phase',''))
        hint=str(event.get('hint',''))
        allowed_phases={'bootstrap','register_provider','provider_login','awaiting_manual_callback','persisting_credential'}
        allowed_hints={'INCOMPATIBLE_API','INCOMPATIBLE_PACKAGE_VERSION','MISSING_DEPENDENCY','VAULT_PERMISSION',
                       'NETWORK_OR_PROXY','INVALID_REDIRECT','AUTH_REJECTED','INPUT_TIMEOUT',
                       'CREDENTIAL_NOT_PERSISTED','UNCLASSIFIED'}
        if phase in allowed_phases:result['phase']=phase
        if hint in allowed_hints:result['hint']=hint
        return result
    if kind=='progress': return {'type':'progress','message':'OpenAI sign-in in progress'}
    return None


async def _login_worker(job):
    proc=job['proc']
    # Drain stderr to avoid subprocess stalls. Do not log raw OAuth output.
    async def drain_stderr():
        if not proc.stderr:return
        while True:
            raw=await proc.stderr.readline()
            if not raw:break
            matched=re.search(rb'\bOAUTH_[A-Z_]+\b',raw[:1000])
            if matched:
                code=matched.group(0).decode('ascii')
                if code in ERROR_DESCRIPTIONS:
                    job['diagnostic_code']=code
                    LOG.warning('OpenAI OAuth runner failure: %s',code)
    stderr_task=asyncio.create_task(drain_stderr())
    try:
        while True:
            line=await asyncio.wait_for(proc.stdout.readline(),timeout=300)
            if not line:break
            try: event=safe_event(json.loads(line))
            except (UnicodeError,ValueError,TypeError):continue
            if event:
                job['events'].append(event)
                if event['type'] in ('done','error'):
                    job['state']='done' if event['type']=='done' else 'error'
        await asyncio.wait_for(proc.wait(),timeout=6)
        if job['state']=='running':
            job['state']='error'
            code=job.get('diagnostic_code','OAUTH_PROCESS_EXIT')
            job['events'].append(_oauth_error(code))
    except (asyncio.TimeoutError,asyncio.CancelledError):
        job['state']='error'
        job['events'].append(_oauth_error('OAUTH_PROCESS_TIMEOUT'))
    finally:
        if proc.returncode is None:
            proc.kill();await proc.wait()
        if not stderr_task.done():
            stderr_task.cancel()
        try: await stderr_task
        except asyncio.CancelledError: pass
        if AUTH_USE_LOCK.locked():
            AUTH_USE_LOCK.release()


async def start_login():
    global LOGIN
    require_local()
    async with LOCK:
        if (LOGIN and LOGIN['state']=='running') or AUTH_USE_LOCK.locked():
            raise HTTPException(409,'OpenAI runtime is busy; retry after the current run or login completes')
        await AUTH_USE_LOCK.acquire()
        try:
            LOCAL_HOME.mkdir(parents=True,exist_ok=True,mode=0o700)
            proc=await asyncio.create_subprocess_exec('node','/runner/openai_oauth.mjs',
                env={**os.environ,'HOME':str(LOCAL_HOME)},stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
        except BaseException:
            AUTH_USE_LOCK.release()
            raise
        job={'id':secrets.token_urlsafe(24),'proc':proc,'events':[], 'state':'running'}
        job['task']=asyncio.create_task(_login_worker(job))
        LOGIN=job
        return {'id':job['id'],'status':'running'}


def poll_login(login_id,offset=0):
    require_local()
    if LOGIN is None or not secrets.compare_digest(login_id,LOGIN['id']):
        raise HTTPException(404,'Login session not found')
    offset=max(0,min(int(offset),10000))
    return {'status':LOGIN['state'],'events':LOGIN['events'][offset:],
            'next_offset':len(LOGIN['events']),'connected':public_status()['connected']}


async def answer_login(login_id,value):
    require_local()
    if LOGIN is None or not secrets.compare_digest(login_id,LOGIN['id']):
        raise HTTPException(404,'Login session not found. Start a new login.')
    if LOGIN['state']!='running':
        code=next((e.get('code') for e in reversed(LOGIN['events']) if e.get('type')=='error'),None)
        raise HTTPException(409,f'Login session already {LOGIN["state"]} ({code or "OAUTH_PROCESS_EXIT"}). Start a new login.')
    if not isinstance(value,str) or not 1<=len(value)<=8192:
        raise HTTPException(422,'Invalid OAuth callback URL or code')
    proc=LOGIN['proc']
    if not proc.stdin or proc.stdin.is_closing():raise HTTPException(409,'Login session no longer accepts input')
    proc.stdin.write((json.dumps({'type':'answer','value':value})+'\n').encode())
    await proc.stdin.drain()
    return {'ok':True}


async def end_login():
    global LOGIN
    require_local()
    async with LOCK:
        if LOGIN:
            job=LOGIN;LOGIN=None
            job['task'].cancel()
            try:await job['task']
            except asyncio.CancelledError:pass
    return {'ok':True}


async def disconnect():
    """Delete only OpenAI entries; local removal is not a server-side revocation."""
    require_local()
    if AUTH_USE_LOCK.locked():raise HTTPException(409,'OpenAI runtime is busy; stop the current run or login first')
    async with AUTH_USE_LOCK:
        return _disconnect_unlocked()

def _disconnect_unlocked():
    try:
        obj=json.loads(AUTH_PATH.read_text('utf8'))
        if not isinstance(obj,dict):raise ValueError()
    except FileNotFoundError:return {'ok':True,'revoked_remotely':False}
    except (ValueError,OSError) as exc:raise HTTPException(500,'Local credential store unreadable') from exc
    obj.pop('openai',None)
    obj.pop('openai-codex',None)
    import uuid
    temp=AUTH_PATH.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    with temp.open('x',encoding='utf8') as file:
        os.chmod(temp,0o600)
        json.dump(obj,file)
        file.flush();os.fsync(file.fileno())
    temp.replace(AUTH_PATH)
    return {'ok':True,'revoked_remotely':False}


async def diagnostics():
    """Non-secret, non-network preflight for a locally controlled OpenAI OAuth runner."""
    require_local()
    # Check the actual installed ESM API and provider OAuth method. No token,
    # callback, credential contents, or external requests are involved.
    script=("import {createModels} from '@earendil-works/pi-ai/models';"
            "import {openaiProvider} from '@earendil-works/pi-ai/providers/openai';"
            "const p=openaiProvider();const m=createModels();m.setProvider(p);"
            "console.log(JSON.stringify({model_login:typeof m.login==='function',"
            "provider_oauth:typeof p.auth?.oauth?.login==='function'}))")
    status={'local_mode':True,'node_import':False,'model_login':False,
            'provider_oauth':False,'vault_writable':False}
    try:
        proc=await asyncio.create_subprocess_exec('node','--input-type=module','-e',script,
            cwd='/runner',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ,'HOME':str(LOCAL_HOME)})
        stdout,_=await asyncio.wait_for(proc.communicate(),timeout=12)
        if proc.returncode==0:
            result=json.loads(stdout.decode('utf8')[:300])
            status.update(node_import=True,model_login=result.get('model_login') is True,
                          provider_oauth=result.get('provider_oauth') is True)
    except (ValueError,OSError,asyncio.TimeoutError):pass
    try:
        # Only inspect/write a random temp probe; never read or modify auth.json.
        folder=AUTH_PATH.parent
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        name=folder/('.probe-'+secrets.token_hex(8))
        fd=os.open(name,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        os.close(fd);name.unlink()
        status['vault_writable']=True
    except OSError:pass
    return status


async def available_models():
    require_local()
    # Query the ACTUAL installed Pi model catalog; do not invent model IDs.
    proc=await asyncio.create_subprocess_exec('pi','--list-models','openai',
        env={**os.environ,'HOME':str(LOCAL_HOME)},
        stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:stdout,_=await asyncio.wait_for(proc.communicate(),timeout=18)
    except asyncio.TimeoutError:
        proc.kill();await proc.wait();raise HTTPException(504,'Pi model catalog timed out')
    if proc.returncode:raise HTTPException(502,'Unable to read local Pi model catalog')
    names=[]
    for raw in stdout.decode(errors='replace').splitlines():
        parts=raw.strip().split()
        if len(parts)>=2 and parts[0]=='openai' and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:-]{1,120}',parts[1]):
            names.append(parts[1])
    return {'models':list(dict.fromkeys(names))[:200]}


async def complete(prompt, model_id, thinking='medium'):
    """No tools, no persistent Pi session: used by Guided Planning and BMAD Review."""
    require_local()
    async with AUTH_USE_LOCK:
        return await _complete_unlocked(prompt, model_id, thinking)

async def _complete_unlocked(prompt, model_id, thinking='medium'):
    require_local()
    if not public_status()['connected']:
        raise HTTPException(409,'OpenAI account is not connected in local Pi runtime')
    if not isinstance(prompt,str) or len(prompt)>115000:raise HTTPException(422,'Prompt too large')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,119}',model_id):raise HTTPException(422,'Invalid model ID')
    cmd=['pi','--mode','rpc','--no-session','--no-builtin-tools','--no-extensions','--no-skills',
         '--no-context-files','--no-mcp','--no-approve','--provider','openai','--model',model_id,
         '--thinking',thinking if thinking in ('off','minimal','low','medium','high','xhigh') else 'medium']
    proc=await asyncio.create_subprocess_exec(*cmd,env={**os.environ,'HOME':str(LOCAL_HOME)},
           cwd=str(LOCAL_HOME),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
           stderr=asyncio.subprocess.DEVNULL)
    chunks=[]
    try:
        proc.stdin.write((json.dumps({'id':'oauth-complete','type':'prompt','message':prompt})+'\n').encode('utf8'))
        await proc.stdin.drain()
        deadline=asyncio.get_running_loop().time()+170
        while True:
            remaining=deadline-asyncio.get_running_loop().time()
            if remaining<=0:raise HTTPException(504,'OpenAI completion timed out')
            raw=await asyncio.wait_for(proc.stdout.readline(),timeout=remaining)
            if not raw:break
            try: event=json.loads(raw)
            except ValueError:continue
            if event.get('type')=='response' and event.get('command')=='prompt' and not event.get('success'):
                raise HTTPException(502,'Pi rejected model completion')
            if event.get('type')=='message_update' and (event.get('assistantMessageEvent') or {}).get('type')=='text_delta':
                chunks.append((event['assistantMessageEvent'].get('delta') or '')[:20000])
            if event.get('type')=='agent_settled':break
        text=''.join(chunks)
        if not text:raise HTTPException(502,'OpenAI model returned no content')
        return {'text':text[:150000], 'usage':{'completion_tokens':max(1,(len(text)+3)//4)}, 'usage_estimated':True}
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:await asyncio.wait_for(proc.wait(),timeout=3)
            except asyncio.TimeoutError:proc.kill();await proc.wait()
