-- api/migrations/SCHEMA_v17_help_escape.sql
-- TASK-009 (roadmap-2026-05): help-keyword escape hatch
-- Adds a "needs_human" flag + timestamp to bot_sessions so the admin UI can
-- surface conversations the bot can't handle.

-- 1. Flag a session as awaiting a human
ALTER TABLE bot_sessions
    ADD COLUMN IF NOT EXISTS needs_human BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. Track when the customer first asked for help (for staff triage order)
ALTER TABLE bot_sessions
    ADD COLUMN IF NOT EXISTS last_help_request_at TIMESTAMPTZ;

-- 3. Index for the admin "Needs human" filter (small expected cardinality;
--    partial index keeps it tight)
CREATE INDEX IF NOT EXISTS idx_bot_sessions_needs_human
    ON bot_sessions (last_help_request_at DESC)
    WHERE needs_human = TRUE;
