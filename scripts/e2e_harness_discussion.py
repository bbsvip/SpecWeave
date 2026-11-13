"""Local regression for harness activity, F5 replay and isolated discussions.

Run with project requirements and Playwright Chromium installed.
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
releases = {name: threading.Event() for name in ('alpha', 'bravo', 'stop-test', 'scroll-test')}
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
        if name.startswith(('Create ONE decision brief', 'Return exactly one JSON object')):
            brief={'question':f'Cần lưu lịch sử quyết định theo cách nào trong lượt {len(calls)}?',
                   'context':'Kiểm thử câu hỏi neo và lịch sử với provider cục bộ.',
                   'options':[{'label':label,'description':'Phương án kiểm thử.','pros':'Có lịch sử để kiểm tra.','cons':'Cần dung lượng lưu trữ.'}
                              for label in ('Lưu đầy đủ','Lưu tối giản')],
                   'recommended_index':0,'recommendation_reason':'Cần lưu đầy đủ để có thể quay lại và kiểm tra.',
                   'impact':'Ảnh hưởng lịch sử thảo luận.','assumptions':[],'sources':[]}
            text=json.dumps({'action':'ask','question':brief},ensure_ascii=False) if name.startswith('Return exactly') else json.dumps(brief,ensure_ascii=False)
            chunk({'content':text},'stop')
            self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush()
            return
        chunk({'role':'assistant','content':f'SpecWeave {name} đang chạy'})
        if name in releases and not releases[name].wait(40):
            return
        try:
            if name=='scroll-test':
                chunk({'content':'\n\n'+('\n\nNội dung mới trong khi người dùng đang đọc lịch sử.'*20)})
            chunk({'content':' · hoàn tất'})
            chunk({},'stop')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except ConnectionError:
            pass


def run(scratch):
    web = f'http://127.0.0.1:{free_port()}'
    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    env = {**os.environ, 'DATA_ROOT':str(scratch/'data'), 'DATABASE_URL':'sqlite:///'+(scratch/'data.sqlite').as_posix(),
           'ENCRYPTION_KEY':Fernet.generate_key().decode(), 'BOOTSTRAP_TOKEN':'isolated-test-bootstrap-token-000',
           'COOKIE_SECURE':'false', 'SPECWEAVE_MIGRATIONS_MANAGED':'false',
           'PORT':web.rsplit(':',1)[-1], 'SPECWEAVE_CHAT_CONCURRENCY':'2'}
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
        start('web', [sys.executable,'source/app.py'])
        client = stack.enter_context(httpx.Client(base_url=web, trust_env=False, timeout=15))
        def wait_until_healthy(url):
            for _ in range(100):
                try:
                    if client.get(url+'/health',timeout=1).is_success:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.1)
            else:
                raise RuntimeError(f'{url} failed health check')
        wait_until_healthy(web)
        worker=start('worker',[sys.executable,'source/review_worker.py'])
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
        pid=post('/api/projects',{'name':'SpecWeave smoke','description':'Local test'})['id']
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
                page.locator('#agentMode').select_option('discuss')
                return page
            def send(page,text):
                page.locator('#chatInput').fill(text)
                page.locator('#sendChat').click()
            def live(page,text):
                expect(page.locator('.message-agent .message-content').filter(has_text=text)).to_have_count(1,timeout=20000)
                expect(page.locator('.turn-live')).to_be_visible()
            a=visit(did)
            send(a,'alpha')
            live(a,'SpecWeave alpha đang chạy')
            assert provider_requests[-1]['max_tokens']==32768, provider_requests[-1].keys()
            assert provider_requests[-1]['temperature']==0.7
            assert provider_requests[-1]['top_p']==0.95
            assert 'max_completion_tokens' not in provider_requests[-1]
            a.reload()
            live(a,'SpecWeave alpha đang chạy')
            assert len(posts)==1,posts
            a.goto(f'{web}/projects/{pid}/documents')
            a.close()
            b=visit(bid)
            send(b,'bravo')
            live(b,'SpecWeave bravo đang chạy')
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
            expect(a.locator('.message-agent .message-content').filter(has_text='SpecWeave alpha đang chạy · hoàn tất')).to_have_count(1)
            expect(a.locator('.turn-live-label')).to_have_text('Hoàn thành')
            b.close()
            releases['bravo'].set()
            wait_until_idle(bid)
            b=visit(bid)
            expect(b.locator('.message-agent .message-content').filter(has_text='SpecWeave bravo đang chạy · hoàn tất')).to_have_count(1)

            a.locator('#agentRole').select_option('architect')
            configure_model(65536,0.3)
            send(a,'charlie')
            expect(a.locator('.message-agent .message-content').filter(has_text='SpecWeave charlie đang chạy · hoàn tất')).to_have_count(1,timeout=15000)
            sent=json.dumps(calls[-1][1],ensure_ascii=False)
            assert 'alpha' in sent and 'bravo' not in sent
            assert 'BMAD Architect' in sent
            assert provider_requests[-1]['max_tokens']==65536
            assert provider_requests[-1]['temperature']==0.3

            wait_until_idle(did)
            worker.terminate()
            worker.wait(5)
            worker=start('worker-restarted',[sys.executable,'source/review_worker.py'])
            send(a,'delta')
            expect(a.locator('.message-agent .message-content').filter(has_text='SpecWeave delta đang chạy · hoàn tất')).to_have_count(1,timeout=15000)
            sent=json.dumps(calls[-1][1],ensure_ascii=False)
            assert 'alpha' in sent and 'charlie' in sent and 'bravo' not in sent
            assert provider_requests[-1]['max_tokens']==65536

            send(b,'stop-test')
            live(b,'SpecWeave stop-test đang chạy')
            b.locator('#stopChat').click()
            expect(b.locator('.turn-live-label')).to_have_text('Đã dừng',timeout=15000)
            releases['stop-test'].set()
            assert [name for name,_ in calls]==['alpha','bravo','charlie','delta','stop-test']
            assert sum(url.endswith('/chat/start') for url in posts)==5
            assert sum(url.endswith('/cancel') for url in posts)==1
            wait_until_idle(bid)
            with closing(sqlite3.connect(scratch/'data.sqlite')) as database:
                for number in range(24):
                    database.execute('insert into messages (id,discussion_id,role,content,created_at) values (?,?,?,?,?)',
                                     (f'scroll-message-{number}',did,'user',f'Nội dung lịch sử {number}. '+('Đoạn văn kiểm thử cuộn. '*15),'2026-01-01 00:00:00'))
                database.commit()
            a.reload()
            expect(a.locator('#chatInput')).to_be_enabled()
            a.locator('#agentMode').select_option('discuss')
            send(a,'scroll-test')
            live(a,'SpecWeave scroll-test đang chạy')
            bottom=lambda:a.locator('#messageList').evaluate('(el)=>el.scrollHeight-el.scrollTop-el.clientHeight')
            assert bottom()<5, 'Sending a user message must scroll to the bottom'
            a.locator('#messageList').evaluate('(el)=>{el.scrollTop=120}')
            a.wait_for_timeout(100)
            before=a.locator('#messageList').evaluate('(el)=>el.scrollTop')
            releases['scroll-test'].set()
            expect(a.locator('.turn-live-label').last).to_have_text('Hoàn thành',timeout=15000)
            after=a.locator('#messageList').evaluate('(el)=>el.scrollTop')
            assert abs(after-before)<3, f'Streaming must preserve the reading position: {before} -> {after}'
            send(a,'scroll-finished')
            expect(a.locator('.turn-live-label').last).to_have_text('Hoàn thành',timeout=15000)
            assert bottom()<5, 'A new user message must resume following'
            wait_until_idle(did)
            post(f'/api/projects/{pid}/planning/questions/suggest',{'discussion_id':did})
            a.reload()
            expect(a.locator('#planningQuestion')).to_be_visible()
            option=a.locator('#planningQuestion .planning-option').first
            expect(option.locator('.planning-option-copy')).to_have_text('Phương án kiểm thử.')
            expect(option.locator('.planning-tradeoffs')).to_contain_text('Ưu điểm: Có lịch sử để kiểm tra.')
            expect(option.locator('.planning-tradeoffs')).to_contain_text('Hạn chế: Cần dung lượng lưu trữ.')
            a.locator('#messageList').evaluate('(el)=>{el.scrollTop=100}')
            a.locator('#planningQuestion .planning-option').first.click()
            assert bottom()<5, 'Selecting an answer must resume following'
            a.locator('#planningQuestion').get_by_role('button',name='Xác nhận & tiếp tục với AI').click()
            expect(a.locator('.turn-live-label').last).to_have_text('Chờ xác nhận',timeout=15000)
            expect(a.locator('.qa-card .qa-answer-choice')).to_have_text('Lưu đầy đủ')
            expect(a.locator('#planningHistory')).to_have_count(0)
            expect(a.locator('.message-user').filter(has_text='Tôi chọn: Lưu đầy đủ')).to_have_count(0)
            a.reload()
            expect(a.locator('.qa-card .qa-answer-choice')).to_have_text('Lưu đầy đủ')
            assert all(not text.startswith('Chờ xác nhận') for text in
                       a.locator('.message-question').first.locator('.turn-activity > summary').all_text_contents())
            for width,height,label in ((1440,1000,'desktop'),(390,844,'mobile')):
                a.set_viewport_size({'width':width,'height':height})
                a.locator('.message-question').first.scroll_into_view_if_needed()
                screenshot_dir=os.getenv('E2E_SCREENSHOT_DIR')
                if screenshot_dir:
                    a.screenshot(path=str(Path(screenshot_dir)/f'planning-qa-answered-{label}.png'),full_page=False,animations='disabled')
            a.set_viewport_size({'width':1440,'height':1000})
            a.get_by_role('button',name='Quay lại trả lời',exact=True).click()
            a.get_by_role('button',name='Quay lại câu hỏi này',exact=True).click()
            expect(a.locator('#planningQuestion')).to_be_visible()
            a.reload()
            expect(a.locator('#planningQuestion')).to_be_visible()
            expect(a.locator('.qa-card .qa-answer-choice')).to_have_text('Lưu đầy đủ')
            expect(a.locator('.message-user').filter(has_text='Tôi chọn: Lưu đầy đủ')).to_have_count(0)
            expect(a.locator('.qa-card')).to_have_count(2)
            for width,height,label in ((1440,1000,'desktop'),(390,844,'mobile')):
                a.set_viewport_size({'width':width,'height':height})
                a.locator('#messageList').evaluate('(el)=>{el.scrollTop=80}')
                a.wait_for_timeout(100)
                bounds=a.locator('#planningQuestion').bounding_box()
                composer=a.locator('#chatInput').bounding_box()
                assert bounds['y']+bounds['height']<=composer['y']+2
                assert composer['y']+composer['height']<height
                assert a.evaluate('document.documentElement.scrollWidth <= innerWidth')
                screenshot_dir=os.getenv('E2E_SCREENSHOT_DIR')
                if screenshot_dir:
                    a.locator('.message-question').first.scroll_into_view_if_needed()
                    a.screenshot(path=str(Path(screenshot_dir)/f'planning-qa-card-{label}.png'),full_page=False,animations='disabled')
            oauth_page=context.new_page()
            oauth_page.goto(f'{web}/settings/providers')
            expect(oauth_page.get_by_role('button',name='Continue with ChatGPT')).to_be_visible()
            for width,height,label in ((1440,1000,'desktop'),(390,844,'mobile')):
                oauth_page.set_viewport_size({'width':width,'height':height})
                oauth_page.locator('#openaiOAuthApp').scroll_into_view_if_needed()
                assert oauth_page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                if screenshot_dir:
                    oauth_page.screenshot(path=str(Path(screenshot_dir)/f'native-chatgpt-oauth-{label}.png'),full_page=False,animations='disabled')
            oauth_page.get_by_role('button',name='Continue with ChatGPT').click()
            expect(oauth_page.locator('#openaiOAuthFlow')).to_be_visible()
            oauth_page.reload()
            expect(oauth_page.locator('#openaiOAuthFlow')).to_be_visible()
            oauth_page.get_by_role('button',name='Hủy',exact=True).click()
            expect(oauth_page.locator('#openaiOAuthFlow')).not_to_be_visible()
            # Account catalogs expose display names while inference uses the slug.
            oauth_page.route(f'**/api/providers/{provider_id}/discover',lambda route:route.fulfill(json={
                'models':['catalog-fixture'],
                'model_details':[{'id':'catalog-fixture','name':'QA catalog model'}],
            }))
            oauth_page.locator(f'.provider-fetch[data-id="{provider_id}"]').click()
            expect(oauth_page.locator('#fetchedModels')).to_contain_text('QA catalog model · catalog-fixture')
            expect(oauth_page.locator('#fetchedModels input')).to_have_value('catalog-fixture')
            oauth_page.locator('#fetchedModels input').check()
            oauth_page.set_viewport_size({'width':1440,'height':1000})
            if screenshot_dir:
                oauth_page.screenshot(path=str(Path(screenshot_dir)/'oauth-model-catalog.png'),full_page=False,animations='disabled')
            oauth_page.locator('#saveModels').click()
            expect(oauth_page.locator(f'[data-provider="{provider_id}"] .model-tag').filter(has_text='catalog-fixture')).to_be_visible()
            with closing(sqlite3.connect(scratch/'data.sqlite')) as database:
                assert database.execute('select model_id from models where provider_id=? and model_id=?',
                                        (provider_id,'catalog-fixture')).fetchone()==('catalog-fixture',)
            assert not errors,errors
            browser.close()
        print('Passed SpecWeave discussion E2E against the native harness and a local provider fixture.')


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='specweave-native-e2e-') as path:
        run(Path(path))
