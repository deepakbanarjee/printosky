-- PRINTOSKY SCHEMA v5 — RLS Hardening
-- Run in Supabase SQL Editor after SCHEMA_v4.
--
-- Changes all "anon" policies to require authenticated Supabase JWT.
-- The anon key remains in admin.html for project routing but gains no data access.
--
-- Prerequisites:
--   1. Create a Supabase Auth user in the dashboard:
--      Authentication → Users → Invite user
--      Email: admin@printosky.internal   Password: <store in SUPABASE_AUTH_PASSWORD env var>
--   2. Set SUPABASE_SERVICE_KEY in .env (Settings → API → service_role key)
--   3. Set SUPABASE_AUTH_EMAIL + SUPABASE_AUTH_PASSWORD in Netlify env vars
--   4. Deploy updated netlify/functions/auth.js

-- ── Drop old permissive anon policies ─────────────────────────────────────────

DROP POLICY IF EXISTS "anon read jobs"             ON jobs;
DROP POLICY IF EXISTS "anon insert jobs"           ON jobs;
DROP POLICY IF EXISTS "anon upsert jobs"           ON jobs;

DROP POLICY IF EXISTS "anon read printer_counters"   ON printer_counters;
DROP POLICY IF EXISTS "anon insert printer_counters" ON printer_counters;

DROP POLICY IF EXISTS "anon read daily_summary"    ON daily_summary;
DROP POLICY IF EXISTS "anon insert daily_summary"  ON daily_summary;
DROP POLICY IF EXISTS "anon update daily_summary"  ON daily_summary;

DROP POLICY IF EXISTS "anon read printer_supplies"   ON printer_supplies;
DROP POLICY IF EXISTS "anon insert printer_supplies" ON printer_supplies;

DROP POLICY IF EXISTS "anon all b2b_clients"       ON b2b_clients;
DROP POLICY IF EXISTS "anon all b2b_payments"      ON b2b_payments;
DROP POLICY IF EXISTS "anon all rate_card"         ON rate_card;

DROP POLICY IF EXISTS "anon read supply_changes"   ON supply_changes;
DROP POLICY IF EXISTS "anon insert supply_changes" ON supply_changes;

DROP POLICY IF EXISTS "anon read konica_jobs"      ON konica_jobs;
DROP POLICY IF EXISTS "anon insert konica_jobs"    ON konica_jobs;
DROP POLICY IF EXISTS "anon upsert konica_jobs"    ON konica_jobs;

DROP POLICY IF EXISTS "anon all partners"          ON partners;
DROP POLICY IF EXISTS "service_role only"          ON staff_sessions;

-- ── Drop new policies if partially applied (idempotent re-run) ───────────────

DROP POLICY IF EXISTS "auth read jobs"               ON jobs;
DROP POLICY IF EXISTS "auth read printer_counters"   ON printer_counters;
DROP POLICY IF EXISTS "auth read daily_summary"      ON daily_summary;
DROP POLICY IF EXISTS "auth read printer_supplies"   ON printer_supplies;
DROP POLICY IF EXISTS "auth all b2b_clients"         ON b2b_clients;
DROP POLICY IF EXISTS "auth all b2b_payments"        ON b2b_payments;
DROP POLICY IF EXISTS "auth all rate_card"           ON rate_card;
DROP POLICY IF EXISTS "auth read supply_changes"     ON supply_changes;
DROP POLICY IF EXISTS "auth read konica_jobs"        ON konica_jobs;
DROP POLICY IF EXISTS "auth read partners"           ON partners;
DROP POLICY IF EXISTS "auth all staff_sessions"      ON staff_sessions;

-- ── New policies: authenticated users can read/write ─────────────────────────
-- Reads: admin/superadmin/mis pages (authenticated JWT from Netlify function)
-- Writes: supabase_sync.py uses service_role key which bypasses RLS entirely

CREATE POLICY "auth read jobs"
    ON jobs FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "auth read printer_counters"
    ON printer_counters FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "auth read daily_summary"
    ON daily_summary FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "auth read printer_supplies"
    ON printer_supplies FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "auth all b2b_clients"
    ON b2b_clients FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');

CREATE POLICY "auth all b2b_payments"
    ON b2b_payments FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');

CREATE POLICY "auth all rate_card"
    ON rate_card FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');

CREATE POLICY "auth read supply_changes"
    ON supply_changes FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "auth read konica_jobs"
    ON konica_jobs FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "auth read partners"
    ON partners FOR SELECT USING (auth.role() = 'authenticated');

CREATE POLICY "auth all staff_sessions"
    ON staff_sessions FOR ALL USING (auth.role() = 'authenticated') WITH CHECK (auth.role() = 'authenticated');
