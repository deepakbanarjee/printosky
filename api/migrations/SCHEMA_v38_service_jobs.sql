-- SCHEMA_v38: post-press service jobs on public.jobs
--
-- Plan: docs/plans/2026-08-30-scaling-and-post-press-services.md §4.3 (B-2).
--
-- A service job — photocopy, scanning, lamination, foiling, bind-only, cutting,
-- punching, photo prints, DTP — is an ordinary `jobs` row with `service_kind`
-- set. Not a new table: revenue, payment, pickup codes, WhatsApp notify, the
-- daily summary and MIS all already read `jobs`, and a parallel table would
-- have to be taught to each of them.
--
--     service_kind IS NULL  =>  print job  =>  everything behaves as today.
--
-- Every column here is additive and nullable, so every row written before this
-- migration keeps meaning exactly what it meant. Nothing reads these columns
-- until B-3; running this migration alone changes no behaviour.
--
-- SQLite counterpart: db_migrations.SERVICE_JOB_COLUMNS (TEXT/REAL there --
-- SQLite has neither jsonb nor timestamptz), applied self-healingly because
-- store PCs never run fix_db.py (docs/AUTO_UPDATE.md).

ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS service_kind text;
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS service_meta jsonb;

-- Inter-store finishing (plan §4.7): the store that sells the job and the store
-- that finishes it can differ, and the revenue splits between them.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS finishing_store_id text;
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS finishing_status text;
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS print_amount real;
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS finishing_amount real;
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS finishing_internal_amount real;

-- Drop-off bookings (plan §4.8): a customer books a service online, then brings
-- the physical item in. Un-received bookings expire.
ALTER TABLE public.jobs
    ADD COLUMN IF NOT EXISTS item_received_at timestamptz;

-- Service jobs are a small minority of rows; a partial index keeps the
-- "services only" console filters cheap without growing the print-job path.
CREATE INDEX IF NOT EXISTS jobs_service_kind_idx
    ON public.jobs (service_kind)
    WHERE service_kind IS NOT NULL;

COMMENT ON COLUMN public.jobs.service_kind IS
    'Post-press service without printing: copy | scan | laminate | foil | bind | cut | punch | photo | dtp | other. NULL = ordinary print job. See rate_card.SERVICE_KINDS.';
COMMENT ON COLUMN public.jobs.service_meta IS
    'JSONB of per-kind quantities priced by rate_card.calculate_service_quote(), e.g. {"sheets":40,"paper_size":"A4","colour":false}.';
COMMENT ON COLUMN public.jobs.finishing_store_id IS
    'Store that performs the finishing when it is not the selling store (inter-store finishing).';
COMMENT ON COLUMN public.jobs.finishing_status IS
    'sent | at_finisher | returned. NULL = finished in-house or no finishing.';
COMMENT ON COLUMN public.jobs.print_amount IS
    'Rupee split of amount_quoted attributable to printing.';
COMMENT ON COLUMN public.jobs.finishing_amount IS
    'Rupee split of amount_quoted attributable to finishing, as charged to the customer.';
COMMENT ON COLUMN public.jobs.finishing_internal_amount IS
    'Rupee amount the finishing store keeps on an inter-store job.';
COMMENT ON COLUMN public.jobs.item_received_at IS
    'When the customer''s physical item reached the counter for a drop-off booking. NULL = not yet received.';
