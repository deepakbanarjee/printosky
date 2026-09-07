-- SCHEMA v42 — click-to-WhatsApp ad attribution
-- =====================================================================
-- Run once in the Supabase SQL Editor. Idempotent. CREATE / ADD COLUMN only.
--
-- WHY THIS EXISTS
-- ---------------
-- Meta bills for a "messaging conversation" but tells us nothing about it
-- afterwards. On 2026-09-06 Ads Manager reported 5 conversations at 147.14 INR
-- each against 735.71 INR of spend, while the only trace anywhere in this
-- database was a single referral_conversion row in wa_message_costs and no job,
-- no order and no rupee that could be tied to any of it.
--
-- The data was never missing — it was discarded. Meta attaches a `referral`
-- object to the FIRST message after someone taps a click-to-WhatsApp ad, and
-- _process_meta_webhook read only id/from/type and dropped the rest.
--
-- WHY A TABLE AND NOT COLUMNS ON whatsapp_contacts
-- ------------------------------------------------
-- One person can arrive from several ads over months. Columns would keep only
-- the newest (or, with a guard, only the oldest) and silently lose the rest,
-- which makes per-campaign cost-per-order impossible to compute the moment a
-- second campaign runs. The first-touch columns added at the bottom are a
-- denormalised convenience for the common join, not the record of truth.
--
-- ctwa_clid is the column that matters most: it is the only handle Meta accepts
-- when sending a conversion back through the Conversions API, and it is sent to
-- us exactly once and never resent.
--
-- WHY THERE IS A channel COLUMN WHEN ONLY WHATSAPP IS WIRED UP
-- -----------------------------------------------------------
-- The campaign being measured is an engagement campaign running alongside an
-- Instagram presence (@printosky_official) whose comment-to-DM bot lives on the
-- store PC, outside this repo. Meta counts an Instagram DM as a "messaging
-- conversation" too, which is the likeliest reason Ads Manager showed 5 and
-- WhatsApp could account for only 1. Instagram DMs reach a different webhook
-- and are recorded nowhere yet; when they are, they belong in this table next
-- to the WhatsApp clicks, because cost-per-order has to span both channels or
-- it is not cost-per-order. One nullable column now beats a migration then.

CREATE TABLE IF NOT EXISTS public.ad_clicks (
    id          bigserial   PRIMARY KEY,
    phone       text        NOT NULL,   -- WhatsApp number, or IG-scoped sender id
    channel     text        NOT NULL DEFAULT 'whatsapp',  -- 'whatsapp' | 'instagram'
    wamid       text        UNIQUE,      -- the message carrying the referral
    source_type text,                    -- 'ad' | 'post'
    source_id   text,                    -- Meta ad id — groups clicks per ad
    source_url  text,
    ctwa_clid   text,                    -- click id; required by the CAPI
    headline    text,                    -- ad copy, as served to this person
    body        text,
    clicked_at  timestamptz NOT NULL DEFAULT now()
);

-- phone: join to jobs.sender / book_orders.phone to reach revenue.
-- source_id + clicked_at: the per-ad, per-day rollup the report runs.
CREATE INDEX IF NOT EXISTS ad_clicks_phone_idx      ON public.ad_clicks (phone);
CREATE INDEX IF NOT EXISTS ad_clicks_source_idx     ON public.ad_clicks (source_id);
CREATE INDEX IF NOT EXISTS ad_clicks_clicked_at_idx ON public.ad_clicks (clicked_at);
CREATE INDEX IF NOT EXISTS ad_clicks_channel_idx    ON public.ad_clicks (channel);

-- Ad spend and click ids are staff data. RLS on, service_role only: the
-- consoles reach this through api/index.py, never with the anon key.
ALTER TABLE public.ad_clicks ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS service_role_all_ad_clicks ON public.ad_clicks;
CREATE POLICY service_role_all_ad_clicks ON public.ad_clicks
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- First touch, denormalised onto the contact so "which ad brought this customer
-- in" is one join rather than a correlated subquery. Written only when
-- first_ad_at IS NULL, so the first ad keeps the credit.
ALTER TABLE public.whatsapp_contacts
  ADD COLUMN IF NOT EXISTS first_ad_source_id text,
  ADD COLUMN IF NOT EXISTS first_ctwa_clid    text,
  ADD COLUMN IF NOT EXISTS first_ad_at        timestamptz;

-- Verify (after the next ad click arrives):
--   SELECT source_id, count(*) AS clicks, min(clicked_at), max(clicked_at)
--     FROM public.ad_clicks GROUP BY source_id ORDER BY 2 DESC;
--
--   SELECT c.first_ad_source_id, count(DISTINCT c.phone) AS customers,
--          coalesce(sum(j.amount_collected), 0) AS print_revenue
--     FROM public.whatsapp_contacts c
--     LEFT JOIN public.jobs j ON j.sender = c.phone AND j.amount_collected > 0
--    WHERE c.first_ad_source_id IS NOT NULL
--    GROUP BY 1;
