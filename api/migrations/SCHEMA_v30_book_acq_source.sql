-- api/migrations/SCHEMA_v30_book_acq_source.sql
-- Acquisition attribution for book orders (Xtraa / Divya campaign).
--
-- Goal: know HOW a customer discovered the books — Divya teacher's own channels
-- vs paid Facebook / Instagram / YouTube ads — so ad spend can be judged by which
-- channel actually converts. Today book_orders.source only records the ENTRY
-- channel ('whatsapp'/'walk_in'/'divya'), NOT the discovery channel, so ad
-- attribution is impossible.
--
-- Two new columns:
--   acq_source — discovery/ad channel, captured PASSIVELY from a tracked deep
--                link (wa.me/919495706405?text=BOOKS%20ig -> 'instagram';
--                ?src=fb on the web -> 'facebook'), with a one-tap "how did you
--                hear?" fallback for untagged orders. Values:
--                instagram | facebook | youtube | divya | referral | friend |
--                other | NULL (unknown / not yet asked).
--   acq_entry  — platform the order came through: 'whatsapp' | 'website'.
--
-- Both nullable / best-effort: the bot stamps them via update_book_order, which
-- swallows errors — so ordering keeps working even if this migration lags the
-- code deploy. No backfill (historical orders = unknown).

ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS acq_source   text;
ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS acq_campaign text;
ALTER TABLE book_orders ADD COLUMN IF NOT EXISTS acq_entry    text;

COMMENT ON COLUMN book_orders.acq_source IS
  'Discovery/ad channel (roll-up): instagram|facebook|youtube|divya|referral|friend|other|NULL. From a tracked link or the "how did you hear?" prompt.';
COMMENT ON COLUMN book_orders.acq_campaign IS
  'Specific campaign tag from the tracked link, e.g. ig-reel-jan / fb-adset-2 (the per-ad granularity under acq_source). NULL if untagged.';
COMMENT ON COLUMN book_orders.acq_entry IS
  'Order entry platform: whatsapp|website.';
