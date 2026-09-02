-- SCHEMA v41 — the drop-off reminder marker
-- =====================================================================
-- Run once in the Supabase SQL Editor. Idempotent. ADD COLUMN only.
--
-- WHY A COLUMN AND NOT A DERIVED TIME
-- -----------------------------------
-- B-9 promises the customer a reminder BEFORE an un-received booking is
-- cancelled (plan §4.8, owner N3: 3 days, WhatsApp reminder first). Deriving
-- "have we reminded them?" from the booking age alone would mean a cron that
-- misses a day cancels a booking whose warning never went out -- the one thing
-- the promise rules out. With the marker, a missed run DELAYS the cancellation
-- instead of skipping the warning, which is the safe direction.
--
-- job_transitions would have been the natural home, but that table is local to
-- each store PC and the sweep runs in the cloud.
--
-- Store PCs add their own copy through db_migrations.ensure_job_service_columns().
-- Safe to run before they pull: NULL means "not reminded yet", which is exactly
-- what every booking already is.

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS dropoff_reminded_at text;

-- The sweep reads: service bookings whose item has not arrived, oldest first.
CREATE INDEX IF NOT EXISTS jobs_dropoff_open_idx
    ON public.jobs (received_at)
 WHERE service_kind IS NOT NULL AND item_received_at IS NULL;

-- Verify:
--   SELECT job_id, service_kind, received_at, dropoff_reminded_at, status
--     FROM public.jobs
--    WHERE service_kind IS NOT NULL AND item_received_at IS NULL
--    ORDER BY received_at;
