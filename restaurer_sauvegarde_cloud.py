"""Restore ChadVoice PostgreSQL + R2 snapshot into a NEW/EMPTY target, not on top of live data.
Requires DATABASE_URL + R2 credentials in environment. Intended for recovery/testing on a fresh project.
Usage: python restaurer_sauvegarde_cloud.py chadvoice_sauvegarde_complete.zip
"""
import os,sys,zipfile,json
if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Usage: python restaurer_sauvegarde_cloud.py backup.zip')
    if not os.getenv('DATABASE_URL') or os.getenv('CHADVOICE_AUDIO_STORAGE') not in ('s3','r2'):
        raise SystemExit('DATABASE_URL et CHADVOICE_AUDIO_STORAGE=s3 requis')
    from postgres_backend import init_postgres, pg_db
    from object_storage import store_audio_bytes
    from pathlib import Path
    init_postgres()
    with zipfile.ZipFile(sys.argv[1]) as z:
        payload=json.loads(z.read('database.json'))
        if payload.get('format')!='chadvoice-postgres-v1':raise SystemExit('Format de sauvegarde non reconnu')
        tables=payload['tables']
        order=['users','segments','translations','translation_versions','reviews',
               'working_leases','notifications','mail_outbox','audit_logs']
        with pg_db() as db:
            if any(db.execute('SELECT 1 FROM '+t+' LIMIT 1').fetchone() for t in order):
                raise SystemExit('Base cible non vide. Arrêt pour éviter toute destruction.')
            for table in order:
                rows=tables.get(table,[])
                for row in rows:
                    cols=list(row)
                    db.conn.execute(f'INSERT INTO "{table}" ('+','.join('"'+c+'"' for c in cols)+') VALUES ('+
                      ','.join('%s' for _ in cols)+')',tuple(row[c] for c in cols))
                if rows:
                    db.conn.execute('SELECT setval(pg_get_serial_sequence(%s,%s),%s,true)',
                      (table,'id',max(r['id'] for r in rows)))
            for row in tables.get('segments',[]):
                name='audio/'+row['file_name']
                content=z.read(name)
                store_audio_bytes(Path(name),row['file_name'],content,row['mime'])
    print('Restauration terminée. Vérifier les comptes, audios et statistiques.')
