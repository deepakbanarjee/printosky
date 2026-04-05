-- SCHEMA v10 — Cloud tables for Vercel deployment
-- Run in Supabase SQL Editor before deploying to Vercel.
-- These tables hold bot conversation state, batch orders, and customer profiles
-- that were previously SQLite-only on the store PC.

-- ── bot_sessions ──────────────────────────────────────────────────────────────
-- Real-time conversation state (one row per active customer phone).
-- High write volume (~10 UPSERTs per conversation). TTL ~24h in practice.
CREATE TABLE IF NOT EXISTS bot_sessions (
    phone               TEXT PRIMARY KEY,
    job_id              TEXT,
    step                TEXT,
    size                TEXT,
    colour              TEXT,
    layout              TEXT,
    multiup_per         TEXT,
    multiup_sided       TEXT,
    copies              INTEGER,
    finishing           TEXT,
    delivery            INTEGER DEFAULT 0,
    page_count          INTEGER DEFAULT 0,
    batch_id            TEXT,
    current_job_index   INTEGER DEFAULT 0,
    jobs_json           TEXT,
    saved_json          TEXT,
    job_settings_json   TEXT DEFAULT '{}',
    updated_at          TEXT
);

-- ── customer_profiles ─────────────────────────────────────────────────────────
-- Stores each customer's last-used print settings for the "use same settings?" prompt.
CREATE TABLE IF NOT EXISTS customer_profiles (
    phone           TEXT PRIMARY KEY,
    last_size       TEXT,
    last_colour     TEXT,
    last_layout     TEXT,
    last_copies     INTEGER,
    last_finishing  TEXT,
    last_delivery   INTEGER DEFAULT 0,
    updated_at      TEXT
);

-- ── job_batches ───────────────────────────────────────────────────────────────
-- Groups multiple files from one customer into a single payment link.
CREATE TABLE IF NOT EXISTS job_batches (
    batch_id            TEXT PRIMARY KEY,
    phone               TEXT,
    job_ids             TEXT,       -- comma-separated list of job_ids
    status              TEXT DEFAULT 'pending',
    total_amount        REAL,
    razorpay_link_id    TEXT,
    link_sent_at        TEXT
);

-- ── Add file_url column to jobs (if not already present) ─────────────────────
-- Cloud webhook uploads files to Supabase Storage; store PC downloads via this URL.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS file_url TEXT;

-- ── RLS policies — allow service_role key full access ─────────────────────────
-- (Service role key bypasses RLS by default; anon key needs explicit policies)
ALTER TABLE bot_sessions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE customer_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_batches     ENABLE ROW LEVEL SECURITY;

-- Service role has full access (used by Vercel webhook and store PC)
DO $$ BEGIN
  CREATE POLICY "service_role_all_bot_sessions"
      ON bot_sessions FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE POLICY "service_role_all_customer_profiles"
      ON customer_profiles FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE POLICY "service_role_all_job_batches"
      ON job_batches FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
