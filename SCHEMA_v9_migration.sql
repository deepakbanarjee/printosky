-- PRINTOSKY SCHEMA v9 — Review & Discount System (Sprint 12B)
-- Run in Supabase SQL Editor after SCHEMA_v8.
-- Also applied automatically to local SQLite by watcher.py / print_server.py on startup.
--
-- Changes:
--   1. job_reviews table  — 1-5 star ratings collected 30 min after collection
--   2. discount_codes table — unique codes generated for 4-5 star reviews

-- ── 1. job_reviews ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS job_reviews (
    id          BIGSERIAL PRIMARY KEY,          -- SQLite: INTEGER PRIMARY KEY AUTOINCREMENT
    job_id      TEXT        NOT NULL,
    phone       TEXT,
    rating      INTEGER,                        -- 1-5 (NULL until customer replies)
    feedback    TEXT,                           -- optional text for low ratings
    review_sent INTEGER     DEFAULT 0,          -- 1 = review request WhatsApp sent
    created_at  TIMESTAMPTZ DEFAULT NOW()       -- SQLite: TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reviews_job   ON job_reviews (job_id);
CREATE INDEX IF NOT EXISTS idx_reviews_phone ON job_reviews (phone);

ALTER TABLE job_reviews ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "auth read job_reviews"  ON job_reviews;
CREATE POLICY "auth read job_reviews"
    ON job_reviews FOR SELECT
    USING (auth.role() = 'authenticated');

-- ── 2. discount_codes ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS discount_codes (
    code        TEXT        PRIMARY KEY,
    phone       TEXT        NOT NULL,
    pct_off     INTEGER     DEFAULT 10,
    source      TEXT        DEFAULT 'review',   -- 'review' | 'manual'
    used        INTEGER     DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_discounts_phone ON discount_codes (phone);
CREATE INDEX IF NOT EXISTS idx_discounts_used  ON discount_codes (phone, used);

ALTER TABLE discount_codes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "auth read discount_codes"  ON discount_codes;
CREATE POLICY "auth read discount_codes"
    ON discount_codes FOR SELECT
    USING (auth.role() = 'authenticated');
