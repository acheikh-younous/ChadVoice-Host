"""Browser regression checks with synthetic pages; no database or private audio."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
import shutil
import os

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.sync_api import sync_playwright


@pytest.fixture(scope='module')
def branding_site(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    pages = tmp_path_factory.mktemp('branding')
    shutil.copytree(root / 'static', pages / 'static')
    templates = Environment(loader=FileSystemLoader(root / 'templates'),
                            autoescape=select_autoescape(['html']))
    for name, user in [('login', None), ('dashboard', SimpleNamespace(
            role='admin', full_name='Utilisateur ' * 30, csrf='test-only'))]:
        template = templates.get_template('login.html' if user is None else 'base.html')
        (pages / f'{name}.html').write_text(template.render(
            user=user, request=SimpleNamespace(url=SimpleNamespace(path='/')),
            msg=None, unread=0), encoding='utf-8')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(
        SimpleHTTPRequestHandler, directory=str(pages)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.mark.parametrize('width', [320, 390, 800, 801, 1440])
@pytest.mark.parametrize('view', ['login', 'dashboard'])
def test_responsive_logo(branding_site, width, view):
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv('CHADVOICE_TEST_BROWSER'))
        page = browser.new_page(viewport={'width': width, 'height': 900})
        try:
            page.goto(f'{branding_site}/{view}.html')
            selector = ('.login-logo' if view == 'login' else
                        '.brand-logo-mobile' if width <= 800 else '.brand-logo')
            logo = page.locator(selector)
            assert logo.is_visible()
            assert logo.get_attribute('alt') == 'Logo ChadVoice'
            assert logo.evaluate('(img) => img.complete && img.naturalWidth > 0')
            box = logo.bounding_box()
            expected = (72 if width <= 800 else 120) if view == 'login' else (32 if width <= 800 else 42)
            assert box['width'] == box['height'] == expected
            assert logo.evaluate('(img) => getComputedStyle(img).objectFit') == 'contain'
            assert box['x'] >= 0 and box['x'] + box['width'] <= width
            if view == 'dashboard':
                hidden = '.brand-logo' if width <= 800 else '.brand-logo-mobile'
                assert not page.locator(hidden).is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        finally:
            browser.close()
