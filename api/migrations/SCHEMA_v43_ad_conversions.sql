-- SCHEMA v43 — conversions sent back to Meta (Conversions API)
-- =====================================================================
-- Run once in the Supabase SQL Editor. Idempotent. CREATE / ADD COLUMN only.
--
-- WHY THIS EXISTS
-- ---------------
-- SCHEMA v42 stopped us discarding `ctwa_clid`, the one handle Meta accepts
-- when a click-to-WhatsApp ad is told what a click was worth. It has been
-- stored on every click since 2026-09-07 and sent back to Meta exactly never.
--
-- That is why the campaign optimises the way it does. Meta's objective was
-- LINK_CLICKS and the only feedback it ever received was the click itself, so
-- it bought the cheapest clicks it could find: 8 clicks, 7 people, Rs.0. An
-- ad account that is never told which click paid cannot learn to find another
-- one like it, and no amount of better ad copy fixes that.
--
-- WHY A TABLE AND NOT A COLUMN ON jobs
-- ------------------------------------
-- Three reasons, and the first is the one that matters:
--
--   1. A send can FAIL. Meta can be down, the token can expire, the dataset id
--      can be wrong. A boolean "sent" column records success and loses every
--      failure, which is the same silence v42 was written to end. This table
--      keeps the failures, with the error, so an unreported conversion is a
--      row someone can see and retry rather than a gap nobody can.
--   2. A payment is not always one job. A batch payment covers several job
--      rows but is ONE conversion worth ONE value; keyed on the payment's
--      reference id, that is one row here and no double-counting.
--   3. Book orders, print jobs and service bookings all pay through different
--      tables and all convert the same ad click.
--
-- order_id is UNIQUE and is also sent to Meta as `event_id`, so a duplicate is
-- refused twice: here, and again on Meta's side. Razorpay fires the same
-- payment.captured more than once and always has.

CREATE TABLE IF NOT EXISTS public.ad_conversions (
    id          bigserial   PRIMARY KEY,
    order_id    text        NOT NULL UNIQUE,  -- job id, batch id or order code
    phone       text        NOT NULL,
    ctwa_clid   text        NOT NULL,         -- the click being credited
    source_id   text,                         -- Meta ad id, denormalised for the report
    event_name  text        NOT NULL DEFAULT 'Purchase',
    value_inr   numeric,
    currency    text        NOT NULL DEFAULT 'INR',
    ok          boolean     NOT NULL DEFAULT false,
    error       text,                         -- why it failed, when it failed
    fbtrace_id  text,                         -- Meta's own handle for the request
    sent_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ad_conversions_phone_idx   ON public.ad_conversions (phone);
CREATE INDEX IF NOT EXISTS ad_conversions_source_idx  ON public.ad_conversions (source_id);
CREATE INDEX IF NOT EXISTS ad_conversions_sent_at_idx ON public.ad_conversions (sent_at);
CREATE INDEX IF NOT EXISTS ad_conversions_ok_idx      ON public.ad_conversions (ok);

-- Click ids and revenue are staff data. RLS on, service_role only: the
-- consoles reach this through api/index.py, never with the anon key. Same
-- posture as ad_clicks in v42.
ALTER TABLE public.ad_conversions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS service_role_all_ad_conversions ON public.ad_conversions;
CREATE POLICY service_role_all_ad_conversions ON public.ad_conversions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Verify (after the next paid order from an ad click):
--   SELECT ok, count(*), sum(value_inr)
--     FROM public.ad_conversions GROUP BY ok;
--
--   -- anything Meta never accepted, newest first — this is the retry queue
--   SELECT order_id, phone, error, sent_at
--     FROM public.ad_conversions WHERE NOT ok ORDER BY sent_at DESC;
