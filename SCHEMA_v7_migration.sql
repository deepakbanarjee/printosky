-- PRINTOSKY SCHEMA v7 — Job Lifecycle Tracking
-- Run in Supabase SQL Editor after SCHEMA_v6.
-- Also applied automatically to local SQLite by watcher.py / print_server.py on startup.
--
-- Changes:
--   1. job_events table — full audit trail of every status transition and action
--   2. New columns on jobs — file_source, colour_page_map, parent_job_id, is_sub_job,
--      sub_job_type, colour_confirmed, collation_warning

-- ── 1. job_events table ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS job_events (
    id           BIGSERIAL PRIMARY KEY,          -- Supabase (SQLite uses INTEGER PRIMARY KEY)
    job_id       TEXT        NOT NULL,
    staff_id     TEXT,                           -- who performed the action (NULL = system/auto)
    action       TEXT        NOT NULL,           -- e.g. 'file_received', 'print_sent', 'status_change'
    from_status  TEXT,
    to_status    TEXT,
    notes        TEXT,
    duration_sec INTEGER,                        -- seconds since previous event on same job
    created_at   TIMESTAMPTZ DEFAULT NOW()       -- SQLite: TEXT DEFAULT (datetime('now'))
);

-- Indexes for Supabase
CREATE INDEX IF NOT EXISTS idx_job_events_job_id    ON job_events (job_id);
CREATE INDEX IF NOT EXISTS idx_job_events_created   ON job_events (created_at);
CREATE INDEX IF NOT EXISTS idx_job_events_action    ON job_events (action);

-- RLS
ALTER TABLE job_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "auth read job_events"  ON job_events;
DROP POLICY IF EXISTS "auth write job_events" ON job_events;

CREATE POLICY "auth read job_events"
    ON job_events FOR SELECT
    USING (auth.role() = 'authenticated');

-- supabase_sync.py uses service_role key which bypasses RLS for inserts

-- ── 2. New columns on jobs ────────────────────────────────────────────────────
-- Run each ALTER individually; ignore errors if column already exists.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS file_source       TEXT;          -- 'whatsapp'|'usb'|'email'|'gdrive'|'hotfolder'|'walk-in'
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS colour_page_map   TEXT;          -- JSON: {"colour":[1,5],"bw":[2,3,4]}
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS colour_confirmed  INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS parent_job_id     TEXT;          -- for sub-jobs
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS is_sub_job        INTEGER DEFAULT 0;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS sub_job_type      TEXT;          -- 'bw' | 'col'
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS collation_warning INTEGER DEFAULT 0;
