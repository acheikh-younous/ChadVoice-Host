"""Isolated cloud-adapter tests; no real Neon/R2 credentials required."""
import sys
import types
import io
from pathlib import Path
from unittest.mock import MagicMock


def test_pg_query_conversion_and_hybrid_row(monkeypatch):
    from postgres_backend import PgConnection
    psycopg=types.ModuleType('psycopg')
    rows=types.ModuleType('psycopg.rows')
    rows.dict_row=object()
    monkeypatch.setitem(sys.modules,'psycopg',psycopg)
    monkeypatch.setitem(sys.modules,'psycopg.rows',rows)
    class Cursor:
        description=['id']
        def __init__(self):self.statement=''; self.parameters=()
        def execute(self,s,p):self.statement=s; self.parameters=p
        def fetchone(self):return {'id':29}
        def fetchall(self):return [{'id':29,'email':'cv@test'}]
    class Conn:
        def __init__(self):self.c=Cursor()
        def cursor(self,row_factory=None):return self.c
    pg=PgConnection(Conn())
    ret=pg.execute('INSERT INTO users(email,full_name,pw_hash,role) VALUES(?,?,?,?)',('cv@test','X','h','admin'))
    assert ret.lastrowid==29
    assert '%s' in pg.conn.c.statement and 'RETURNING id' in pg.conn.c.statement
    res=pg.execute("SELECT id FROM users WHERE email=? AND created_at<datetime('now')",('cv@test',))
    assert res.fetchone()[0]==29
    assert res.fetchone()['id']==29
    assert 'to_char(now()' in pg.conn.c.statement


def test_s3_path_and_chunking(monkeypatch,tmp_path):
    import object_storage as st
    class FakeBody(io.BytesIO):
        def __init__(self,data):super().__init__(data);self.closed_seen=False
        def close(self):self.closed_seen=True;super().close()
    class Fake:
        def __init__(self):self.objects={}
        def put_object(self,**kwargs):self.objects[kwargs['Key']]=kwargs['Body']
        def get_object(self,**kwargs):
            payload=self.objects[kwargs['Key']]
            return {'Body':FakeBody(payload),'ContentLength':len(payload)}
        def delete_object(self,**kwargs):self.objects.pop(kwargs['Key'])
    fake=Fake()
    monkeypatch.setattr(st,'MODE','s3')
    monkeypatch.setattr(st,'r2_client',lambda:fake)
    monkeypatch.setattr(st,'bucket_name',lambda:'private')
    st.store_audio_bytes(tmp_path/'x.wav','x.wav',b'hello','audio/wav')
    assert fake.objects['audio/x.wav']==b'hello'
    data=st.audio_response_payload('x.wav')
    assert b''.join(st.iter_body(data['Body']))==b'hello'
    assert st.audio_response_payload('x.wav','bad range') is None
    st.remove_audio_file(tmp_path/'x.wav','x.wav')
    assert fake.objects=={}


def test_pg_advisory_lock_uses_cursor(monkeypatch):
    """Guard the recent lock fix without connecting to PostgreSQL."""
    from postgres_backend import PgConnection
    rows = types.ModuleType('psycopg.rows')
    rows.dict_row = object()
    monkeypatch.setitem(sys.modules, 'psycopg', types.ModuleType('psycopg'))
    monkeypatch.setitem(sys.modules, 'psycopg.rows', rows)
    cursor = MagicMock()
    cursor.fetchone.return_value = {'pg_advisory_xact_lock': None}
    connection = MagicMock()
    connection.cursor.return_value = cursor
    result = PgConnection(connection).execute('BEGIN IMMEDIATE')
    connection.cursor.assert_called_once_with(row_factory=rows.dict_row)
    cursor.execute.assert_called_once_with('SELECT pg_advisory_xact_lock(2026101701)')
    connection.execute.assert_not_called()
    assert result.fetchone()[0] is None
