-- HAK v1 schema — per spec v0.5.1 (docs/drafts/2026-09-01_hak-spec-v0.5.1.md)
-- SQLite, WAL mode. Append-only: messages and scope_events are never UPDATEd
-- (lapse markers excepted — compaction-time replay clarity only, D15/D23).
-- Everything mutable that clients see is a projector (D2).

-- HAK — inter-agent messaging bus. Copyright (C) 2026 asb (operator seat).
-- SPDX-License-Identifier: AGPL-3.0-only
-- This file is part of HAK. See LICENSE for the full notice.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Rooms + charter (immutable after creation in v1, D32; defaults materialized, D46)
CREATE TABLE IF NOT EXISTS rooms (
  name        TEXT PRIMARY KEY,            -- URL identifier, immutable (D43)
  charter     TEXT NOT NULL,               -- canonical JSON, fully populated (D46)
  created_at  TEXT NOT NULL
);

-- Tokens: N per seat (D19); stored hashed SHA-256 unsalted (D43); shown once.
CREATE TABLE IF NOT EXISTS tokens (
  token_id    TEXT PRIMARY KEY,
  seat       TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,         -- sha256 hex of the bearer secret
  created_at TEXT NOT NULL,
  revoked_at TEXT                          -- NULL = live; set = immediate 401 (D24)
);

-- Membership state machine (D36): pending | member | revoked.
-- Row is replaced on revoked-seat rejoin (new pending). operator non-revocable (D45)
-- enforced in app layer with 422.
CREATE TABLE IF NOT EXISTS memberships (
  room   TEXT NOT NULL REFERENCES rooms(name),
  seat   TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','member','revoked')),
  PRIMARY KEY (room, seat)
);

-- Message envelopes — append-only, total order per room (D10/D14).
-- seq: per-room, 1-based, gapless; allocated inside BEGIN IMMEDIATE.
-- idem_hash: JCS hash of the normalized client body (D12/D31) — nullable
--   (client_msg_id optional, D4).
CREATE TABLE IF NOT EXISTS messages (
  id            TEXT PRIMARY KEY,          -- m_<room>_<seq> (globally unique; seq is per-room, D10)
  room          TEXT NOT NULL REFERENCES rooms(name),
  seq           INTEGER NOT NULL,
  client_msg_id TEXT,                      -- dedupe key part (D11)
  idem_hash     TEXT,                      -- canonical content hash (D12/D31)
  from_seat     TEXT NOT NULL,
  backend       TEXT,                      -- free string, metadata only (Q3)
  to_seat       TEXT,                      -- NULL = broadcast
  type          TEXT NOT NULL CHECK (type IN
                  ('chat','status','task_request','task_result',
                   'artifact_ref','review_verdict','retraction')),
  reply_to      TEXT REFERENCES messages(id),
  body          TEXT NOT NULL,
  attachments   TEXT,                      -- JSON array (room-scoped file_ids, D29)
  refs          TEXT,                      -- JSON array of {uri, note}
  meta          TEXT,                      -- JSON object, closed kind set (D39)
  ts            TEXT NOT NULL,             -- server wall clock, informational (D21)
  UNIQUE (room, seq),
  UNIQUE (room, from_seat, client_msg_id)  -- D11 uniqueness key
);
CREATE INDEX IF NOT EXISTS idx_messages_room_since ON messages(room, seq);
CREATE INDEX IF NOT EXISTS idx_messages_reply  ON messages(reply_to);

-- Scope events — append-only, separate log, own gapless per-room scope_seq (D14).
-- action: claim | renew | release | lapse. Liveness derives from events + TTL (D15);
-- never from lapse markers.
CREATE TABLE IF NOT EXISTS scope_events (
  scope_seq    INTEGER NOT NULL,
  room         TEXT NOT NULL REFERENCES rooms(name),
  scope_id     TEXT NOT NULL,
  seat         TEXT NOT NULL,
  action       TEXT NOT NULL CHECK (action IN ('claim','renew','release','lapse')),
  resource_uri TEXT NOT NULL,              -- exact normalized URI (D34)
  kind         TEXT NOT NULL CHECK (kind IN ('write','read-exclusive','exclusive','share')),
  units        INTEGER NOT NULL DEFAULT 1, -- share units consumed (D34)
  note         TEXT,
  ttl_min      INTEGER NOT NULL,
  expires_at   TEXT NOT NULL,              -- derived: last event + TTL
  ts           TEXT NOT NULL,
  UNIQUE (room, scope_seq)
);
CREATE INDEX IF NOT EXISTS idx_scopes_lookup ON scope_events(room, resource_uri, scope_id);

-- Files — room-scoped (D29); sweep is the only deleter (D23);
-- deletion DB-authoritative: deletion_pending commits with the envelope (D44),
-- unlink retried by later sweeps.
CREATE TABLE IF NOT EXISTS files (
  file_id          TEXT PRIMARY KEY,
  room             TEXT NOT NULL REFERENCES rooms(name),
  uploader         TEXT NOT NULL,
  name             TEXT NOT NULL,          -- display-only, never a filesystem path (D43)
  content_type     TEXT NOT NULL,
  sha256           TEXT NOT NULL,
  size             INTEGER NOT NULL,
  created_at       TEXT NOT NULL,
  deletion_pending INTEGER NOT NULL DEFAULT 0,
  deleted_at       TEXT                   -- set when unlink finally succeeded
);

-- Read/presence projector state (UX only, Q6; activity semantics, D27/D43).
CREATE TABLE IF NOT EXISTS member_state (
  room         TEXT NOT NULL REFERENCES rooms(name),
  seat         TEXT NOT NULL,
  last_read_seq INTEGER NOT NULL DEFAULT 0,
  last_poll    TEXT,
  PRIMARY KEY (room, seat)
);
