-- Referral tracking migration for Printosky
-- Run once in Supabase SQL Editor (same as academic_orders.sql pattern)
-- Enables: wa.me/919495706405?text=ref_CODE → track who drove each order

ALTER TABLE bot_sessions ADD COLUMN IF NOT EXISTS referral_code TEXT;

CREATE TABLE IF NOT EXISTS referrers (
  code           TEXT PRIMARY KEY,
  label          TEXT NOT NULL,
  platform       TEXT DEFAULT 'whatsapp',
  total_orders   INT  DEFAULT 0,
  total_credited INT  DEFAULT 0,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS referral_credits (
  id             BIGSERIAL PRIMARY KEY,
  referrer_code  TEXT NOT NULL REFERENCES referrers(code),
  customer_phone TEXT NOT NULL,
  order_id       TEXT NOT NULL,
  amount_inr     INT  NOT NULL DEFAULT 20,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_referral_credits_code ON referral_credits(referrer_code);
