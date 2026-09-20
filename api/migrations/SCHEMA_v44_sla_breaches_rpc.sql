-- SCHEMA v44 — server-side SLA breach detection
--
-- WHY
-- ---
-- find_sla_breaches() used to pull conversation_log rows to the serverless
-- function and reduce them in Python. The SLA cron runs every 30 minutes, so
-- that was ~48 transfers a day of up to 2000 rows each, and it grew with
-- conversation volume.
--
-- The row cap was also a silent correctness bug, which is the real reason this
-- moved. The query took the NEWEST 2000 rows in the lookback window; once a
-- busy 48 hours produced more than that, a customer whose unanswered message
-- sat just outside the cap was invisible to the sweep. The alert that exists to
-- catch a waiting customer quietly stopped catching them, with nothing in the
-- logs to say so — exactly the failure docs/FAIL_LOUD.md is about. Aggregating
-- in Postgres needs no cap.
--
-- SEMANTICS
-- ---------
-- Identical to db_cloud._compute_sla_breaches(), which remains the reference
-- implementation and the fallback. A phone is breaching when, inside the
-- lookback window:
--   * it has an inbound message, AND
--   * its newest inbound is at least threshold_hours old, AND
--   * it has no outbound reply later than (newest inbound - tolerance).
--
-- The tolerance absorbs a logging artifact: a reply can be written a moment
-- BEFORE the inbound it answers, so an outbound within `tolerance_seconds`
-- before the newest inbound still counts as a reply.
--
-- The lookback window bounds "newest outbound" as well as "newest inbound",
-- matching the Python exactly: a reply older than the window does not count.
--
-- Verified against the Python reference with randomised property tests in
-- tests/test_sla_breaches_sql.py, which runs both over the same rows in a real
-- Postgres and asserts identical output.

-- Range scan on the lookback window. The existing (phone, created_at DESC)
-- index serves per-phone thread reads; this one serves the sweep.
CREATE INDEX IF NOT EXISTS conversation_log_created_at_idx
    ON public.conversation_log (created_at);

CREATE OR REPLACE FUNCTION public.sla_breaches(
    threshold_hours   double precision DEFAULT 1,
    tolerance_seconds double precision DEFAULT 120,
    lookback_hours    double precision DEFAULT 48
)
RETURNS TABLE (phone text, last_inbound_at timestamptz)
LANGUAGE sql
STABLE
AS $$
    WITH latest AS (
        SELECT cl.phone AS ph,
               max(cl.created_at) FILTER (WHERE cl.direction = 'inbound')  AS last_in,
               max(cl.created_at) FILTER (WHERE cl.direction = 'outbound') AS last_out
        FROM public.conversation_log cl
        WHERE cl.created_at >= now() - make_interval(secs => lookback_hours * 3600)
          AND cl.phone IS NOT NULL
        GROUP BY cl.phone
    )
    SELECT l.ph, l.last_in
    FROM latest l
    WHERE l.last_in IS NOT NULL
      AND (
            l.last_out IS NULL
         OR l.last_out <= l.last_in - make_interval(secs => tolerance_seconds)
          )
      AND l.last_in <= now() - make_interval(secs => threshold_hours * 3600)
    ORDER BY l.last_in;
$$;

-- SECURITY INVOKER (the default) is deliberate: the caller's own RLS applies,
-- so this cannot become a way to read conversation_log without the rights to
-- read it directly. A DEFINER function here would hand every caller the
-- owner's access to the whole table.
--
-- The result is a list of customer phone numbers, so execution is not public:
-- v16 already revoked anon's read on conversation_log, and this keeps the
-- function from becoming a way around that.
REVOKE ALL ON FUNCTION public.sla_breaches(double precision, double precision, double precision) FROM PUBLIC;

-- service_role always exists on Supabase; the guard is so this file still
-- applies against a plain Postgres (a local dev database, or the test harness)
-- instead of failing on an unknown role.
DO $$ BEGIN
    GRANT EXECUTE ON FUNCTION public.sla_breaches(double precision, double precision, double precision) TO service_role;
EXCEPTION WHEN undefined_object THEN
    RAISE NOTICE 'role service_role not found — skipping GRANT (not a Supabase database)';
END $$;

COMMENT ON FUNCTION public.sla_breaches(double precision, double precision, double precision) IS
    'Customers whose newest inbound is older than threshold_hours with no later '
    'reply. Mirrors db_cloud._compute_sla_breaches; see SCHEMA_v44 for semantics.';

-- PostgREST serves RPC from a cached schema, so a function it has not seen yet
-- answers 404 and find_sla_breaches would sit on its fallback indefinitely.
-- Supabase reloads on DDL via an event trigger, but this makes it immediate and
-- is a no-op where nothing is listening.
NOTIFY pgrst, 'reload schema';
