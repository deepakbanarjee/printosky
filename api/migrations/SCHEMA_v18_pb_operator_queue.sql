-- api/migrations/SCHEMA_v18_pb_operator_queue.sql
-- Project-Builder operator-queue table — receives orders whose AI structure
-- detection failed both Sonnet and Opus passes (the "human-finishing" path
-- agreed in P0 Day 2.5). The customer is told "your project needs a personal
-- touch — delivery via WhatsApp within 6 hours" and the order shows up here.
--
-- Forward-only and Oxygen-safe:
--   * CREATE TABLE IF NOT EXISTS so the migration is idempotent
--   * No existing column dropped / renamed
--   * No dependencies on other tables (loose-coupled by pb_order_id text)
--
-- Apply order:
--   psql ... -f api/migrations/SCHEMA_v18_pb_operator_queue.sql
-- (or paste into the Supabase Dashboard -> SQL Editor)
--
-- DO NOT APPLY TO PRODUCTION WITHOUT EXPLICIT OWNER APPROVAL.
-- See plan-of-record: this session, P0 Day 2.5.

-- ---------------------------------------------------------------------------
-- 1. Table
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS pb_operator_queue (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Loose-coupled reference. Some failures happen post-payment but before
    -- a project_builder_orders row exists, so this is text-not-FK.
    pb_order_id              TEXT,

    -- Customer routing
    customer_phone           TEXT,            -- E.164 digits (e.g. 919495706405)
    student_name             TEXT,
    university               TEXT NOT NULL,
    tier                     TEXT NOT NULL,   -- 'standard'|'premium'|'luxury'

    -- The input that AI failed on
    input_text               TEXT,
    input_size_bytes         INTEGER,

    -- AI attempt history. Each element:
    --   {"model": "claude-sonnet-4-6", "errors": [...],
    --    "model_used": "...", "phase": "post_payment"}
    ai_attempts              JSONB DEFAULT '[]'::jsonb,

    -- Best partial structures we have so the operator can start from them
    sonnet_partial_structure JSONB,
    opus_partial_structure   JSONB,
    last_model_used          TEXT,

    -- Operator workflow
    status                   TEXT NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'claimed', 'delivered', 'abandoned')),
    assigned_to              TEXT,             -- operator email/name once claimed
    claimed_at               TIMESTAMPTZ,
    deadline_ts              TIMESTAMPTZ NOT NULL DEFAULT (now() + INTERVAL '6 hours'),
    delivered_at             TIMESTAMPTZ,

    -- Final deliverable (operator-finished DOCX)
    delivered_docx_path      TEXT,             -- supabase storage path
    delivered_download_url   TEXT,             -- signed url (30-day)

    -- Telemetry
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 2. Indexes
-- ---------------------------------------------------------------------------

-- "What's next" view for the operator: pending or claimed jobs by deadline
CREATE INDEX IF NOT EXISTS pb_operator_queue_pending_deadline_idx
    ON pb_operator_queue (status, deadline_ts)
    WHERE status IN ('pending', 'claimed');

-- Quick lookup by phone (for WhatsApp status replies)
CREATE INDEX IF NOT EXISTS pb_operator_queue_phone_idx
    ON pb_operator_queue (customer_phone);

-- ---------------------------------------------------------------------------
-- 3. updated_at trigger
-- ---------------------------------------------------------------------------

-- search_path is pinned to defeat any attacker who can create objects in
-- another schema on the role's search_path. Required by Supabase linter
-- (lint 0011_function_search_path_mutable).
CREATE OR REPLACE FUNCTION update_pb_operator_queue_updated_at()
RETURNS TRIGGER
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pb_operator_queue_updated_at_trigger ON pb_operator_queue;
CREATE TRIGGER pb_operator_queue_updated_at_trigger
    BEFORE UPDATE ON pb_operator_queue
    FOR EACH ROW
    EXECUTE FUNCTION update_pb_operator_queue_updated_at();

-- ---------------------------------------------------------------------------
-- 4. Row-Level Security: admin-only
-- ---------------------------------------------------------------------------
-- The api/index.py uses SUPABASE_SERVICE_ROLE_KEY which bypasses RLS.
-- All other clients (anon, authenticated) are denied. No customer ever
-- queries this table directly.

ALTER TABLE pb_operator_queue ENABLE ROW LEVEL SECURITY;

-- Drop any prior permissive policy if a previous migration created one
DROP POLICY IF EXISTS pb_operator_queue_admin_all ON pb_operator_queue;

-- Deny-by-default for non-service-role callers
CREATE POLICY pb_operator_queue_deny_all
    ON pb_operator_queue
    FOR ALL
    USING (false)
    WITH CHECK (false);

-- ---------------------------------------------------------------------------
-- 5. Comments (documentation visible in Supabase Dashboard)
-- ---------------------------------------------------------------------------

COMMENT ON TABLE pb_operator_queue IS
'Project Builder orders whose AI structure detection (Sonnet -> Opus) failed validation. Operator finishes manually and delivers via WhatsApp. SLA = 6h from created_at.';

COMMENT ON COLUMN pb_operator_queue.tier IS
'standard | premium | luxury — drives priority (luxury first).';

COMMENT ON COLUMN pb_operator_queue.status IS
'pending (unassigned) -> claimed (operator picked up) -> delivered (DOCX uploaded + customer notified) | abandoned (manual override).';

COMMENT ON COLUMN pb_operator_queue.ai_attempts IS
'Append-only JSONB array of {model, errors, model_used, phase} per AI pass.';
