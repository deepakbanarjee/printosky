-- SCHEMA v40 — when a job LEFT for the finishing store
-- =====================================================================
-- Run once in the Supabase SQL Editor. Idempotent.
--
-- WHY
-- ---
-- B-8 shipped an over-48h digest line that could never fire, for three
-- independent reasons. This file is one of them; the other two are code.
--
--   1. supabase_sync.collect_jobs() never selected the finishing columns, so a
--      job sent to Nattika for binding was invisible in the cloud. Fixed in
--      supabase_sync.py; this migration is what makes that push legal.
--   2. `finishing_sent_at` was read by store_digest.overdue_finishing() and
--      written by NOTHING. The age fell back to `received_at`, which measures a
--      different interval: a job taken in three weeks ago and sent to the
--      finisher this morning read as ~500h overdue. The fallback is gone, and
--      /finishing-send now writes this column.
--   3. The cron called compose_closing_message() without finishing_rows at all.
--
-- Store PCs add their own copy of this column through
-- db_migrations.ensure_job_service_columns(), which the finishing handlers
-- already call -- nothing runs fix_db.py for a counter. This file is the cloud
-- half. Adding a column only; no row is read or rewritten.
--
-- Safe to run before the store PCs pull: a column nothing writes yet is NULL,
-- and a NULL send time is reported as "out for finishing, age unknown" rather
-- than being silently dropped or guessed at.

ALTER TABLE public.jobs
  ADD COLUMN IF NOT EXISTS finishing_sent_at text;

-- The digest reads: status IN ('sent','at_finisher') ordered by how long ago.
CREATE INDEX IF NOT EXISTS jobs_finishing_open_idx
    ON public.jobs (finishing_sent_at)
 WHERE finishing_status IN ('sent', 'at_finisher');

-- Verify:
--   SELECT job_id, finishing_store_id, finishing_status, finishing_sent_at
--     FROM public.jobs
--    WHERE finishing_status IN ('sent','at_finisher')
--    ORDER BY finishing_sent_at;
