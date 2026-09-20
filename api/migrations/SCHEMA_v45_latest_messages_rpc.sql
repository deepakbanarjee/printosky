-- SCHEMA v45 — newest message per phone, server-side
--
-- WHY
-- ---
-- chat_audit_snapshot() needs one thing from conversation_log: the newest
-- message for each phone currently flagged needs_human, so it can tell a chat
-- still waiting on a person from one a human already answered.
--
-- It got that by pulling up to 4000 rows — every message from every phone
-- across a 14-day window — and keeping the first row seen per phone. The
-- flagged set is usually a handful of numbers, so nearly all of that transfer
-- was discarded on arrival.
--
-- The cap misreports, too, in the opposite direction to SCHEMA_v44's. When a
-- flagged phone's newest message falls outside the newest 4000, the lookup
-- returns nothing for it, `last` is None, and is_handled evaluates False — so a
-- chat a human already replied to is reported as STILL WAITING, and its stale
-- needs_human flag is never cleared. Noise and a flag that sticks, rather than
-- a customer going unseen.
--
-- DISTINCT ON returns exactly one row per phone: no cap, and nothing fetched
-- that is thrown away.
--
-- NO NEW INDEX
-- ------------
-- conversation_log already has (phone, created_at DESC) from SCHEMA_v11, which
-- is precisely the ordering DISTINCT ON (phone) ... ORDER BY phone, created_at
-- DESC wants. Verified by EXPLAIN in tests/test_latest_messages_sql.py.
--
-- SEMANTICS
-- ---------
-- Mirrors the Python it replaces, which remains the fallback. Ties on
-- created_at within a phone are broken by id DESC so the answer is stable;
-- the row-scan it replaced left ties to whatever order the API returned.

CREATE OR REPLACE FUNCTION public.latest_messages(
    phones         text[],
    lookback_hours double precision DEFAULT 336
)
RETURNS TABLE (phone text, direction text, body text, created_at timestamptz)
LANGUAGE sql
STABLE
AS $$
    SELECT DISTINCT ON (cl.phone)
           cl.phone, cl.direction, cl.body, cl.created_at
    FROM public.conversation_log cl
    WHERE cl.phone = ANY(phones)
      AND cl.created_at >= now() - make_interval(secs => lookback_hours * 3600)
    ORDER BY cl.phone, cl.created_at DESC, cl.id DESC;
$$;

-- SECURITY INVOKER (the default) is deliberate, as in v44: the caller's own RLS
-- applies, so this cannot become a way to read conversation_log without the
-- rights to read it directly. The result carries message bodies and customer
-- phone numbers, so EXECUTE is not public — v16 revoked anon's read on the
-- table and this keeps the function from routing around it.
REVOKE ALL ON FUNCTION public.latest_messages(text[], double precision) FROM PUBLIC;

DO $$ BEGIN
    GRANT EXECUTE ON FUNCTION public.latest_messages(text[], double precision) TO service_role;
EXCEPTION WHEN undefined_object THEN
    RAISE NOTICE 'role service_role not found — skipping GRANT (not a Supabase database)';
END $$;

COMMENT ON FUNCTION public.latest_messages(text[], double precision) IS
    'Newest conversation_log row per phone, for the given phones inside the '
    'lookback window. Feeds chat_audit_snapshot; see SCHEMA_v45.';

-- PostgREST serves RPC from a cached schema; a function it has not seen yet
-- answers 404 and the caller would sit on its fallback. No-op where nothing
-- is listening.
NOTIFY pgrst, 'reload schema';
