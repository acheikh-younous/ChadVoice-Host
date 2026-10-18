"""Migration one-time: local ChadVoice SQLite + audio -> empty Neon PostgreSQL + private R2.

Usage (from terminal with secrets only in environment):
  python migrer_sqlite_vers_cloud.py "C:/.../private_data"

Stop local app, keep a full untouched backup and verify permissions/size before use.
NO deletion of local files. Never commit source DB, tokens, or exports to GitHub.
"""
import os
import sys
import sqlite3
import hashlib
from pathlib import Path

TABLES=['users','segments','translations','translation_versions','reviews',
        'working_leases','notifications','mail_outbox','audit_logs']
# Intentionally omit sessions: local login cookies must not carry over to public web.

def main():
    if len(sys.argv)!=2: raise SystemExit('Usage: python migrer_sqlite_vers_cloud.py CHEMIN_PRIVATE_DATA')
    if not os.getenv('DATABASE_URL') or os.getenv('CHADVOICE_AUDIO_STORAGE') not in ('s3','r2'):
        raise SystemExit('DATABASE_URL and CHADVOICE_AUDIO_STORAGE=s3 are mandatory.')
    src=Path(sys.argv[1]).expanduser().resolve()
    sqlite_file=src/'chadvoice.sqlite3'; audios=src/'audio'
    if not sqlite_file.is_file() or not audios.is_dir():raise SystemExit('private_data incomplet')
    from postgres_backend import init_postgres,pg_db
    from object_storage import store_audio_bytes
    init_postgres()
    with sqlite3.connect(f'file:{sqlite_file.as_posix()}?mode=ro',uri=True) as local:
        local.row_factory=sqlite3.Row
        counts={t:local.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in TABLES}
        segments=[dict(r) for r in local.execute('SELECT file_name,mime FROM segments')]
        print('Source SQLite :',counts,'audios:',len(segments))
        with pg_db() as remote:
            if any(remote.execute('SELECT 1 FROM '+t+' LIMIT 1').fetchone() for t in TABLES):
                raise SystemExit('Neon NON VIDE : migration annulée pour éviter écrasements et doublons.')
            # Schema FK order; passwords are preserved as original hashes.
            for table in TABLES:
                rows=[dict(row) for row in local.execute('SELECT * FROM '+table)]
                if not rows: continue
                cols=list(rows[0]); q=','.join(['%s']*len(cols))
                # psycopg direct statement, explicit original IDs.
                quoted=','.join('"'+col+'"' for col in cols)
                sql=f'INSERT INTO "{table}" ({quoted}) VALUES ({q})'
                for row in rows:
                    remote.conn.execute(sql,tuple(row[col] for col in cols))
                if 'id' in cols:
                    max_id=max(int(r['id']) for r in rows)
                    remote.conn.execute('SELECT setval(pg_get_serial_sequence(%s,%s),%s,true)',(table,'id',max_id))
                print('Import table:',table,len(rows))
            for entry in segments:
                fp=audios/entry['file_name']
                if not fp.is_file():raise FileNotFoundError('Audio manquant : '+str(fp))
                content=fp.read_bytes()
                # Verify content hash before upload. Same scheme as ChadVoice local.
                if not entry['file_name'].startswith(hashlib.sha256(content).hexdigest()):
                    raise RuntimeError('Empreinte audio non conforme : '+str(fp))
                store_audio_bytes(fp,entry['file_name'],content,entry['mime'])
            for table,count in counts.items():
                actual=remote.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]
                if actual!=count:raise RuntimeError(f'Nombre incorrect : {table} {actual}!={count}')
    print('Migration terminée. La base locale reste intacte. Vérifier le site avant la collecte.')

if __name__=='__main__':main()
