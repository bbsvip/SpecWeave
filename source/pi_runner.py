"""Standalone optional Pi RPC bridge. Runs in runner container, no built-in tools or extensions."""
import os
import re
import json
import asyncio
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from openai_oauth_runtime import (require_local, public_status, start_login, poll_login, answer_login, end_login, disconnect, available_models, complete, diagnostics, LOCAL_HOME, AUTH_USE_LOCK)

app = FastAPI(docs_url=None, redoc_url=None)
ROOT = Path(os.environ.get("PROJECTS_ROOT", "/data/projects")).resolve()
LOCKS = {}

class RunnerRequest(BaseModel):
    project_id: str
    discussion_id: str
    run_id: str
    tool_token: str = Field(min_length=64,max_length=64)
    message: str = Field(max_length=100000)
    system_context: str = Field(max_length=30000)
    provider_base: str
    provider_key: str = ""
    model_id: str
    thinking: str = "medium"
    reasoning: bool = False
    context_window: int = 32768
    auth_mode: str = "api_key"

def runner_auth(key: str):
    import hmac
    expected=os.environ.get('PI_RUNNER_KEY','')
    if len(expected)<32 or not hmac.compare_digest(expected,key):
        raise HTTPException(401,'Forbidden')

@app.get('/oauth/openai/status')
def openai_status(x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return public_status()

@app.get('/oauth/openai/diagnostics')
async def openai_diagnostics(x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return await diagnostics()

@app.post('/oauth/openai/start')
async def openai_start(x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return await start_login()

@app.get('/oauth/openai/login/{login_id}')
def openai_poll(login_id:str,offset:int=0,x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return poll_login(login_id,offset)

class OAuthReply(BaseModel):
    value: str = Field(min_length=1,max_length=8192)

@app.post('/oauth/openai/login/{login_id}/answer')
async def openai_answer(login_id:str,data:OAuthReply,x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return await answer_login(login_id,data.value)

@app.post('/oauth/openai/cancel')
async def openai_cancel(x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return await end_login()

@app.post('/oauth/openai/disconnect')
async def openai_disconnect(x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return await disconnect()

@app.get('/oauth/openai/models')
async def openai_models(x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return await available_models()

class OAuthCompletion(BaseModel):
    prompt: str = Field(max_length=115000)
    model_id: str = Field(min_length=1,max_length=120)
    thinking: str = 'medium'

@app.post('/oauth/openai/complete')
async def openai_complete(data:OAuthCompletion,x_runner_key: str = Header(default='')):
    runner_auth(x_runner_key)
    return await complete(data.prompt,data.model_id,data.thinking)

@app.get("/health")
def health():
    return {"status":"ok","runner":"pi-rpc"}

@app.post("/run")
async def run(data: RunnerRequest, x_runner_key: str = Header(default="")):
    runner_auth(x_runner_key)
    if not all(re.fullmatch(r"[a-f0-9-]{36}", item) for item in (data.project_id,data.discussion_id,data.run_id)):
        raise HTTPException(400,"Invalid project/session ID")
    if data.auth_mode not in ("api_key","openai-oauth"):
        raise HTTPException(422,"Unsupported auth mode")
    if data.auth_mode == "openai-oauth":
        require_local()
        if not public_status()["connected"]:raise HTTPException(409,"OpenAI OAuth not connected")
    root = ROOT / data.project_id
    if not root.is_dir() or root.is_symlink(): raise HTTPException(404,"Project not found")
    base = data.provider_base.rstrip("/")
    if data.auth_mode == "api_key" and not base.startswith(("http://","https://")): raise HTTPException(400,"Invalid provider")
    if data.auth_mode == "openai-oauth" and (data.provider_base != "pi://openai" or data.provider_key):
        raise HTTPException(422,"OAuth provider must not include API keys or custom endpoints")
    key = f"{data.project_id}:{data.discussion_id}"
    lock = LOCKS.setdefault(key,asyncio.Lock())
    if lock.locked(): raise HTTPException(409,"Session busy")

    async def stream_impl():
        async with lock:
            # Isolate Pi config per discussion, avoiding model/provider write races across sessions.
            # OAuth credentials live ONLY in locally controlled runner vault, never
            # copied to project directories or passed through SpecWeave's database.
            home = LOCAL_HOME if data.auth_mode=="openai-oauth" else root / ".system" / "pi-home" / data.discussion_id
            config = home / ".pi" / "agent"
            config.mkdir(parents=True,exist_ok=True,mode=0o700)
            if data.auth_mode == "api_key":
                models = {"providers":{"specweave":{"baseUrl":base,"api":"openai-completions",
                          "apiKey":"$SPECWEAVE_MODEL_KEY", "authHeader":True,
                          "models":[{"id":data.model_id,"name":data.model_id,"reasoning":data.reasoning,
                                     "contextWindow":max(4096,data.context_window),"maxTokens":8192}]}}}
                filename = config / "models.json"
                filename.write_text(json.dumps(models),encoding="utf-8")
                filename.chmod(0o600)
            sessions = root / "sessions"
            sessions.mkdir(exist_ok=True)
            # Never enable Pi's native bash/read/write/edit tools. Only our reviewed custom gateway is exposed.
            tool_names="search_web,fetch_url,list_project_files,read_project_file,write_document,propose_decision,propose_requirement,propose_memory,propose_planning_question"
            cmd = ["pi","--mode","rpc","--no-builtin-tools","--no-extensions",
                   "--extension","/runner/pi_tools.ts","--tools",tool_names,"--no-skills",
                   "--no-context-files","--no-mcp","--no-approve","--provider",("openai" if data.auth_mode=="openai-oauth" else "specweave"),"--model",data.model_id,
                   "--session-dir",str(sessions),"--session-id","s"+data.discussion_id.replace("-",""),
                   "--thinking",data.thinking if data.thinking in ("off","minimal","low","medium","high","xhigh","max") else "medium"]
            env = {**os.environ,"HOME":str(home),"SPECWEAVE_MODEL_KEY":(data.provider_key or "none") if data.auth_mode=="api_key" else "",
                   "SPECWEAVE_TOOL_URL":os.environ.get("PI_TOOL_URL","http://web:8080/api/internal/pi-tool"),
                   "SPECWEAVE_TOOL_RUN":data.run_id,"SPECWEAVE_TOOL_TOKEN":data.tool_token}
            proc = None
            collected = []
            try:
                proc = await asyncio.create_subprocess_exec(*cmd,cwd=str(root),env=env,
                                  stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
                                  stderr=asyncio.subprocess.DEVNULL)
                # Pi session retains history. Reinforce current project profile each turn, not global memory.
                obj = {"id":"prompt1","type":"prompt","message":data.system_context + "\n\nUSER MESSAGE:\n" + data.message}
                proc.stdin.write((json.dumps(obj,ensure_ascii=False)+"\n").encode("utf-8"))
                await proc.stdin.drain()
                while True:
                    raw = await asyncio.wait_for(proc.stdout.readline(),timeout=270)
                    if not raw: break
                    try: event = json.loads(raw)
                    except ValueError: continue
                    tp = event.get("type")
                    if tp == "response" and event.get("command") == "prompt" and not event.get("success"):
                        raise RuntimeError("Pi did not accept request")
                    if tp == "message_update":
                        upd = event.get("assistantMessageEvent") or {}
                        if upd.get("type") == "text_delta":
                            delta=upd.get("delta","")
                            collected.append(delta)
                            yield "data: "+json.dumps({"type":"delta","payload":{"text":delta}},ensure_ascii=False)+"\n\n"
                    if tp == "tool_execution_start":
                        name=event.get("toolName") or event.get("tool_name") or "Pi tool"
                        yield "data: "+json.dumps({"type":"tool","payload":{"name":str(name)[:80],"status":"running"}},ensure_ascii=False)+"\n\n"
                    if tp == "tool_execution_end":
                        name=event.get("toolName") or event.get("tool_name") or "Pi tool"
                        yield "data: "+json.dumps({"type":"tool","payload":{"name":str(name)[:80],"status":"complete"}},ensure_ascii=False)+"\n\n"
                    if tp == "agent_settled": break
                yield "data: "+json.dumps({"type":"final","payload":{"text":"".join(collected)}},ensure_ascii=False)+"\n\n"
            except Exception as exc:
                yield "data: "+json.dumps({"type":"error","payload":{"message":type(exc).__name__+": "+str(exc)[:300]}},ensure_ascii=False)+"\n\n"
            finally:
                if proc and proc.returncode is None:
                    if proc.stdin: proc.stdin.close()
                    try: await asyncio.wait_for(proc.wait(),timeout=5)
                    except asyncio.TimeoutError:
                        proc.terminate()
                        try: await asyncio.wait_for(proc.wait(),timeout=3)
                        except asyncio.TimeoutError: proc.kill(); await proc.wait()
    async def stream():
        if data.auth_mode=="openai-oauth":
            # Prevent login/disconnect and simultaneous refreshes while this local
            # owner's chat is using the only OAuth vault in this Pi runner.
            async with AUTH_USE_LOCK:
                async for chunk in stream_impl():
                    yield chunk
        else:
            async for chunk in stream_impl():
                yield chunk
    return StreamingResponse(stream(),media_type="text/event-stream")
