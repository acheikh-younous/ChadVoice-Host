"""Static regression checks for the ChadVoice light-blue responsive redesign."""
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

ROOT=Path(__file__).resolve().parents[1]

def test_login_research_copy_and_secure_form():
    text=(ROOT/'templates/login.html').read_text(encoding='utf-8')
    assert 'Chaque voix compte' in text
    assert 'Chaque mot préserve une langue' in text
    assert 'arabe tchadien' in text
    assert 'name="email"' in text and 'name="password"' in text
    assert 'autocomplete="current-password"' in text
    Environment(loader=FileSystemLoader(ROOT/'templates')).get_template('login.html')

def test_mobile_drawer_and_role_links():
    html=(ROOT/'templates/base.html').read_text(encoding='utf-8')
    js=(ROOT/'static/app.js').read_text(encoding='utf-8')
    css=(ROOT/'static/app.css').read_text(encoding='utf-8')
    assert 'data-menu-toggle' in html and 'data-menu-close' in html
    assert 'aria-expanded="false"' in html
    assert 'aria-controls="primary-sidebar"' in html
    assert "event.key==='Escape'" in js
    assert 'body.menu-open .sidebar' in css
    assert '/admin/users' in html and '/admin/segments' in html
    assert 'class="mobile-logout"' in html
    Environment(loader=FileSystemLoader(ROOT/'templates')).get_template('base.html')

def test_light_palette_and_dashboards():
    css=(ROOT/'static/app.css').read_text(encoding='utf-8')
    dashboard=(ROOT/'templates/dashboard.html').read_text(encoding='utf-8')
    for needle in ('--light:#f5f9ff','--navy:#123c6a','.login-purpose','.admin-shortcuts','@media(max-width:540px)'):
        assert needle in css
    assert 'MON ESPACE CONTRIBUTEUR' in dashboard
    assert 'user.role==\'admin\'' in dashboard
    for path in ('templates/dashboard.html','templates/admin.html','templates/contribute.html','templates/admin_segments.html','templates/admin_users.html'):
        Environment(loader=FileSystemLoader(ROOT/'templates')).get_template(Path(path).name)
