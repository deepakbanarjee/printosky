-- SCHEMA_v3_migration.sql — Printosky Staff Performance
-- Run this in Supabase SQL Editor (safe — uses IF NOT EXISTS)
-- Run ONCE after deploying the staff performance update.

-- ── Staff sessions table ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS staff_sessions (
  id          BIGINT PRIMARY KEY,
  staff_id    TEXT,
  pc_id       TEXT,
  store_id    TEXT,
  login_at    TIMESTAMPTZ,
  logout_at   TIMESTAMPTZ,
  idle_logout BOOLEAN DEFAULT false
);
ALTER TABLE staff_sessions ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY "service_role only" ON staff_sessions USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ── Add columns to existing tables ───────────────────────────────────────────
ALTER TABLE jobs        ADD COLUMN IF NOT EXISTS printed_by    TEXT;
ALTER TABLE konica_jobs ADD COLUMN IF NOT EXISTS attributed_to TEXT;
