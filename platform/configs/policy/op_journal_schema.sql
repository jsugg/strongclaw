PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS op (
  op_id              TEXT PRIMARY KEY,
  created_at_ms      INTEGER NOT NULL,
  updated_at_ms      INTEGER NOT NULL,
  scope              TEXT NOT NULL,
  idempotency_key    TEXT NOT NULL,
  kind               TEXT NOT NULL,
  trust_zone         TEXT NOT NULL,
  normalized_target  TEXT NOT NULL,
  inputs_json        TEXT NOT NULL,
  inputs_hash        TEXT NOT NULL,
  status             TEXT NOT NULL,
  attempt            INTEGER NOT NULL DEFAULT 0,
  last_error         TEXT,
  compensation_state TEXT,
  UNIQUE(scope, idempotency_key)
);
