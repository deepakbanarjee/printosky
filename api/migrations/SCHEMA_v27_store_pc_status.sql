-- SCHEMA v27 — Store-PC liveness tracking (opening / closing / offline alerts)
--
-- Backs the /cron/store-pc-check heartbeat watcher. The store PC already writes
-- daily_summary.synced_at every ~5 min; the cron compares that heartbeat to now
-- and uses this table to remember the last known up/down state so it fires the
-- opening message (down -> up) and the closing message + day/week/month logs
-- (up -> down) exactly once per transition.
--
-- One row per store. RLS enabled with no policy: only the service role (which
-- bypasses RLS) can read/write; anon/public clients get no access.
CREATE TABLE IF NOT EXISTS store_pc_status (
    store_id          text PRIMARY KEY,
    state             text        NOT NULL DEFAULT 'unknown',  -- 'up' | 'down' | 'unknown'
    last_up_at        timestamptz,
    last_down_at      timestamptz,
    last_heartbeat_at text,        -- last observed daily_summary.synced_at (IST wall-clock text)
    opening_sent_date text,        -- YYYY-MM-DD guard: opening alerted once per day
    closing_sent_date text,        -- YYYY-MM-DD guard: closing alerted once per close
    clean_shutdown    boolean     NOT NULL DEFAULT false,      -- set by the PC clean-shutdown ping
    updated_at        timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE store_pc_status ENABLE ROW LEVEL SECURITY;
