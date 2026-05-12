-- api/migrations/SCHEMA_v19_rls_gaps.sql
-- TASK-023 (roadmap-2026-05): close RLS gaps surfaced by TASK-019 schema doc.
--
-- Three tables currently have RLS DISABLED in live Supabase, so the Supabase
-- anon key can read them via the REST API. This is a real exposure:
--   - project_builder_orders : payment IDs, download URLs, customer phone
--   - referrers              : referrer codes + commercial totals
--   - referral_credits       : customer phone + order_id (PII pairs)
--
-- Fix: enable RLS with NO policy = deny anon/auth. Service role (used by the
-- Vercel function via db_cloud._client() with SUPABASE_SERVICE_KEY) bypasses
-- RLS by Supabase design, so legitimate writes/reads from api/index.py keep
-- working.
--
-- Verification audit (before writing this migration):
--   1. Every write in api/index.py and db_cloud.py uses _client() (service
--      role). Confirmed via grep of `table("project_builder_orders"|...)`.
--   2. admin.html reads referrers via the Vercel function /referrals/leaderboard,
--      not direct Supabase REST. Confirmed at website/admin.html:3714.
--   3. mis.html does not reference any of these three tables. Confirmed.
--   4. The existing TASK-009 / TASK-013 writes to RLS-enabled tables
--      (bot_sessions, processed_webhooks) succeed in prod, proving _client()
--      uses service-role in the deployed environment.

-- ── project_builder_orders ────────────────────────────────────────────────────
ALTER TABLE project_builder_orders ENABLE ROW LEVEL SECURITY;
-- No policy created: service role bypasses RLS, all other roles denied.

-- ── referrers ─────────────────────────────────────────────────────────────────
ALTER TABLE referrers ENABLE ROW LEVEL SECURITY;

-- ── referral_credits ──────────────────────────────────────────────────────────
ALTER TABLE referral_credits ENABLE ROW LEVEL SECURITY;

-- Side note (not part of this migration):
-- The v18 processed_webhooks policy created `USING (true) WITH CHECK (true)`
-- without a `TO` clause -- permissive for *all* roles including anon. That
-- policy negates the RLS protection it appears to provide. Worth tightening
-- in a follow-up (drop the policy; rely on service-role-bypass like v19).
-- Filed as TASK-024 for the next cleanup pass.
