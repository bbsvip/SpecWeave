/** No live OpenAI call. Exercises the REAL login broker against a fake Pi OAuth API. */
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {mkdtemp, mkdir, writeFile, copyFile, readFile, stat, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve} from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const broker = join(root, 'source/openai_oauth.mjs');
const helper = join(root, 'source/pi_device_id.mjs');
const uuidRe = /^[0-9a-f]{8}-[0-9a-f-]{27}$/i;

async function sandbox() {
  const dir = await mkdtemp(join(tmpdir(), 'specweave-oauth-'));
  const runner = join(dir, 'runner');
  const home = join(dir, 'home');
  const packageDir = join(runner, 'node_modules/@earendil-works/pi-ai');
  await mkdir(packageDir, {recursive:true});
  await mkdir(home);
  await copyFile(broker, join(runner, 'openai_oauth.mjs'));
  await copyFile(helper, join(runner, 'pi_device_id.mjs'));
  await writeFile(join(packageDir, 'package.json'), JSON.stringify({type:'module',exports:{'./models':'./models.mjs','./providers/openai':'./openai.mjs'}}));
  await writeFile(join(packageDir, 'openai.mjs'), 'export function openaiProvider(){return {auth:{oauth:{login(){}}}};}');
  await writeFile(join(packageDir, 'models.mjs'), `
export function createModels({credentials}) {
  return {
    setProvider() {},
    async login(provider, method, interaction, options) {
      if (provider!=='openai' || method!=='oauth') throw Error('Invalid provider');
      if (typeof options?.getDeviceId !== 'function' || !/^[0-9a-f-]{36}$/i.test(options.getDeviceId())) {
        throw Error('Sign in with ChatGPT requires a device ID (UUID) for this installation');
      }
      interaction.notify({type:'auth_url',url:'https://auth.openai.com/authorize?stub=1'});
      const code=await interaction.prompt({type:'manual_code',message:'Paste callback'});
      if (code !== 'test-callback') throw Error('invalid_grant');
      await credentials.modify('openai', async()=>({type:'oauth',access:'SENSITIVE_TEST_TOKEN'}));
    }
  };
}`);
  return {dir,runner,home};
}

function runLogin(ctx, answer='test-callback') {
  return new Promise((resolve, reject) => {
    const p=spawn(process.execPath,[join(ctx.runner,'openai_oauth.mjs')],{
      cwd:ctx.runner,env:{...process.env,HOME:ctx.home},stdio:['pipe','pipe','pipe']});
    let stdout='',stderr='',sent=false;
    p.stdout.on('data', chunk=>{
      stdout+=chunk.toString();
      if(!sent && stdout.includes('"type":"prompt"')) {
        sent=true;
        p.stdin.write(JSON.stringify({type:'answer',value:answer})+'\n');
      }
    });
    p.stderr.on('data',d=>stderr+=d.toString());
    p.on('error',reject);
    p.on('close', code=>resolve({code,stdout,stderr}));
    setTimeout(()=>{if(p.exitCode===null) p.kill('SIGKILL')},4000).unref();
  });
}

test('Pi OpenAI login has stable device ID and persists OAuth in the Pi vault',async()=>{
  const ctx=await sandbox();
  try {
    const first=await runLogin(ctx);
    assert.equal(first.code,0,first.stderr);
    assert.match(first.stdout, /"type":"auth_url"/);
    assert.match(first.stdout, /"type":"done"/);
    assert.doesNotMatch(first.stdout+first.stderr,/SENSITIVE_TEST_TOKEN/);
    const settingsPath=join(ctx.home,'.pi/agent/settings.json');
    const firstSettings=JSON.parse(await readFile(settingsPath,'utf8'));
    assert.match(firstSettings.deviceId,uuidRe);
    assert.equal((await stat(settingsPath)).mode & 0o777, 0o600);
    const second=await runLogin(ctx);
    assert.equal(second.code,0,second.stderr);
    assert.equal(JSON.parse(await readFile(settingsPath,'utf8')).deviceId, firstSettings.deviceId);
    const auth=JSON.parse(await readFile(join(ctx.home,'.pi/agent/auth.json'),'utf8'));
    assert.equal(auth.openai.type,'oauth');
  } finally { await rm(ctx.dir,{recursive:true,force:true}); }
});

test('Reuses existing Pi device ID and preserves other Pi settings',async()=>{
  const ctx=await sandbox();
  try {
    const settings=join(ctx.home,'.pi/agent/settings.json');
    await mkdir(join(ctx.home,'.pi/agent'),{recursive:true});
    await writeFile(settings,JSON.stringify({theme:'dark',deviceId:'123e4567-e89b-42d3-a456-426614174000'}));
    const result=await runLogin(ctx);
    assert.equal(result.code,0,result.stderr);
    assert.deepEqual(JSON.parse(await readFile(settings,'utf8')),{theme:'dark',deviceId:'123e4567-e89b-42d3-a456-426614174000'});
  } finally {await rm(ctx.dir,{recursive:true,force:true})}
});

test('Invalid Pi identity fails closed without overwriting settings',async()=>{
  const ctx=await sandbox();
  try {
    const settings=join(ctx.home,'.pi/agent/settings.json');
    await mkdir(join(ctx.home,'.pi/agent'),{recursive:true});
    await writeFile(settings,JSON.stringify({deviceId:'not-a-uuid'}));
    const result=await runLogin(ctx);
    assert.equal(result.code,1);
    assert.match(result.stdout,/OAUTH_DEVICE_ID/);
    assert.match(result.stderr,/phase=device_identity hint=DEVICE_ID_MISSING/);
    assert.equal(JSON.parse(await readFile(settings,'utf8')).deviceId,'not-a-uuid');
  } finally {await rm(ctx.dir,{recursive:true,force:true})}
});

test('Malformed Pi settings are reported without overwriting or printing the file contents',async()=>{
  const ctx=await sandbox();
  try {
    const settings=join(ctx.home,'.pi/agent/settings.json');
    await mkdir(join(ctx.home,'.pi/agent'),{recursive:true});
    await writeFile(settings,'{invalid json with SENSITIVE_MARKER');
    const result=await runLogin(ctx);
    assert.equal(result.code,1);
    assert.match(result.stdout,/OAUTH_SETTINGS_INVALID/);
    assert.match(result.stderr,/phase=device_identity hint=PI_SETTINGS_INVALID/);
    assert.doesNotMatch(result.stdout+result.stderr,/SENSITIVE_MARKER/);
    assert.equal(await readFile(settings,'utf8'),'{invalid json with SENSITIVE_MARKER');
  } finally {await rm(ctx.dir,{recursive:true,force:true})}
});
