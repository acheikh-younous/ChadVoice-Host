"""User/code/Excel CSV regressions on isolated SQLite and optional local PostgreSQL."""
import csv
import io
import re

import pytest
from fastapi.testclient import TestClient

import app as application
import postgres_backend
from test_v151 import site, upload


def temporary_password(response):
    assert response.status_code == 200
    assert 'no-store' in response.headers['cache-control']
    match = re.search(r'<p style="[^"]+">([0-9]{10})</p>', response.text)
    assert match
    return match.group(1)


def test_existing_accounts_survive_additive_migration(site):
    clients, users = site
    with application.db() as cx:
        before = dict(cx.execute('SELECT * FROM users WHERE id=?',(users['contributor'],)).fetchone())
        sessions = cx.execute('SELECT count(*) FROM sessions').fetchone()[0]
        cx.execute('ALTER TABLE users DROP COLUMN must_change_password')
    if application.DB_URL:
        postgres_backend.init_postgres()
        postgres_backend.init_postgres()
    else:
        application.init_db()
        application.init_db()
    with application.db() as cx:
        after = dict(cx.execute('SELECT * FROM users WHERE id=?',(users['contributor'],)).fetchone())
        assert after == before
        assert cx.execute('SELECT count(*) FROM sessions').fetchone()[0] == sessions
    member,_ = clients['contributor']
    assert member.get('/').status_code == 200
    assert 'MON ESPACE CONTRIBUTEUR' in member.get('/').text
    with TestClient(application.app) as fresh:
        response = fresh.post('/login',data={'email':'contributor@test','password':'test-password-2026'},follow_redirects=False)
        assert response.status_code == 303 and response.headers['location'] == '/'


def test_temporary_password_lifecycle_and_no_credential_exports(site):
    clients, _ = site
    admin, csrf = clients['admin']
    response = admin.post('/admin/users',data={
        'csrf':csrf,'email':'temporary@test','full_name':'Élodie Test','role':'contributor'})
    password = temporary_password(response)
    with application.db() as cx:
        row = dict(cx.execute("SELECT * FROM users WHERE email='temporary@test'").fetchone())
        assert row['must_change_password'] == 1
        assert application.check_password(password,row['pw_hash'])
        assert password not in row['pw_hash']
        assert password not in str([dict(r) for r in cx.execute('SELECT * FROM audit_logs')])
    for path in ['/admin/users','/admin/users/export.csv',f"/admin/users/{row['id']}"]:
        content = admin.get(path).text
        assert password not in content and row['pw_hash'] not in content
    with TestClient(application.app,follow_redirects=True) as member:
        assert member.post('/login',data={'email':'temporary@test','password':password}).url.path == '/account'
        import hashlib
        with application.db() as cx:
            token = cx.execute('SELECT csrf FROM sessions WHERE token_hash=?',
                               (hashlib.sha256(member.cookies['cv_session'].encode()).hexdigest(),)).fetchone()[0]
        for path in ['/','/contribute','/admin/users/export.csv',f"/audio/1"]:
            assert member.get(path,follow_redirects=False).headers['location'].startswith('/account')
        assert member.post('/claim',data={'csrf':token},follow_redirects=False).headers['location'].startswith('/account')
        assert member.post('/account/password',data={'csrf':token,'old':password,'new':password}).status_code == 400
        assert member.post('/account/password',data={'csrf':'invalid','old':password,'new':'personal-password-2026'}).status_code == 403
        assert member.post('/account/password',data={'csrf':token,'old':password,'new':'personal-password-2026'}).status_code == 200
        assert 'MON ESPACE CONTRIBUTEUR' in member.get('/').text
        assert member.get('/admin/users/export.csv').status_code == 403
    with application.db() as cx:
        assert cx.execute('SELECT must_change_password FROM users WHERE id=?',(row['id'],)).fetchone()[0] == 0
    with TestClient(application.app) as fresh:
        assert fresh.post('/login',data={'email':'temporary@test','password':password}).status_code == 401
        assert fresh.post('/login',data={'email':'temporary@test','password':'personal-password-2026'},follow_redirects=False).status_code == 303


def test_generated_reset_invalidates_sessions_and_requires_change(site):
    clients, users = site
    admin,csrf = clients['admin']
    member,_ = clients['contributor']
    password = temporary_password(admin.post(f"/admin/users/{users['contributor']}/generate-password",data={'csrf':csrf}))
    assert member.get('/',follow_redirects=False).headers['location'] == '/login'
    with application.db() as cx:
        row = cx.execute('SELECT * FROM users WHERE id=?',(users['contributor'],)).fetchone()
        assert application.check_password(password,row['pw_hash']) and row['must_change_password'] == 1
        assert cx.execute('SELECT count(*) FROM sessions WHERE user_id=?',(users['contributor'],)).fetchone()[0] == 0
        assert password not in str([dict(r) for r in cx.execute('SELECT * FROM audit_logs')])


@pytest.mark.parametrize('role',['contributor','reviewer','admin'])
def test_user_and_code_mutations_require_admin_and_csrf(site,role):
    clients,users = site
    segment = upload(site)
    client,csrf = clients[role]
    token = 'invalid' if role=='admin' else csrf
    for path,data in [('/admin/users',{'email':'new@test','full_name':'New','role':'admin'}),
        (f"/admin/users/{users['other']}/update",{'email':'changed@test','full_name':'Changed','role':'admin'}),
        (f"/admin/users/{users['other']}/generate-password",{}),
        (f"/admin/users/{users['other']}/password",{'password':'malicious-password'}),
        (f"/admin/users/{users['other']}/toggle",{}),
        (f"/admin/segments/{segment['id']}/code",{'new_code':'CV12345678'})]:
        assert client.post(path,data={'csrf':token,**data}).status_code == 403
    if role!='admin':
        for path in ['/admin/users',f"/admin/users/{users['other']}"]:
            assert client.get(path,follow_redirects=False).status_code == 303
        assert client.get('/admin/users/export.csv').status_code == 403
    with TestClient(application.app) as anonymous:
        assert anonymous.get('/admin/users/export.csv').status_code == 403
    with application.db() as cx:
        row = cx.execute('SELECT * FROM users WHERE id=?',(users['other'],)).fetchone()
        assert row['email']=='other@test' and row['role']=='contributor' and row['active']==1


def test_user_updates_roles_activation_and_history(site):
    clients,users = site
    segment = upload(site)
    member,token = clients['contributor']
    member.post('/claim',data={'csrf':token})
    assert member.post(f"/contribute/{segment['id']}/save",data={
        'csrf':token,'action':'submit','latin':'latin','french':'français'}).status_code == 200
    admin,csrf = clients['admin']
    uid = users['contributor']
    assert admin.post(f'/admin/users/{uid}/update',data={
        'csrf':csrf,'email':'OTHER@test','full_name':'Élodie Test','role':'reviewer'}).status_code == 409
    assert admin.post(f"/admin/users/{users['admin']}/update",data={
        'csrf':csrf,'email':'admin@test','full_name':'Admin','role':'contributor'}).status_code == 403
    assert admin.post(f'/admin/users/{uid}/update',data={
        'csrf':csrf,'email':'UPDATED@test','full_name':'Élodie Test','role':'reviewer'}).status_code == 200
    assert member.get('/',follow_redirects=False).headers['location']=='/login'
    assert admin.post(f'/admin/users/{uid}/toggle',data={'csrf':csrf}).status_code == 200
    with TestClient(application.app) as fresh:
        assert fresh.post('/login',data={'email':'updated@test','password':'test-password-2026'}).status_code == 401
    assert admin.post(f'/admin/users/{uid}/toggle',data={'csrf':csrf}).status_code == 200
    with TestClient(application.app) as fresh:
        assert fresh.post('/login',data={'email':'updated@test','password':'test-password-2026'},follow_redirects=False).status_code == 303
    assert admin.post(f"/admin/users/{users['admin']}/toggle",data={'csrf':csrf}).status_code == 200
    with application.db() as cx:
        row = cx.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone()
        assert row['role']=='reviewer' and row['active']==1 and row['full_name']=='Élodie Test'
        assert application.check_password('test-password-2026',row['pw_hash'])
        assert cx.execute('SELECT contributor_id FROM translations WHERE segment_id=?',(segment['id'],)).fetchone()[0]==uid
        assert cx.execute('SELECT count(*) FROM translation_versions WHERE segment_id=?',(segment['id'],)).fetchone()[0]==1
        assert cx.execute('SELECT active FROM users WHERE id=?',(users['admin'],)).fetchone()[0]==1


def test_audio_code_unique_and_preserves_audio_annotations_and_export(site):
    clients,users = site
    segment = upload(site)
    admin,csrf = clients['admin']
    sid = segment['id']
    content = (application.AUDIO/segment['file_name']).read_bytes()
    with application.db() as cx:
        cx.execute("UPDATE segments SET status='approved',assigned_to=? WHERE id=?",(users['contributor'],sid))
        cx.execute('INSERT INTO translations(segment_id,contributor_id,latin,french) VALUES(?,?,?,?)',
                   (sid,users['contributor'],'original latin','original français'))
        cx.execute('INSERT INTO reviews(segment_id,reviewer_id,decision,final_latin,final_french) VALUES(?,?,?,?,?)',
                   (sid,users['reviewer'],'approved','latin','français'))
        cx.execute('INSERT INTO segments(code,original_name,file_name,mime,bytes) VALUES(?,?,?,?,?)',
                   ('CV9999','second.wav','not-a-real-object.wav','audio/wav',1))
    assert admin.post(f'/admin/segments/{sid}/code',data={'csrf':csrf,'new_code':'CV9999'}).status_code == 409
    for code in ['CV123','CV123456789','../CV1234','CV１２３４','ABC']:
        assert admin.post(f'/admin/segments/{sid}/code',data={'csrf':csrf,'new_code':code}).status_code == 400
    assert admin.post(f'/admin/segments/{sid}/code',data={'csrf':csrf,'new_code':' cv12345678 '}).status_code == 200
    with application.db() as cx:
        updated = dict(cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone())
        assert updated['code']=='CV12345678'
        assert updated['file_name']==segment['file_name'] and updated['id']==sid
        assert cx.execute('SELECT latin FROM translations WHERE segment_id=?',(sid,)).fetchone()[0]=='original latin'
        assert cx.execute('SELECT count(*) FROM reviews WHERE segment_id=?',(sid,)).fetchone()[0]==1
        assert cx.execute("SELECT details FROM audit_logs WHERE action='admin_segment_code_changed'").fetchone()[0]==segment['code']+' -> CV12345678'
    assert (application.AUDIO/segment['file_name']).read_bytes()==content
    assert admin.get(f'/audio/{sid}').content==content
    assert admin.get('/admin/export/json').json()[0]['segment_id']=='CV12345678'
    assert 'CV12345678' in admin.get('/admin/export/csv').text


def test_excel_csv_format_formula_protection_and_no_secrets(site):
    clients,users = site
    admin,csrf = clients['admin']
    with application.db() as cx:
        cx.execute('UPDATE users SET full_name=? WHERE id=?',('=HYPERLINK("https://example.invalid")',users['contributor']))
        cx.execute('UPDATE users SET full_name=? WHERE id=?',('Élodie; "Test"\nSuite',users['other']))
        secrets = [r['pw_hash'] for r in cx.execute('SELECT pw_hash FROM users')]
        secrets += [r['csrf'] for r in cx.execute('SELECT csrf FROM sessions')]
    response = admin.get('/admin/users/export.csv')
    assert response.content.startswith(b'\xef\xbb\xbf')
    assert b'\r\n' in response.content
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['x-content-type-options']=='nosniff'
    assert 'attachment' in response.headers['content-disposition']
    rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')),delimiter=';'))
    assert len(rows)==len(users)+(1 if application.DB_URL else 0)
    assert set(rows[0])=={'id','nom_complet','email','role','statut','contributions','revisions','changement_mot_de_passe_requis'}
    by_email = {r['email']:r for r in rows}
    assert by_email['contributor@test']['nom_complet'].startswith("'=HYPERLINK")
    assert by_email['other@test']['nom_complet']=='Élodie; "Test"\nSuite'
    for value in secrets:
        assert value not in response.text
    for field in ['pw_hash','csrf','token_hash','temporary_password','private_file','file_name']:
        assert field not in response.text
    for formula in ['=1+1','+1+1','-1+1','@SUM(1;2)','\t=1+1','\r=1+1']:
        with application.db() as cx:
            cx.execute('UPDATE users SET full_name=? WHERE id=?',(formula,users['contributor']))
        output=admin.get('/admin/users/export.csv')
        parsed=list(csv.DictReader(io.StringIO(output.content.decode('utf-8-sig')),delimiter=';'))
        assert next(row for row in parsed if row['email']=='contributor@test')['nom_complet']=="'"+formula


def test_temporary_password_generator_uses_secure_independent_digits(monkeypatch):
    values=iter([0,1,2,3,4,5,6,7,8,9,9,8,7,6,5,4,3,2,1,0])
    calls=[]
    def digit(bound):
        calls.append(bound)
        return next(values)
    monkeypatch.setattr(application.secrets,'randbelow',digit)
    assert application.generate_temp_password()=='0123456789'
    assert application.generate_temp_password()=='9876543210'
    assert calls==[10]*20


def test_password_length_limits_apply_to_account_and_admin_reset(site):
    clients,users=site
    member,csrf=clients['contributor']
    assert member.post('/account/password',data={
        'csrf':csrf,'old':'test-password-2026','new':'x'*257}).status_code==400
    admin,token=clients['admin']
    response=admin.post(f"/admin/users/{users['other']}/password",data={'csrf':token,'password':'x'*257})
    assert response.status_code==400
    with application.db() as cx:
        for uid in [users['contributor'],users['other']]:
            assert application.check_password('test-password-2026',cx.execute('SELECT pw_hash FROM users WHERE id=?',(uid,)).fetchone()[0])


def test_unknown_users_are_not_reported_as_success(site):
    clients,_=site
    admin,csrf=clients['admin']
    for suffix,data in [('toggle',{}),('password',{'password':'new-password-2026'}),
                        ('generate-password',{}),('update',{'email':'missing@test','full_name':'Missing','role':'contributor'})]:
        assert admin.post('/admin/users/999999/'+suffix,data={'csrf':csrf,**data}).status_code==404
    assert admin.get('/admin/users/999999').status_code==404


def test_manual_reset_requires_change_and_revokes_all_sessions(site):
    clients,users=site
    admin,csrf=clients['admin']
    member,_=clients['contributor']
    assert admin.post(f"/admin/users/{users['contributor']}/password",data={
        'csrf':csrf,'password':'new-temporary-2026'}).status_code==200
    assert member.get('/',follow_redirects=False).headers['location']=='/login'
    with TestClient(application.app,follow_redirects=True) as fresh:
        response=fresh.post('/login',data={'email':'contributor@test','password':'new-temporary-2026'})
        assert response.url.path=='/account'
        assert 'Veuillez définir votre mot de passe personnel' in response.text
    with application.db() as cx:
        assert cx.execute('SELECT must_change_password FROM users WHERE id=?',(users['contributor'],)).fetchone()[0]==1
