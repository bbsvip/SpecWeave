/** OpenAI-only login bridge, backed by Pi's native OpenAI OAuth provider.
 * Tokens are maintained exclusively by Pi in /auth-local/.pi/agent/auth.json.
 * The child process is for the interactive OAuth handshake, NOT an agent run.
 */
import {spawn} from 'node:child_process';
import {readFile,writeFile,mkdir,rename,chmod,access} from 'node:fs/promises';
import {constants as fsConstants} from 'node:fs';
import {join,dirname} from 'node:path';
import {randomBytes,randomUUID} from 'node:crypto';

const HOME=process.env.OPENAI_OAUTH_LOCAL_HOME || '/auth-local';
const AUTH=join(HOME,'.pi','agent','auth.json');
let active=null;
const jobs=new Map();
export const oauthPaths={HOME,AUTH};
export const oauthEnabled=()=>/^(1|true|yes)$/i.test(process.env.OPENAI_OAUTH_LOCAL_MODE||'');
export function requireOAuth(){if(!oauthEnabled()){const e=new Error('OpenAI OAuth is disabled');e.status=503;throw e;}}
async function authRecord() {
  try {const file=JSON.parse(await readFile(AUTH,'utf8'));return file?.openai || file?.['openai-codex'] || null;}
  catch (e) {if(e?.code==='ENOENT')return null;return null;}
}
export async function oauthStatus(){requireOAuth();const record=await authRecord();return {connected:record?.type==='oauth',provider:'openai',storage:'local-pi-vault',shared:false};}
export function oauthBusy(){return Boolean(active?.status==='running');}
function safeOAuthEvent(raw) {
  const t=raw?.type;
  if(t==='auth_url') {
    try {const u=new URL(raw.url);if(u.protocol!=='https:' || !['auth.openai.com','auth.chatgpt.com','chatgpt.com'].includes(u.hostname))return null;
      return {type:'auth_url',url:raw.url};}catch{return null;}
  }
  if(t==='device_code')return {type:t,user_code:String(raw.user_code||'').slice(0,150),url:String(raw.verification_uri||'').slice(0,1200)};
  if(t==='prompt')return {type:t,message:String(raw.message||'Paste the redirect URL').slice(0,400),input_type:String(raw.input_type||'manual_code').slice(0,80)};
  if(t==='progress')return {type:t,message:String(raw.message||'Authenticating…').slice(0,200)};
  if(t==='done')return {type:'done'};
  if(t==='error')return {type:'error',code:/^OAUTH_[A-Z_]+$/.test(raw.code)?raw.code:'OAUTH_RUNTIME_FAILURE',message:'Pi OpenAI sign-in failed; check the classified error code'};
  return null;
}
export async function oauthStart(){
  requireOAuth();
  if(oauthBusy()){const e=new Error('OpenAI login already running');e.status=409;throw e;}
  const id=randomBytes(24).toString('base64url');
  const job={id,status:'running',events:[],child:null,started:Date.now()};jobs.set(id,job);active=job;
  const child=spawn(process.execPath,['/runner/openai_oauth.mjs'],{
    cwd:'/runner',env:{...process.env,HOME},stdio:['pipe','pipe','ignore']
  });
  job.child=child;let pending='';
  child.stdout.setEncoding('utf8');
  child.stdout.on('data',chunk=>{
    pending+=chunk;
    if(pending.length>250_000){job.events.push({type:'error',code:'OAUTH_STREAM_TOO_LARGE'});child.kill();return;}
    for(;;){const end=pending.indexOf('\n');if(end<0)break;
      const line=pending.slice(0,end);pending=pending.slice(end+1);
      try {const e=safeOAuthEvent(JSON.parse(line));if(!e)continue;
        job.events.push(e);if(job.events.length>150)job.events.shift();
        if(e.type==='done')job.status='done';
        if(e.type==='error')job.status='error';
      }catch{}
    }
  });
  child.on('error',()=>{job.status='error';job.events.push({type:'error',code:'OAUTH_PROCESS_ERROR'});});
  child.on('exit',code=>{
    if(job.status==='running'){
      job.status='error';job.events.push({type:'error',code:code===0?'OAUTH_NO_CREDENTIAL':'OAUTH_PROCESS_EXIT'});
    }
    if(active===job)active=null;
  });
  // Bound the lifetime; never retain stale callbacks or URL tokens in memory indefinitely.
  setTimeout(()=>{
    if(job.status==='running'){job.status='error';job.events.push({type:'error',code:'OAUTH_TIMEOUT'});child.kill();}
    jobs.delete(id);if(active===job)active=null;
  },360_000).unref();
  return {login_id:id,status:'running'};
}
export function oauthPoll(id,offset=0){
  const job=jobs.get(id);if(!job){const e=new Error('Login not found or expired');e.status=404;throw e;}
  const from=Math.max(0,Math.min(10000,Number(offset)||0));
  return {login_id:id,status:job.status,phase:job.status==='running'?'provider_login':job.status,events:job.events.slice(from),offset:job.events.length};
}
export async function oauthAnswer(id,value){
  const job=jobs.get(id);if(!job){const e=new Error('Login not found');e.status=404;throw e;}
  if(job.status!=='running'){const e=new Error('Login finished. Start a new connection.');e.status=409;throw e;}
  if(typeof value!=='string'||!value.trim()||value.length>8192){const e=new Error('Invalid callback');e.status=422;throw e;}
  if(!job.child?.stdin?.writable){const e=new Error('OAuth input closed');e.status=409;throw e;}
  job.child.stdin.write(JSON.stringify({type:'answer',value:value.trim()})+'\n');
  return {ok:true,status:'running'};
}
export async function oauthCancel(){
  requireOAuth();if(active?.child&&active.status==='running'){
    active.status='cancelled';active.events.push({type:'error',code:'OAUTH_CANCELLED'});active.child.kill();
  }
  active=null;return {ok:true};
}
export async function oauthDisconnect(isInUse){
  requireOAuth();if(oauthBusy()||isInUse()){const e=new Error('OpenAI is in use');e.status=409;throw e;}
  let records={};try{records=JSON.parse(await readFile(AUTH,'utf8'));if(!records||Array.isArray(records)||typeof records!=='object')throw Error();}
  catch(e){if(e?.code!=='ENOENT'){const error=new Error('Credential vault unreadable');error.status=500;throw error;}}
  delete records.openai;delete records['openai-codex'];
  await mkdir(dirname(AUTH),{recursive:true,mode:0o700});
  const tmp=AUTH+'.'+randomUUID()+'.tmp';
  await writeFile(tmp,JSON.stringify(records),{mode:0o600,flag:'wx'});
  await rename(tmp,AUTH);await chmod(AUTH,0o600);
  return {ok:true,revoked_remotely:false};
}
export async function oauthDiagnostics(){
  requireOAuth();let writable=false,device_id_ready=false;
  try {await mkdir(dirname(AUTH),{recursive:true,mode:0o700});await access(dirname(AUTH),fsConstants.W_OK);writable=true;}catch{}
  try {const cfg=JSON.parse(await readFile(join(dirname(AUTH),'settings.json'),'utf8'));device_id_ready=typeof cfg.deviceId==='string'&&/^[0-9a-f-]{36}$/i.test(cfg.deviceId);}catch{}
  return {local_mode:true,node_import:true,model_login:true,provider_oauth:true,vault_writable:writable,device_id_ready};
}
export async function oauthModels(modelRuntimeFactory){
  requireOAuth();const runtime=await modelRuntimeFactory();
  const candidates=(await runtime.getAvailable()).filter(m=>m.provider==='openai');
  return {models:[...new Set(candidates.map(m=>m.id))].slice(0,200)};
}
