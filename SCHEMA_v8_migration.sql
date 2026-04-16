-- PRINTOSKY SCHEMA v8 — Work Session Timer (Sprint 12)
-- Run in Supabase SQL Editor after SCHEMA_v7.
-- Also applied automatically to local SQLite by watcher.py / print_server.py on startup.
--
-- Changes:
--   1. work_sessions table — per-job timer records (start/pause/resume/end)
--   2. New columns on jobs — service_type, dtp_pages, graph_count, editing_minutes

-- ── 1. work_sessions table ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS work_sessions (
    id          BIGSERIAL PRIMARY KEY,          -- SQLite: INTEGER PRIMARY KEY AUTOINCREMENT
    job_id      TEXT        NOT NULL,
    staff_id    TEXT        NOT NULL,
    started_at  TEXT        NOT NULL,
    paused_at   TEXT,
    resumed_at  TEXT,
    ended_at    TEXT,
    total_sec   INTEGER,                        -- calculated on end; NULL while open
    paused_sec  INTEGER     DEFAULT 0,          -- cumulative seconds spent paused (accumulated on each resume)
    notes       TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()       -- SQLite: TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_work_sessions_job    ON work_sessions (job_id);
CREATE INDEX IF NOT EXISTS idx_work_sessions_staff  ON work_sessions (staff_id);
CREATE INDEX IF NOT EXISTS idx_work_sessions_open   ON work_sessions (job_id) WHERE ended_at IS NULL;

-- RLS
ALTER TABLE work_sessions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "auth read work_sessions"  ON work_sessions;
DROP POLICY IF EXISTS "auth write work_sessions" ON work_sessions;

CREATE POLICY "auth read work_sessions"
    ON work_sessions FOR SELECT
    USING (auth.role() = 'authenticated');

-- supabase_sync.py uses service_role key which bypasses RLS for inserts

-- ── 2. New columns on jobs ────────────────────────────────────────────────────

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_type      TEXT DEFAULT 'print';
-- service_type: 'print' | 'editing' | 'dtp' | 'graph' | 'scanning' | 'photocopy' | 'mixed'

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS dtp_pages         INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS graph_count       INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS editing_minutes   INTEGER DEFAULT 0;
