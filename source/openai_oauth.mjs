/**
 * Local, per-owner OpenAI OAuth login using Pi's OWN provider OAuth implementation.
 * No OpenAI credentials are sent to SpecWeave's Python API or project storage.
 * JSONL stdin: {"type":"answer","value":"<callback URL or manual code>"}
 * JSONL stdout: public progress/auth_url/prompt/done/error events ONLY.
 * Run as the same unix user and HOME as the Pi agent. Never log secrets.
 */
import {createModels} from '@earendil-works/pi-ai';
import {openaiProvider} from '@earendil-works/pi-ai/providers/openai';
import {readFile, mkdir, writeFile, rename, chmod, open, unlink} from 'node:fs/promises';
import {join, dirname} from 'node:path';
import {homedir} from 'node:os';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';

const path = join(process.env.HOME || homedir(), '.pi', 'agent', 'auth.json');
// Errors are classified before crossing the runner boundary; raw provider
// exceptions can include callback URLs, tokens or authorization codes.
function classifyFailure(err) {
  const raw = String(err?.code || '')+' '+String(err?.message || err || '');
  if (/ERR_MODULE_NOT_FOUND|Cannot find (module|package)|does not provide an export|Unknown file extension/i.test(raw))
    return ['OAUTH_MODULE_ERROR','Pi OAuth module could not be loaded; check the pinned Pi installation'];
  if (/EACCES|EPERM|permission denied|read-only file system/i.test(raw))
    return ['OAUTH_VAULT_PERMISSION','Pi cannot read or write its local credential volume'];
  if (/ENOTFOUND|EAI_AGAIN|ECONNREFUSED|ECONNRESET|fetch failed|network|TLS|certificate|ETIMEDOUT/i.test(raw))
    return ['OAUTH_NETWORK','Pi could not reach the OpenAI authorization service'];
  if (/invalid_grant|invalid_request|access_denied|unauthorized|expired|code verifier|callback/i.test(raw))
    return ['OAUTH_REJECTED','OpenAI declined or expired the authorization; start a new login'];
  if (/timeout|timed out|expired input|aborted/i.test(raw))
    return ['OAUTH_TIMEOUT','OpenAI login timed out or was cancelled'];
  if (/Unsupported.*oauth|not.*support.*oauth|Unknown provider|not.*registered/i.test(raw))
    return ['OAUTH_UNSUPPORTED','Installed Pi provider does not support this OpenAI OAuth flow'];
  return ['OAUTH_RUNTIME_FAILURE','OpenAI sign-in failed inside the Pi OAuth runtime'];
}

const send=(type,props={})=>process.stdout.write(JSON.stringify({type,...props})+'\n');
const answers=[];
let answerResolve;
const input=createInterface({input:process.stdin,crlfDelay:Infinity,terminal:false});
input.on('line',line=>{
  let item;try{item=JSON.parse(line)}catch{return}
  if(item.type==='answer' && typeof item.value==='string' && item.value.length<=8192){
    if(answerResolve){const fn=answerResolve;answerResolve=undefined;fn(item.value)}
    else if(answers.length<2)answers.push(item.value);
  }
});
const ask=async (req)=>{
  send('prompt',{message:String(req.message||'Paste the final redirect URL / authorization code').slice(0,1000),input_type:req.type||'manual_code'});
  if(answers.length)return answers.shift();
  return await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>{answerResolve=undefined;reject(new Error('Login input expired'))},180000);
    answerResolve=v=>{clearTimeout(timeout);resolve(v)};
  });
};
// Pi auth.json is local-only and 0600. Single user / single runner is intentional.
async function load(){
  try{const obj=JSON.parse(await readFile(path,'utf8'));return obj&&typeof obj==='object'&&!Array.isArray(obj)?obj:{}}
  catch(e){if(e.code==='ENOENT')return {};throw new Error('Local OAuth store unreadable')}
}
async function save(obj){
  await mkdir(dirname(path),{recursive:true,mode:0o700});
  const temp=path+'.'+randomUUID()+'.tmp';
  const fd=await open(temp,'wx',0o600);
  try{await fd.writeFile(JSON.stringify(obj));await fd.sync()}finally{await fd.close()}
  await rename(temp,path);await chmod(path,0o600);
}
const store={
  async read(providerId){return (await load())[providerId]},
  async list(){return Object.entries(await load()).map(([providerId,c])=>({providerId,type:c?.type||'unknown'}))},
  async modify(providerId,fn){
    // A single OAuth connection/login is allowed at a time by the local runner.
    const obj=await load();const next=await fn(obj[providerId]??null);
    // Pi CredentialStore: undefined means NO CHANGE, never a deletion.
    if(next===undefined)return obj[providerId];
    if(!next || typeof next!=='object')throw new Error('Invalid credential from provider');
    obj[providerId]=next;
    await save(obj);return next;
  },
  async delete(providerId){const obj=await load();delete obj[providerId];await save(obj)},
};
async function main(){
  // Only OpenAI provider is registered: never expose any other provider login.
  const models=createModels({credentials:store});
  models.setProvider(openaiProvider());
  await models.login('openai','oauth',{
    prompt:ask,
    notify:(event)=>{
      if(event?.type==='auth_url' && typeof event.url==='string')send('auth_url',{url:event.url});
      else if(event?.type==='device_code')send('device_code',{user_code:String(event.userCode||'').slice(0,120),verification_uri:String(event.verificationUri||'').slice(0,2000)});
      else if(event?.type==='info'||event?.type==='progress')send('progress',{message:String(event.message||'').slice(0,800)});
    },
  });
  // Check that an OAuth record (not API key) was stored, without exposing it.
  const record=(await load()).openai;
  if(record?.type!=='oauth')throw new Error('OpenAI OAuth provider did not persist an OAuth credential');
  send('done',{connected:true});
  input.close();process.stdin.pause();
}
main().catch(e=>{
  const [code,message]=classifyFailure(e);
  // Never print raw error messages to stdout/stderr: they can contain secrets.
  console.error(`[openai-oauth] ${code}`);
  send('error',{code,message});
  input.close();process.stdin.pause();process.exitCode=1;
});
