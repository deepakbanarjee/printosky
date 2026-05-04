-- Adds per-referrer credit_amount to the referrers table.
-- Default 20 keeps all existing referrers unchanged.
-- Set credit_amount = 50 on campaign codes like ref_PROJ50.

ALTER TABLE referrers ADD COLUMN IF NOT EXISTS credit_amount INT NOT NULL DEFAULT 20;

-- Campaign code for the project creation offer (₹50 self-discount + ₹50 store credit per referral).
-- platform = 'campaign' distinguishes these from real customer referrers.
INSERT INTO referrers (code, label, platform, credit_amount)
VALUES ('PROJ50', 'Project Creation Campaign — May 2026', 'campaign', 50)
ON CONFLICT (code) DO UPDATE SET credit_amount = 50;
