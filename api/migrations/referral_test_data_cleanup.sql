-- Removes the seeded referral test data (Deepak + Anu).
-- Safe to run multiple times. Affects ONLY rows with platform='test'.

DELETE FROM referral_credits
 WHERE referrer_code IN (
   SELECT code FROM referrers WHERE platform = 'test'
 );

DELETE FROM referrers WHERE platform = 'test';

-- Optional: also wipe any bot_sessions row that captured a test code as referral_code
UPDATE bot_sessions
   SET referral_code = NULL
 WHERE referral_code IN ('REF2033DK', 'REF4907AN');

SELECT
  (SELECT COUNT(*) FROM referrers)        AS referrers_remaining,
  (SELECT COUNT(*) FROM referral_credits) AS credits_remaining;
