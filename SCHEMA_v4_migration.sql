-- PRINTOSKY SCHEMA v4 MIGRATION
-- Run in Supabase SQL Editor after SCHEMA_v3
-- Adds columns for manual walk-in job entry and payment-gate flow

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS override_reason  TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS amount_partial   NUMERIC;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS queued_at        TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS printing_at      TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS printed_at       TEXT;
