/** Pi-native host HTTP/SSE contract test. Replaces Pi SDK at module boundary, never calls an LLM. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtemp,mkdir,writeFile,copyFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {createServer} from 'node:net';
import {once} from 'node:events';
const root=resolve(fileURLToPath(new URL('..',import.meta.url)));
const PID='123e4567-e89b-42d3-a456-426614174000';
const DID='123e4567-e89b-42d3-a456-426614174001';
const RID='123e4567-e89b-42d3-a456-426614174002';
const SECRET='1234567890abcdefghijklmnopqrstuvwxyz-EXTRA';
const fakeSdk=`
import {readFile} from 'node:fs/promises';
export class DefaultResourceLoader {constructor(options){this.options=options}async reload(){}}
export const SettingsManager={inMemory:()=>({})};
export const SessionManager={continueRecent:()=>({}),inMemory:()=>({})};
export const ModelRuntime={create:async options=>{
 const registry=JSON.parse(await readFile(options.modelsPath,'utf8').catch(()=>'{"providers":{}}'));
 return {getModel:(provider,id)=>({api:registry.providers?.[provider]?.api,...registry.providers?.[provider]?.models?.find(m=>m.id===id),provider,id}),setRuntimeApiKey:async()=>{},getAvailable:async()=>[]};
}};
export function defineTool(tool){return tool;}
export async function createAgentSession(options){
 if(!['builtin','all'].includes(options.noTools))throw Error('Unsafe builtin tools');
 if(options.tools?.length)throw Error('Unsafe tool allowlist');
 const listeners=new Set();
 let beforeStart, release, aborted=false;
 for(const factory of options.resourceLoader.options.extensionFactories||[]){
   factory({on(name,handler){if(name==='before_agent_start')beforeStart=handler}});
 }
 const session={messages:[],setThinkingLevel:()=>{},dispose:()=>{},abort:async()=>{aborted=true;release?.()},
   subscribe(cb){listeners.add(cb);return()=>listeners.delete(cb)},
   async prompt(user){
     aborted=false;
     const emit=e=>listeners.forEach(fn=>fn(e));
     emit({type:'agent_start'});
     if(user==='hold')await new Promise(resolve=>{release=resolve});
     if(aborted)return;
     emit({type:'message_update',assistantMessageEvent:{type:'thinking_delta',delta:'Considering the request.'}});
     if(options.customTools.length){emit({type:'tool_execution_start',toolName:'search_web',toolCallId:'call1',args:{query:'a'}});
       emit({type:'tool_execution_end',toolName:'search_web',toolCallId:'call1',result:{ok:true}});}
     const text=user==='model-report'?JSON.stringify(options.model):user==='report'?'Turn '+(session.messages.length+1)+': '+beforeStart().systemPrompt:'Native Pi output';
     emit({type:'message_update',assistantMessageEvent:{type:'text_delta',delta:text}});
     const message={role:'assistant',content:[{type:'text',text}],usage:{input:12,output:4}};
     if(user==='truncated draft'){
       message.stopReason='length';
       message.content.push({type:'toolCall',name:'write_document'});
     }
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
async function stopSandbox({child,dir}){
 if(child.exitCode===null&&child.signalCode===null){
   const stopped=once(child,'exit');
   child.kill('SIGTERM');
   await stopped;
 }
 await rm(dir,{recursive:true,force:true,maxRetries:5});
}
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
   const truncated=await post(x.base,'/run',{...requestData,message:'truncated draft'});
   assert.match(await truncated.text(),/"phase":"document_output_limit"/);
   assert.equal((await post(x.base,'/run',{...requestData,project_id:'../x'})).status,422);
   assert.equal((await post(x.base,'/oauth/openai/start',{})).status,503);
 }finally{await stopSandbox(x);}
});

test('Custom model settings reach chat and completion and refresh an existing session',async()=>{
 const x=await startSandbox();
 try{
   const custom={...requestData,message:'model-report',context_window:262144,max_output_tokens:32768,
     api:'openai-completions',compat:{maxTokensField:'max_tokens'},sampling_params:{temperature:0.7,top_p:0.95}};
   const completion=await(await post(x.base,'/complete',custom)).json();
   const model=JSON.parse(completion.text);
   assert.equal(model.contextWindow,262144);assert.equal(model.maxTokens,32768);
   assert.equal(model.api,'openai-completions');
   assert.deepEqual(model.compat,custom.compat);assert.deepEqual(model.samplingParams,custom.sampling_params);
   const first=await(await post(x.base,'/run',custom)).text();
   assert.ok(first.includes('32768'));
   const changed=await(await post(x.base,'/run',{...custom,max_output_tokens:65536,sampling_params:{temperature:0.3}})).text();
   const frames=changed.split('\n').filter(line=>line.startsWith('data: ')).map(line=>JSON.parse(line.slice(6)));
   const updated=JSON.parse(frames.find(frame=>frame.type==='final').payload.text);
   assert.equal(updated.maxTokens,65536);assert.deepEqual(updated.samplingParams,{temperature:0.3});
   assert.equal((await post(x.base,'/run',{...custom,max_output_tokens:262144})).status,422);
 }finally{await stopSandbox(x);}
});

test('Pi sessions run independently, keep history, and refresh context for each turn',async()=>{
 const x=await startSandbox();
 try{
   const held=await post(x.base,'/run',{...requestData,message:'hold'});
   assert.equal(held.status,200);
   const other={...requestData,discussion_id:'123e4567-e89b-42d3-a456-426614174010',
     run_id:'123e4567-e89b-42d3-a456-426614174011',message:'report',system_context:'Context B'};
   const busy=await post(x.base,'/run',{...requestData,run_id:other.run_id});
   assert.equal(busy.status,409);
   assert.equal((await busy.json()).code,'PI_SESSION_BUSY');
   const parallel=await post(x.base,'/run',other);
   assert.match(await parallel.text(),/Turn 1: Context B/);
   assert.equal((await post(x.base,'/abort',{...other,discussion_id:DID})).status,404);
   assert.equal((await post(x.base,'/abort',requestData)).status,200);
   assert.match(await held.text(),/Pi run cancelled/);
   const first=await post(x.base,'/run',{...requestData,message:'report',system_context:'Context A'});
   assert.match(await first.text(),/Turn 1: Context A/);
   const next=await post(x.base,'/run',{...other,message:'report',system_context:'Updated context B'});
   assert.match(await next.text(),/Turn 2: Updated context B/);
 }finally{await stopSandbox(x);}
});
