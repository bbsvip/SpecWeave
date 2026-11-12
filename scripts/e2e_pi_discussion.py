"""Local regression for Pi Activity, F5 replay and independent discussion sessions.

Run with the pinned Pi SDK and Playwright Chromium installed.
CHROMIUM_EXECUTABLE can point to an existing Chromium-based browser.
"""
from contextlib import ExitStack, closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import subprocess
import re
import sqlite3
import sys
import tempfile
import threading
import time

import httpx
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright, expect

from e2e_local import free_port

ROOT = Path(__file__).resolve().parents[1]
calls = []
provider_requests = []
releases = {name: threading.Event() for name in ('alpha', 'bravo', 'stop-test')}
posts = []

class Provider(BaseHTTPRequestHandler):
    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        provider_requests.append(request)
        messages = request['messages']
        name = [m['content'] for m in messages if m['role'] == 'user'][-1]
        if isinstance(name, list):
            name = ''.join(item.get('text', '') for item in name)
        calls.append((name, messages))
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        def chunk(delta, reason=None):
            payload = {'id':'specweave-local','object':'chat.completion.chunk','created':1,
                       'model':'local-test','choices':[{'index':0,'delta':delta,'finish_reason':reason}]}
            self.wfile.write(('data: '+json.dumps(payload,ensure_ascii=False)+'\n\n').encode())
            self.wfile.flush()
        chunk({'role':'assistant','content':f'Pi {name} đang chạy'})
        if name in releases and not releases[name].wait(40):
            return
        try:
            chunk({'content':' · hoàn tất'})
            chunk({},'stop')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except ConnectionError:
            pass


def run(scratch):
    web = f'http://127.0.0.1:{free_port()}'
    pi = f'http://127.0.0.1:{free_port()}'
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    env = {**os.environ, 'DATA_ROOT':str(scratch/'data'), 'DATABASE_URL':'sqlite:///'+(scratch/'data.sqlite').as_posix(),
           'ENCRYPTION_KEY':Fernet.generate_key().decode(), 'BOOTSTRAP_TOKEN':'isolated-test-bootstrap-token-000',
           'COOKIE_SECURE':'false', 'SPECWEAVE_MIGRATIONS_MANAGED':'false',
           'OPENAI_OAUTH_LOCAL_MODE':'false', 'OPENAI_OAUTH_LOCAL_HOME':str(scratch/'vault'),
           'PI_RUNNER_URL':pi,'PI_RUNNER_KEY':'isolated-test-runner-key-00000000000000','PI_TOOL_URL':web+'/api/internal/pi-tool',
           'PI_HOST_PORT':pi.rsplit(':',1)[-1], 'PORT':web.rsplit(':',1)[-1],
           'PI_HOST_BIND':'127.0.0.1','PROJECTS_ROOT':str(scratch/'data'/'projects'),
           'SPECWEAVE_PI_SAFE_RESOURCES_DIR':str(scratch/'resources'), 'SPECWEAVE_CHAT_CONCURRENCY':'2'}
    with ExitStack() as stack:
        stack.callback(provider.server_close)
        stack.callback(provider.shutdown)
        def release_provider():
            for event in releases.values():
                event.set()
        stack.callback(release_provider)
        def start(name, args):
            log = stack.enter_context((scratch/f'{name}.log').open('wb'))
            proc = subprocess.Popen(args, cwd=ROOT, env=env, stdout=log, stderr=log,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            def stop():
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            stack.callback(stop)
            return proc
        host=start('pi', ['node','source/pi_host.mjs'])
        start('web', [sys.executable,'source/app.py'])
        client = stack.enter_context(httpx.Client(base_url=web, trust_env=False, timeout=15))
        def wait_until_healthy(url):
            for _ in range(100):
                if url==pi and host.poll() is not None:
                    raise RuntimeError('Pi host exited; check the pinned SDK dependencies')
                try:
                    if client.get(url+'/health',timeout=1).is_success:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError(f'{url} failed health check')
        for url in (web,pi):
            wait_until_healthy(url)
        start('worker',[sys.executable,'source/review_worker.py'])
        boot=client.post('/first-run',data={'username':'admin','email':'admin@example.org','password':'local-test-password','bootstrap_token':env['BOOTSTRAP_TOKEN']})
        assert boot.status_code==303,boot.text
        assert client.post('/login',data={'username':'admin','password':'local-test-password'}).status_code==303
        csrf = re.search(r'name="csrf-token" content="([^"]+)"',client.get('/').text)[1]
        def post(path, data):
            res=client.post(path,json=data,headers={'X-CSRF-Token':csrf})
            assert res.is_success,res.text
            return res.json()
        provider_id=post('/api/providers',{'name':'Local smoke','base_url':f'http://127.0.0.1:{provider.server_port}/v1','api_key':'fake'})['id']
        post(f'/api/providers/{provider_id}/models',{'model_ids':['local-test']})
        pid=post('/api/projects',{'name':'Pi smoke','description':'Local test'})['id']
        with closing(sqlite3.connect(scratch/'data.sqlite')) as db:
            mid=db.execute('select id from models where model_id=?',('local-test',)).fetchone()[0]
            did=db.execute('select id from discussions where project_id=?',(pid,)).fetchone()[0]
        post(f'/api/projects/{pid}/settings',{'active_model_id':mid})
        bid=post(f'/api/projects/{pid}/discussions',{'title':'Discussion B'})['id']
        def discussion(key):
            return f'{web}/projects/{pid}/discussion?did={key}'
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM_EXECUTABLE'))
            context=browser.new_context()
            # Load the same pinned UI assets as Docker; keep every other external
            # destination blocked. Model requests still use only our local fixture.
            context.route('https://**/*', lambda route: route.continue_()
                          if route.request.url.startswith('https://cdn.jsdelivr.net/npm/') else route.abort())
            context.add_cookies([{'name':'specweave_session','value':client.cookies['specweave_session'],'url':web}])
            errors=[]
            def configure_model(output, temperature, capture=False):
                page=context.new_page()
                page.set_viewport_size({'width':1440,'height':1000})
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'{web}/settings/providers/{provider_id}')
                form=page.locator('.model-config-form')
                form.locator('[name=context_window]').fill('262144')
                form.locator('[name=max_output_tokens]').fill(str(output))
                form.locator('summary').click()
                form.locator('[name=compat]').fill('{"maxTokensField":"max_tokens"}')
                form.locator('[name=sampling_params]').fill(json.dumps({'temperature':temperature,'top_p':0.95}))
                with page.expect_navigation():
                    form.locator('.model-save').click()
                expect(form.locator('[name=max_output_tokens]')).to_have_value(str(output))
                expect(form.locator('[name=context_window]')).to_have_value('262144')
                form.locator('summary').click()
                if capture:
                    page.evaluate('document.fonts.ready')
                    page.evaluate('window.scrollTo(0,0)')
                    screenshot_dir=os.getenv('E2E_SCREENSHOT_DIR')
                    if screenshot_dir:
                        page.screenshot(path=str(Path(screenshot_dir)/'custom-model-desktop.png'),full_page=True,animations='disabled')
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Provider settings overflow on mobile'
                    if screenshot_dir:
                        page.screenshot(path=str(Path(screenshot_dir)/'custom-model-mobile.png'),full_page=True,animations='disabled')
                page.close()
            configure_model(32768,0.7,capture=True)
            def visit(key, before_load=None):
                page=context.new_page()
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('request',lambda r:posts.append(r.url) if r.method=='POST' else None)
                if before_load:
                    page.route('**/static/js/discussion.js?v=*',before_load,times=1)
                page.goto(discussion(key))
                expect(page.locator('#chatInput')).to_be_enabled()
                page.locator('#autoNextPlanning').uncheck()
                return page
            def send(page,text):
                page.locator('#chatInput').fill(text)
                page.locator('#sendChat').click()
            def live(page,text):
                expect(page.locator('.message-agent .message-content').filter(has_text=text)).to_have_count(1,timeout=20000)
                expect(page.locator('.turn-live')).to_be_visible()
            a=visit(did)
            send(a,'alpha')
            live(a,'Pi alpha đang chạy')
            assert provider_requests[-1]['max_tokens']==32768, provider_requests[-1].keys()
            assert provider_requests[-1]['temperature']==0.7
            assert provider_requests[-1]['top_p']==0.95
            assert 'max_completion_tokens' not in provider_requests[-1]
            a.reload()
            live(a,'Pi alpha đang chạy')
            assert len(posts)==1,posts
            a.goto(f'{web}/projects/{pid}/documents')
            a.close()
            b=visit(bid)
            send(b,'bravo')
            live(b,'Pi bravo đang chạy')
            assert [name for name,_ in calls]==['alpha','bravo'],calls
            assert 'alpha' not in json.dumps(calls[-1][1])
            def wait_until_idle(discussion_id):
                for _ in range(100):
                    response=client.get(f'/api/projects/{pid}/discussions/{discussion_id}/active-run')
                    if response.json()['status']=='idle':
                        return
                    time.sleep(.05)
                raise AssertionError('The run did not reach a durable terminal state')

            def finish_before_javascript(route):
                releases['alpha'].set()
                wait_until_idle(did)
                route.continue_()

            # The HTML sees the active run, but JS only starts after it finishes.
            a=visit(did,finish_before_javascript)
            expect(a.locator('.message-agent .message-content').filter(has_text='Pi alpha đang chạy · hoàn tất')).to_have_count(1)
            expect(a.locator('.turn-live-label')).to_have_text('Hoàn thành')
            b.close()
            releases['bravo'].set()
            wait_until_idle(bid)
            b=visit(bid)
            expect(b.locator('.message-agent .message-content').filter(has_text='Pi bravo đang chạy · hoàn tất')).to_have_count(1)

            a.locator('#agentRole').select_option('architect')
            configure_model(65536,0.3)
            send(a,'charlie')
            expect(a.locator('.message-agent .message-content').filter(has_text='Pi charlie đang chạy · hoàn tất')).to_have_count(1,timeout=15000)
            sent=json.dumps(calls[-1][1],ensure_ascii=False)
            assert 'alpha' in sent and 'bravo' not in sent
            assert 'BMAD Architect' in sent
            assert provider_requests[-1]['max_tokens']==65536
            assert provider_requests[-1]['temperature']==0.3

            host.terminate()
            host.wait(5)
            host=start('pi-restarted',['node','source/pi_host.mjs'])
            wait_until_healthy(pi)
            send(a,'delta')
            expect(a.locator('.message-agent .message-content').filter(has_text='Pi delta đang chạy · hoàn tất')).to_have_count(1,timeout=15000)
            sent=json.dumps(calls[-1][1],ensure_ascii=False)
            assert 'alpha' in sent and 'charlie' in sent and 'bravo' not in sent
            assert provider_requests[-1]['max_tokens']==65536

            send(b,'stop-test')
            live(b,'Pi stop-test đang chạy')
            b.locator('#stopChat').click()
            expect(b.locator('.turn-live-label')).to_have_text('Đã dừng',timeout=15000)
            releases['stop-test'].set()
            assert [name for name,_ in calls]==['alpha','bravo','charlie','delta','stop-test']
            assert sum(url.endswith('/chat/start') for url in posts)==5
            assert sum(url.endswith('/cancel') for url in posts)==1
            assert not errors,errors
            browser.close()
        print('Passed Pi discussion E2E with the real SDK and a local provider fixture.')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='specweave-pi-e2e-') as path:
        run(Path(path))
