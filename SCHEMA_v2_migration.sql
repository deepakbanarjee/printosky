-- PRINTOSKY SCHEMA v2 MIGRATION
-- Run this in Supabase → SQL Editor → New Query
-- Safe to run even if some parts already exist (uses IF NOT EXISTS / exception guards)
-- DO NOT run the full SCHEMA.sql again — this file adds only the new tables.

-- ── Supply change log ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS supply_changes (
    id            BIGSERIAL PRIMARY KEY,
    store_id      TEXT NOT NULL DEFAULT 'OSP',
    changed_at    TEXT NOT NULL,
    printer       TEXT NOT NULL,
    supply_index  INTEGER NOT NULL,
    description   TEXT,
    level_before  INTEGER,
    level_after   INTEGER,
    pct_before    REAL,
    pct_after     REAL,
    UNIQUE (store_id, id)
);

ALTER TABLE supply_changes ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "anon read supply_changes"
    ON supply_changes FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE POLICY "anon insert supply_changes"
    ON supply_changes FOR INSERT WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Konica Job Log ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS konica_jobs (
    id             BIGSERIAL PRIMARY KEY,
    store_id       TEXT NOT NULL DEFAULT 'OSP',
    job_number     INTEGER NOT NULL,
    job_type       TEXT,
    user_name      TEXT,
    file_name      TEXT,
    result         TEXT,
    num_pages      INTEGER,
    pages_printed  INTEGER,
    mono_pages     INTEGER,
    color_pages    INTEGER,
    copies         INTEGER,
    job_date       TEXT,
    print_end_date TEXT,
    paper_size     TEXT,
    paper_type     TEXT,
    UNIQUE (store_id, job_number)
);

ALTER TABLE konica_jobs ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "anon read konica_jobs"
    ON konica_jobs FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE POLICY "anon insert konica_jobs"
    ON konica_jobs FOR INSERT WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE POLICY "anon upsert konica_jobs"
    ON konica_jobs FOR UPDATE USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_konica_jobs_date    ON konica_jobs (job_date);
CREATE INDEX IF NOT EXISTS idx_konica_jobs_user    ON konica_jobs (user_name);
CREATE INDEX IF NOT EXISTS idx_konica_jobs_type    ON konica_jobs (job_type);
CREATE INDEX IF NOT EXISTS idx_konica_jobs_storeid ON konica_jobs (store_id);
