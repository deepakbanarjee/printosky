-- api/migrations/SCHEMA_v16_chat.sql
-- WhatsApp messenger rebuild — media_url, contacts, RLS tightening

-- 1. Store storage path for inbound/outbound media
ALTER TABLE conversation_log ADD COLUMN IF NOT EXISTS media_url TEXT;

-- 2. Contact name + unread tracking
CREATE TABLE IF NOT EXISTS whatsapp_contacts (
    phone        TEXT PRIMARY KEY,
    name         TEXT,
    last_seen_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE whatsapp_contacts ENABLE ROW LEVEL SECURITY;

-- Service role only — no anon access; contacts read via backend proxy
CREATE POLICY "service_role_all_contacts"
    ON whatsapp_contacts FOR ALL
    USING (true) WITH CHECK (true);

-- 3. Remove open anon read on conversation_log
--    All reads now go through authenticated Vercel endpoints
DROP POLICY IF EXISTS "anon_read_conversation_log" ON conversation_log;
