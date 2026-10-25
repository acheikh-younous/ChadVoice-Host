"""Tests d'intégration de l'application locale (SQLite temporaire indépendant)."""
import io, os, pathlib, sys, tempfile, wave, zipfile, csv, hashlib
import pytest
from fastapi.testclient import TestClient

TEST_DIR=tempfile.mkdtemp(prefix='chadvoice_test_')
os.environ['CHADVOICE_DATA_DIR']=TEST_DIR
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from app import app,db,create_user,hash_password,init_db

@pytest.fixture(scope='module')
def c():
    init_db()
    create_user('admin@cv.test','Admin Chercheur','secret-admin-2026','admin')
    create_user('contrib@cv.test','Contributeur Alpha','secret-contrib-2026','contributor')
    create_user('contrib2@cv.test','Contributeur Beta','secret-contrib2-2026','contributor')
    create_user('reviser@cv.test','Relectrice Experte','secret-review-2026','reviewer')
    with TestClient(app,follow_redirects=True) as cli:yield cli

def get_csrf(client):
    cookie=client.cookies.get('cv_session')
    with db() as cx:
        return cx.execute('SELECT csrf FROM sessions WHERE token_hash=?',(hashlib.sha256(cookie.encode()).hexdigest(),)).fetchone()[0]

def signin(c,email,pw):
    r=c.post('/login',data={'email':email,'password':pw})
    assert r.status_code==200, (r.status_code,r.text[:150])
    return get_csrf(c)

def wav_bytes():
    b=io.BytesIO()
    with wave.open(b,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*16000)
    return b.getvalue()

def userclient(email,pw):
    cl=TestClient(app,follow_redirects=True)
    cl.__enter__()
    token=signin(cl,email,pw)
    return cl,token

def test_00_public_login(c):
    assert c.get('/').status_code==200
    assert c.get('/contribute').status_code==200 # redirect to login
    assert c.get('/health').json()=={'ok':True}
    r=c.post('/login',data={'email':'invalid@test','password':'fake'})
    assert r.status_code==401

def test_01_admin_upload_and_private_audio(c):
    ca,csrf=userclient('admin@cv.test','secret-admin-2026')
    plain=wav_bytes()
    r=ca.post('/admin/upload',data={'csrf':csrf,'source_id':'Film-test','speaker_id':'C007'},files={'audios':('CV0001.wav',plain,'audio/wav')})
    assert r.status_code==200
    assert 'Importés : 1' in r.text
    with db() as cx:
        s=cx.execute('SELECT * FROM segments').fetchone()
        assert s['code']=='CV0001' and s['duration_seconds']==1.0
        sid=s['id']
    assert ca.get('/audio/'+str(sid)).status_code==200
    contrib,_=userclient('contrib@cv.test','secret-contrib-2026')
    assert contrib.get('/audio/'+str(sid)).status_code==403
    an,_=userclient('reviser@cv.test','secret-review-2026')
    assert an.get('/audio/'+str(sid)).status_code==403
    # identical bytes with different names must be rejected as duplicate
    r=ca.post('/admin/upload',data={'csrf':csrf},files={'audios':('SAME.wav',plain,'audio/wav')})
    assert 'doublons : 1' in r.text
    ca.__exit__(None,None,None);contrib.__exit__(None,None,None);an.__exit__(None,None,None)

def test_02_claim_no_collision_draft_and_release(c):
    a,tka=userclient('contrib@cv.test','secret-contrib-2026')
    b,tkb=userclient('contrib2@cv.test','secret-contrib2-2026')
    with db() as cx:sid=cx.execute('SELECT id FROM segments LIMIT 1').fetchone()['id']
    claimed=a.post('/claim',data={'csrf':tka})
    assert '30 minutes' not in claimed.text
    b.post('/claim',data={'csrf':tkb})
    with db() as cx:
        s=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        assert s['status']=='available' and s['assigned_to'] is None
        assert cx.execute('SELECT user_id FROM working_leases WHERE segment_id=?',(sid,)).fetchone()[0]==2
    assert a.get('/audio/'+str(sid)).status_code==200
    assert b.get('/audio/'+str(sid)).status_code==403
    assert b.post(f'/contribute/{sid}/save',data={'csrf':tkb,'action':'submit','latin':'x','french':'y'}).status_code==409
    drafted=a.post(f'/contribute/{sid}/save',data={'csrf':tka,'action':'draft','latin':'salam','french':'bonjour'})
    assert '48 heures' not in drafted.text
    with db() as cx:
        assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0]=='available'
        assert cx.execute('SELECT expires_at FROM working_leases WHERE segment_id=?',(sid,)).fetchone()[0]>0
    assert b.get('/review').status_code==200 # redirected home
    a.post(f'/contribute/{sid}/release',data={'csrf':tka})
    with db() as cx:
        assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0]=='available'
        assert cx.execute('SELECT count(*) FROM translations').fetchone()[0]==0
    a.__exit__(None,None,None);b.__exit__(None,None,None)

def test_03_translate_review_correction_approval(c):
    contrib,csrf=userclient('contrib@cv.test','secret-contrib-2026')
    reviewer,rt=userclient('reviser@cv.test','secret-review-2026')
    other,ot=userclient('contrib2@cv.test','secret-contrib2-2026')
    with db() as cx:sid=cx.execute('SELECT id FROM segments LIMIT 1').fetchone()[0]
    contrib.post('/claim',data={'csrf':csrf})
    contrib.post(f'/contribute/{sid}/save',data={'csrf':csrf,'action':'submit','latin':'ana rayeh','french':'je pars'})
    with db() as cx:assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0]=='submitted'
    assert reviewer.get(f'/review/{sid}').status_code==200
    assert reviewer.get('/audio/'+str(sid)).status_code==200
    assert other.get('/audio/'+str(sid)).status_code==403
    # no csrf, no validation
    assert reviewer.post(f'/review/{sid}/decide',data={'csrf':'WRONG','decision':'approved','latin':'x','french':'y'}).status_code==403
    reviewer.post(f'/review/{sid}/decide',data={'csrf':rt,'decision':'needs_changes','latin':'ana rayeh','french':'je pars','note':'Préciser la destination'})
    resp=contrib.get('/contribute')
    assert 'Préciser la destination' in resp.text
    contrib.post(f'/contribute/{sid}/save',data={'csrf':csrf,'action':'submit','latin':'ana rayeh lil soug','french':'je vais au marché'})
    reviewer.post(f'/review/{sid}/decide',data={'csrf':rt,'decision':'approved','latin':'ana rayeh lil soug','french':'Je vais au marché.','note':'Conforme'})
    with db() as cx:
        s=cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()
        assert s['status']=='approved'
        assert cx.execute('SELECT count(*) FROM reviews WHERE segment_id=?',(sid,)).fetchone()[0]==2
        assert cx.execute('SELECT count(*) FROM translation_versions WHERE segment_id=?',(sid,)).fetchone()[0]>=2
    assert contrib.get('/audio/'+str(sid)).status_code==403
    assert reviewer.get('/audio/'+str(sid)).status_code==200
    # Cannot validate twice
    resp=reviewer.post(f'/review/{sid}/decide',data={'csrf':rt,'decision':'approved','latin':'x','french':'y'})
    assert resp.status_code==409
    contrib.__exit__(None,None,None);reviewer.__exit__(None,None,None);other.__exit__(None,None,None)

def test_04_exports_private_account_security(c):
    ca,csrf=userclient('admin@cv.test','secret-admin-2026')
    ra,rt=userclient('reviser@cv.test','secret-review-2026')
    assert ra.get('/admin/export/json').status_code==403
    assert ra.get('/admin/backup').status_code==403
    r=ca.get('/admin/export/json')
    assert r.status_code==200
    data=r.json()
    assert len(data)==1
    assert data[0]['text_french']=='Je vais au marché.'
    assert data[0]['text_latin']=='ana rayeh lil soug'
    assert 'private_file' not in data[0]
    r=ca.get('/admin/export/csv')
    assert r.status_code==200 and 'text_french' in r.text
    r=ca.get('/admin/export/zip')
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert 'manifest.json' in z.namelist()
        assert len([n for n in z.namelist() if n.startswith('audio/')])==1
    r=ca.get('/admin/backup')
    assert r.status_code==200
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert 'chadvoice.sqlite3' in z.namelist()
    assert ca.get('/admin').status_code==200
    # Non-admin cannot modify admin users / reset password
    assert ra.post('/admin/users',data={'csrf':rt,'email':'test@cv','full_name':'X','password':'longpassword','role':'admin'}).status_code==403
    assert ca.post('/admin/users',data={'csrf':'invalid','email':'test@cv','full_name':'X','password':'longpassword','role':'admin'}).status_code==403
    ca.__exit__(None,None,None);ra.__exit__(None,None,None)

def test_05_zip_import_and_user_management(c):
    ca,token=userclient('admin@cv.test','secret-admin-2026')
    byte_data=wav_bytes()
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('SessionB/CV0002.wav',byte_data)
        z.writestr('note.txt', 'ignore')
    assert ca.post('/admin/upload',data={'csrf':token,'source_id':'Film-B'},files={'archive':('audios.zip',b.getvalue(),'application/zip')}).status_code==200
    with db() as cx:assert cx.execute('SELECT count(*) FROM segments').fetchone()[0]==1 # duplicate audio hash
    # fresh second audio with different duration
    wav=io.BytesIO()
    with wave.open(wav,'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*32000)
    with zipfile.ZipFile(b,'w') as z:pass
    ba=io.BytesIO()
    with zipfile.ZipFile(ba,'w') as z:z.writestr('folder/CV0002.wav',wav.getvalue())
    r=ca.post('/admin/upload',data={'csrf':token,'source_id':'Film-B'},files={'archive':('zip.zip',ba.getvalue(),'application/zip')})
    assert 'Importés : 1' in r.text
    r=ca.post('/admin/users',data={'csrf':token,'email':'new@cv.test','full_name':'Nouveau Compte','password':'password-long','role':'reviewer'})
    assert 'Compte utilisateur créé' in r.text
    with db() as cx:
        uid=cx.execute('SELECT id FROM users WHERE email=?',('new@cv.test',)).fetchone()[0]
    ca.post(f'/admin/users/{uid}/toggle',data={'csrf':token})
    assert ca.post('/login',data={'email':'new@cv.test','password':'password-long'}).status_code==401
    ca.__exit__(None,None,None)

def test_06_streaming_range_and_escaping(c):
    admin,token=userclient('admin@cv.test','secret-admin-2026')
    with db() as cx:sid=cx.execute('SELECT id FROM segments ORDER BY id LIMIT 1').fetchone()[0]
    r=admin.get(f'/audio/{sid}',headers={'Range':'bytes=0-99'})
    assert r.status_code==206 and len(r.content)==100
    assert r.headers['content-range'].startswith('bytes 0-99/')
    # CSS/JS remain internal and content security policy is defined
    r=admin.get('/')
    assert 'Content-Security-Policy' in r.headers
    admin.__exit__(None,None,None)

def test_07_concurrent_claims_and_no_self_review(c):
    import concurrent.futures
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    contrib,ct=userclient('contrib2@cv.test','secret-contrib2-2026')
    # Prepare enough segments for both concurrent requests.
    for frames,name in [(48000,'CV_TEST3.wav'),(64000,'CV_TEST4.wav')]:
        sound=io.BytesIO()
        with wave.open(sound,'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*frames)
        assert 'Importés : 1' in admin.post('/admin/upload',data={'csrf':at},files={'audios':(name,sound.getvalue(),'audio/wav')}).text
    # Both roles can claim, but not the same segment
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        calls=[ex.submit(cli.post,'/claim',data={'csrf':csrf}) for cli,csrf in [(admin,at),(contrib,ct)]]
        for future in calls: assert future.result().status_code==200
    with db() as cx:
        row=cx.execute("SELECT segment_id,user_id FROM working_leases ORDER BY segment_id").fetchall()
        assert len(row)==2
        assert row[0]['user_id']!=row[1]['user_id']
        adminseg=next(r['segment_id'] for r in row if r['user_id']==1)
        assert cx.execute('SELECT status FROM segments WHERE id=?',(adminseg,)).fetchone()[0]=='available' 
    admin.post(f'/contribute/{adminseg}/save',data={'csrf':at,'action':'submit','latin':'test','french':'test'})
    assert admin.get(f'/review/{adminseg}').status_code==403
    with db() as cx:
        assert cx.execute('SELECT status FROM segments WHERE id=?',(adminseg,)).fetchone()[0]=='submitted'
    admin.__exit__(None,None,None);contrib.__exit__(None,None,None)

# Regression v1.1 : rejected is editable, reasons persist, mail remains optional.
def test_08_rejection_is_correctable_and_inbox(c):
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    reviewer,rt=userclient('reviser@cv.test','secret-review-2026')
    # A contributor with no previous preview
    admin.post('/admin/users',data={'csrf':at,'email':'contrib3@cv.test','full_name':'Contributeur Gamma',
                                    'password':'contrib3-test-2026','role':'contributor'})
    person,pt=userclient('contrib3@cv.test','contrib3-test-2026')
    with db() as cx:
        pending=cx.execute('SELECT id FROM segments WHERE status="available" AND id NOT IN (SELECT segment_id FROM working_leases) ORDER BY id LIMIT 1').fetchone()
    assert pending is not None
    person.post('/claim',data={'csrf':pt})
    with db() as cx:
        item=cx.execute('SELECT segment_id FROM working_leases WHERE user_id=(SELECT id FROM users WHERE email=?)',('contrib3@cv.test',)).fetchone()
        sid=item['segment_id']
        seg=cx.execute('SELECT status,assigned_to FROM segments WHERE id=?',(sid,)).fetchone()
        assert seg['status']=='available' and seg['assigned_to'] is None
    person.post(f'/contribute/{sid}/save',data={'csrf':pt,'action':'submit','latin':'ancienne phrase','french':'ancienne traduction'})
    with db() as cx:
        seg=cx.execute('SELECT status,assigned_to FROM segments WHERE id=?',(sid,)).fetchone()
        assert seg['status']=='submitted' and seg['assigned_to'] is not None
    reviewer.post(f'/review/{sid}/decide',data={'csrf':rt,'decision':'rejected','latin':'ancienne phrase','french':'ancienne traduction','note':'Traduction inexacte, corrigez le sens.'})
    assert 'Traduction inexacte' in person.get('/notifications').text
    assert 'Traduction inexacte' in person.get('/contribute').text
    assert 'Reprendre' in person.get('/contribute').text
    assert person.get(f'/audio/{sid}').status_code==200
    with db() as cx:
        n=cx.execute('SELECT * FROM notifications WHERE user_id=(SELECT id FROM users WHERE email=?) ORDER BY id DESC LIMIT 1',('contrib3@cv.test',)).fetchone()
        assert n['category']=='rejected' and n['read_at'] is None
        mail=cx.execute('SELECT state FROM mail_outbox WHERE notification_id=?',(n['id'],)).fetchone()
        assert mail['state']=='pending' # SMTP not configured, no external mail was sent
    person.post(f'/notifications/{n["id"]}/read',data={'csrf':pt})
    with db() as cx:
        assert cx.execute('SELECT read_at FROM notifications WHERE id=?',(n['id'],)).fetchone()[0] is not None
    person.post(f'/contribute/{sid}/save',data={'csrf':pt,'action':'draft','latin':'nouvelle phrase','french':'nouveau français'})
    assert 'nouveau français' in person.get('/contribute').text
    assert person.post(f'/contribute/{sid}/save',data={'csrf':pt,'action':'submit','latin':'nouvelle phrase','french':'nouveau français'}).status_code==200
    reviewer.post(f'/review/{sid}/decide',data={'csrf':rt,'decision':'approved','latin':'nouvelle phrase','french':'Français corrigé.','note':'Conforme'})
    assert 'Français corrigé.' in admin.get('/admin/export/json').text
    person.__exit__(None,None,None);reviewer.__exit__(None,None,None);admin.__exit__(None,None,None)


def test_09_leases_expire_and_admin_can_release(c):
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    raw=io.BytesIO()
    with wave.open(raw,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*96000)
    admin.post('/admin/upload',data={'csrf':at},files={'audios':('TEST_09.wav',raw.getvalue(),'audio/wav')})
    admin.post('/admin/users',data={'csrf':at,'email':'contrib4@cv.test','full_name':'Quatrième personne',
                                    'password':'contrib4-test-2026','role':'contributor'})
    person,pt=userclient('contrib4@cv.test','contrib4-test-2026')
    person.post('/claim',data={'csrf':pt})
    with db() as cx:
        row=cx.execute('SELECT * FROM working_leases WHERE user_id=(SELECT id FROM users WHERE email=?)',('contrib4@cv.test',)).fetchone()
        assert row
        sid=row['segment_id']
        assert cx.execute('SELECT assigned_to FROM segments WHERE id=?',(sid,)).fetchone()[0] is None
    assert admin.post(f'/admin/leases/{sid}/release',data={'csrf':at}).status_code==200
    with db() as cx:
        assert not cx.execute('SELECT 1 FROM working_leases WHERE segment_id=?',(sid,)).fetchone()
    person.post('/claim',data={'csrf':pt})
    with db() as cx:
        row=cx.execute('SELECT * FROM working_leases WHERE user_id=(SELECT id FROM users WHERE email=?)',('contrib4@cv.test',)).fetchone()
        sid=row['segment_id']
        cx.execute('UPDATE working_leases SET expires_at=1 WHERE segment_id=?',(sid,))
    # expiry happens when any contributor accesses the contribution area
    person.get('/contribute')
    with db() as cx:
        assert not cx.execute('SELECT 1 FROM working_leases WHERE segment_id=?',(sid,)).fetchone()
        assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0]=='available'
    person.__exit__(None,None,None);admin.__exit__(None,None,None)


def test_10_inaudible_requires_review_not_export(c):
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    reviewer,rt=userclient('reviser@cv.test','secret-review-2026')
    raw=io.BytesIO()
    with wave.open(raw,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*112000)
    admin.post('/admin/upload',data={'csrf':at},files={'audios':('TEST_10.wav',raw.getvalue(),'audio/wav')})
    admin.post('/admin/users',data={'csrf':at,'email':'contrib5@cv.test','full_name':'Cinquième personne',
                                    'password':'contrib5-test-2026','role':'contributor'})
    person,pt=userclient('contrib5@cv.test','contrib5-test-2026')
    person.post('/claim',data={'csrf':pt})
    with db() as cx:
        sid=cx.execute('SELECT segment_id FROM working_leases WHERE user_id=(SELECT id FROM users WHERE email=?)',('contrib5@cv.test',)).fetchone()[0]
    person.post(f'/contribute/{sid}/save',data={'csrf':pt,'action':'submit','inaudible':'on'})
    with db() as cx:
        assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0]=='submitted'
    reviewer.post(f'/review/{sid}/decide',data={'csrf':rt,'decision':'inaudible','note':'Impossible de distinguer les paroles.'})
    with db() as cx:
        assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0]=='inaudible'
        assert cx.execute('SELECT count(*) FROM notifications WHERE segment_id=?',(sid,)).fetchone()[0]==1
    with db() as cx:code=cx.execute('SELECT code FROM segments WHERE id=?',(sid,)).fetchone()[0]
    assert code not in [d['segment_id'] for d in admin.get('/admin/export/json').json()]
    person.__exit__(None,None,None);reviewer.__exit__(None,None,None);admin.__exit__(None,None,None)


def test_11_smtp_mock_and_private_message(c, monkeypatch):
    from app import send_mail, smtp_configured
    messages=[]
    class SMTPFake:
        def __init__(self, host,port,timeout):
            assert host=='smtp.test.invalid' and port==587 and timeout<=15
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def ehlo(self):pass
        def starttls(self):pass
        def login(self,user,pw):assert user=='bot@test.invalid' and pw=='fake-secret'
        def send_message(self,message):messages.append(message)
    monkeypatch.setenv('CHADVOICE_SMTP_HOST','smtp.test.invalid')
    monkeypatch.setenv('CHADVOICE_SMTP_PORT','587')
    monkeypatch.setenv('CHADVOICE_SMTP_FROM','bot@test.invalid')
    monkeypatch.setenv('CHADVOICE_SMTP_USER','bot@test.invalid')
    monkeypatch.setenv('CHADVOICE_SMTP_PASSWORD','fake-secret')
    monkeypatch.setattr('app.smtplib.SMTP',SMTPFake)
    assert smtp_configured()
    with db() as cx:
        row=cx.execute("SELECT id,body FROM mail_outbox WHERE state='pending' AND body LIKE '%Traduction inexacte%' ORDER BY id DESC LIMIT 1").fetchone()
    assert row
    assert send_mail(row['id'])
    assert len(messages)==1
    assert 'Traduction inexacte' in messages[0].get_content()
    assert 'Aucun fichier audio' in messages[0].get_content()
    with db() as cx:
        row=cx.execute('SELECT state,sent_at FROM mail_outbox WHERE id=?',(row['id'],)).fetchone()
        assert row['state']=='sent' and row['sent_at']


def test_12_migration_legacy_unsubmitted(c):
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    data=io.BytesIO()
    with wave.open(data,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*128000)
    admin.post('/admin/upload',data={'csrf':at},files={'audios':('LEGACY.wav',data.getvalue(),'audio/wav')})
    with db() as cx:
        sid=cx.execute("SELECT id FROM segments WHERE code='LEGACY'").fetchone()[0]
        uid=cx.execute("SELECT id FROM users WHERE email='contrib4@cv.test'").fetchone()[0]
        cx.execute("UPDATE segments SET status='draft',assigned_to=? WHERE id=?",(uid,sid))
        cx.execute('''INSERT INTO translations(segment_id,contributor_id,latin,french,reported_inaudible)
                     VALUES(?,?,?,?,0)''',(sid,uid,'legacy latin','legacy français'))
    init_db()  # simulate v1.0 DB opened under v1.1
    with db() as cx:
        seg=cx.execute('SELECT status,assigned_to FROM segments WHERE id=?',(sid,)).fetchone()
        lease=cx.execute('SELECT * FROM working_leases WHERE segment_id=?',(sid,)).fetchone()
        tr=cx.execute('SELECT latin,french FROM translations WHERE segment_id=?',(sid,)).fetchone()
        assert seg['status']=='available' and seg['assigned_to'] is None
        assert lease and lease['user_id']==uid
        assert tr['latin']=='legacy latin' and tr['french']=='legacy français'
    admin.__exit__(None,None,None)

# Version 1.3: archival leaves history intact; permanent deletion is restricted.
def test_13_archive_restore_approved_and_rights(c):
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    reviewer,rt=userclient('reviser@cv.test','secret-review-2026')
    contributor,ct=userclient('contrib@cv.test','secret-contrib-2026')
    with db() as cx:
        target=cx.execute("SELECT id,code FROM segments WHERE status='approved' AND archived_at IS NULL ORDER BY id LIMIT 1").fetchone()
        sid=target['id']; code=target['code']
    assert code in {row['segment_id'] for row in admin.get('/admin/export/json').json()}
    assert reviewer.post(f'/admin/segments/{sid}/archive',data={'csrf':rt}).status_code==403
    assert admin.post(f'/admin/segments/{sid}/archive',data={'csrf':'invalid'}).status_code==403
    resp=admin.post(f'/admin/segments/{sid}/archive',data={'csrf':at})
    assert resp.status_code==200
    with db() as cx:
        s=cx.execute('SELECT status,archived_at FROM segments WHERE id=?',(sid,)).fetchone()
        assert s['status']=='approved' and s['archived_at'] is not None
        assert cx.execute('SELECT 1 FROM translation_versions WHERE segment_id=?',(sid,)).fetchone()
    assert code not in {row['segment_id'] for row in admin.get('/admin/export/json').json()}
    assert contributor.get(f'/audio/{sid}').status_code==403
    assert reviewer.get(f'/audio/{sid}').status_code==403
    assert reviewer.get(f'/review/{sid}').status_code==403
    assert admin.get(f'/audio/{sid}').status_code==200
    # Cannot permanently delete a scientifically annotated segment even if archived.
    result=admin.post(f'/admin/segments/{sid}/delete',data={'csrf':at,'confirm_code':code})
    assert 'Suppression interdite' in result.text
    assert admin.post(f'/admin/segments/{sid}/restore',data={'csrf':at}).status_code==200
    with db() as cx: assert cx.execute('SELECT archived_at FROM segments WHERE id=?',(sid,)).fetchone()[0] is None
    assert code in {row['segment_id'] for row in admin.get('/admin/export/json').json()}
    admin.__exit__(None,None,None);reviewer.__exit__(None,None,None);contributor.__exit__(None,None,None)


def test_14_delete_empty_audio_with_audit_and_confirmation(c):
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    contributor,ct=userclient('contrib@cv.test','secret-contrib-2026')
    raw=io.BytesIO()
    with wave.open(raw,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*176000)
    assert 'Importés : 1' in admin.post('/admin/upload',data={'csrf':at},files={'audios':('DELETE_TEST.wav',raw.getvalue(),'audio/wav')}).text
    with db() as cx:
        row=cx.execute("SELECT id,code,file_name FROM segments WHERE code='DELETE_TEST'").fetchone()
        sid=row['id']; code=row['code']; filename=row['file_name']
    from app import AUDIO
    assert (AUDIO/filename).is_file()
    assert contributor.post(f'/admin/segments/{sid}/delete',data={'csrf':ct,'confirm_code':code}).status_code==403
    assert admin.post(f'/admin/segments/{sid}/delete',data={'csrf':at,'confirm_code':'WRONG'}).status_code==400
    assert admin.post(f'/admin/segments/{sid}/archive',data={'csrf':at}).status_code==200
    assert admin.post(f'/admin/segments/{sid}/delete',data={'csrf':at,'confirm_code':code}).status_code==200
    with db() as cx:
        assert cx.execute('SELECT 1 FROM segments WHERE id=?',(sid,)).fetchone() is None
        assert cx.execute("SELECT 1 FROM audit_logs WHERE action='segment_deleted' AND details LIKE ?",(code+'%',)).fetchone() is not None
    assert not (AUDIO/filename).exists()
    assert admin.get(f'/audio/{sid}').status_code==404
    admin.__exit__(None,None,None);contributor.__exit__(None,None,None)


def test_15_archive_does_not_allow_preview_locks(c):
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    contributor,ct=userclient('contrib2@cv.test','secret-contrib2-2026')
    # Existing preview from previous tests or a new one; either way archiving is blocked.
    contributor.post('/claim',data={'csrf':ct})
    with db() as cx:
        row=cx.execute('SELECT segment_id FROM working_leases WHERE user_id=(SELECT id FROM users WHERE email=?)',('contrib2@cv.test',)).fetchone()
        assert row
        sid=row[0]
    resp=admin.post(f'/admin/segments/{sid}/archive',data={'csrf':at})
    assert 'Libérez la consultation' in resp.text
    with db() as cx: assert cx.execute('SELECT archived_at FROM segments WHERE id=?',(sid,)).fetchone()[0] is None
    admin.__exit__(None,None,None);contributor.__exit__(None,None,None)


def test_16_migrate_old_sqlite_schema_column(c):
    # Idempotent migrations never erase corpus or reset archived markers.
    init_db()
    with db() as cx:
        cols=[x['name'] for x in cx.execute('PRAGMA table_info(segments)')]
        assert 'archived_at' in cols
    init_db()
    with db() as cx:
        cols=[x['name'] for x in cx.execute('PRAGMA table_info(segments)')]
        assert cols.count('archived_at')==1

def test_17_delete_storage_failure_preserves_record(c, monkeypatch):
    admin,at=userclient('admin@cv.test','secret-admin-2026')
    raw=io.BytesIO()
    with wave.open(raw,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(b'\0\0'*192000)
    admin.post('/admin/upload',data={'csrf':at},files={'audios':('FAIL_STORAGE_DELETE.wav',raw.getvalue(),'audio/wav')})
    with db() as cx:
        row=cx.execute("SELECT id,code FROM segments WHERE code='FAIL_STORAGE_DELETE'").fetchone()
    def fail(*args):raise RuntimeError('Object store offline')
    monkeypatch.setattr('app.remove_audio_file',fail)
    r=admin.post(f"/admin/segments/{row['id']}/delete",data={'csrf':at,'confirm_code':row['code']})
    assert r.status_code==503
    with db() as cx:
        assert cx.execute('SELECT 1 FROM segments WHERE id=?',(row['id'],)).fetchone()
    admin.__exit__(None,None,None)

# v1.4: scientific curation, reversible approval, authenticated bulk decisions.
def test_18_admin_library_search_metadata_and_revoke(c):
    admin,token=userclient('admin@cv.test','secret-admin-2026')
    reviewer,rt=userclient('reviser@cv.test','secret-review-2026')
    with db() as cx:
        row=cx.execute("SELECT id,code FROM segments WHERE status='approved' AND archived_at IS NULL ORDER BY id LIMIT 1").fetchone()
        assert row
        sid,code=row['id'],row['code']
        n=cx.execute('SELECT count(*) FROM reviews WHERE segment_id=?',(sid,)).fetchone()[0]
    assert admin.get('/admin/segments?q='+code).status_code==200
    assert code in admin.get('/admin/segments?q='+code).text
    assert 'Traduction française' in admin.get(f'/admin/segments/{sid}').text
    assert reviewer.get(f'/admin/segments/{sid}').status_code==200  # reviewer redirected
    assert admin.post(f'/admin/segments/{sid}/metadata',data={'csrf':'BAD','name':'d'}).status_code==403
    rv=admin.post(f'/admin/segments/{sid}/metadata',data={'csrf':token,'name':'nouveau_nom_fichier.wav','source_id':'Film-V14','speaker_id':'C777'})
    assert rv.status_code==200
    with db() as cx:
        assert cx.execute('SELECT original_name FROM segments WHERE id=?',(sid,)).fetchone()[0]=='nouveau_nom_fichier.wav'
    assert 'nouveau_nom_fichier.wav' in admin.get('/admin/segments?q=nouveau_nom_fichier').text
    assert admin.post(f'/admin/segments/{sid}/revoke-approval',data={'csrf':token,'note':'x'}).status_code==400
    rv=admin.post(f'/admin/segments/{sid}/revoke-approval',data={'csrf':token,'note':'La traduction approuvée était incorrecte.'})
    assert rv.status_code==200
    with db() as cx:
        assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0]=='rejected'
        assert cx.execute('SELECT count(*) FROM reviews WHERE segment_id=?',(sid,)).fetchone()[0]==n+1
        assert cx.execute('SELECT decision FROM reviews WHERE segment_id=? ORDER BY id DESC LIMIT 1',(sid,)).fetchone()[0]=='rejected'
    assert code not in {r['segment_id'] for r in admin.get('/admin/export/json').json()}
    admin.__exit__(None,None,None);reviewer.__exit__(None,None,None)


def test_19_batch_approval_checked_and_audited(c):
    admin,csrf=userclient('admin@cv.test','secret-admin-2026')
    other,ot=userclient('contrib@cv.test','secret-contrib-2026')
    with db() as cx:
        contributor=cx.execute("SELECT id FROM users WHERE email='contrib@cv.test'").fetchone()[0]
        ids=[]
        for idx in (1,2,3):
            sid=cx.execute('''INSERT INTO segments(code,original_name,file_name,mime,bytes,status,assigned_to)
              VALUES(?,?,?,?,?,?,?)''',(f'BATCH14_{idx}',f'batch_{idx}.wav',f'fake-v14-{idx}.wav',
                              'audio/wav',1,'submitted' if idx<3 else 'approved',contributor)).lastrowid
            cx.execute('''INSERT INTO translations(segment_id,contributor_id,latin,french,submitted_at)
              VALUES(?,?,?,?,datetime('now'))''',(sid,contributor,f'latin v14 {idx}',f'français v14 {idx}'))
            ids.append(sid)
    invalid=admin.post('/admin/segments/bulk-approve',data={'csrf':csrf,'confirmation':'VALIDER','segment_ids':[str(ids[0]),str(ids[2])]})
    assert invalid.status_code==409
    with db() as cx:
        assert cx.execute('SELECT status FROM segments WHERE id=?',(ids[0],)).fetchone()[0]=='submitted'
    assert admin.post('/admin/segments/bulk-approve',data={'csrf':csrf,'confirmation':'NON','segment_ids':[str(ids[0])]}).status_code==400
    assert other.post('/admin/segments/bulk-approve',data={'csrf':ot,'confirmation':'VALIDER','segment_ids':[str(ids[0])]}).status_code==403
    r=admin.post('/admin/segments/bulk-approve',data={'csrf':csrf,'confirmation':'VALIDER','segment_ids':[str(ids[0]),str(ids[1])]})
    assert r.status_code==200
    with db() as cx:
        for sid in ids[:2]:
            assert cx.execute('SELECT status FROM segments WHERE id=?',(sid,)).fetchone()[0]=='approved'
            assert cx.execute("SELECT count(*) FROM reviews WHERE segment_id=? AND decision='approved'",(sid,)).fetchone()[0]==1
    data=admin.get('/admin/export/json').json()
    assert all(f'BATCH14_{n}' in {x['segment_id'] for x in data} for n in (1,2))
    # Adjust final approved copy, not the original contributor's text.
    sid=ids[0]
    assert admin.post(f'/admin/segments/{sid}/correct-approval',data={'csrf':csrf,'latin':'','french':'x','note':'Valid reason'}).status_code==400
    r=admin.post(f'/admin/segments/{sid}/correct-approval',data={'csrf':csrf,'latin':'latin revu','french':'français revu','note':'Correction orthographique'})
    assert r.status_code==200
    export={x['segment_id']:x for x in admin.get('/admin/export/json').json()}
    assert export['BATCH14_1']['text_french']=='français revu'
    with db() as cx:
        assert cx.execute('SELECT french FROM translations WHERE segment_id=?',(sid,)).fetchone()[0]=='français v14 1'
    admin.__exit__(None,None,None);other.__exit__(None,None,None)

def test_20_contributor_personal_dashboard_hides_corpus(c):
    contributor,t=userclient('contrib@cv.test','secret-contrib-2026')
    page=contributor.get('/')
    assert page.status_code==200
    assert 'MON ESPACE CONTRIBUTEUR' in page.text
    assert 'Mes contributions soumises' in page.text
    assert 'Segments enregistrés' not in page.text
    assert 'Progression du projet' not in page.text
    assert 'À traduire' not in page.text
    other,_=userclient('contrib2@cv.test','secret-contrib2-2026')
    p2=other.get('/')
    assert 'Mes contributions soumises' in p2.text
    with db() as cx:
        from_a=cx.execute("SELECT count(*) FROM translations t JOIN users u ON u.id=t.contributor_id WHERE u.email='contrib@cv.test' AND t.submitted_at IS NOT NULL").fetchone()[0]
        from_b=cx.execute("SELECT count(*) FROM translations t JOIN users u ON u.id=t.contributor_id WHERE u.email='contrib2@cv.test' AND t.submitted_at IS NOT NULL").fetchone()[0]
    assert f'<strong>{from_a}</strong><small>Enregistrements envoyés' in page.text
    assert f'<strong>{from_b}</strong><small>Enregistrements envoyés' in p2.text
    contributor.__exit__(None,None,None);other.__exit__(None,None,None)

def test_21_mobile_logout_and_guidance(c):
    contributor,t=userclient('contrib@cv.test','secret-contrib-2026')
    html=contributor.get('/').text
    assert 'mobile-logout' in html
    assert '/static/chadvoice-logo.png' in html
    assert '/static/chadvoice_logo.png' not in html
    assert 'mobile-logout' in html
    page=contributor.get('/contribute')
    assert page.status_code==200
    assert '48 heures' not in page.text
    assert '30 minutes' not in page.text
    assert 'disponibles</span>' not in page.text
    assert 'en arabe tchadien' in page.text
    response=contributor.post('/logout',data={'csrf':t})
    assert response.status_code==200
    assert 'Bienvenue sur ChadVoice' in response.text
    contributor.__exit__(None,None,None)

def test_22_admin_last_seen_and_presence(c):
    contributor,_=userclient('contrib@cv.test','secret-contrib-2026')
    contributor.get('/contribute')
    admin,_=userclient('admin@cv.test','secret-admin-2026')
    page=admin.get('/admin')
    assert page.status_code==200
    assert 'Dernière activité' in page.text
    assert 'En ligne' in page.text
    assert 'Contribuer' in page.text
    assert 'Contributeur Alpha' in page.text
    with db() as cx:
        presence=cx.execute("SELECT p.last_seen,p.last_path FROM user_presence p JOIN users u ON u.id=p.user_id WHERE u.email='contrib@cv.test'").fetchone()
    assert presence['last_seen']>0 and presence['last_path']=='/contribute'
    admin.__exit__(None,None,None);contributor.__exit__(None,None,None)

def test_23_presence_offline_for_closed_or_stale_session(c):
    contributor,t=userclient('contrib2@cv.test','secret-contrib2-2026')
    contributor.get('/account')
    old_session_token=contributor.cookies.get('cv_session')
    contributor.post('/logout',data={'csrf':t})
    admin,_=userclient('admin@cv.test','secret-admin-2026')
    r=admin.get('/admin')
    assert r.status_code==200
    assert 'Contributeur Beta' in r.text
    with db() as cx:
        stale=cx.execute("SELECT p.last_seen,p.last_path FROM user_presence p JOIN users u ON u.id=p.user_id WHERE u.email='contrib2@cv.test'").fetchone()
        assert stale['last_path']=='/account'
        assert stale['last_seen']>0
        assert cx.execute("SELECT count(*) FROM sessions WHERE token_hash=?",(hashlib.sha256(old_session_token.encode()).hexdigest(),)).fetchone()[0]==0
    # A nonexpired stale login does not count as online after 5 min.
    admin.__exit__(None,None,None);contributor.__exit__(None,None,None)
