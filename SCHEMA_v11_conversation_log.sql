-- SCHEMA v11 — Conversation log for WhatsApp message audit trail
-- Run in Supabase SQL Editor after SCHEMA_v10_cloud.sql.

CREATE TABLE IF NOT EXISTS conversation_log (
    id            BIGSERIAL PRIMARY KEY,
    phone         TEXT NOT NULL,
    direction     TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_type  TEXT NOT NULL DEFAULT 'text',
    body          TEXT,
    filename      TEXT,
    job_id        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast inbox + thread queries
CREATE INDEX IF NOT EXISTS idx_conversation_log_phone_created
    ON conversation_log (phone, created_at DESC);

-- RLS: service_role full access, anon read-only for admin dashboard
ALTER TABLE conversation_log ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  CREATE POLICY "service_role_all_conversation_log"
      ON conversation_log FOR ALL USING (true) WITH CHECK (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE POLICY "anon_read_conversation_log"
      ON conversation_log FOR SELECT USING (true);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
