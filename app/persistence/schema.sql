CREATE TABLE IF NOT EXISTS samples (
  id         TEXT PRIMARY KEY,
  owner_id   TEXT NOT NULL,
  name       TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS files (
  id            TEXT PRIMARY KEY,
  sample_id     TEXT NOT NULL REFERENCES samples(id),
  filename      TEXT NOT NULL,
  qc_status     TEXT NOT NULL DEFAULT 'pending'
                CHECK (qc_status IN ('pending', 'passed', 'failed')),
  qc_updated_at TEXT,
  created_at    TEXT NOT NULL,
  UNIQUE (sample_id, filename)
);

CREATE INDEX IF NOT EXISTS ix_files_sample ON files(sample_id);

CREATE TABLE IF NOT EXISTS access_grants (
  sample_id  TEXT NOT NULL REFERENCES samples(id),
  user_id    TEXT NOT NULL,
  granted_by TEXT NOT NULL,
  granted_at TEXT NOT NULL,
  PRIMARY KEY (sample_id, user_id)
);
