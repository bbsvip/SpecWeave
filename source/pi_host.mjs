/**
 * SpecWeave Pi-Native Agent Host.
 * Pi SDK is the ONLY agent/model/session runtime. FastAPI is an authenticated
 * business gateway, not an agent. No subprocess `pi --mode rpc`, no homegrown LLM loop.
 *
 * Host is single-process by design. Do not add replicas against the same sessions
 * without first implementing distributed session ownership/leases.
 */
import {createServer} from 'node:http';
import {mkdir,writeFile,realpath,stat} from 'node:fs/promises';
import {join,resolve,sep} from 'node:path';
import {createHash,timingSafeEqual} from 'node:crypto';
import {createAgentSession,ModelRuntime,SessionManager,DefaultResourceLoader,SettingsManager} from '@earendil-works/pi-coding-agent';
import {createScopedTools} from './pi_host_tools.mjs';
import {oauthEnabled,oauthStatus,oauthBusy,oauthStart,oauthPoll,oauthAnswer,oauthCancel,oauthDisconnect,oauthModels,oauthDiagnostics,oauthPaths} from './pi_host_oauth.mjs';

const PORT=Number(process.env.PORT||8090);
const ROOT=resolve(process.env.PROJECTS_ROOT||'/data/projects');
// The container owns /runner, but the unprivileged GitHub Actions test runner does not.
// A test may override ONLY this resource directory, never agent/project session paths.
// Keep it isolated from project uploads so Pi cannot discover user extensions/skills.
const SAFE_RESOURCES=resolve(process.env.SPECWEAVE_PI_SAFE_RESOURCES_DIR||'/runner/safe-resources');
if(SAFE_RESOURCES===ROOT||SAFE_RESOURCES.startsWith(ROOT+sep)){
  throw new Error('Pi resources directory must be outside project storage');
}
const AUTH=oauthPaths.AUTH;
const MODEL_REGISTRY=join(oauthPaths.HOME,'.pi','agent','models.json');
const sessions=new Map();
const UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const THOUGHT=new Set(['off','minimal','low','medium','high','xhigh','max']);
const err=(status,message)=>Object.assign(new Error(message),{status});
const sha=value=>createHash('sha256').update(value).digest('hex');
const safeDetail=e=>e?.status?e.message:`Pi Agent Host failed (${e?.name||'Error'}). See server logs without exposing credentials.`;
const loggedError=e=>console.error(`[pi-host] ${e?.name||'Error'} in ${e?.phase||'request'} (${e?.status||500})`);

function authenticate(req){
  const expected=process.env.PI_RUNNER_KEY||'';
  const received=req.headers['x-runner-key']||'';
  if(expected.length<32||typeof received!=='string'||received.length!==expected.length||
     !timingSafeEqual(Buffer.from(expected),Buffer.from(received)))throw err(401,'Forbidden');
}
async function readJson(req,max=250_000){
  const parts=[];let size=0;
  for await (const chunk of req){size+=chunk.length;if(size>max)throw err(413,'Payload too large');parts.push(chunk);}
  try{return JSON.parse(Buffer.concat(parts).toString('utf8')||'{}');}
  catch{throw err(400,'Invalid JSON');}
}
function sendJson(res,code,obj){
  if(res.headersSent)return;
  res.writeHead(code,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});
  res.end(JSON.stringify(obj));
}
function sse(res,type,payload={}){
  if(!res.destroyed&&!res.writableEnded)res.write(`data: ${JSON.stringify({type,payload})}\n\n`);
}
function normalizeUsage(raw){
  if(!raw||typeof raw!=='object')return null;
  const n=(...names)=>{for(const k of names){if(Number.isFinite(raw[k])&&raw[k]>=0)return raw[k];}return null;};
  const input=n('input','inputTokens','prompt_tokens','input_tokens');
  const output=n('output','outputTokens','completion_tokens','output_tokens');
  const cache=n('cacheRead','cache_read','cached_tokens');
  if(input===null&&output===null&&cache===null)return null;
  return {input_tokens:input,output_tokens:output,cache_read_tokens:cache,source:'pi'};
}
function assistantText(message){
  return (message?.content||[]).filter(x=>x.type==='text').map(x=>x.text||'').join('');
}
async function checkedProject(id){
  if(!UUID.test(id))throw err(422,'Invalid project ID');
  const location=join(ROOT,id);
  const found=await realpath(location).catch(()=>null);
  if(!found||found!==location)throw err(404,'Project workspace unavailable');
  if(!(await stat(found)).isDirectory())throw err(404,'Project workspace unavailable');
  return found;
}
function modelSignature(data){
  return sha(JSON.stringify([data.auth_mode,data.provider_base,data.model_id,data.reasoning,
    data.context_window,sha(data.provider_key||'')]));
}
async function runtimeFor(data,scope){
  if(data.auth_mode==='openai-oauth'){
    if(!oauthEnabled())throw err(503,'OpenAI OAuth is disabled');
    const status=await oauthStatus();if(!status.connected)throw err(409,'OpenAI account not connected');
    if(data.provider_base!=='pi://openai'||data.provider_key)throw err(422,'OpenAI OAuth must use Pi provider only');
    const modelRuntime=await ModelRuntime.create({authPath:AUTH,modelsPath:MODEL_REGISTRY});
    const model=modelRuntime.getModel('openai',data.model_id);
    if(!model)throw err(422,'OpenAI model is not in Pi catalog');
    return {modelRuntime,model};
  }
  if(data.auth_mode!=='api_key')throw err(422,'Unsupported auth mode');
  let url;
  try{url=new URL(data.provider_base);}catch{throw err(422,'Invalid model endpoint');}
  if(!['http:','https:'].includes(url.protocol))throw err(422,'Unsupported model endpoint');
  const modelId=String(data.model_id||'');
  if(!/^[\w.:-]{1,120}$/.test(modelId))throw err(422,'Invalid model ID');
  await mkdir(scope,{recursive:true,mode:0o700});
  const modelsPath=join(scope,'models.json');
  const registry={providers:{specweave:{baseUrl:data.provider_base.replace(/\/$/,''),api:'openai-completions',authHeader:true,
    models:[{id:modelId,name:modelId,reasoning:Boolean(data.reasoning),input:['text'],
      cost:{input:0,output:0,cacheRead:0,cacheWrite:0},
      contextWindow:Math.max(4096,Math.min(Number(data.context_window)||32768,2_000_000)),maxTokens:8192}]}}};
  await writeFile(modelsPath,JSON.stringify(registry),{mode:0o600});
  const modelRuntime=await ModelRuntime.create({authPath:join(scope,'auth.json'),modelsPath});
  // Runtime-only credential. Never persist provider API key into Pi sessions/models JSON.
  await modelRuntime.setRuntimeApiKey('specweave',String(data.provider_key||'none'));
  const model=modelRuntime.getModel('specweave',modelId);
  if(!model)throw err(422,'Pi could not register custom OpenAI-compatible model');
  return {modelRuntime,model};
}
function createResourceLoader(systemContext,settingsManager){
  return new DefaultResourceLoader({cwd:SAFE_RESOURCES,agentDir:join(SAFE_RESOURCES,'.pi','agent'),
    settingsManager,
    systemPromptOverride:()=>systemContext,appendSystemPromptOverride:()=>[]});
}
async function newSession(data,{persistent,withTools}){
  const workspace=await checkedProject(data.project_id);
  if(!UUID.test(data.discussion_id||''))throw err(422,'Invalid discussion ID');
  const scope=join(workspace,'.system','pi-native',data.discussion_id);
  const {modelRuntime,model}=await runtimeFor(data,join(scope,'registry'));
  const sessionDir=join(scope,'sessions');
  await mkdir(sessionDir,{recursive:true,mode:0o700});
  // Never load user-controlled Pi settings/extensions from uploaded project files.
  const settingsManager=SettingsManager.inMemory();
  const loader=createResourceLoader(String(data.system_context||'').slice(0,30000),settingsManager);
  await loader.reload();
  const active={current:null};
  const {session}=await createAgentSession({
    cwd:workspace,modelRuntime,model,
    thinkingLevel:THOUGHT.has(data.thinking)?data.thinking:'medium',
    resourceLoader:loader,settingsManager,
    sessionManager:persistent?SessionManager.continueRecent(workspace,sessionDir):SessionManager.inMemory(),
    noTools:withTools?'builtin':'all',
    customTools:withTools?createScopedTools(active):[],
  });
  return {session,active,signature:modelSignature(data),workspace};
}
async function getDiscussionSession(data){
  const key=`${data.project_id}:${data.discussion_id}`;
  let current=sessions.get(key);
  if(current?.busy)throw err(409,'Pi session is busy');
  const signature=modelSignature(data);
  if(current&&current.signature!==signature){current.session.dispose();sessions.delete(key);current=null;}
  if(!current){
    current=await newSession(data,{persistent:true,withTools:true});
    sessions.set(key,current);
  }
  current.session.setThinkingLevel(THOUGHT.has(data.thinking)?data.thinking:'medium');
  return current;
}
function validateRun(data){
  for(const key of ['project_id','discussion_id','run_id'])if(!UUID.test(data[key]||''))throw err(422,'Invalid '+key);
  if(!/^[0-9a-f]{64}$/i.test(data.tool_token||''))throw err(422,'Invalid tool capability');
  if(typeof data.message!=='string'||data.message.length>100000)throw err(422,'Invalid message');
  if(typeof data.system_context!=='string'||data.system_context.length>30000)throw err(422,'Invalid planning context');
}
async function runStream(data,res){
  validateRun(data);
  if(data.auth_mode==='openai-oauth'&&oauthBusy())throw err(409,'OpenAI login is in progress');
  const current=await getDiscussionSession(data);
  current.busy=true;current.runId=data.run_id;current.authMode=data.auth_mode;
  current.active.current={runId:data.run_id,token:data.tool_token};
  const sess=current.session;let output='',thought='',usage={},assistantCount=0;
  res.writeHead(200,{'Content-Type':'text/event-stream; charset=utf-8',
    'Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no','Connection':'keep-alive'});
  const keepalive=setInterval(()=>{if(!res.destroyed&&!res.writableEnded)res.write(':heartbeat\n\n');},12000);
  // Pi is authoritative: forward events as deltas and final usage, don't emulate a model.
  const unsubscribe=sess.subscribe(event=>{
    if(event.type==='message_update'){
      const update=event.assistantMessageEvent||{};
      if(update.type==='text_delta'&&typeof update.delta==='string'){output+=update.delta;sse(res,'delta',{text:update.delta});}
      if(update.type==='thinking_delta'&&typeof update.delta==='string'){thought+=update.delta;sse(res,'thinking',{text:update.delta});}
      if(update.type==='toolcall_start'||update.type==='toolcall_delta'||update.type==='toolcall_end'){
        sse(res,'tool',{name:update.toolCall?.name||'Pi tool',id:update.toolCall?.id||'',status:update.type,
          arguments:typeof update.delta==='string'?update.delta.slice(0,12000):undefined});
      }
    }
    if(event.type==='agent_start'||event.type==='turn_start')sse(res,'status',{phase:'thinking',message:'Pi đang xử lý…'});
    if(event.type==='tool_execution_start')sse(res,'tool',{name:event.toolName,id:event.toolCallId,status:'running',arguments:JSON.stringify(event.args||{}).slice(0,8000)});
    if(event.type==='tool_execution_update')sse(res,'tool',{name:event.toolName,id:event.toolCallId,status:'update'});
    if(event.type==='tool_execution_end')sse(res,'tool',{name:event.toolName,id:event.toolCallId,status:event.isError?'error':'complete',
      result:JSON.stringify(event.result||{}).slice(0,8000)});
    if(event.type==='message_end'&&event.message?.role==='assistant'){
      assistantCount++;
      const complete=assistantText(event.message);
      if(complete){if(assistantCount===1)output=complete;else output+=complete;}
      const stats=normalizeUsage(event.message.usage);
      if(stats){for(const k of ['input_tokens','output_tokens','cache_read_tokens'])
        if(typeof stats[k]==='number')usage[k]=(usage[k]||0)+stats[k];
        usage.source='pi';sse(res,'usage',usage);
      }
    }
    if(event.type==='auto_compaction_start'||event.type==='compaction_start')sse(res,'status',{phase:'compaction',message:'Pi đang nén context…'});
    if(event.type==='agent_settled')sse(res,'status',{phase:'settled',message:'Pi đã hoàn thành lượt xử lý'});
  });
  try {
    const oldMessages=Array.isArray(data.bootstrap_history)?data.bootstrap_history.slice(-18):[];
    let message=data.message;
    if(!sess.messages?.length&&oldMessages.length){
      const history=oldMessages.filter(x=>['user','assistant'].includes(x.role))
        .map(x=>`[${x.role.toUpperCase()}]\n${String(x.content||'').slice(0,1800)}`).join('\n\n');
      if(history)message=`Prior conversation from existing SpecWeave history (context only):\n${history.slice(-25000)}\n\nCURRENT USER MESSAGE:\n${message}`;
    }
    if(!message.trim())throw err(422,'Empty prompt');
    sse(res,'status',{phase:'request',message:'Pi đã nhận yêu cầu; đang gọi model…'});
    await sess.prompt(message);
    const last=sess.messages?.filter(m=>m.role==='assistant').at(-1);
    if(!output&&last)output=assistantText(last);
    sse(res,'final',{text:output});
  } catch(e) {
    loggedError(e);
    sse(res,'error',{message:safeDetail(e)});
  } finally {
    unsubscribe();clearInterval(keepalive);
    current.busy=false;current.active.current=null;current.runId=null;
    if(!res.writableEnded)res.end();
  }
}
async function complete(data){
  // All one-shot planning/review jobs also use Pi AgentSession, with NO tools.
  if(!UUID.test(data.project_id||''))throw err(422,'Invalid project ID');
  const taskData={...data,discussion_id:data.discussion_id&&UUID.test(data.discussion_id)?data.discussion_id:data.project_id};
  const current=await newSession(taskData,{persistent:false,withTools:false});
  const sess=current.session;
  let output='',usage={},assistantCount=0;
  const unsubscribe=sess.subscribe(event=>{
    if(event.type==='message_update'&&event.assistantMessageEvent?.type==='text_delta')output+=event.assistantMessageEvent.delta||'';
    if(event.type==='message_end'&&event.message?.role==='assistant'){
      assistantCount++;
      const final=assistantText(event.message);
      if(final){if(assistantCount===1)output=final;else output+=final;}
      const stats=normalizeUsage(event.message.usage);
      if(stats){for(const k of ['input_tokens','output_tokens','cache_read_tokens'])if(typeof stats[k]==='number')usage[k]=(usage[k]||0)+stats[k];}
    }
  });
  try{await sess.prompt(String(data.message||data.prompt||''));
    const last=sess.messages?.filter(m=>m.role==='assistant').at(-1);
    return {text:output||assistantText(last)||'',usage,usage_estimated:false};}
  finally{unsubscribe();sess.dispose();}
}
async function readRequest(req,res){
  try{
    const {pathname,searchParams}=new URL(req.url||'/',`http://localhost:${PORT}`);
    if(pathname==='/health')return sendJson(res,200,{status:'ok',runner:'pi-sdk-native',sessions:sessions.size});
    authenticate(req);
    if(pathname==='/run'&&req.method==='POST')return await runStream(await readJson(req),res);
    if(pathname==='/complete'&&req.method==='POST')return sendJson(res,200,await complete(await readJson(req)));
    if(pathname==='/abort'&&req.method==='POST'){
      const data=await readJson(req);const item=sessions.get(`${data.project_id}:${data.discussion_id}`);
      if(!item?.busy||item.runId!==data.run_id)throw err(404,'Active run not found');
      await item.session.abort();return sendJson(res,200,{ok:true});
    }
    if(pathname==='/oauth/openai/status'&&req.method==='GET')return sendJson(res,200,await oauthStatus());
    if(pathname==='/oauth/openai/diagnostics'&&req.method==='GET')return sendJson(res,200,await oauthDiagnostics());
    if(pathname==='/oauth/openai/start'&&req.method==='POST'){
      if([...sessions.values()].some(s=>s.busy&&s.authMode==='openai-oauth'))throw err(409,'OpenAI agent run in progress');
      const start=await oauthStart();return sendJson(res,200,{id:start.login_id,status:start.status});
    }
    const login=pathname.match(/^\/oauth\/openai\/login\/([\w-]+)(?:\/(answer))?$/);
    if(login&&req.method==='GET'&&!login[2]){
      const result=oauthPoll(login[1],searchParams.get('offset'));
      return sendJson(res,200,{status:result.status,events:result.events,next_offset:result.offset,connected:(await oauthStatus()).connected});
    }
    if(login&&req.method==='POST'&&login[2]==='answer'){
      const body=await readJson(req,12000);return sendJson(res,200,await oauthAnswer(login[1],body.value));
    }
    if(pathname==='/oauth/openai/cancel'&&req.method==='POST')return sendJson(res,200,await oauthCancel());
    if(pathname==='/oauth/openai/disconnect'&&req.method==='POST'){
      const result=await oauthDisconnect(()=>[...sessions.values()].some(s=>s.busy&&s.authMode==='openai-oauth'));
      for(const [key,s] of sessions)if(s.authMode==='openai-oauth'&&!s.busy){s.session.dispose();sessions.delete(key);}
      return sendJson(res,200,result);
    }
    if(pathname==='/oauth/openai/models'&&req.method==='GET')return sendJson(res,200,await oauthModels(()=>ModelRuntime.create({authPath:AUTH,modelsPath:MODEL_REGISTRY})));
    throw err(404,'Not found');
  }catch(e){loggedError(e);sendJson(res,e.status||500,{detail:safeDetail(e)});}
}

export const _test={normalizeUsage,modelSignature,validateRun};
if(process.env.SPECWEAVE_HOST_TEST!=='1'){
  await mkdir(SAFE_RESOURCES,{recursive:true,mode:0o700});
  const server=createServer(readRequest);
  server.listen(PORT,'0.0.0.0',()=>console.log(`[pi-host] Pi SDK Agent Host listening on :${PORT}`));
  process.on('SIGTERM',()=>{for(const item of sessions.values())item.session.dispose();server.close();});
}
