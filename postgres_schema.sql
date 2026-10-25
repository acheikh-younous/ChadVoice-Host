-- ChadVoice v1.2 PostgreSQL schema. All times stored as UTC, normalized strings for compatibility.
CREATE TABLE IF NOT EXISTS users (
 id BIGSERIAL PRIMARY KEY, email TEXT NOT NULL UNIQUE, full_name TEXT NOT NULL,
 pw_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','reviewer','contributor')),
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE TABLE IF NOT EXISTS user_presence (
 user_id BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
 last_seen BIGINT NOT NULL DEFAULT 0,
 last_path TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 csrf TEXT NOT NULL, expiry BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS segments (
 id BIGSERIAL PRIMARY KEY, code TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL,
 file_name TEXT NOT NULL UNIQUE, mime TEXT NOT NULL, bytes BIGINT NOT NULL,
 duration_seconds DOUBLE PRECISION, speaker_id TEXT NOT NULL DEFAULT '', source_id TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL DEFAULT 'available' CHECK(status IN ('available','assigned','draft','submitted','needs_changes','approved','rejected','inaudible')),
 assigned_to BIGINT REFERENCES users(id), expires_at BIGINT, archived_at TEXT,
 created_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE TABLE IF NOT EXISTS translations (
 id BIGSERIAL PRIMARY KEY, segment_id BIGINT NOT NULL UNIQUE REFERENCES segments(id) ON DELETE CASCADE,
 contributor_id BIGINT NOT NULL REFERENCES users(id), latin TEXT NOT NULL DEFAULT '', french TEXT NOT NULL DEFAULT '',
 reported_inaudible INTEGER NOT NULL DEFAULT 0, submitted_at TEXT,
 updated_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE TABLE IF NOT EXISTS translation_versions (
 id BIGSERIAL PRIMARY KEY, segment_id BIGINT NOT NULL REFERENCES segments(id),
 contributor_id BIGINT NOT NULL REFERENCES users(id), latin TEXT NOT NULL DEFAULT '', french TEXT NOT NULL DEFAULT '',
 reported_inaudible INTEGER NOT NULL DEFAULT 0, stage TEXT NOT NULL CHECK(stage IN ('draft','submit')),
 created_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE TABLE IF NOT EXISTS reviews (
 id BIGSERIAL PRIMARY KEY, segment_id BIGINT NOT NULL REFERENCES segments(id),
 reviewer_id BIGINT NOT NULL REFERENCES users(id),
 decision TEXT NOT NULL CHECK(decision IN ('approved','needs_changes','rejected','inaudible')),
 final_latin TEXT NOT NULL DEFAULT '', final_french TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE TABLE IF NOT EXISTS working_leases (
 segment_id BIGINT PRIMARY KEY REFERENCES segments(id) ON DELETE CASCADE,
 user_id BIGINT NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
 expires_at BIGINT NOT NULL,
 created_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE TABLE IF NOT EXISTS notifications (
 id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 segment_id BIGINT REFERENCES segments(id), category TEXT NOT NULL, title TEXT NOT NULL,
 message TEXT NOT NULL, read_at TEXT,
 created_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id,read_at,id);
CREATE TABLE IF NOT EXISTS mail_outbox (
 id BIGSERIAL PRIMARY KEY, notification_id BIGINT NOT NULL UNIQUE REFERENCES notifications(id) ON DELETE CASCADE,
 email TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','sent','error')),
 attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '', sent_at TEXT,
 created_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id BIGSERIAL PRIMARY KEY, actor_id BIGINT REFERENCES users(id), action TEXT NOT NULL,
 segment_id BIGINT REFERENCES segments(id), details TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT to_char(now() at time zone 'UTC','YYYY-MM-DD HH24:MI:SS')
);
CREATE INDEX IF NOT EXISTS ix_segment_status ON segments(status,id);
CREATE INDEX IF NOT EXISTS ix_reviewer_seg ON reviews(segment_id,id);

ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password INTEGER NOT NULL DEFAULT 0;
