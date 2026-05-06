-- api/migrations/SCHEMA_v17_marketplace.sql
-- Multi-store marketplace foundations (Block 1 of plan v2).
--
-- All changes are forward-only and Oxygen-safe:
--   - new columns are nullable or have safe defaults
--   - no existing column is dropped or renamed
--   - default store_id values keep current single-tenant flows working
--
-- Apply order:
--   psql ... -f api/migrations/SCHEMA_v17_marketplace.sql
-- (or via the Supabase migrations dashboard).
--
-- DO NOT APPLY TO PRODUCTION WITHOUT EXPLICIT OWNER APPROVAL.
-- See plan: ~/.claude/plans/printosky-multistore-mvp.md

-- ---------------------------------------------------------------------------
-- 1. Extend `partners` for marketplace onboarding.
--    Existing columns retained (commission, status, location, etc.).
--    New columns drive: routing eligibility, dispatch, payouts, brand surface.
-- ---------------------------------------------------------------------------

ALTER TABLE partners ADD COLUMN IF NOT EXISTS kyc_status            TEXT DEFAULT 'pending';      -- 'pending' | 'in_progress' | 'active' | 'rejected'
ALTER TABLE partners ADD COLUMN IF NOT EXISTS capabilities_json     JSONB DEFAULT '{}'::jsonb;   -- {colour:bool, max_paper_size:'A3', finishing:['spiral','wiro'], ...}
ALTER TABLE partners ADD COLUMN IF NOT EXISTS capacity_jobs_per_day INTEGER DEFAULT 0;           -- soft cap; 0 = unlimited
ALTER TABLE partners ADD COLUMN IF NOT EXISTS pickup_address        TEXT;                        -- shown to customer in pickup-ready message
ALTER TABLE partners ADD COLUMN IF NOT EXISTS pickup_hours_json     JSONB DEFAULT '{}'::jsonb;   -- {mon:[9,21], tue:[9,21], ...} 24h
ALTER TABLE partners ADD COLUMN IF NOT EXISTS geo_lat               DOUBLE PRECISION;            -- for distance scoring in routing v1
ALTER TABLE partners ADD COLUMN IF NOT EXISTS geo_lng               DOUBLE PRECISION;
ALTER TABLE partners ADD COLUMN IF NOT EXISTS take_rate_pct         REAL DEFAULT 10.0;           -- Printosky's cut on each job, 0..100
ALTER TABLE partners ADD COLUMN IF NOT EXISTS route_account_id      TEXT;                        -- Razorpay Route linked-account ID (acc_xxx)
ALTER TABLE partners ADD COLUMN IF NOT EXISTS dispatch_whatsapp     TEXT;                        -- store-owner number for the dispatch bot
ALTER TABLE partners ADD COLUMN IF NOT EXISTS display_pickup_label  TEXT;                        -- e.g. "Pickup Point A"

-- Backfill Oxygen so the OSP row is operational immediately on apply.
-- Synthetic geo + hours; owner can adjust via admin panel later.
UPDATE partners
   SET kyc_status        = COALESCE(kyc_status, 'active'),
       pickup_address    = COALESCE(pickup_address, 'Oxygen Students Paradise, Thrissur'),
       pickup_hours_json = COALESCE(pickup_hours_json,
                                    '{"mon":[9,21],"tue":[9,21],"wed":[9,21],"thu":[9,21],"fri":[9,21],"sat":[9,21],"sun":[10,18]}'::jsonb),
       dispatch_whatsapp = COALESCE(dispatch_whatsapp, '919495706405'),
       display_pickup_label = COALESCE(display_pickup_label, 'Pickup Point A'),
       capabilities_json = COALESCE(capabilities_json, '{"colour":true,"max_paper_size":"A3","finishing":["spiral","wiro","strip"]}'::jsonb)
 WHERE store_id = 'OSP';


-- ---------------------------------------------------------------------------
-- 2. Extend `jobs` for routing decisions, pickup, and Razorpay Route splits.
--    `store_id` already exists with DEFAULT 'OSP' (since SCHEMA.sql v1).
--    `assigned_store_id` is new and is the routing engine's decision.
--    For now `store_id == assigned_store_id` for every Oxygen job;
--    they diverge when a job is reassigned mid-flight (failure re-route).
-- ---------------------------------------------------------------------------

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS assigned_store_id  TEXT;     -- routing engine's pick; FK-by-convention to partners.store_id
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pickup_code        TEXT;     -- 5-char e.g. 'P-7421'; shown to customer + at counter
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS pickup_ready_at    TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS delivered_at       TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS take_rate_amount   REAL;     -- Printosky's cut, in INR (paise/100)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS route_transfer_id  TEXT;     -- Razorpay transfer ID once Route splits the captured payment

-- Index on pickup_code for the public tracker page lookup.
CREATE INDEX IF NOT EXISTS idx_jobs_pickup_code ON jobs (pickup_code) WHERE pickup_code IS NOT NULL;

-- Backfill: every existing Oxygen job is assigned to OSP.
UPDATE jobs SET assigned_store_id = 'OSP' WHERE assigned_store_id IS NULL;


-- ---------------------------------------------------------------------------
-- 3. New `routing_decisions` — append-only log for debugging + fairness.
--    Every call to the routing engine writes one row, even when there's only
--    one eligible store. Critical when stores dispute "why didn't I get that
--    job?" and for tuning routing v2 against historical SLA performance.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS routing_decisions (
    id                BIGSERIAL PRIMARY KEY,
    job_id            TEXT NOT NULL,
    decided_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    eligible_stores   JSONB NOT NULL,              -- ["OSP", "STORE2"]
    scores_json       JSONB NOT NULL,              -- {"OSP": 12.4, "STORE2": 8.1}
    chosen_store_id   TEXT NOT NULL,
    reason            TEXT,                        -- 'highest_score' | 'round_robin_tiebreak' | 'reroute_after_reject' | ...
    reroute_count     INTEGER NOT NULL DEFAULT 0,  -- bumps each time a job is re-routed (ack timeout, reject, fail)
    notes             TEXT
);

CREATE INDEX IF NOT EXISTS idx_routing_decisions_job_id  ON routing_decisions (job_id);
CREATE INDEX IF NOT EXISTS idx_routing_decisions_chosen  ON routing_decisions (chosen_store_id, decided_at DESC);

ALTER TABLE routing_decisions ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename='routing_decisions' AND policyname='service_role_all_routing_decisions') THEN
    CREATE POLICY "service_role_all_routing_decisions" ON routing_decisions FOR ALL USING (true) WITH CHECK (true);
  END IF;
END $$;


-- ---------------------------------------------------------------------------
-- 4. `academic_orders` becomes multi-tenant.
--    Defaults to 'OSP' so every existing academic order keeps flowing through
--    the Oxygen-fulfilled pipeline. The routing engine starts populating this
--    once a second store opts into academic capability.
-- ---------------------------------------------------------------------------

ALTER TABLE academic_orders ADD COLUMN IF NOT EXISTS store_id TEXT NOT NULL DEFAULT 'OSP';

CREATE INDEX IF NOT EXISTS idx_academic_orders_store_id ON academic_orders (store_id);


-- ---------------------------------------------------------------------------
-- Verification (manual, after apply):
--   SELECT store_id, kyc_status, take_rate_pct, dispatch_whatsapp,
--          display_pickup_label, capabilities_json
--     FROM partners;
--   -- expect: OSP row populated; any other rows show their backfill state.
--
--   SELECT count(*), count(assigned_store_id), count(pickup_code)
--     FROM jobs;
--   -- expect: every existing row has assigned_store_id='OSP', pickup_code is null
--   --         (filled by routing engine on new jobs only).
--
--   SELECT count(*) FROM routing_decisions;
--   -- expect: 0 immediately after apply; grows as routing engine v1 ships.
--
--   SELECT count(*), count(*) FILTER (WHERE store_id IS NOT NULL)
--     FROM academic_orders;
--   -- expect: both counts equal (all rows defaulted to 'OSP').
-- ---------------------------------------------------------------------------
