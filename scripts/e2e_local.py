"""Isolated local Chromium E2E; never touches a deployed instance or user data.

Run: python scripts/e2e_local.py
Requires `playwright`, installed Chromium (`python -m playwright install chromium`)
and installed app requirements. Uses temporary
SQLite + project storage, and launches app.py directly without a Python package.
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from cryptography.fernet import Fernet
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def run():
    port=free_port()
    with tempfile.TemporaryDirectory(prefix='specweave-e2e-') as path:
        data=Path(path)
        env={**os.environ,'DATA_ROOT':str(data),'DATABASE_URL':'sqlite:///'+str(data/'app.db'),
             'ENCRYPTION_KEY':Fernet.generate_key().decode(),
             'BOOTSTRAP_TOKEN':'e2e-bootstrap-token-strong-only','COOKIE_SECURE':'false',
             'PORT':str(port),'SPECWEAVE_MIGRATIONS_MANAGED':'false'}
        log=(data/'server.log').open('w')
        server=subprocess.Popen([sys.executable,'source/app.py'],cwd=ROOT,env=env,
                                stdout=log,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{port}'
        try:
            for attempt in range(100):
                if server.poll() is not None:raise RuntimeError(f'Server exited ({server.returncode})')
                try:
                    if httpx.get(base+'/health',timeout=.5).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError('Local test server did not become ready')
            with sync_playwright() as p:
                opts={'headless':True,'args':['--no-sandbox','--disable-dev-shm-usage']}
                if os.environ.get('CHROMIUM_EXECUTABLE'):
                    opts['executable_path']=os.environ['CHROMIUM_EXECUTABLE']
                browser=p.chromium.launch(**opts)
                try:
                    page=browser.new_page(viewport={'width':1440,'height':900},device_scale_factor=1)
                    js_errors=[]
                    page.on('pageerror',lambda exc:js_errors.append(str(exc)))
                    page.goto(base+'/first-run',wait_until='domcontentloaded')
                    page.locator('input[name=username]').fill('admin')
                    page.locator('input[name=email]').fill('admin@example.org')
                    page.locator('input[name=password]').fill('secure-e2e-password')
                    page.locator('input[name=bootstrap_token]').fill(env['BOOTSTRAP_TOKEN'])
                    page.get_by_role('button',name='Tạo quản trị viên').click()
                    page.wait_for_url('**/login?created=1')
                    page.locator('input[name=username]').fill('admin')
                    page.locator('input[name=password]').fill('secure-e2e-password')
                    page.get_by_role('button',name='Đăng nhập').click()
                    page.wait_for_url(base+'/')
                    assert page.get_by_text('SpecWeave').first.is_visible()
                    page.goto(base+'/projects/new',wait_until='domcontentloaded')
                    page.locator('#newName').fill('E2E Architecture Project')
                    page.locator('#newDescription').fill('Browser and API smoke checks')
                    for _ in range(4):page.locator('#wizardNext').click()
                    page.locator('#researchPolicy').select_option('No external research')
                    assert 'No external research' in page.locator('#wizardReview').inner_text()
                    assert 'Research on-demand' not in page.locator('#wizardReview').inner_text()
                    page.locator('#wizardNext').click()
                    page.get_by_role('heading',name='E2E Architecture Project').wait_for(timeout=20000)
                    pid=page.url.rsplit('/',1)[-1]
                    page.goto(base+f'/projects/{pid}/quality',wait_until='domcontentloaded')
                    shots=os.environ.get('SPECWEAVE_E2E_SCREENSHOT_DIR')
                    if shots:
                        Path(shots).mkdir(parents=True,exist_ok=True)
                        page.screenshot(path=str(Path(shots)/'quality-desktop.png'),full_page=True)
                    assert page.locator('#qJobs').is_visible()
                    assert page.locator('#qEnqueueReview').is_visible()
                    page.goto(base+f'/projects/{pid}/files',wait_until='domcontentloaded')
                    page.locator('#openTrash').click()
                    assert page.locator('#trashPanel').is_visible()
                    page.set_viewport_size({'width':390,'height':844})
                    page.goto(base+f'/projects/{pid}/quality',wait_until='domcontentloaded')
                    if shots:page.screenshot(path=str(Path(shots)/'quality-mobile.png'),full_page=True)
                    assert page.locator('#qEnqueueReview').count()==1
                    horizontal=page.evaluate('document.documentElement.scrollWidth-window.innerWidth')
                    page.goto(base+f'/projects/{pid}/discussion',wait_until='domcontentloaded')
                    page.locator('#chatInput').wait_for()
                    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    page.goto(base+f'/projects/{pid}/settings',wait_until='domcontentloaded')
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.locator('#deleteProject').click()
                    page.wait_for_url(base+'/projects')
                    page.goto(base+'/projects/trash',wait_until='domcontentloaded')
                    page.get_by_text('E2E Architecture Project',exact=True).wait_for()
                    if shots:page.screenshot(path=str(Path(shots)/'project-trash-mobile.png'),full_page=True)
                    page.once('dialog',lambda dialog:dialog.accept())
                    page.get_by_role('button',name='Xóa vĩnh viễn').click()
                    page.get_by_text('Thùng rác trống.',exact=True).wait_for()
                    print(f'Chromium desktop+mobile E2E passed; mobile overflow={horizontal}px; JS errors={len(js_errors)}')
                    if js_errors:raise AssertionError('Page JavaScript errors: '+repr(js_errors[:5]))
                finally:browser.close()
        finally:
            server.terminate()
            try:server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill();server.wait()
            log.close()
            if server.returncode not in (0,-15):
                print((data/'server.log').read_text()[-4000:])

if __name__=='__main__':
    run()
