"""Isolated regression checks; optional PostgreSQL must be a disposable local DB."""
import hashlib
import io
import os
import uuid
import wave
from contextlib import ExitStack
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

import app as application
import postgres_backend


BACKENDS = ['sqlite'] + (['postgres'] if os.getenv('CHADVOICE_TEST_POSTGRES_URL') else [])


@pytest.fixture(params=BACKENDS)
def site(request, monkeypatch, tmp_path):
    """Never reuse the application DB, private audio, SMTP or cloud credentials."""
    monkeypatch.setattr(application, 'DATA', tmp_path)
    monkeypatch.setattr(application, 'AUDIO', tmp_path / 'audio')
    monkeypatch.setattr(application, 'DB', tmp_path / 'test.sqlite3')
    application.AUDIO.mkdir()
    monkeypatch.setattr(application, 'smtp_configured', lambda: False)
    monkeypatch.setattr(application, 'DB_URL', '')
    monkeypatch.setattr(application, 'STORAGE_MODE', 'local')
    import object_storage
    monkeypatch.setattr(object_storage, 'MODE', 'local')
    schema = None
    if request.param == 'postgres':
        import psycopg
        from psycopg import sql
        url = os.environ['CHADVOICE_TEST_POSTGRES_URL']
        assert urlparse(url).hostname in ('localhost', '127.0.0.1', '::1'), 'Local disposable PostgreSQL only'
        schema = 'cv_test_' + uuid.uuid4().hex
        with psycopg.connect(url, autocommit=True) as cx:
            cx.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        dsn = psycopg.conninfo.make_conninfo(url, options=f'-c search_path={schema}')
        monkeypatch.setattr(postgres_backend, 'DB_URL', dsn)
        monkeypatch.setattr(application, 'DB_URL', dsn)
        # Exercise the additive migration from v1.2, preserving an existing user.
        source = Path(postgres_backend.__file__).with_name('postgres_schema.sql').read_text()
        old_schema = source.replace('archived_at TEXT,', '')
        with psycopg.connect(dsn) as cx:
            for statement in old_schema.split(';'):
                if statement.strip() and 'CREATE TABLE IF NOT EXISTS user_presence' not in statement:
                    cx.execute(statement)
            cx.execute("INSERT INTO users(email,full_name,pw_hash,role) VALUES('legacy@test','Legacy','unused','contributor')")
        postgres_backend.init_postgres()
        postgres_backend.init_postgres()
        with application.db() as cx:
            assert cx.execute("SELECT full_name FROM users WHERE email='legacy@test'").fetchone()[0] == 'Legacy'
            assert cx.execute('SELECT archived_at FROM segments').fetchall() == []
            assert cx.execute('SELECT * FROM user_presence').fetchall() == []
    else:
        application.init_db()
    try:
        users = {}
        for role in ('admin', 'contributor', 'reviewer'):
            users[role] = application.create_user(role+'@test', role, 'test-password-2026', role)
        users['other'] = application.create_user('other@test', 'other', 'test-password-2026', 'contributor')
        with ExitStack() as stack:
            clients = {}
            for role in users:
                client = stack.enter_context(TestClient(application.app, follow_redirects=True))
                assert client.post('/login', data={'email':role+'@test','password':'test-password-2026'}).status_code == 200
                with application.db() as cx:
                    csrf = cx.execute('SELECT csrf FROM sessions WHERE token_hash=?',
                                      (hashlib.sha256(client.cookies['cv_session'].encode()).hexdigest(),)).fetchone()[0]
                clients[role] = (client, csrf)
            yield clients, users
    finally:
        if schema:
            with psycopg.connect(url, autocommit=True) as cx:
                cx.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))


def upload(site):
    clients, _ = site
    admin, csrf = clients['admin']
    raw = io.BytesIO()
    with wave.open(raw, 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000)
        f.writeframes(b'\0\0' * 1600)
    response = admin.post('/admin/upload', data={'csrf':csrf},
                          files={'audios':('SYNTHETIC.wav',raw.getvalue(),'audio/wav')})
    assert 'Importés : 1' in response.text
    with application.db() as cx:
        return dict(cx.execute('SELECT * FROM segments').fetchone())


@pytest.mark.parametrize('role', ['contributor', 'reviewer', 'admin'])
def test_admin_mutations_require_role_and_csrf(site, role):
    clients, _ = site
    seg = upload(site)
    client, csrf = clients[role]
    data = {'csrf': 'invalid' if role == 'admin' else csrf,
            'name':'renamed.wav', 'note':'Valid reason', 'latin':'latin', 'french':'français',
            'confirm_code':seg['code']}
    for action in ('metadata', 'archive', 'restore', 'delete', 'revoke-approval', 'correct-approval'):
        assert client.post(f"/admin/segments/{seg['id']}/{action}", data=data).status_code == 403
    assert client.post('/admin/segments/bulk-approve', data={**data,
        'confirmation':'VALIDER','segment_ids':[seg['id']]}).status_code == 403
    with application.db() as cx:
        row = cx.execute('SELECT * FROM segments WHERE id=?',(seg['id'],)).fetchone()
        assert row['archived_at'] is None and row['original_name'] == 'SYNTHETIC.wav'
    assert (application.AUDIO / seg['file_name']).is_file()


def test_preview_history_blocks_delete_after_release(site):
    clients, _ = site
    seg = upload(site)
    contributor, csrf = clients['contributor']
    assert contributor.post('/claim',data={'csrf':csrf}).status_code == 200
    assert contributor.get(f"/audio/{seg['id']}").status_code == 200
    assert contributor.post(f"/contribute/{seg['id']}/release",data={'csrf':csrf}).status_code == 200
    admin, token = clients['admin']
    response = admin.post(f"/admin/segments/{seg['id']}/delete",
                          data={'csrf':token,'confirm_code':seg['code']})
    assert 'Suppression interdite' in response.text
    assert 'Supprimer définitivement' not in admin.get(f"/admin/segments/{seg['id']}").text
    assert 'Supprimer définitivement' not in admin.get('/admin').text
    assert (application.AUDIO / seg['file_name']).is_file()


@pytest.mark.parametrize('storage_failure', [False, True])
def test_delete_unannotated_audio_and_storage_failure(site, monkeypatch, storage_failure):
    clients, _ = site
    seg = upload(site)
    if storage_failure:
        def unavailable(*args):
            raise OSError('Synthetic storage failure')
        monkeypatch.setattr(application, 'remove_audio_file', unavailable)
    admin, csrf = clients['admin']
    response = admin.post(f"/admin/segments/{seg['id']}/delete",data={
        'csrf':csrf,'confirm_code':seg['code']})
    assert response.status_code == (503 if storage_failure else 200)
    with application.db() as cx:
        assert bool(cx.execute('SELECT 1 FROM segments WHERE id=?',(seg['id'],)).fetchone()) == storage_failure
        if not storage_failure:
            assert cx.execute("SELECT count(*) FROM audit_logs WHERE action='segment_deleted' AND segment_id IS NULL").fetchone()[0] == 1
    assert (application.AUDIO / seg['file_name']).exists() == storage_failure


def test_curation_and_personal_dashboard(site):
    clients, users = site
    seg = upload(site)
    sid = seg['id']
    contributor, csrf = clients['contributor']
    contributor.post('/claim',data={'csrf':csrf})
    response = contributor.post(f'/contribute/{sid}/save',data={
        'csrf':csrf,'action':'submit','latin':'ana','french':'moi'})
    assert response.status_code == 200
    admin, token = clients['admin']
    reviewer, _ = clients['reviewer']
    other, _ = clients['other']
    assert other.get(f'/audio/{sid}').status_code == 403
    assert reviewer.get(f'/audio/{sid}').status_code == 200
    assert seg['code'] in contributor.get('/').text
    assert seg['code'] not in other.get('/').text
    assert 'Dernière activité' not in other.get('/').text
    assert seg['code'] in admin.get('/admin/segments?q=ana').text
    assert 'Métadonnées invalides' in admin.post(f'/admin/segments/{sid}/metadata',data={
        'csrf':token,'name':'folder\\file.wav'}).text
    assert admin.post(f'/admin/segments/{sid}/metadata',data={
        'csrf':token,'name':'renamed.wav','source_id':'Test source'}).status_code == 200
    assert (application.AUDIO / seg['file_name']).is_file()
    assert admin.post('/admin/segments/bulk-approve',data={
        'csrf':token,'confirmation':'VALIDER','segment_ids':[sid]}).status_code == 200
    assert admin.post(f'/admin/segments/{sid}/correct-approval',data={
        'csrf':token,'latin':'ana corrected','french':'moi corrigé','note':'Correction motivée'}).status_code == 200
    assert admin.get('/admin/export/json').json()[0]['text_french'] == 'moi corrigé'
    with application.db() as cx:
        assert cx.execute('SELECT french FROM translations WHERE segment_id=?',(sid,)).fetchone()[0] == 'moi'
        assert cx.execute('SELECT count(*) FROM reviews WHERE segment_id=?',(sid,)).fetchone()[0] == 2
    assert admin.post(f'/admin/segments/{sid}/archive',data={'csrf':token}).status_code == 200
    assert admin.get('/admin/export/json').json() == []
    assert reviewer.get(f'/audio/{sid}').status_code == 403
    assert admin.get(f'/audio/{sid}').status_code == 200
    assert admin.post(f'/admin/segments/{sid}/restore',data={'csrf':token}).status_code == 200
    assert len(admin.get('/admin/export/json').json()) == 1
    assert admin.post(f'/admin/segments/{sid}/revoke-approval',data={
        'csrf':token,'note':'Traduction à reprendre'}).status_code == 200
    assert admin.get('/admin/export/json').json() == []
    assert 'Traduction à reprendre' in contributor.get('/notifications').text
    assert contributor.post(f'/contribute/{sid}/save',data={
        'csrf':csrf,'action':'submit','latin':'new latin','french':'nouveau français'}).status_code == 200
    with application.db() as cx:
        assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0] == 'submitted'
        assert cx.execute('SELECT count(*) FROM translation_versions WHERE segment_id=?',(sid,)).fetchone()[0] == 2
        assert cx.execute('SELECT last_seen FROM user_presence WHERE user_id=?',(users['contributor'],)).fetchone()[0] > 0


@pytest.mark.parametrize('invalid', ['self', 'inaudible', 'incomplete', 'archived', 'too_many'])
def test_bulk_approval_rejects_entire_invalid_selection(site, invalid):
    clients, users = site
    seg = upload(site)
    with application.db() as cx:
        cx.execute("UPDATE segments SET status='submitted',assigned_to=? WHERE id=?",(users['contributor'],seg['id']))
        cx.execute('''INSERT INTO translations(segment_id,contributor_id,latin,french,submitted_at)
                   VALUES(?,?,?,?,datetime('now'))''',(seg['id'],users['contributor'],'latin','français'))
        second = cx.execute('''INSERT INTO segments(code,original_name,file_name,mime,bytes,status,assigned_to)
                   VALUES(?,?,?,?,?,?,?)''',('SECOND','second.wav','synthetic-second.wav','audio/wav',1,'submitted',users['contributor'])).lastrowid
        cx.execute('''INSERT INTO translations(segment_id,contributor_id,latin,french,reported_inaudible,submitted_at)
                   VALUES(?,?,?,?,?,datetime('now'))''',(second,users['admin'] if invalid=='self' else users['contributor'],
                   'latin','' if invalid=='incomplete' else 'français',int(invalid=='inaudible')))
        if invalid=='archived':
            cx.execute("UPDATE segments SET archived_at=datetime('now') WHERE id=?",(second,))
    admin, token = clients['admin']
    ids = [seg['id'],second] if invalid != 'too_many' else list(range(1,32))
    response = admin.post('/admin/segments/bulk-approve',data={
        'csrf':token,'confirmation':'VALIDER','segment_ids':ids})
    assert response.status_code == (400 if invalid=='too_many' else 409)
    with application.db() as cx:
        assert cx.execute('SELECT count(*) FROM reviews').fetchone()[0] == 0
        assert cx.execute('SELECT status FROM segments WHERE id=?',(seg['id'],)).fetchone()[0] == 'submitted'
