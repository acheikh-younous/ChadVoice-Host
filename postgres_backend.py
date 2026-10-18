"""Minimal psycopg compatibility shim for legacy ChadVoice SQL and SQLite row usage.
Keep this module small and replace with a structured database layer in future releases.
"""
import os
import re
from contextlib import contextmanager
from pathlib import Path

DB_URL = os.environ.get('DATABASE_URL','').strip()
TABLES_WITH_IDS = {'users','segments','translations','translation_versions','reviews','notifications','mail_outbox','audit_logs'}

class HybridRow(dict):
    def __getitem__(self, key):
        if isinstance(key,int):
            return list(self.values())[key]
        return super().__getitem__(key)

class QueryResult:
    def __init__(self, cursor, inserted=False):
        self.cursor=cursor
        self.lastrowid=None
        if inserted and cursor.description:
            row=cursor.fetchone()
            if row: self.lastrowid=row['id']
    def fetchone(self):
        row=self.cursor.fetchone()
        return HybridRow(row) if row is not None else None
    def fetchall(self):
        return [HybridRow(x) for x in self.cursor.fetchall()]
    def __iter__(self):
        for x in self.cursor: yield HybridRow(x)

class PgConnection:
    def __init__(self,conn): self.conn=conn
    def execute(self,sql,params=()):
        import psycopg
        from psycopg.rows import dict_row
        s=sql.strip()
        if s.upper()=='BEGIN IMMEDIATE':
            cur=self.conn.cursor(row_factory=dict_row)
            cur.execute('SELECT pg_advisory_xact_lock(2026101701)')
            return QueryResult(cur)
        # Compatible SQL string/time syntax. UTC timestamps use text fields as in SQLite.
        s=re.sub(r"datetime\('now'\)","to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')",s,flags=re.IGNORECASE)
        s=re.sub(r'\bINSERT\s+OR\s+IGNORE\s+INTO\b','INSERT INTO',s,flags=re.IGNORECASE)
        ignore=bool(re.search(r'INSERT\s+OR\s+IGNORE\s+INTO',sql,re.IGNORECASE))
        if ignore and 'ON CONFLICT' not in s.upper(): s += ' ON CONFLICT DO NOTHING'
        inserted=False
        match=re.match(r'INSERT\s+INTO\s+(\w+)',s,re.IGNORECASE)
        if match and match.group(1).lower() in TABLES_WITH_IDS and 'RETURNING' not in s.upper():
            inserted=True
            s += ' RETURNING id'
        # Question-mark placeholders used by legacy sqlite3 queries.
        s=s.replace('?', '%s')
        cur=self.conn.cursor(row_factory=dict_row)
        cur.execute(s,tuple(params))
        return QueryResult(cur,inserted)

@contextmanager
def pg_db():
    import psycopg
    if not DB_URL: raise RuntimeError('DATABASE_URL missing')
    # One connection per request/context; use Neon pooled URL for production.
    with psycopg.connect(DB_URL, connect_timeout=12, application_name='chadvoice') as conn:
        yield PgConnection(conn)


def init_postgres():
    from psycopg.rows import dict_row
    import psycopg
    sql=Path(__file__).with_name('postgres_schema.sql').read_text(encoding='utf-8')
    with psycopg.connect(DB_URL, connect_timeout=12) as conn:
        # Schema is idempotent and does not reset or erase scientific data.
        for statement in sql.split(';'):
            statement=statement.strip()
            if statement: conn.execute(statement)
