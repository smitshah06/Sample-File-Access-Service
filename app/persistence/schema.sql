CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('admin', 'user')),
  is_active     INTEGER NOT NULL DEFAULT 1,
  failed_logins INTEGER NOT NULL DEFAULT 0,  -- within the current window (see last_failed_at)
  last_failed_at TEXT,
  locked_until  TEXT,
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,          -- sha256 of the cookie value; the token itself is never stored
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS samples (
  id         TEXT PRIMARY KEY,
  owner_id   TEXT NOT NULL REFERENCES users(id),
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_samples_owner ON samples(owner_id);

CREATE TABLE IF NOT EXISTS files (
  id            TEXT PRIMARY KEY,
  sample_id     TEXT NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
  filename      TEXT NOT NULL,
  qc_status     TEXT NOT NULL DEFAULT 'pending'
                CHECK (qc_status IN ('pending', 'passed', 'failed')),
  qc_updated_at TEXT,
  created_at    TEXT NOT NULL,
  size_bytes    INTEGER,                -- NULL for files registered by metadata only
  sha256        TEXT,
  content_type  TEXT,
  storage_key   TEXT,
  UNIQUE (sample_id, filename)
);

CREATE INDEX IF NOT EXISTS ix_files_sample ON files(sample_id);

-- In-progress resumable uploads. Bytes are appended to storage/<id>.part chunk by chunk;
-- on completion the part becomes a file whose id is the upload id.
CREATE TABLE IF NOT EXISTS uploads (
  id             TEXT PRIMARY KEY,
  sample_id      TEXT NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
  owner_id       TEXT NOT NULL REFERENCES users(id),
  filename       TEXT NOT NULL,
  content_type   TEXT NOT NULL,
  size_bytes     INTEGER NOT NULL CHECK (size_bytes >= 0),
  received_bytes INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  expires_at     TEXT NOT NULL,
  UNIQUE (sample_id, filename)
);

CREATE TABLE IF NOT EXISTS access_grants (
  sample_id  TEXT NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
  user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  granted_by TEXT NOT NULL,
  granted_at TEXT NOT NULL,
  PRIMARY KEY (sample_id, user_id)
);

CREATE INDEX IF NOT EXISTS ix_grants_user ON access_grants(user_id);

CREATE TABLE IF NOT EXISTS audit_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  event     TEXT NOT NULL,
  actor_id  TEXT,
  sample_id TEXT,
  file_id   TEXT,
  details   TEXT NOT NULL DEFAULT '{}'   -- JSON; IDs and codes only, never names, tokens or URLs
);

CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_events(ts);
