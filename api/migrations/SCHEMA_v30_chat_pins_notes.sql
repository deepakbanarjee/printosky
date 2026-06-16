-- api/migrations/SCHEMA_v30_chat_pins_notes.sql
-- Manual chat triage: pin a conversation + attach follow-up notes.
--
-- Staff pin a chat from the admin Conversations tab and jot timestamped notes
-- ("owes ₹5370, waiting on payment slip — call tomorrow"). Pinned chats sort to
-- the top of the inbox and are surfaced in the twice-daily chat-audit digest so
-- a promised follow-up isn't forgotten.
--
-- RLS: service_role only (no anon access); all reads/writes go through the
-- Vercel API, matching whatsapp_contacts (SCHEMA v16).

-- 1. Pin flag on the existing contact row. Additive + IF NOT EXISTS so this is
--    safe to re-run and backend code degrades gracefully before it is applied.
ALTER TABLE whatsapp_contacts ADD COLUMN IF NOT EXISTS pinned    BOOLEAN     NOT NULL DEFAULT false;
ALTER TABLE whatsapp_contacts ADD COLUMN IF NOT EXISTS pinned_at TIMESTAMPTZ;

-- 2. Append-only note thread — one row per note, never overwritten.
CREATE TABLE IF NOT EXISTS contact_notes (
    id          BIGSERIAL   PRIMARY KEY,
    phone       TEXT        NOT NULL,
    note        TEXT        NOT NULL,
    created_by  TEXT,                                   -- staff id / 'admin', best-effort
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_contact_notes_phone
    ON contact_notes (phone, created_at DESC);

ALTER TABLE contact_notes ENABLE ROW LEVEL SECURITY;

-- Service role only — no anon access; notes read/written via backend proxy.
CREATE POLICY "service_role_all_contact_notes"
    ON contact_notes FOR ALL
    USING (true) WITH CHECK (true);
