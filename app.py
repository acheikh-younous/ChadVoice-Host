"""ChadVoice: local private corpus collection and expert review system."""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import mimetypes
import re
import smtplib
from email.message import EmailMessage
from fastapi import BackgroundTasks
import os
import secrets
import shutil
import sqlite3
import tempfile
import time
import unicodedata
import uuid
import wave
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.background import BackgroundTask
from postgres_backend import DB_URL, pg_db, init_postgres
from object_storage import MODE as STORAGE_MODE, store_audio_bytes, remove_audio_file, audio_response_payload, iter_body, copy_audio_to_zip


ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('CHADVOICE_DATA_DIR', ROOT / 'private_data')).resolve()
AUDIO = DATA / 'audio'
DB = DATA / 'chadvoice.sqlite3'
DATA.mkdir(parents=True, exist_ok=True)
AUDIO.mkdir(parents=True, exist_ok=True)
LEASE = 30 * 60  # temporary preview; permanent owner only after submission
DRAFT_LEASE = 48 * 3600
SESSION_TTL = 12 * 3600
SEGMENT_CODE_PATTERN = re.compile(r'CV[0-9]{4,8}\Z')
MAX_AUDIO = 20 * 1024 * 1024
MAX_ARCHIVE = 1024 * 1024 * 1024
ACCEPT = {'.wav': 'audio/wav', '.mp3': 'audio/mpeg', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4', '.webm': 'audio/webm'}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
jinja = Environment(loader=FileSystemLoader(ROOT/'templates'), autoescape=select_autoescape(['html','xml']))

def unicode_search_fold(value):
    if value is None:
        return None
    return unicodedata.normalize('NFC', str(value)).casefold()


@contextmanager
def db():
    if DB_URL:
        with pg_db() as cx:
            yield cx
        return
    cx = sqlite3.connect(DB, timeout=20, isolation_level=None)
    cx.row_factory = sqlite3.Row
    # SQLite LOWER is ASCII-only: register a Unicode-aware normalization function.
    cx.create_function('cv_search_fold', 1, unicode_search_fold, deterministic=True)
    cx.execute('PRAGMA foreign_keys=ON')
    cx.execute('PRAGMA busy_timeout=20000')
    try:
        yield cx
        if cx.in_transaction:
            cx.commit()
    except BaseException:
        if cx.in_transaction:
            cx.rollback()
        raise
    finally:
        cx.close()

def init_db():
    if DB_URL:
        if STORAGE_MODE not in ('s3','r2'):
            raise RuntimeError('Cloud database requires private S3 audio storage; set CHADVOICE_AUDIO_STORAGE=s3')
        init_postgres()
        return
    with db() as cx:
        cx.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT NOT NULL UNIQUE COLLATE NOCASE,
          full_name TEXT NOT NULL,
          pw_hash TEXT NOT NULL,
          must_change_password INTEGER NOT NULL DEFAULT 0,
          role TEXT NOT NULL CHECK(role IN ('admin','reviewer','contributor')),
          active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS user_presence (
          user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
          last_seen INTEGER NOT NULL DEFAULT 0,
          last_path TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          csrf TEXT NOT NULL, expiry INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS segments (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL UNIQUE,
          original_name TEXT NOT NULL,
          file_name TEXT NOT NULL UNIQUE,
          mime TEXT NOT NULL,
          bytes INTEGER NOT NULL,
          duration_seconds REAL,
          speaker_id TEXT NOT NULL DEFAULT '',
          source_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','assigned','draft','submitted','needs_changes','approved','rejected','inaudible')),
          assigned_to INTEGER REFERENCES users(id),
          expires_at INTEGER,
          archived_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS translations (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          segment_id INTEGER NOT NULL UNIQUE REFERENCES segments(id) ON DELETE CASCADE,
          contributor_id INTEGER NOT NULL REFERENCES users(id),
          latin TEXT NOT NULL DEFAULT '',
          french TEXT NOT NULL DEFAULT '',
          reported_inaudible INTEGER NOT NULL DEFAULT 0,
          submitted_at TEXT,
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS translation_versions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          segment_id INTEGER NOT NULL REFERENCES segments(id),
          contributor_id INTEGER NOT NULL REFERENCES users(id),
          latin TEXT NOT NULL DEFAULT '', french TEXT NOT NULL DEFAULT '',
          reported_inaudible INTEGER NOT NULL DEFAULT 0,
          stage TEXT NOT NULL CHECK(stage IN ('draft','submit')),
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS reviews (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          segment_id INTEGER NOT NULL REFERENCES segments(id),
          reviewer_id INTEGER NOT NULL REFERENCES users(id),
          decision TEXT NOT NULL CHECK(decision IN ('approved','needs_changes','rejected','inaudible')),
          final_latin TEXT NOT NULL DEFAULT '',
          final_french TEXT NOT NULL DEFAULT '',
          note TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS working_leases (
          segment_id INTEGER PRIMARY KEY REFERENCES segments(id) ON DELETE CASCADE,
          user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
          expires_at INTEGER NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS notifications (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          segment_id INTEGER REFERENCES segments(id),
          category TEXT NOT NULL,
          title TEXT NOT NULL,
          message TEXT NOT NULL,
          read_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id,read_at,id);
        CREATE TABLE IF NOT EXISTS mail_outbox (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          notification_id INTEGER NOT NULL UNIQUE REFERENCES notifications(id) ON DELETE CASCADE,
          email TEXT NOT NULL,
          subject TEXT NOT NULL,
          body TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sent','error')),
          attempts INTEGER NOT NULL DEFAULT 0,
          last_error TEXT NOT NULL DEFAULT '',
          sent_at TEXT,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          actor_id INTEGER REFERENCES users(id),
          action TEXT NOT NULL,
          segment_id INTEGER REFERENCES segments(id),
          details TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS ix_segment_status ON segments(status, id);
        CREATE INDEX IF NOT EXISTS ix_reviewer_seg ON reviews(segment_id,id);
        ''')
        # Existing users retain passwords; only new temporary passwords require a reset.
        if 'must_change_password' not in [r['name'] for r in cx.execute('PRAGMA table_info(users)')]:
            cx.execute('ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0')
        # Backwards-compatible v1.3 migration for existing SQLite databases.
        if 'archived_at' not in [row['name'] for row in cx.execute('PRAGMA table_info(segments)')]:
            cx.execute('ALTER TABLE segments ADD COLUMN archived_at TEXT')
        # Migrate legacy v1.0 pre-submission assignments to temporary previews.
        now=int(time.time())
        for row in cx.execute("SELECT id,assigned_to,status FROM segments WHERE status IN ('assigned','draft') AND assigned_to IS NOT NULL").fetchall():
            cx.execute('INSERT OR IGNORE INTO working_leases(segment_id,user_id,expires_at) VALUES(?,?,?)',
                  (row['id'],row['assigned_to'],now+(DRAFT_LEASE if row['status']=='draft' else LEASE)))
            if cx.execute('SELECT 1 FROM working_leases WHERE segment_id=?',(row['id'],)).fetchone():
                cx.execute("UPDATE segments SET status='available',assigned_to=NULL,expires_at=NULL WHERE id=?",(row['id'],))

def expire_leases(cx):
    # Free abandoned previews; preserve version records.
    now=int(time.time())
    stale=cx.execute('SELECT segment_id,user_id FROM working_leases WHERE expires_at <= ?', (now,)).fetchall()
    for row in stale:
        cx.execute('DELETE FROM working_leases WHERE segment_id=?',(row['segment_id'],))
        cx.execute('DELETE FROM translations WHERE segment_id=? AND submitted_at IS NULL', (row['segment_id'],))
        log(cx, row['user_id'], 'temporary_lease_expired', row['segment_id'])


def smtp_configured():
    return all(os.environ.get(k) for k in ('CHADVOICE_SMTP_HOST','CHADVOICE_SMTP_FROM','CHADVOICE_SMTP_USER','CHADVOICE_SMTP_PASSWORD'))


def record_notification(cx,user_id,segment_id,decision,code,note):
    names={'needs_changes':'Correction demandée','rejected':'Contribution rejetée : correction possible','approved':'Contribution validée','inaudible':'Audio confirmé inaudible'}
    title=f"ChadVoice — {names.get(decision,'Notification')} ({code})"
    message=(note or 'Votre contribution a été examinée.').strip()
    notification_id=cx.execute('''INSERT INTO notifications(user_id,segment_id,category,title,message)
       VALUES(?,?,?,?,?)''',(user_id,segment_id,decision,title,message)).lastrowid
    email=cx.execute('SELECT email FROM users WHERE id=?',(user_id,)).fetchone()
    if email:
        body=(f"Bonjour,\n\nVotre contribution au segment {code} a été examinée.\n"
              f"Décision : {names.get(decision,decision)}\n\nMotif / commentaire :\n{message}\n\n"
              "Connectez-vous à ChadVoice pour consulter cette notification et, si nécessaire, corriger votre contribution.\n"
              "Aucun fichier audio ni mot de passe n'est joint à ce message.\n")
        cx.execute('''INSERT INTO mail_outbox(notification_id,email,subject,body) VALUES(?,?,?,?)''',
          (notification_id,email['email'],title,body))
    return notification_id


def send_mail(outbox_id):
    # SMTP best effort; database inbox remains authoritative.
    if not smtp_configured():
        return False
    with db() as cx:
        rec=cx.execute("SELECT * FROM mail_outbox WHERE id=? AND state!='sent'",(outbox_id,)).fetchone()
    if not rec:
        return False
    try:
        message=EmailMessage()
        message['From']=os.environ['CHADVOICE_SMTP_FROM']
        message['To']=rec['email']
        message['Subject']=rec['subject']
        message.set_content(rec['body'])
        host=os.environ['CHADVOICE_SMTP_HOST']; port=int(os.environ.get('CHADVOICE_SMTP_PORT','587'))
        smtp_driver=smtplib.SMTP_SSL if port==465 else smtplib.SMTP
        with smtp_driver(host,port,timeout=12) as smtp:
            smtp.ehlo()
            if port!=465:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(os.environ['CHADVOICE_SMTP_USER'], os.environ['CHADVOICE_SMTP_PASSWORD'])
            smtp.send_message(message)
        with db() as cx:cx.execute("UPDATE mail_outbox SET state='sent',attempts=attempts+1,sent_at=datetime('now'),last_error='' WHERE id=?",(outbox_id,))
        return True
    except Exception as exc:
        with db() as cx:cx.execute("UPDATE mail_outbox SET state='error',attempts=attempts+1,last_error=? WHERE id=?",(str(exc)[:280],outbox_id))
        return False


def send_pending_email(notification_id):
    with db() as cx:
        rec=cx.execute('SELECT id FROM mail_outbox WHERE notification_id=?',(notification_id,)).fetchone()
    if rec:send_mail(rec['id'])


def hash_password(pw: str):
    salt = secrets.token_bytes(16)
    rounds = 260_000
    dk = hashlib.pbkdf2_hmac('sha256', pw.encode('utf8'), salt, rounds)
    return f'pbkdf2_sha256${rounds}${salt.hex()}${dk.hex()}'

def check_password(pw: str, stored: str):
    try:
        _, count, salt, expected = stored.split('$')
        actual = hashlib.pbkdf2_hmac('sha256', pw.encode('utf8'), bytes.fromhex(salt), int(count))
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False

def create_user(email: str, name: str, pw: str, role='contributor', *, must_change_password=False):
    if len(pw)<10 or len(pw)>256: raise ValueError('Mot de passe : 10 à 256 caractères.')
    if role not in ('contributor','reviewer','admin'): raise ValueError('Rôle non autorisé')
    email=email.strip().lower(); name=name.strip()
    if '@' not in email or len(email)>200 or not name or len(name)>200: raise ValueError('Compte invalide')
    with db() as cx:
        return cx.execute('INSERT INTO users(email,full_name,pw_hash,role,must_change_password) VALUES(?,?,?,?,?)',(email,name,hash_password(pw),role,int(must_change_password))).lastrowid

def generate_temp_password():
    # One unpredictable ten-digit password per account; never use a shared default.
    return ''.join(str(secrets.randbelow(10)) for _ in range(10))

def valid_segment_code(code):
    return bool(SEGMENT_CODE_PATTERN.fullmatch(str(code or '')))

def log(cx, actor, action, seg=None, details=''):
    cx.execute('INSERT INTO audit_logs(actor_id,action,segment_id,details) VALUES(?,?,?,?)',(actor,action,seg,str(details)[:1000]))

def segment_has_history(cx, sid):
    tables=('translations','translation_versions','reviews','working_leases','notifications')
    return any(cx.execute(f'SELECT 1 FROM {table} WHERE segment_id=? LIMIT 1',(sid,)).fetchone()
               for table in tables) or bool(cx.execute(
        "SELECT 1 FROM audit_logs WHERE segment_id=? AND action='preview_started' LIMIT 1",(sid,)).fetchone())

def mark_presence(cx,user_id,now,path):
    # Record section-level activity with at most one update per minute.
    path=path if path in ('/','/contribute','/review','/admin','/account','/notifications') else ('/admin' if path.startswith('/admin/') else '/review' if path.startswith('/review/') else '/contribute' if path.startswith('/contribute/') else '')
    if not path:
        return
    row=cx.execute('SELECT last_seen,last_path FROM user_presence WHERE user_id=?',(user_id,)).fetchone()
    if row and now-int(row['last_seen'])<60 and row['last_path']==path:
        return
    cx.execute('''INSERT INTO user_presence(user_id,last_seen,last_path) VALUES(?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen,last_path=excluded.last_path''',(user_id,now,path))


def user_session(req: Request):
    raw=req.cookies.get('cv_session','')
    if not raw: return None,None
    now=int(time.time())
    with db() as cx:
        row=cx.execute('''SELECT u.*,s.csrf,s.expiry FROM sessions s JOIN users u ON s.user_id=u.id
          WHERE s.token_hash=?''',(hashlib.sha256(raw.encode()).hexdigest(),)).fetchone()
        if row and row['active'] and row['expiry']>now:
            mark_presence(cx,row['id'],now,req.url.path)
    if not row or not row['active'] or row['expiry']<=now: return None,None
    return dict(row),raw

def page(req, filename, *, user=None, status=200, **values):
    tmpl=jinja.get_template(filename)
    unread=0
    if user:
        with db() as cx:unread=cx.execute('SELECT count(*) FROM notifications WHERE user_id=? AND read_at IS NULL',(user['id'],)).fetchone()[0]
    base={'request':req,'user':user,'unread':unread,'status_label':STATUS_LABEL,'now':int(time.time()), **values}
    return HTMLResponse(tmpl.render(**base),status_code=status,headers={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"})

def redirect(url, *, msg=None):
    if msg:
        from urllib.parse import quote
        url += ('&' if '?' in url else '?') + 'msg=' + quote(msg[:180])
    return RedirectResponse(url,status_code=303)

def require(req, roles=None):
    u,raw=user_session(req)
    if not u: return None
    if roles and u['role'] not in roles: return None
    return u

def valid_csrf(req,user,token):
    return bool(user and token and hmac.compare_digest(token,user['csrf']))

def secure(req, user, csrf, roles=None):
    if not user:
        current,_=user_session(req)
        if current: return page(req,'error.html',user=current,error='Accès refusé',status=403)
        return redirect('/login')
    if roles and user['role'] not in roles: return page(req,'error.html',user=user,error='Accès refusé',status=403)
    if not valid_csrf(req,user,csrf): return page(req,'error.html',user=user,error='Jeton de sécurité invalide. Rechargez la page.',status=403)
    return None

STATUS_LABEL = {'available':'Disponible','assigned':'Attribué','draft':'Brouillon','submitted':'En révision','needs_changes':'À corriger','approved':'Validé','rejected':'Rejeté — à corriger','inaudible':'Inaudible'}

def read_stats(cx):
    data={r['status']:r['n'] for r in cx.execute('SELECT status, count(*) n FROM segments WHERE archived_at IS NULL GROUP BY status')}
    archived=cx.execute('SELECT count(*) FROM segments WHERE archived_at IS NOT NULL').fetchone()[0]
    return {**{s:0 for s in STATUS_LABEL},**data,'total':sum(data.values()),'archived':archived}

@app.get('/health')
def health():
    with db() as cx: cx.execute('SELECT 1')
    return {'ok':True}

@app.get('/',response_class=HTMLResponse)
def home(req:Request):
    u=require(req)
    if not u: return redirect('/login')
    with db() as cx:
        # Only privileged users can request aggregate statistics.
        stats=read_stats(cx) if u['role'] in ('admin','reviewer') else None
        mine=cx.execute('''SELECT count(*) n FROM translations WHERE contributor_id=? AND submitted_at IS NOT NULL''',(u['id'],)).fetchone()['n']
        my_reviews=cx.execute('SELECT count(*) n FROM reviews WHERE reviewer_id=?',(u['id'],)).fetchone()['n']
        personal=None
        if u['role']=='contributor':
            counts={r['status']:r['n'] for r in cx.execute('''SELECT s.status,count(*) n
                FROM translations t JOIN segments s ON s.id=t.segment_id
                WHERE t.contributor_id=? AND t.submitted_at IS NOT NULL
                GROUP BY s.status''',(u['id'],))}
            personal={'submitted':mine,'approved':counts.get('approved',0),
                'in_review':counts.get('submitted',0),
                'corrections':counts.get('needs_changes',0)+counts.get('rejected',0),
                'inaudible':counts.get('inaudible',0)}
            personal['approval_percent']=round(100*personal['approved']/mine,1) if mine else 0
            personal['recent']=[dict(r) for r in cx.execute('''SELECT s.id,s.code,s.status,t.submitted_at
                FROM translations t JOIN segments s ON s.id=t.segment_id
                WHERE t.contributor_id=? AND t.submitted_at IS NOT NULL
                ORDER BY t.updated_at DESC,s.id DESC LIMIT 8''',(u['id'],))]
    return page(req,'dashboard.html',user=u,stats=stats,mine=mine,my_reviews=my_reviews,personal=personal,msg=req.query_params.get('msg',''))

@app.get('/login',response_class=HTMLResponse)
def login_page(req: Request):
    if require(req):return redirect('/')
    with db() as cx: exists=cx.execute('SELECT 1 FROM users LIMIT 1').fetchone() is not None
    return page(req,'login.html',has_accounts=exists,error='')

@app.post('/login')
def login(req: Request,email: str=Form(...),password: str=Form(...)):
    with db() as cx:
        u=cx.execute('SELECT * FROM users WHERE email=? AND active=1',(email.strip().lower(),)).fetchone()
        if not u or not check_password(password,u['pw_hash']):
            time.sleep(.16)
            return page(req,'login.html',has_accounts=True,error='Identifiants incorrects.',status=401)
        raw=secrets.token_urlsafe(36)
        cx.execute('INSERT INTO sessions(token_hash,user_id,csrf,expiry) VALUES(?,?,?,?)',(
            hashlib.sha256(raw.encode()).hexdigest(),u['id'],secrets.token_urlsafe(32),int(time.time())+SESSION_TTL))
        mark_presence(cx,u['id'],int(time.time()),'/')
        log(cx,u['id'],'login')
    r=redirect('/account' if u['must_change_password'] else '/')
    r.set_cookie('cv_session',raw,max_age=SESSION_TTL,httponly=True,samesite='strict',secure=os.getenv('CHADVOICE_HTTPS','0')=='1',path='/')
    return r

@app.post('/logout')
def logout(req:Request, csrf: str=Form(...)):
    u,raw=user_session(req)
    if not u or not valid_csrf(req,u,csrf): return redirect('/login')
    with db() as cx:
        cx.execute('DELETE FROM sessions WHERE token_hash=?',(hashlib.sha256(raw.encode()).hexdigest(),))
        log(cx,u['id'],'logout')
    r=redirect('/login');r.delete_cookie('cv_session',path='/');return r

@app.middleware('http')
async def enforce_first_password_change(req:Request, call_next):
    path=req.url.path
    if req.cookies.get('cv_session') and not (path.startswith('/static/') or path in
       ('/login','/health','/account','/account/password','/logout','/setup')):
        current,_=user_session(req)
        if current and current.get('must_change_password'):
            return redirect('/account',msg='Veuillez définir votre mot de passe personnel avant de continuer.')
    return await call_next(req)

@app.get('/account')
def account(req:Request):
    u=require(req)
    if not u:return redirect('/login')
    return page(req,'account.html',user=u,msg=req.query_params.get('msg',''))

@app.post('/account/password')
def change_password(req:Request, csrf:str=Form(...),old:str=Form(...),new:str=Form(...)):
    u=require(req);err=secure(req,u,csrf)
    if err:return err
    if not check_password(old,u['pw_hash']) or not 10<=len(new)<=256 or new==old:
        return page(req,'account.html',user=u,error='Vérifiez votre ancien mot de passe et choisissez un nouveau mot de passe différent, de 10 à 256 caractères.',status=400)
    with db() as cx:
        cx.execute('UPDATE users SET pw_hash=?,must_change_password=0 WHERE id=?',(hash_password(new),u['id']))
        cx.execute('DELETE FROM sessions WHERE user_id=? AND token_hash<>?',(u['id'],hashlib.sha256(req.cookies['cv_session'].encode()).hexdigest()))
        log(cx,u['id'],'password_changed')
    return redirect('/account',msg='Mot de passe mis à jour.')

# First admin on Render Free (no shell): one-time bootstrap page, disabled after an admin exists.
def bootstrap_allowed():
    if not os.getenv('CHADVOICE_BOOTSTRAP_TOKEN'): return False
    with db() as cx:
        return cx.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone() is None

@app.get('/setup')
def setup_page(req:Request):
    if not bootstrap_allowed(): return Response('Indisponible',status_code=404)
    return page(req,'setup.html',error='')

@app.post('/setup')
def setup_first_admin(req:Request, token:str=Form(...), email:str=Form(...),
                      full_name:str=Form(...),password:str=Form(...)):
    secret=os.getenv('CHADVOICE_BOOTSTRAP_TOKEN','')
    if not secret or not hmac.compare_digest(token,secret):
        return page(req,'setup.html',status=403,error='Jeton d’initialisation invalide.')
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        if cx.execute("SELECT 1 FROM users WHERE role='admin' LIMIT 1").fetchone():
            return Response('Un administrateur existe déjà.',status_code=409)
        try:
            if len(password)<10 or len(password)>256 or '@' not in email or not full_name.strip():
                raise ValueError('Compte invalide : e-mail, nom et mot de passe de 10 caractères minimum requis.')
            uid=cx.execute('INSERT INTO users(email,full_name,pw_hash,role) VALUES(?,?,?,?)',
              (email.strip().lower(),full_name.strip(),hash_password(password),'admin')).lastrowid
        except ValueError as exc:
            return page(req,'setup.html',status=400,error=str(exc))
        log(cx,uid,'initial_admin_created')
    return redirect('/login',msg='Administrateur créé. Retirez CHADVOICE_BOOTSTRAP_TOKEN des variables Render.')

# Contributor actions
@app.get('/contribute')
def contribute(req:Request):
    u=require(req,('contributor','admin'))
    if not u:return redirect('/' if require(req) else '/login')
    selected=req.query_params.get('segment','')
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        expire_leases(cx)
        corrections=cx.execute('''SELECT s.id,s.code,s.status,r.note FROM segments s
          JOIN translations t ON t.segment_id=s.id
          JOIN reviews r ON r.id=(SELECT MAX(r2.id) FROM reviews r2 WHERE r2.segment_id=s.id)
          WHERE s.archived_at IS NULL AND s.assigned_to=? AND t.contributor_id=? AND s.status IN ('needs_changes','rejected')
          ORDER BY r.id DESC''',(u['id'],u['id'])).fetchall()
        lease=cx.execute('''SELECT s.*,t.latin,t.french,t.reported_inaudible,t.submitted_at,
            w.expires_at lease_expires_at FROM working_leases w JOIN segments s ON s.id=w.segment_id
            LEFT JOIN translations t ON t.segment_id=s.id AND t.contributor_id=w.user_id
            WHERE w.user_id=? AND s.archived_at IS NULL''',(u['id'],)).fetchone()
        correction_id=next((x['id'] for x in corrections if str(x['id'])==selected),None)
        if correction_id is None and corrections: correction_id=corrections[0]['id']
        if correction_id:
            pending=cx.execute('''SELECT s.*,t.latin,t.french,t.reported_inaudible,t.submitted_at,
              NULL lease_expires_at FROM segments s JOIN translations t ON t.segment_id=s.id
              WHERE s.id=? AND s.archived_at IS NULL''',(correction_id,)).fetchone()
        else:pending=lease
        my_last=cx.execute('''SELECT s.id,s.code,s.status,t.updated_at FROM translations t
          JOIN segments s ON s.id=t.segment_id WHERE t.contributor_id=? AND s.archived_at IS NULL
          ORDER BY t.updated_at DESC LIMIT 16''',(u['id'],)).fetchall()
        reviewnote=None
        if pending and pending['status'] in ('needs_changes','rejected'):
            reviewnote=cx.execute("SELECT note FROM reviews WHERE segment_id=? AND decision IN ('needs_changes','rejected') ORDER BY id DESC LIMIT 1",(pending['id'],)).fetchone()
        stats=read_stats(cx) if u['role']=='admin' else None
    return page(req,'contribute.html',user=u,seg=pending,reviewnote=reviewnote,stats=stats,mine=my_last,corrections=corrections,msg=req.query_params.get('msg',''))

@app.post('/claim')
def claim(req:Request,csrf:str=Form(...)):
    u=require(req,('contributor','admin'));err=secure(req,u,csrf)
    if err:return err
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        expire_leases(cx)
        existing=cx.execute('SELECT segment_id FROM working_leases WHERE user_id=?',(u['id'],)).fetchone()
        if existing:return redirect('/contribute',msg='Vous avez déjà un segment en consultation. Soumettez ou libérez-le.')
        correction=cx.execute("SELECT id FROM segments WHERE archived_at IS NULL AND assigned_to=? AND status IN ('needs_changes','rejected') LIMIT 1",(u['id'],)).fetchone()
        if correction:return redirect('/contribute',msg='Corrigez votre contribution rejetée avant de prendre un autre segment.')
        segment=cx.execute('''SELECT id FROM segments WHERE archived_at IS NULL AND status='available' AND
           NOT EXISTS(SELECT 1 FROM working_leases w WHERE w.segment_id=segments.id)
           ORDER BY id LIMIT 1''').fetchone()
        if not segment:return redirect('/contribute',msg='Aucun nouveau segment disponible.')
        cx.execute('INSERT INTO working_leases(segment_id,user_id,expires_at) VALUES(?,?,?)',
          (segment['id'],u['id'],int(time.time())+LEASE))
        log(cx,u['id'],'preview_started',segment['id'])
    return redirect('/contribute',msg='Segment ouvert. Écoutez-le, transcrivez et traduisez, ou libérez-le si vous ne pouvez pas continuer.')

@app.post('/contribute/{sid}/save')
def save(req:Request,sid:int,csrf:str=Form(...),latin:str=Form(''),french:str=Form(''),action:str=Form('draft'),inaudible:str=Form('')):
    u=require(req,('contributor','admin'));err=secure(req,u,csrf)
    if err:return err
    latin=latin.strip();french=french.strip();flag=inaudible=='on'
    if len(latin)>5000 or len(french)>5000:return page(req,'error.html',user=u,error='Texte trop long.',status=400)
    if action=='submit' and not flag and (not latin or not french):
        return redirect('/contribute',msg='Les deux textes sont obligatoires, sauf audio signalé inaudible.')
    if action not in ('draft','submit'):return page(req,'error.html',user=u,error='Action invalide',status=400)
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        expire_leases(cx)
        seg=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        lease=cx.execute('SELECT * FROM working_leases WHERE segment_id=? AND user_id=?',(sid,u['id'])).fetchone()
        if seg and seg['archived_at'] is not None:
            return page(req,'error.html',user=u,error='Ce segment est archivé.',status=409)
        correcting=seg and seg['assigned_to']==u['id'] and seg['status'] in ('needs_changes','rejected')
        temporary=seg and seg['status']=='available' and lease is not None
        if not (correcting or temporary):
            return page(req,'error.html',user=u,error='Cette consultation est expirée ou ce segment ne vous est plus attribué. Rechargez Contribuer.',status=409)
        cx.execute('''INSERT INTO translations(segment_id,contributor_id,latin,french,reported_inaudible,submitted_at)
          VALUES(?,?,?,?,?,CASE WHEN ?='submit' THEN datetime('now') ELSE NULL END)
          ON CONFLICT(segment_id) DO UPDATE SET contributor_id=excluded.contributor_id,
          latin=excluded.latin,french=excluded.french,reported_inaudible=excluded.reported_inaudible,
          updated_at=datetime('now'),submitted_at=excluded.submitted_at''',
          (sid,u['id'],latin,french,int(flag),action))
        cx.execute('''INSERT INTO translation_versions(segment_id,contributor_id,latin,french,reported_inaudible,stage)
          VALUES(?,?,?,?,?,?)''',(sid,u['id'],latin,french,int(flag),action))
        if temporary:
            if action=='submit':
                cx.execute("UPDATE segments SET status='submitted',assigned_to=?,expires_at=NULL WHERE id=?",(u['id'],sid))
                cx.execute('DELETE FROM working_leases WHERE segment_id=?',(sid,))
            else:
                cx.execute('UPDATE working_leases SET expires_at=? WHERE segment_id=?',(int(time.time())+DRAFT_LEASE,sid))
        elif correcting and action=='submit':
            cx.execute("UPDATE segments SET status='submitted',expires_at=NULL WHERE id=?",(sid,))
        log(cx,u['id'],action,sid)
    return redirect('/contribute',msg='Contribution soumise à la révision.' if action=='submit' else 'Brouillon enregistré. Vous pouvez revenir le compléter avant de soumettre.')

@app.post('/contribute/{sid}/release')
def release(req:Request,sid:int,csrf:str=Form(...)):
    u=require(req,('contributor','admin'));err=secure(req,u,csrf)
    if err:return err
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        lease=cx.execute('SELECT * FROM working_leases WHERE segment_id=? AND user_id=?',(sid,u['id'])).fetchone()
        if not lease:return page(req,'error.html',user=u,error='Cette consultation ne peut pas être libérée.',status=403)
        cx.execute('DELETE FROM translations WHERE segment_id=? AND contributor_id=? AND submitted_at IS NULL',(sid,u['id']))
        cx.execute('DELETE FROM working_leases WHERE segment_id=?',(sid,))
        log(cx,u['id'],'preview_released',sid)
    return redirect('/contribute',msg='Consultation libérée. Aucun segment n’a été attribué définitivement.')

# Notifications persistantes, visibles même sans serveur SMTP
@app.get('/notifications')
def notifications(req:Request):
    u=require(req)
    if not u:return redirect('/login')
    with db() as cx:
        items=cx.execute('''SELECT n.*,s.code FROM notifications n LEFT JOIN segments s ON s.id=n.segment_id
          WHERE n.user_id=? ORDER BY n.id DESC LIMIT 150''',(u['id'],)).fetchall()
    return page(req,'notifications.html',user=u,items=items,msg=req.query_params.get('msg',''))

@app.post('/notifications/{nid}/read')
def mark_notification(req:Request,nid:int,csrf:str=Form(...)):
    u=require(req);err=secure(req,u,csrf)
    if err:return err
    with db() as cx:
        row=cx.execute('SELECT segment_id,category FROM notifications WHERE id=? AND user_id=?',(nid,u['id'])).fetchone()
        if not row:return page(req,'error.html',user=u,error='Notification introuvable',status=404)
        cx.execute("UPDATE notifications SET read_at=datetime('now') WHERE id=? AND user_id=?",(nid,u['id']))
    if row['segment_id'] and row['category'] in ('rejected','needs_changes') and u['role'] in ('contributor','admin'):
        return redirect(f'/contribute?segment={row["segment_id"]}')
    return redirect('/notifications')

# Reviewer
@app.get('/review')
def review_list(req:Request):
    u=require(req,('reviewer','admin'))
    if not u:return redirect('/' if require(req) else '/login')
    with db() as cx:
        records=cx.execute('''SELECT s.id,s.code,s.status,s.source_id,s.speaker_id,t.submitted_at,u.full_name contributor
           FROM segments s JOIN translations t ON t.segment_id=s.id
           JOIN users u ON u.id=t.contributor_id
           WHERE s.archived_at IS NULL AND s.status='submitted' AND t.contributor_id<>? ORDER BY t.submitted_at,s.id LIMIT 200''',(u['id'],)).fetchall()
        recent=cx.execute('''SELECT s.code,r.decision,r.created_at FROM reviews r JOIN segments s ON r.segment_id=s.id
           WHERE r.reviewer_id=? ORDER BY r.id DESC LIMIT 10''',(u['id'],)).fetchall()
    return page(req,'review_list.html',user=u,items=records,recent=recent,msg=req.query_params.get('msg',''))

@app.get('/review/{sid}')
def review_one(req:Request,sid:int):
    u=require(req,('reviewer','admin'))
    if not u:return redirect('/' if require(req) else '/login')
    with db() as cx:
        item=cx.execute('''SELECT s.*,t.latin,t.french,t.reported_inaudible,t.submitted_at,
            t.contributor_id,u.full_name contributor FROM segments s JOIN translations t ON t.segment_id=s.id
            JOIN users u ON u.id=t.contributor_id WHERE s.id=?''',(sid,)).fetchone()
        if not item:return page(req,'error.html',user=u,error='Segment introuvable',status=404)
        if item['archived_at'] is not None:
            return page(req,'error.html',user=u,error='Segment archivé par l’administrateur.',status=403)
        if item['contributor_id']==u['id']:return page(req,'error.html',user=u,error='Vous ne pouvez pas réviser votre propre contribution.',status=403)
        if item['status'] not in ('submitted','approved','rejected','needs_changes','inaudible'):
            return page(req,'error.html',user=u,error='Brouillon non accessible aux réviseurs.',status=403)
        history=cx.execute('''SELECT r.*,u.full_name reviewer FROM reviews r JOIN users u ON u.id=r.reviewer_id
          WHERE r.segment_id=? ORDER BY r.id DESC''',(sid,)).fetchall()
        versions=cx.execute('''SELECT latin,french,stage,created_at FROM translation_versions
          WHERE segment_id=? ORDER BY id DESC LIMIT 30''',(sid,)).fetchall()
    return page(req,'review_one.html',user=u,item=item,history=history,versions=versions,msg=req.query_params.get('msg',''))

@app.post('/review/{sid}/decide')
def decide(req:Request,sid:int,background_tasks:BackgroundTasks,csrf:str=Form(...),decision:str=Form(...),latin:str=Form(''),french:str=Form(''),note:str=Form('')):
    u=require(req,('reviewer','admin'));err=secure(req,u,csrf)
    if err:return err
    latin=latin.strip();french=french.strip();note=note.strip()
    if decision not in ('approved','needs_changes','rejected','inaudible'):return page(req,'error.html',user=u,error='Décision invalide',status=400)
    if len(latin)>5000 or len(french)>5000 or len(note)>3000:return page(req,'error.html',user=u,error='Texte trop long',status=400)
    if decision=='approved' and (not latin or not french):return redirect('/review/'+str(sid),msg='La validation nécessite deux textes complets.')
    if decision in ('needs_changes','rejected','inaudible') and not note:return redirect('/review/'+str(sid),msg='Veuillez préciser la raison de cette décision.')
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        seg=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        tr=cx.execute('SELECT * FROM translations WHERE segment_id=?',(sid,)).fetchone()
        if not seg or seg['archived_at'] is not None or not tr or seg['status']!='submitted' or tr['contributor_id']==u['id']:
            return page(req,'error.html',user=u,error='Révision impossible : statut modifié ou droits insuffisants.',status=409)
        cx.execute('INSERT INTO reviews(segment_id,reviewer_id,decision,final_latin,final_french,note) VALUES(?,?,?,?,?,?)',
           (sid,u['id'],decision,latin,french,note))
        cx.execute('UPDATE segments SET status=?,expires_at=NULL WHERE id=?',(decision,sid))
        notification_id=record_notification(cx,tr['contributor_id'],sid,decision,seg['code'],note)
        log(cx,u['id'],'review_'+decision,sid)
    if smtp_configured():background_tasks.add_task(send_pending_email,notification_id)
    return redirect('/review',msg='Décision enregistrée et notification interne créée.' + (' Envoi e-mail tenté en arrière-plan.' if smtp_configured() else ' SMTP non configuré : aucun e-mail externe envoyé.'))

# Admin
@app.get('/admin')
def administration(req:Request):
    u=require(req,('admin',))
    if not u:return redirect('/' if require(req) else '/login')
    with db() as cx:
        stats=read_stats(cx)
        people=cx.execute('''SELECT u.id,u.full_name,u.email,u.role,u.active,u.created_at,p.last_seen,p.last_path,
           (SELECT count(*) FROM translations t WHERE t.contributor_id=u.id AND t.submitted_at IS NOT NULL) contributions,
           (SELECT count(*) FROM reviews r WHERE r.reviewer_id=u.id) validations
          FROM users u LEFT JOIN user_presence p ON p.user_id=u.id ORDER BY u.id''').fetchall()
        segments=cx.execute('''SELECT s.*,u.full_name contributor,w.user_id lease_user,
          (EXISTS(SELECT 1 FROM translations t WHERE t.segment_id=s.id) OR
           EXISTS(SELECT 1 FROM translation_versions v WHERE v.segment_id=s.id) OR
           EXISTS(SELECT 1 FROM reviews r WHERE r.segment_id=s.id) OR
           EXISTS(SELECT 1 FROM notifications n WHERE n.segment_id=s.id) OR
           EXISTS(SELECT 1 FROM audit_logs a WHERE a.segment_id=s.id AND a.action='preview_started')) has_history,
          v.full_name previewer,w.expires_at lease_expiration FROM segments s
          LEFT JOIN users u ON u.id=s.assigned_to
          LEFT JOIN working_leases w ON w.segment_id=s.id
          LEFT JOIN users v ON v.id=w.user_id ORDER BY s.id DESC LIMIT 80''').fetchall()
        online_ids={r['user_id'] for r in cx.execute('SELECT DISTINCT user_id FROM sessions WHERE expiry>?',(int(time.time()),))}
        mail_stats=cx.execute("SELECT state,count(*) n FROM mail_outbox GROUP BY state").fetchall()
    now=int(time.time())
    sections={'/':'Vue générale','/contribute':'Contribuer','/review':'Réviser',
        '/admin':'Administration','/account':'Mon compte','/notifications':'Notifications'}
    people=[{**dict(x), 'online':bool(x['active'] and x['id'] in online_ids and x['last_seen'] and now-int(x['last_seen'])<=300),
        'last_visit':datetime.fromtimestamp(x['last_seen'],timezone.utc).strftime('%d/%m/%Y %H:%M UTC') if x['last_seen'] else 'Jamais connecté',
        'last_section':sections.get(x['last_path'],'—')} for x in people]
    return page(req,'admin.html',user=u,stats=stats,people=people,segments=segments,mail_stats={x['state']:x['n'] for x in mail_stats},smtp_ready=smtp_configured(),msg=req.query_params.get('msg',''))

# Curated corpus management (v1.4): all operations require admin + CSRF.
@app.post('/admin/segments/bulk-approve')
def admin_bulk_approve(req:Request,csrf:str=Form(...),segment_ids:list[int]=Form(...),
                       confirmation:str=Form('')):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    ids=list(dict.fromkeys(segment_ids))
    if confirmation!='VALIDER' or not ids or len(ids)>30 or any(i<=0 for i in ids):
        return Response('Sélection limitée à 30 segments et confirmation VALIDER obligatoire.',status_code=400)
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        items=[]
        for sid in ids:
            item=cx.execute('''SELECT s.id,s.code,s.status,s.archived_at,t.latin,t.french,
                   t.reported_inaudible,t.contributor_id FROM segments s
                   JOIN translations t ON t.segment_id=s.id WHERE s.id=?''',(sid,)).fetchone()
            if not item or item['status']!='submitted' or item['archived_at'] is not None or \
                    item['reported_inaudible'] or item['contributor_id']==u['id'] or \
                    not item['latin'].strip() or not item['french'].strip():
                return Response('La sélection comporte un segment non admissible. Aucune validation effectuée.',status_code=409)
            items.append(item)
        for item in items:
            cx.execute('''INSERT INTO reviews(segment_id,reviewer_id,decision,final_latin,final_french,note)
                   VALUES(?,?,?,?,?,?)''',(item['id'],u['id'],'approved',item['latin'],item['french'],
                   'Validation groupée à partir des textes soumis, sans correction'))
            cx.execute("UPDATE segments SET status='approved',expires_at=NULL WHERE id=?",(item['id'],))
            record_notification(cx,item['contributor_id'],item['id'],'approved',item['code'],
                   'Contribution validée dans une opération groupée.')
            log(cx,u['id'],'admin_bulk_approved',item['id'])
    return redirect('/admin/segments?status=submitted',msg=f'{len(items)} segment(s) validé(s) et historisé(s).')

@app.get('/admin/segments')
def admin_segment_library(req:Request, q:str='', status:str='all', contributor:str='all', page_num:int=1):
    u=require(req,('admin',))
    if not u:return redirect('/login')
    q=q.strip()[:120]
    allowed={*STATUS_LABEL.keys(),'archived','all','not_approved','attention'}
    if status not in allowed: status='all'
    # Only numeric ids returned by the contributor selector are accepted.
    contributor=contributor.strip()
    contributor_id=int(contributor) if contributor.isdecimal() and len(contributor)<=18 else None
    contributor=str(contributor_id) if contributor_id is not None else 'all'
    page_num=max(1,min(page_num,100000))
    filters=[]; args=[]
    if status=='archived':
        filters.append('s.archived_at IS NOT NULL')
    elif status=='not_approved':
        filters.append("s.archived_at IS NULL AND s.status!='approved'")
    elif status=='attention':
        filters.append("s.archived_at IS NULL AND s.status IN ('submitted','needs_changes','rejected')")
    elif status!='all':
        filters.append('s.archived_at IS NULL AND s.status=?');args.append(status)
    if contributor_id is not None:
        filters.append('''EXISTS(SELECT 1 FROM translations ct WHERE
           ct.segment_id=s.id AND ct.contributor_id=?)''')
        args.append(contributor_id)
    if q:
        # PostgreSQL LOWER handles Unicode; SQLite requires Unicode-aware case folding.
        fold='LOWER' if DB_URL else 'cv_search_fold'
        filters.append(f'''({fold}(s.code) LIKE ? OR {fold}(s.original_name) LIKE ?
            OR {fold}(s.source_id) LIKE ? OR {fold}(s.speaker_id) LIKE ?
            OR EXISTS (SELECT 1 FROM translations t WHERE t.segment_id=s.id
                AND ({fold}(t.latin) LIKE ? OR {fold}(t.french) LIKE ?))
            OR EXISTS (SELECT 1 FROM translations t JOIN users cu ON cu.id=t.contributor_id
                WHERE t.segment_id=s.id AND ({fold}(cu.full_name) LIKE ? OR {fold}(cu.email) LIKE ?)))''')
        args.extend(['%'+unicode_search_fold(q)+'%']*8)
    where=' WHERE '+(' AND '.join(filters)) if filters else ''
    with db() as cx:
        invalid_code_count=sum(not valid_segment_code(row['code']) for row in cx.execute('SELECT code FROM segments').fetchall())
        contributors=[dict(r) for r in cx.execute('''SELECT DISTINCT u.id,u.full_name FROM users u
           JOIN translations t ON t.contributor_id=u.id ORDER BY u.full_name''').fetchall()]
        total=cx.execute('SELECT count(*) n FROM segments s'+where,args).fetchone()['n']
        page_num=min(page_num,max(1,(total+39)//40))
        records=cx.execute('''SELECT s.id,s.code,s.original_name,s.source_id,s.speaker_id,s.status,
           s.archived_at,t.latin,t.french,t.reported_inaudible,t.contributor_id,u.full_name contributor FROM segments s
           LEFT JOIN translations t ON t.segment_id=s.id
           LEFT JOIN users u ON u.id=t.contributor_id'''+where+
           ' ORDER BY s.id DESC LIMIT 40 OFFSET ?',args+[40*(page_num-1)]).fetchall()
    records=[{**dict(rec),'invalid_code':not valid_segment_code(rec['code'])} for rec in records]
    return page(req,'admin_segments.html',user=u,records=records,total=total,q=q,filter_status=status,
         invalid_code_count=invalid_code_count,
         filter_contributor=contributor,contributors=contributors,
         current_page=page_num,page_count=max(1,(total+39)//40),statuses=STATUS_LABEL,msg=req.query_params.get('msg',''))

@app.get('/admin/segments/{sid}')
def admin_segment_detail(req:Request,sid:int):
    u=require(req,('admin',))
    if not u:return redirect('/login')
    with db() as cx:
        seg=cx.execute('''SELECT s.*,t.latin,t.french,t.reported_inaudible,t.submitted_at,
           t.contributor_id,uc.full_name contributor FROM segments s
           LEFT JOIN translations t ON t.segment_id=s.id
           LEFT JOIN users uc ON uc.id=t.contributor_id WHERE s.id=?''',(sid,)).fetchone()
        if not seg:return page(req,'error.html',user=u,error='Segment introuvable.',status=404)
        reviews=cx.execute('''SELECT r.*,u.full_name reviewer FROM reviews r JOIN users u ON u.id=r.reviewer_id
           WHERE r.segment_id=? ORDER BY r.id DESC LIMIT 30''',(sid,)).fetchall()
        versions=cx.execute('''SELECT * FROM translation_versions WHERE segment_id=? ORDER BY id DESC LIMIT 20''',(sid,)).fetchall()
        busy=cx.execute('SELECT 1 FROM working_leases WHERE segment_id=?',(sid,)).fetchone() is not None
        has_history=segment_has_history(cx,sid)
    approved=next((r for r in reviews if r['decision']=='approved'),None)
    return page(req,'admin_segment_detail.html',user=u,s=seg,reviews=reviews,versions=versions,
          invalid_code=not valid_segment_code(seg['code']),
          latest_approval=approved,busy=busy,has_history=has_history,msg=req.query_params.get('msg',''))

@app.post('/admin/segments/{sid}/code')
def admin_change_segment_code(req:Request,sid:int,csrf:str=Form(...),new_code:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    new_code=new_code.strip().upper()
    if not valid_segment_code(new_code):
        return Response('Identifiant invalide : utilisez CV suivi de 4 à 8 chiffres (ex. CV1064).',status_code=400)
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        old=cx.execute('SELECT code FROM segments WHERE id=?',(sid,)).fetchone()
        if not old:return Response('Segment introuvable',status_code=404)
        if old['code']==new_code:return redirect(f'/admin/segments/{sid}',msg='Identifiant inchangé.')
        if cx.execute('SELECT 1 FROM segments WHERE code=? AND id<>?',(new_code,sid)).fetchone():
            return Response('Cet identifiant existe déjà.',status_code=409)
        cx.execute('UPDATE segments SET code=? WHERE id=?',(new_code,sid))
        log(cx,u['id'],'admin_segment_code_changed',sid,f"{old['code']} -> {new_code}")
    return redirect(f'/admin/segments/{sid}',msg='Code modifié sans changer les références audio ni les traductions.')

@app.post('/admin/segments/{sid}/metadata')
def admin_segment_metadata(req:Request,sid:int,csrf:str=Form(...),name:str=Form(...),
                           speaker_id:str=Form(''),source_id:str=Form('')):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    name=name.strip();speaker_id=speaker_id.strip();source_id=source_id.strip()
    if not name or len(name)>180 or len(speaker_id)>180 or len(source_id)>180 or '/' in name or '\\' in name:
        return Response('Métadonnées invalides',status_code=400)
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        old=cx.execute('SELECT original_name,source_id,speaker_id FROM segments WHERE id=?',(sid,)).fetchone()
        if not old:return Response('Introuvable',status_code=404)
        cx.execute('UPDATE segments SET original_name=?,speaker_id=?,source_id=? WHERE id=?',
                   (name,speaker_id,source_id,sid))
        log(cx,u['id'],'admin_metadata_updated',sid,
            json.dumps({'old':dict(old),'new':{'name':name,'speaker_id':speaker_id,'source_id':source_id}},ensure_ascii=False))
    # Underlying S3 object key remains unchanged; renaming is a display/metadata change.
    return redirect(f'/admin/segments/{sid}',msg='Nom et métadonnées mis à jour ; audio original conservé.')

@app.post('/admin/segments/{sid}/revoke-approval')
def admin_revoke_approval(req:Request,sid:int,csrf:str=Form(...),note:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    note=note.strip()
    if len(note)<6 or len(note)>3000:return Response('Justification obligatoire (6 à 3000 caractères).',status_code=400)
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        seg=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        tr=cx.execute('SELECT * FROM translations WHERE segment_id=?',(sid,)).fetchone()
        if not seg or seg['archived_at'] is not None or seg['status']!='approved' or not tr:
            return Response('La validation a déjà changé ou le segment est archivé.',status_code=409)
        # Keep original approval in review history; add a new, motivated correction decision.
        cx.execute('''INSERT INTO reviews(segment_id,reviewer_id,decision,final_latin,final_french,note)
           VALUES(?,?,?,?,?,?)''',(sid,u['id'],'rejected',tr['latin'],tr['french'],note))
        cx.execute("UPDATE segments SET status='rejected',expires_at=NULL WHERE id=?",(sid,))
        record_notification(cx,tr['contributor_id'],sid,'rejected',seg['code'],note)
        log(cx,u['id'],'admin_approval_revoked',sid,note)
    return redirect(f'/admin/segments/{sid}',msg='Validation retirée. Le segment est exclu des exports validés ; le contributeur peut le corriger.')

@app.post('/admin/segments/{sid}/correct-approval')
def admin_correct_approval(req:Request,sid:int,csrf:str=Form(...),latin:str=Form(''),
                           french:str=Form(''),note:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    latin=latin.strip();french=french.strip();note=note.strip()
    if not latin or not french or len(latin)>5000 or len(french)>5000 or len(note)<6 or len(note)>3000:
        return Response('Deux textes et un motif précis sont obligatoires.',status_code=400)
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        seg=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        tr=cx.execute('SELECT * FROM translations WHERE segment_id=?',(sid,)).fetchone()
        if not seg or seg['archived_at'] is not None or seg['status']!='approved' or not tr:
            return Response('Correction non autorisée dans cet état.',status_code=409)
        if tr['contributor_id']==u['id']:
            return Response('Auto-validation interdite. Demandez à un autre administrateur de vérifier.',status_code=403)
        cx.execute('''INSERT INTO reviews(segment_id,reviewer_id,decision,final_latin,final_french,note)
           VALUES(?,?,?,?,?,?)''',(sid,u['id'],'approved',latin,french,note))
        log(cx,u['id'],'admin_approved_text_corrected',sid,note)
    return redirect(f'/admin/segments/{sid}',msg='Correction approuvée et historisée dans le corpus exportable.')

@app.post('/admin/leases/{sid}/release')
def admin_release_lease(req:Request,sid:int,csrf:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        lease=cx.execute('SELECT * FROM working_leases WHERE segment_id=?',(sid,)).fetchone()
        if not lease:return redirect('/admin',msg='Aucune consultation temporaire pour ce segment.')
        cx.execute('DELETE FROM translations WHERE segment_id=? AND submitted_at IS NULL',(sid,))
        cx.execute('DELETE FROM working_leases WHERE segment_id=?',(sid,))
        log(cx,u['id'],'admin_lease_released',sid)
    return redirect('/admin',msg='Consultation temporaire libérée par l’administrateur.')

@app.post('/admin/segments/{sid}/archive')
def admin_archive_segment(req:Request,sid:int,csrf:str=Form(...)):
    u=require(req,('admin',)); err=secure(req,u,csrf)
    if err:return err
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        seg=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        if not seg:return page(req,'error.html',user=u,error='Segment introuvable.',status=404)
        if seg['archived_at'] is not None:return redirect('/admin',msg='Segment déjà archivé.')
        if cx.execute('SELECT 1 FROM working_leases WHERE segment_id=?',(sid,)).fetchone():
            return redirect('/admin',msg='Libérez la consultation temporaire avant d’archiver ce segment.')
        cx.execute("UPDATE segments SET archived_at=datetime('now') WHERE id=?",(sid,))
        log(cx,u['id'],'segment_archived',sid,seg['code'])
    return redirect('/admin',msg='Segment archivé. Audio et contributions conservés ; retiré des tâches et exports validés.')

@app.post('/admin/segments/{sid}/restore')
def admin_restore_segment(req:Request,sid:int,csrf:str=Form(...)):
    u=require(req,('admin',)); err=secure(req,u,csrf)
    if err:return err
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        seg=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        if not seg:return page(req,'error.html',user=u,error='Segment introuvable.',status=404)
        if seg['archived_at'] is None:return redirect('/admin',msg='Segment déjà actif.')
        cx.execute('UPDATE segments SET archived_at=NULL WHERE id=?',(sid,))
        log(cx,u['id'],'segment_restored',sid,seg['code'])
    return redirect('/admin',msg='Segment restauré et de nouveau pris en compte.')

@app.post('/admin/segments/{sid}/delete')
def admin_delete_segment(req:Request,sid:int,csrf:str=Form(...),confirm_code:str=Form(...)):
    u=require(req,('admin',)); err=secure(req,u,csrf)
    if err:return err
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        seg=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        if not seg:return page(req,'error.html',user=u,error='Segment introuvable.',status=404)
        if seg['code']!=confirm_code:return page(req,'error.html',user=u,error='Code de confirmation incorrect.',status=400)
        if seg['status']!='available' or seg['assigned_to'] is not None:
            return redirect('/admin',msg='Suppression interdite : segment non disponible ou déjà attribué. Archivez-le.')
        if segment_has_history(cx,sid):
            return redirect('/admin',msg='Suppression interdite : contribution, brouillon, révision ou consultation existante. Archivez-le.')
        # Keep transaction uncommitted until deletion of private object succeeds.
        # A failure in object storage aborts SQL deletion.
        try:
            remove_audio_file(AUDIO/seg['file_name'],seg['file_name'])
        except Exception:
            return page(req,'error.html',user=u,error='Le stockage audio n’a pas confirmé la suppression. Aucune suppression SQL n’a été validée.',status=503)
        # Keep audit trail without leaving FK references to the removed row.
        cx.execute('UPDATE audit_logs SET segment_id=NULL WHERE segment_id=?',(sid,))
        cx.execute('DELETE FROM segments WHERE id=?',(sid,))
        log(cx,u['id'],'segment_deleted',details=f"{seg['code']} | {seg['original_name']}")
    return redirect('/admin',msg='Segment sans annotation supprimé définitivement du stockage et de la base.')

@app.post('/admin/mail/retry')
def admin_retry_mail(req:Request,background_tasks:BackgroundTasks,csrf:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    if not smtp_configured():return redirect('/admin',msg='Configurez SMTP avant l’envoi des e-mails.')
    with db() as cx:
        ids=[r['id'] for r in cx.execute("SELECT id FROM mail_outbox WHERE state IN ('pending','error') AND attempts<5 ORDER BY id LIMIT 30")]
    for id in ids:background_tasks.add_task(send_mail,id)
    return redirect('/admin',msg=f'{len(ids)} message(s) programmés pour une nouvelle tentative.')

@app.get('/admin/users')
def admin_user_library(req:Request):
    u=require(req,('admin',))
    if not u:return redirect('/login')
    with db() as cx:
        people=cx.execute('''SELECT u.id,u.full_name,u.email,u.role,u.active,u.must_change_password,
          (SELECT count(*) FROM translations t WHERE t.contributor_id=u.id AND t.submitted_at IS NOT NULL) contributions,
          (SELECT count(*) FROM reviews r WHERE r.reviewer_id=u.id) revisions
          FROM users u ORDER BY u.id''').fetchall()
    return page(req,'admin_users.html',user=u,people=people,msg=req.query_params.get('msg',''))

@app.get('/admin/users/export.csv')
def admin_export_users_csv(req:Request):
    u=require(req,('admin',))
    if not u:return Response('Accès refusé',status_code=403)
    with db() as cx:
        records=cx.execute('''SELECT u.id,u.full_name,u.email,u.role,u.active,u.must_change_password,
          (SELECT count(*) FROM translations t WHERE t.contributor_id=u.id AND t.submitted_at IS NOT NULL) contributions,
          (SELECT count(*) FROM reviews r WHERE r.reviewer_id=u.id) revisions
          FROM users u ORDER BY u.id''').fetchall()
        log(cx,u['id'],'users_csv_export',details=str(len(records)))
    output=io.StringIO(newline='')
    writer=csv.writer(output,delimiter=';',lineterminator='\r\n')
    writer.writerow(('id','nom_complet','email','role','statut','contributions','revisions','changement_mot_de_passe_requis'))
    for record in records:
        writer.writerow([safe_csv(v) for v in (
          record['id'],record['full_name'],record['email'],record['role'],
          'Actif' if record['active'] else 'Desactive',record['contributions'],record['revisions'],
          'Oui' if record['must_change_password'] else 'Non')])
    return Response('\ufeff'+output.getvalue(),media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':'attachment; filename="chadvoice_utilisateurs.csv"',
                 'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})

@app.post('/admin/users')
def admin_new_user(req:Request,csrf:str=Form(...),email:str=Form(...),full_name:str=Form(...),
                   password:str=Form(''),role:str=Form('contributor')):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    generated=not bool(password)
    secret=generate_temp_password() if generated else password
    try:
        uid=create_user(email,full_name,secret,role,must_change_password=generated)
    except ValueError as ex:
        return redirect('/admin/users',msg='Compte non créé : '+str(ex))
    except Exception:
        return redirect('/admin/users',msg='Compte non créé : adresse e-mail déjà utilisée ou erreur de base.')
    with db() as cx:log(cx,u['id'],'user_created',details=str(uid))
    if generated:
        return page(req,'admin_password_once.html',user=u,temporary_password=secret,
                    account_email=email.strip().lower(),account_name=full_name.strip(),heading='Compte créé')
    return redirect('/admin/users',msg='Compte utilisateur créé.')

@app.get('/admin/users/{uid}')
def admin_user_detail(req:Request,uid:int):
    u=require(req,('admin',))
    if not u:return redirect('/login')
    with db() as cx:
        other=cx.execute('SELECT id,email,full_name,role,active,must_change_password FROM users WHERE id=?',(uid,)).fetchone()
    if not other:return Response('Utilisateur introuvable.',status_code=404)
    return page(req,'admin_user_detail.html',user=u,person=other,msg=req.query_params.get('msg',''))

@app.post('/admin/users/{uid}/update')
def admin_update_user(req:Request,uid:int,csrf:str=Form(...),email:str=Form(...),
                      full_name:str=Form(...),role:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    email=email.strip().lower();full_name=full_name.strip()
    if not full_name or len(full_name)>200 or '@' not in email or len(email)>200 or role not in ('admin','contributor','reviewer'):
        return Response('Informations du compte invalides.',status_code=400)
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        old=cx.execute('SELECT email,full_name,role FROM users WHERE id=?',(uid,)).fetchone()
        if not old:return Response('Utilisateur introuvable.',status_code=404)
        if uid==u['id'] and role!='admin':
            return Response('Vous ne pouvez pas retirer vos propres droits administrateur.',status_code=403)
        existing=cx.execute('SELECT id FROM users WHERE email=? AND id<>?',(email,uid)).fetchone()
        if existing:return Response('Cette adresse e-mail appartient à un autre compte.',status_code=409)
        cx.execute('UPDATE users SET email=?,full_name=?,role=? WHERE id=?',(email,full_name,role,uid))
        if role!=old['role']:
            cx.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        log(cx,u['id'],'admin_user_updated',details=json.dumps({'uid':uid,'previous':dict(old),
               'new':{'email':email,'full_name':full_name,'role':role}},ensure_ascii=False))
    return redirect(f'/admin/users/{uid}',msg='Informations du compte mises à jour.')

@app.post('/admin/users/{uid}/generate-password')
def admin_generate_password(req:Request,uid:int,csrf:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    new_password=generate_temp_password()
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        other=cx.execute('SELECT full_name,email FROM users WHERE id=?',(uid,)).fetchone()
        if not other:return Response('Utilisateur introuvable.',status_code=404)
        cx.execute('UPDATE users SET pw_hash=?,must_change_password=1 WHERE id=?',
                   (hash_password(new_password),uid))
        cx.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        log(cx,u['id'],'password_reset_generated',details=str(uid))
    return page(req,'admin_password_once.html',user=u,temporary_password=new_password,
                account_email=other['email'],account_name=other['full_name'],heading='Mot de passe temporaire renouvelé')

@app.post('/admin/users/{uid}/toggle')
def toggle_user(req:Request,uid:int,csrf:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    if uid==u['id']:return redirect('/admin/users',msg='Impossible de désactiver votre propre compte.')
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        other=cx.execute('SELECT active FROM users WHERE id=?',(uid,)).fetchone()
        if not other:return Response('Utilisateur introuvable.',status_code=404)
        cx.execute('UPDATE users SET active=? WHERE id=?',(0 if other['active'] else 1,uid))
        cx.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        log(cx,u['id'],'user_toggle',details=str(uid))
    return redirect('/admin/users',msg='Statut du compte modifié.')

@app.post('/admin/users/{uid}/password')
def admin_reset_password(req:Request,uid:int,csrf:str=Form(...),password:str=Form(...)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    if not 10<=len(password)<=256:return Response('Mot de passe : 10 à 256 caractères.',status_code=400)
    with db() as cx:
        cx.execute('BEGIN IMMEDIATE')
        if not cx.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone():
            return Response('Utilisateur introuvable.',status_code=404)
        cx.execute('UPDATE users SET pw_hash=?,must_change_password=1 WHERE id=?',(hash_password(password),uid))
        cx.execute('DELETE FROM sessions WHERE user_id=?',(uid,))
        log(cx,u['id'],'password_reset',details=str(uid))
    return redirect('/admin/users',msg='Mot de passe temporaire mis à jour.')

def duration_of(content, suffix):
    if suffix!='.wav':return None
    try:
        with wave.open(io.BytesIO(content),'rb') as w:
            return round(w.getnframes()/w.getframerate(),2)
    except (wave.Error,EOFError,ZeroDivisionError):return None

def valid_audio(content, suffix):
    if len(content)==0 or len(content)>MAX_AUDIO:return False
    if suffix=='.wav':return content[:4]==b'RIFF' and content[8:12]==b'WAVE'
    if suffix=='.mp3':return content[:3]==b'ID3' or (content[0]==255 and (content[1]&224)==224)
    if suffix=='.ogg':return content[:4]==b'OggS'
    if suffix=='.m4a':return content[4:8]==b'ftyp'
    if suffix=='.webm':return content[:4]==b'\x1a\x45\xdf\xa3'
    return False

def safe_code(filename):
    stem=Path(filename).stem
    stem=unicodedata.normalize('NFKD',stem).encode('ascii','ignore').decode('ascii')
    stem=''.join(c if c.isalnum() or c in '-_' else '_' for c in stem).strip('_')
    return (stem[:74] or 'audio')

def store_audio(cx, original_name, content, source_id, speaker_id, actor):
    filename=Path(original_name.replace('\\','/')).name
    suffix=Path(filename).suffix.lower()
    if suffix not in ACCEPT or not valid_audio(content,suffix):return 'format'
    duration=duration_of(content,suffix)
    digest=hashlib.sha256(content).hexdigest()
    # prevent exact audio duplicates regardless of filename
    exists=cx.execute('SELECT id FROM segments WHERE file_name LIKE ?',(digest+'%',)).fetchone()
    if exists:return 'duplicate'
    stored=digest+suffix
    code=safe_code(filename)
    base=code
    n=2
    while cx.execute('SELECT 1 FROM segments WHERE code=?',(code,)).fetchone():
        code=f'{base[:65]}_{n}';n+=1
    dest=AUDIO/stored
    store_audio_bytes(dest,stored,content,ACCEPT[suffix])
    try:
        sid=cx.execute('''INSERT INTO segments(code,original_name,file_name,mime,bytes,duration_seconds,source_id,speaker_id)
            VALUES(?,?,?,?,?,?,?,?)''',(code,filename,stored,ACCEPT[suffix],len(content),duration,source_id[:180],speaker_id[:180])).lastrowid
        log(cx,actor,'upload',sid,filename)
    except Exception:
        remove_audio_file(dest,stored)
        raise
    return 'ok'

@app.post('/admin/upload')
async def upload(req:Request,csrf:str=Form(...),source_id:str=Form(''),speaker_id:str=Form(''),audios:list[UploadFile]=File(default=[]),archive:UploadFile|None=File(default=None)):
    u=require(req,('admin',));err=secure(req,u,csrf)
    if err:return err
    result={'ok':0,'duplicate':0,'format':0,'too_large':0}
    with db() as cx:
        for f in audios:
            if not f.filename:continue
            content=await f.read(MAX_AUDIO+1)
            key='too_large' if len(content)>MAX_AUDIO else store_audio(cx,f.filename,content,source_id,speaker_id,u['id'])
            result[key]+=1
        if archive and archive.filename:
            if not archive.filename.lower().endswith('.zip'):
                return redirect('/admin',msg='L’archive doit être un fichier .zip')
            archive.file.seek(0,os.SEEK_END)
            archive_size=archive.file.tell()
            archive.file.seek(0)
            if archive_size>MAX_ARCHIVE:return redirect('/admin',msg='Archive trop volumineuse (>1 Go).')
            try:
                with zipfile.ZipFile(archive.file) as z:
                    entries=[m for m in z.infolist() if not m.is_dir() and Path(m.filename).suffix.lower() in ACCEPT]
                    if len(entries)>3000 or sum(m.file_size for m in entries)>MAX_ARCHIVE:
                        return redirect('/admin',msg='Archive trop volumineuse ou trop de fichiers.')
                    for entry in entries:
                        if entry.file_size>MAX_AUDIO:
                            result['too_large']+=1;continue
                        content=z.read(entry)
                        key=store_audio(cx,Path(entry.filename).name,content,source_id,speaker_id,u['id'])
                        result[key]+=1
            except (zipfile.BadZipFile,RuntimeError,ValueError):
                return redirect('/admin',msg='Archive ZIP invalide.')
    return redirect('/admin',msg=f"Importés : {result['ok']} ; doublons : {result['duplicate']} ; invalides : {result['format']} ; trop gros : {result['too_large']}.")

@app.get('/audio/{sid}')
def audio_file(req:Request,sid:int):
    u=require(req)
    if not u:return Response('Authentification requise',status_code=401)
    with db() as cx:
        s=cx.execute('SELECT * FROM segments WHERE id=?',(sid,)).fetchone()
        if not s:return Response('Introuvable',status_code=404)
        if s['archived_at'] is not None and u['role']!='admin':
            return Response('Segment archivé',status_code=403)
        allowed=(u['role']=='admin' or
                 (u['role']=='contributor' and ((s['assigned_to']==u['id'] and s['status'] in ('needs_changes','rejected'))
                   or (s['status']=='available' and cx.execute('SELECT 1 FROM working_leases WHERE segment_id=? AND user_id=? AND expires_at>?',(sid,u['id'],int(time.time()))).fetchone() is not None))) or
                 (u['role']=='reviewer' and s['status'] in ('submitted','approved','rejected','inaudible') and
                  not cx.execute('SELECT 1 FROM translations WHERE segment_id=? AND contributor_id=?',(sid,u['id'])).fetchone()))
    if not allowed:return Response('Accès refusé',status_code=403)
    if STORAGE_MODE in ('s3','r2'):
        try:
            obj=audio_response_payload(s['file_name'],req.headers.get('range'))
        except Exception:
            return Response('Audio temporairement indisponible',status_code=503)
        if not obj:return Response('Plage audio incorrecte',status_code=416)
        h={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff',
           'Content-Disposition':'inline','Accept-Ranges':'bytes'}
        if 'ContentRange' in obj:h['Content-Range']=obj['ContentRange']
        if 'ContentLength' in obj:h['Content-Length']=str(obj['ContentLength'])
        return StreamingResponse(iter_body(obj['Body']),media_type=s['mime'],
          status_code=206 if req.headers.get('range') else 200,headers=h)
    path=AUDIO/s['file_name']
    if not path.is_file():return Response('Fichier absent',status_code=404)
    return FileResponse(path,media_type=s['mime'],headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Content-Disposition':'inline'})

def export_rows(cx):
    return [dict(r) for r in cx.execute('''SELECT s.code segment_id,s.original_name audio_filename,
      s.duration_seconds,s.speaker_id,s.source_id, t.latin original_latin,t.french original_french,
      r.final_latin text_latin,r.final_french text_french,
      uc.full_name contributor,ur.full_name reviewer,r.created_at validated_at,s.file_name private_file
      FROM segments s JOIN translations t ON t.segment_id=s.id
      JOIN reviews r ON r.id=(SELECT max(r2.id) FROM reviews r2 WHERE r2.segment_id=s.id AND r2.decision='approved')
      JOIN users uc ON uc.id=t.contributor_id JOIN users ur ON ur.id=r.reviewer_id
      WHERE s.archived_at IS NULL AND s.status='approved' ORDER BY s.id''')]

def safe_csv(value):
    val='' if value is None else str(value)
    return "'"+val if val.lstrip().startswith(('=','+','-','@','\t','\r')) else val

@app.get('/admin/export/{format}')
def export(req:Request,format:str):
    u=require(req,('admin',))
    if not u:return Response('Accès refusé',status_code=403)
    with db() as cx:
        rows=export_rows(cx)
        log(cx,u['id'],'export_'+format,details=str(len(rows)))
    if format=='json':
        return Response(json.dumps([{k:v for k,v in r.items() if k!='private_file'} for r in rows],ensure_ascii=False,indent=2),
                 media_type='application/json; charset=utf-8',headers={'Content-Disposition':'attachment; filename="chadvoice_valide.json"','Cache-Control':'no-store'})
    if format=='csv':
        out=io.StringIO()
        cols=[k for k in rows[0] if k!='private_file'] if rows else ['segment_id','text_latin','text_french']
        wr=csv.DictWriter(out,fieldnames=cols);wr.writeheader()
        for r in rows:wr.writerow({k:safe_csv(r[k]) for k in cols})
        return Response('\ufeff'+out.getvalue(),media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition':'attachment; filename="chadvoice_valide.csv"','Cache-Control':'no-store'})
    if format=='zip':
        fd,tmp=tempfile.mkstemp(suffix='.zip');os.close(fd)
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=4) as z:
            z.writestr('manifest.json',json.dumps([{k:v for k,v in r.items() if k!='private_file'} for r in rows],ensure_ascii=False,indent=2))
            for r in rows:
                fn=r['private_file']
                copy_audio_to_zip(z,fn,'audio/'+r['segment_id']+Path(fn).suffix,AUDIO)
        return FileResponse(tmp,media_type='application/zip',filename='chadvoice_corpus_valide.zip',background=BackgroundTask(lambda:Path(tmp).unlink(missing_ok=True)),headers={'Cache-Control':'no-store'})
    return Response('Format inconnu',status_code=404)

@app.get('/admin/backup')
def backup(req:Request):
    u=require(req,('admin',))
    if not u:return Response('Accès refusé',status_code=403)
    if DB_URL:
        import datetime as dt
        # Portable snapshot of every SQL table plus every audio object.
        # Store encrypted at rest when downloaded; includes password hashes and user e-mails.
        tables=['users','sessions','segments','translations','translation_versions','reviews',
                'working_leases','notifications','mail_outbox','audit_logs']
        with db() as cx:
            log(cx,u['id'],'full_backup')
            snapshot={name:[dict(r) for r in cx.execute('SELECT * FROM '+name)] for name in tables}
        fd,tmp=tempfile.mkstemp(suffix='.zip');os.close(fd)
        try:
            with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=3,allowZip64=True) as z:
                z.writestr('database.json',json.dumps({'format':'chadvoice-postgres-v1',
                    'generated_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
                    'tables':snapshot},ensure_ascii=False,indent=2))
                for entry in snapshot['segments']:
                    copy_audio_to_zip(z,entry['file_name'],'audio/'+entry['file_name'],AUDIO)
        except Exception:
            Path(tmp).unlink(missing_ok=True)
            raise
        return FileResponse(tmp,media_type='application/zip',filename='chadvoice_sauvegarde_complete.zip',
          background=BackgroundTask(lambda:Path(tmp).unlink(missing_ok=True)),headers={'Cache-Control':'no-store'})
    fd,tmp=tempfile.mkstemp(suffix='.zip');os.close(fd)
    ssfd,ssname=tempfile.mkstemp(suffix='.sqlite3');os.close(ssfd)
    snapshot=Path(ssname)
    with db() as source,sqlite3.connect(snapshot) as target:
        log(source,u['id'],'full_backup')
        source.backup(target)
    try:
        with zipfile.ZipFile(tmp,'w',zipfile.ZIP_DEFLATED,compresslevel=4) as z:
            z.write(snapshot,'chadvoice.sqlite3')
            for fp in AUDIO.iterdir():
                if fp.is_file():z.write(fp,'audio/'+fp.name)
    finally:snapshot.unlink(missing_ok=True)
    return FileResponse(tmp,media_type='application/zip',filename='chadvoice_sauvegarde_complete.zip',background=BackgroundTask(lambda:Path(tmp).unlink(missing_ok=True)),headers={'Cache-Control':'no-store'})

init_db()
