-- SCHEMA v45 — Instagram Direct, as a channel of its own
-- =====================================================================
-- Run once in the Supabase SQL Editor. Idempotent. CREATE / ADD COLUMN only.
--
-- WHY THIS EXISTS
-- ---------------
-- Every table in this system is keyed on a phone number, because for two
-- years every customer arrived on WhatsApp. Instagram Direct does not have
-- one. A person who DMs the Printosky profile is identified by an IGSID — an
-- Instagram-Scoped ID, unique to that person AND that business account, and
-- not a phone number in any form.
--
-- So an Instagram DM had nowhere to be written, and the webhook that would
-- have received it was never built. Those conversations are invisible: no
-- row, no console entry, no alert, no reply. Nobody finds out until someone
-- opens the Instagram app.
--
-- HOW MUCH TRAFFIC IS THIS, HONESTLY
-- ----------------------------------
-- Today: none that we can measure. Checked 2026-09-21 — all 16 conversations
-- Meta billed between 17 Aug and 21 Sep are WhatsApp, and 14 of the 15 people
-- in them have an ad_clicks row (the 15th first wrote on 5 Sep, two days
-- before the table existed). The ad set's destination_type is WHATSAPP, so an
-- Instagram PLACEMENT of that ad still opens WhatsApp — placement is where the
-- ad is shown, not where the tap lands.
--
-- This is therefore for organic DMs to the profile, and for the day an
-- Instagram-Direct-destination ad runs. It is built now because the cost of
-- the hole is asymmetric: an unanswered DM from someone who found the shop on
-- Instagram is a lost customer nobody ever hears about.
--
-- WHY NOT JUST PUT THE IGSID IN `phone`
-- -------------------------------------
-- Because _normalize_phone() strips every non-digit and prepends "91" to
-- anything ten digits long. An IGSID surviving that would be a phone number
-- belonging to someone else. Instagram gets its own column and its own table,
-- and the two channels meet only where they should: the ad report.

-- ── ad_clicks: an ad tap that landed in Instagram, not WhatsApp ────────────
-- `phone` was NOT NULL because a click could only ever be a WhatsApp click.
-- Now a row carries one identity or the other, never neither.
ALTER TABLE public.ad_clicks ADD COLUMN IF NOT EXISTS igsid text;
ALTER TABLE public.ad_clicks ALTER COLUMN phone DROP NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ad_clicks_has_an_identity') THEN
        ALTER TABLE public.ad_clicks
            ADD CONSTRAINT ad_clicks_has_an_identity
            CHECK (phone IS NOT NULL OR igsid IS NOT NULL);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ad_clicks_igsid_idx ON public.ad_clicks (igsid);

COMMENT ON COLUMN public.ad_clicks.igsid IS
    'Instagram-Scoped ID when channel = instagram; phone is NULL on those rows.';
COMMENT ON COLUMN public.ad_clicks.wamid IS
    'Platform message id of the message that carried the referral — a wamid on '
    'WhatsApp, a message mid on Instagram. UNIQUE, so it dedups Meta''s retries '
    'on both channels.';

-- ── conversation_log: which channel a line was spoken on ───────────────────
-- Defaulted to whatsapp so every existing row keeps its meaning. The
-- Conversations console reads this to label a thread it cannot dial.
ALTER TABLE public.conversation_log
    ADD COLUMN IF NOT EXISTS channel text NOT NULL DEFAULT 'whatsapp';

CREATE INDEX IF NOT EXISTS conversation_log_channel_idx
    ON public.conversation_log (channel) WHERE channel <> 'whatsapp';

-- ── instagram_threads: the whatsapp_contacts of Instagram ──────────────────
-- One row per person per business account. Holds first-touch attribution
-- (same shape as whatsapp_contacts.first_ad_*), the welcome-once guard, and
-- the needs_human flag the digest reads.
CREATE TABLE IF NOT EXISTS public.instagram_threads (
    igsid            text        PRIMARY KEY,
    username         text,                       -- @handle, when Meta sends it
    name             text,
    first_ad_id      text,                       -- the ad that introduced them
    first_ad_ref     text,                       -- `ref` payload from the ad
    first_ad_at      timestamptz,
    -- Set the moment the ad welcome is sent. The WhatsApp side answers this
    -- question by reading conversation_log (db_cloud.ad_welcome_already_sent);
    -- here it is a column, because a thread with no phone cannot be looked up
    -- that way and a welcome that fires twice reads as a broken bot.
    welcomed_at      timestamptz,
    needs_human      boolean     NOT NULL DEFAULT false,
    last_inbound_at  timestamptz,
    last_outbound_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS instagram_threads_needs_human_idx
    ON public.instagram_threads (needs_human) WHERE needs_human;
CREATE INDEX IF NOT EXISTS instagram_threads_last_inbound_idx
    ON public.instagram_threads (last_inbound_at DESC);

-- Customer identities and message timing. Same posture as ad_clicks (v42) and
-- ad_conversions (v43): RLS on, service_role only, reached through
-- api/index.py and never with the anon key.
ALTER TABLE public.instagram_threads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS service_role_all_instagram_threads ON public.instagram_threads;
CREATE POLICY service_role_all_instagram_threads ON public.instagram_threads
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Verify:
--   SELECT channel, count(*) FROM public.ad_clicks GROUP BY channel;
--   SELECT channel, count(*) FROM public.conversation_log GROUP BY channel;
--   SELECT igsid, username, first_ad_id, welcomed_at, needs_human
--     FROM public.instagram_threads ORDER BY last_inbound_at DESC;
--
--   -- threads waiting on a person (what the chat-audit digest counts)
--   SELECT count(*) FROM public.instagram_threads WHERE needs_human;

-- ── the SLA sweep stays a WhatsApp sweep ───────────────────────────────────
-- v44's sla_breaches() groups conversation_log by phone. Instagram rows put an
-- IGSID in that column, so without this the sweep would report a "customer"
-- nobody can dial, every 30 minutes forever: the cooldown it uses to stop
-- repeating itself lives in whatsapp_contacts, and an IGSID never matches a
-- row there, so it would never cool down.
--
-- Instagram is not thereby unwatched — instagram_threads.needs_human is what
-- the chat-audit digest counts, and ops_watchdog check `instagram.queue`
-- alerts on it. This only keeps the two channels from being measured with
-- each other's ruler.
--
-- Same signature as v44, so nothing that calls it changes. The body is v44's
-- with one predicate added; tests/test_sla_breaches_sql.py holds this and
-- db_cloud._compute_sla_breaches to identical output on randomised input.
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
          AND cl.channel = 'whatsapp'
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

REVOKE ALL ON FUNCTION public.sla_breaches(double precision, double precision, double precision) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.sla_breaches(double precision, double precision, double precision) TO service_role;
