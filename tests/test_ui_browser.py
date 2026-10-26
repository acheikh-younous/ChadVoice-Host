"""Live browser checks using a separate app, temporary SQLite and synthetic audio."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import sync_playwright


@pytest.fixture(scope='module')
def live_ui(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    work = tmp_path_factory.mktemp('ui_live')
    script = work / 'serve.py'
    script.write_text('''
import io, time, wave
import app as cv
import uvicorn
users={role:cv.create_user(role+'@ui.test',role.title()+' Test','visual-test-2026',role)
       for role in ('admin','contributor','reviewer')}
raw=io.BytesIO()
with wave.open(raw,'wb') as f:
    f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(b'\\0\\0'*3200)
with cv.db() as cx:
    for i,status in enumerate(('available','submitted','approved'),1):
        filename=f'synthetic-{i}.wav'
        (cv.AUDIO/filename).write_bytes(raw.getvalue())
        sid=cx.execute("INSERT INTO segments(code,original_name,file_name,mime,bytes,status,assigned_to) VALUES(?,?,?,?,?,?,?)",
            (f'CV100{i}',f'Synthétique {i}.wav',filename,'audio/wav',len(raw.getvalue()),status,
             None if i==1 else users['contributor'])).lastrowid
        if i==1:
            cx.execute('INSERT INTO working_leases(segment_id,user_id,expires_at) VALUES(?,?,?)',
                       (sid,users['contributor'],int(time.time())+3600))
        else:
            cx.execute("INSERT INTO translations(segment_id,contributor_id,latin,french,submitted_at) VALUES(?,?,?,?,datetime('now'))",
                       (sid,users['contributor'],'salam','bonjour'))
        if i==3:
            cx.execute("INSERT INTO reviews(segment_id,reviewer_id,decision,final_latin,final_french,note) VALUES(?,?,?,?,?,?)",
                       (sid,users['reviewer'],'approved','salam','bonjour','Exemple synthétique'))
uvicorn.run(cv.app,host='127.0.0.1',port=int(__import__('os').environ['UI_TEST_PORT']))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    environment=os.environ.copy()
    for name in list(environment):
        if name.startswith('CHADVOICE_SMTP_'):
            environment.pop(name)
    environment.update(DATABASE_URL='',CHADVOICE_AUDIO_STORAGE='local',
        CHADVOICE_DATA_DIR=str(work/'data'),CHADVOICE_HTTPS='0',
        PYTHONPATH=str(root),UI_TEST_PORT=str(port))
    url=f'http://127.0.0.1:{port}'
    with (work/'server.log').open('w') as log:
        process=subprocess.Popen([sys.executable,str(script)],cwd=root,env=environment,stdout=log,stderr=log)
        try:
            for _ in range(100):
                if process.poll() is not None:
                    pytest.fail((work/'server.log').read_text())
                try:
                    if httpx.get(url+'/health').json()=={'ok':True}:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail('Synthetic UI server did not start')
            yield url,work
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()


def login(page,url,role):
    page.goto(url+'/login')
    page.locator('[name=email]').fill(role+'@ui.test')
    page.locator('[name=password]').fill('visual-test-2026')
    page.locator('button[type=submit]').click()
    page.wait_for_url(url+'/')


@pytest.mark.parametrize('width',[360,390,768,1366])
def test_live_pages_navigation_and_logout(live_ui,width):
    url,work=live_ui
    screenshots=Path(os.getenv('CHADVOICE_UI_SCREENSHOTS',str(work/'screenshots')))
    screenshots.mkdir(parents=True,exist_ok=True)
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.getenv('CHADVOICE_TEST_BROWSER'))
        try:
            anonymous=browser.new_context(viewport={'width':width,'height':900})
            page=anonymous.new_page()
            page.goto(url+'/login')
            assert page.locator('.login-logo').evaluate('(img)=>img.complete && img.naturalWidth>0')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(screenshots/f'{width}-login.png'),full_page=True)
            anonymous.close()
            routes={
                'admin':[('dashboard','/'),('audios','/admin/segments'),('audio-detail','/admin/segments/2'),
                         ('users','/admin/users'),('user-detail','/admin/users/2'),('administration','/admin')],
                'contributor':[('dashboard','/'),('contribution','/contribute')],
                'reviewer':[('dashboard','/'),('revision-list','/review'),('revision','/review/2')]}
            for role,views in routes.items():
                context=browser.new_context(viewport={'width':width,'height':900})
                page=context.new_page()
                errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                login(page,url,role)
                for name,path in views:
                    response=page.goto(url+path)
                    assert response.status==200,(role,path)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(role,path,width)
                    assert page.locator('h1').first.is_visible()
                    if path=='/':
                        progress=page.locator('progress.progress')
                        assert progress.count()==1
                        assert progress.evaluate('(el)=>el.value===1 && (el.max===2 || el.max===3)')
                    assert not errors,errors
                    page.screenshot(path=str(screenshots/f'{width}-{role}-{name}.png'),full_page=True)
                page.goto(url+'/')
                if width<=800:
                    toggle=page.locator('[data-menu-toggle]')
                    assert toggle.is_visible()
                    assert toggle.get_attribute('aria-expanded')=='false'
                    assert not page.locator('#primary-sidebar').is_visible()
                    toggle.click()
                    assert toggle.get_attribute('aria-expanded')=='true'
                    assert page.locator('#primary-sidebar').is_visible()
                    assert page.locator('.main').evaluate('(el)=>el.inert')
                    close=page.locator('.sidebar [data-menu-close]')
                    assert close.evaluate('(el)=>el===document.activeElement')
                    page.keyboard.press('Shift+Tab')
                    assert page.locator('.sidebar .signout').evaluate('(el)=>el===document.activeElement')
                    page.keyboard.press('Tab')
                    assert close.evaluate('(el)=>el===document.activeElement')
                    if role=='admin':
                        assert page.locator('#primary-sidebar a[href="/admin/users"]').is_visible()
                    else:
                        assert page.locator('#primary-sidebar a[href="/admin/users"]').count()==0
                    page.screenshot(path=str(screenshots/f'{width}-{role}-menu.png'),full_page=True)
                    page.keyboard.press('Escape')
                    assert toggle.get_attribute('aria-expanded')=='false'
                    assert toggle.evaluate('(el)=>el===document.activeElement')
                    assert page.locator('#primary-sidebar').evaluate('(el)=>el.inert')
                    assert not page.locator('.main').evaluate('(el)=>el.inert')
                    toggle.click()
                    close.click()
                    assert toggle.get_attribute('aria-expanded')=='false'
                    toggle.click()
                    page.locator('.sidebar-scrim').click(position={'x':width-10,'y':300})
                    assert toggle.get_attribute('aria-expanded')=='false'
                    toggle.click()
                    page.locator('#primary-sidebar a[href="/account"]').click()
                    page.wait_for_url(url+'/account')
                    assert page.locator('[data-menu-toggle]').get_attribute('aria-expanded')=='false'
                    page.locator('.mobile-logout button').click()
                else:
                    assert page.locator('#primary-sidebar').is_visible()
                    page.locator('.sidebar .signout').click()
                page.wait_for_url(url+'/login')
                assert page.locator('[name=password]').is_visible()
                assert context.request.get(url+'/audio/2').status==401
                context.close()
        finally:
            browser.close()
