/** Pi-native host HTTP/SSE contract test. Replaces Pi SDK at module boundary, never calls an LLM. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtemp,mkdir,writeFile,copyFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const PID='123e4567-e89b-42d3-a456-426614174000';
const DID='123e4567-e89b-42d3-a456-426614174001';
const RID='123e4567-e89b-42d3-a456-426614174002';
const SECRET='1234567890abcdefghijklmnopqrstuvwxyz-EXTRA';
const fakeSdk=`
export class DefaultResourceLoader {constructor(options){this.options=options}async reload(){}}
export const SettingsManager={inMemory:()=>({})};
export const SessionManager={continueRecent:()=>({}),inMemory:()=>({})};
export const ModelRuntime={create:async()=>({getModel:(provider,id)=>({provider,id}),setRuntimeApiKey:async()=>{},getAvailable:async()=>[]})};
export function defineTool(tool){return tool;}
export async function createAgentSession(options){
 if(!['builtin','all'].includes(options.noTools))throw Error('Unsafe builtin tools');
 if(options.tools?.length)throw Error('Unsafe tool allowlist');
 const listeners=[];
 const session={messages:[],setThinkingLevel:()=>{},dispose:()=>{},abort:async()=>{},
   subscribe(cb){listeners.push(cb);return()=>{}},
   async prompt(user){
     const emit=e=>listeners.forEach(fn=>fn(e));
     emit({type:'agent_start'});
     emit({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'Considering the request.'}});
     if(options.customTools.length){emit({type:'tool_execution_start',toolName:'search_web',toolCallId:'call1',args:{query:'a'}});
       emit({type:'tool_execution_end',toolName:'search_web',toolCallId:'call1',result:{ok:true}});}
     emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:'Native Pi output'}});
     const message={role:'assistant',content:[{type:'text',text:'Native Pi output'}],usage:{input:12,output:4}};
     session.messages.push(message);emit({type:'message_end',message});emit({type:'agent_settled'});
   },getLastAssistantText:()=> 'Native Pi output'};
 return {session};
}`;
function freePort(){return new Promise((resolve,reject)=>{const s=createServer();s.on('error',reject);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p));});});}
async function startSandbox(){
 const dir=await mkdtemp(join(tmpdir(),'specweave-pi-host-'));
 const moduleDir=join(dir,'node_modules/@earendil-works/pi-coding-agent');
 const typeboxDir=join(dir,'node_modules/typebox');
 await mkdir(moduleDir,{recursive:true});await mkdir(typeboxDir,{recursive:true});
 await writeFile(join(moduleDir,'package.json'),JSON.stringify({name:'@earendil-works/pi-coding-agent',type:'module',exports:'./index.mjs'}));
 await writeFile(join(moduleDir,'index.mjs'),fakeSdk);
 await writeFile(join(typeboxDir,'package.json'),JSON.stringify({name:'typebox',type:'module',exports:'./index.mjs'}));
 await writeFile(join(typeboxDir,'index.mjs'),`export const Type={Object:(...a)=>a,String:(...a)=>a,Boolean:()=>({}),Optional:x=>x,Array:(...a)=>a,Integer:(...a)=>a};`);
 for(const file of ['pi_host.mjs','pi_host_oauth.mjs','pi_host_tools.mjs'])await copyFile(join(root,'source',file),join(dir,file));
 const projectRoot=join(dir,'projects');await mkdir(join(projectRoot,PID),{recursive:true});
 const port=await freePort();const child=spawn(process.execPath,[join(dir,'pi_host.mjs')],{
   cwd:dir,env:{...process.env,PORT:String(port),PROJECTS_ROOT:projectRoot,PI_RUNNER_KEY:SECRET,
     SPECWEAVE_HOST_TEST:'',SPECWEAVE_PI_SAFE_RESOURCES_DIR:join(dir,'safe-resources'),
     OPENAI_OAUTH_LOCAL_MODE:'false',OPENAI_OAUTH_LOCAL_HOME:join(dir,'auth')},
   stdio:['ignore','pipe','pipe']});
 let error='';child.stderr.on('data',d=>error+=d.toString());
 const base='http://127.0.0.1:'+port;
 for(let i=0;i<100;i++){
   if(child.exitCode!==null)throw Error('Pi host exited '+error);
   try{const res=await fetch(base+'/health');if(res.ok)return {dir,child,base,error:()=>error};}catch{}
   await new Promise(r=>setTimeout(r,40));
 }
 child.kill();throw Error('Pi host did not listen '+error);
}
const requestData={project_id:PID,discussion_id:DID,run_id:RID,tool_token:'a'.repeat(64),
 auth_mode:'api_key',provider_base:'http://example.invalid/v1',provider_key:'not-a-real-key',
 model_id:'local-model',system_context:'System planning context',thinking:'high',message:'Plan this project.'};
const post=(base,path,body,secret=SECRET)=>fetch(base+path,{method:'POST',headers:{'x-runner-key':secret,'content-type':'application/json'},body:JSON.stringify(body)});
test('Pi Host handles chat and planning through same SDK and exposes native SSE',async()=>{
 const x=await startSandbox();
 try{
   const health=await(await fetch(x.base+'/health')).json();assert.equal(health.runner,'pi-sdk-native');
   const denied=await post(x.base,'/run',requestData,'bad secret');assert.equal(denied.status,401);
   const completion=await post(x.base,'/complete',requestData);
   assert.equal(completion.status,200,await completion.text().catch(()=>''));
   // Re-send because Response.text() may have consumed the body on error.
   const completed=await(await post(x.base,'/complete',requestData)).json();
   assert.equal(completed.text,'Native Pi output');assert.equal(completed.usage.output_tokens,4);
   const response=await post(x.base,'/run',requestData);assert.equal(response.status,200);
   const stream=await response.text();
   assert.match(stream,/"type":"thinking"/);
   assert.match(stream,/"type":"tool"/);assert.match(stream,/"type":"usage"/);
   assert.match(stream,/"type":"final"/);assert.match(stream,/Native Pi output/);
   assert.equal((await post(x.base,'/run',{...requestData,project_id:'../x'})).status,422);
   assert.equal((await post(x.base,'/oauth/openai/start',{})).status,503);
 }finally{x.child.kill('SIGTERM');await rm(x.dir,{recursive:true,force:true});}
});
