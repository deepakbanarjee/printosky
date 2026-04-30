-- Adds redemption tracking to referral_credits.
-- redeemed_at NULL  → credit is available
-- redeemed_at SET   → credit was applied to redeemed_order_id by staff redeemed_by
ALTER TABLE referral_credits ADD COLUMN IF NOT EXISTS redeemed_at TIMESTAMPTZ;
ALTER TABLE referral_credits ADD COLUMN IF NOT EXISTS redeemed_order_id TEXT;
ALTER TABLE referral_credits ADD COLUMN IF NOT EXISTS redeemed_by TEXT;

CREATE INDEX IF NOT EXISTS idx_referral_credits_unredeemed
  ON referral_credits(referrer_code) WHERE redeemed_at IS NULL;
