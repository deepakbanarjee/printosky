-- api/migrations/SCHEMA_v18_webhook_idempotency.sql
-- TASK-013 (roadmap-2026-05): webhook idempotency table.
--
-- Meta retries webhooks if the handler is slow. Razorpay can fire twice on
-- the same payment. Without dedupe, both producers can drive double-print,
-- duplicate "ready" notifications, and (worst case) duplicate payment
-- captures. This table records every webhook event_id we've handled, with
-- a PRIMARY KEY constraint that makes the dedupe race-safe at the database
-- level (no app-side locking needed).

CREATE TABLE IF NOT EXISTS processed_webhooks (
    event_id    TEXT        PRIMARY KEY,
    handler     TEXT        NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    result      JSONB
);

ALTER TABLE processed_webhooks ENABLE ROW LEVEL SECURITY;

-- Service role only — no anon/auth access; webhook handlers run with the
-- service key from Vercel functions.
DROP POLICY IF EXISTS "service_role_all_processed_webhooks" ON processed_webhooks;
CREATE POLICY "service_role_all_processed_webhooks"
    ON processed_webhooks FOR ALL
    USING (true) WITH CHECK (true);

-- For GC scans: delete rows older than N days. Index keeps GC cheap.
CREATE INDEX IF NOT EXISTS idx_processed_webhooks_received_at
    ON processed_webhooks (received_at);

-- GC suggestion (run as a Supabase scheduled function, e.g. monthly):
--   DELETE FROM processed_webhooks WHERE received_at < NOW() - INTERVAL '30 days';
