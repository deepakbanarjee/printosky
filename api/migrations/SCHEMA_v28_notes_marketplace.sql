-- SCHEMA v28 — Student Notes Marketplace
--
-- Students upload PDF notes via WhatsApp. Others browse and print them.
-- Uploaders earn non-cashable store credit (10% of print value, in paise).
--
-- Four tables:
--   notes              — uploaded PDFs with moderation lifecycle
--   credit_wallet      — one row per phone; running balance in paise (never float)
--   note_credits       — append-only ledger (positive=earned, negative=redeemed)
--   note_subscriptions — Plus/Pro tier for read-online access (Phase 2)
--
-- Storage (create buckets manually in Supabase dashboard):
--   "notes"           — private bucket for PDFs
--   "incoming-files"  — existing public bucket; previews stored under notes-preview/
--
-- RLS: service_role only on all four tables (no public reads).
-- Anon/public clients must go through the Vercel API.

-- ── notes ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notes (
    note_code          text        PRIMARY KEY,        -- NOTE-YYYYMMDD-XXXX
    uploader_phone     text        NOT NULL,
    title              text        NOT NULL,
    category           text        NOT NULL CHECK (
                                       category IN (
                                           'kerala_university',
                                           'mg_university',
                                           'calicut_university',
                                           'cusat',
                                           'entrance_exam'
                                       )
                                   ),
    subject            text        NOT NULL,
    page_count         int         NOT NULL DEFAULT 0,
    storage_path       text,                           -- private "notes" bucket path
    preview_path       text,                           -- public "incoming-files/notes-preview/X.png"
    status             text        NOT NULL DEFAULT 'pending'
                                       CHECK (status IN ('pending','approved','rejected','withdrawn')),
    reject_reason      text,
    uploader_attests   boolean     NOT NULL DEFAULT false,  -- "these are my original notes"
    print_count        int         NOT NULL DEFAULT 0,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE notes ENABLE ROW LEVEL SECURITY;

-- ── credit_wallet ─────────────────────────────────────────────────────────────
-- One row per phone. Balance is the source of truth; note_credits is the audit trail.
-- balance_paise >= 0 enforced at DB level as a last resort, but wallet_redeem()
-- guards this in application code first.
CREATE TABLE IF NOT EXISTS credit_wallet (
    phone           text        PRIMARY KEY,
    balance_paise   int         NOT NULL DEFAULT 0 CHECK (balance_paise >= 0),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE credit_wallet ENABLE ROW LEVEL SECURITY;

-- ── note_credits ──────────────────────────────────────────────────────────────
-- Append-only ledger. credit_paise > 0 = earned; credit_paise < 0 = redeemed.
-- note_code is nullable for redemption rows (no specific note associated).
CREATE TABLE IF NOT EXISTS note_credits (
    id              bigserial   PRIMARY KEY,
    note_code       text        REFERENCES notes(note_code) ON DELETE SET NULL,
    uploader_phone  text        NOT NULL,
    print_job_id    text,                   -- references jobs.job_id (loose FK — no constraint)
    pages_printed   int         NOT NULL DEFAULT 0,
    credit_paise    int         NOT NULL,   -- positive = commission, negative = redemption
    note            text,                   -- 'print_commission' | 'redemption'
    created_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE note_credits ENABLE ROW LEVEL SECURITY;

-- ── note_subscriptions ────────────────────────────────────────────────────────
-- Phase 2: read-online access tiers. Created now so Phase 1 code can
-- check subscription status without a migration mid-sprint.
CREATE TABLE IF NOT EXISTS note_subscriptions (
    phone           text        PRIMARY KEY,
    tier            text        NOT NULL CHECK (tier IN ('plus', 'pro')),
    status          text        NOT NULL DEFAULT 'active'
                                    CHECK (status IN ('active', 'expired')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    expires_at      timestamptz NOT NULL,
    last_payment_id text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE note_subscriptions ENABLE ROW LEVEL SECURITY;
