-- SCHEMA v26 — Part-payment support for book orders
--
-- Customers sometimes pay in instalments and send multiple screenshots.
-- This adds a payments ledger (one row per screenshot Anu reviews) plus a
-- running verified total on the order, so part-payments can be captured and
-- validated amount-by-amount before the order is confirmed.
--
-- Additive and idempotent (safe to re-run). No data loss: existing rows get
-- amount_paid = 0; already-confirmed orders are untouched.

-- 1) Running total of VERIFIED payments, denormalised onto the order for quick reads.
ALTER TABLE book_orders
    ADD COLUMN IF NOT EXISTS amount_paid numeric NOT NULL DEFAULT 0;

-- 2) Payments ledger: one row per screenshot Anu reviews.
--    No FK on order_code by design (avoids migration coupling); integrity is
--    enforced at the application layer.
CREATE TABLE IF NOT EXISTS book_payments (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_code  text        NOT NULL,
    amount      numeric,                              -- set when Anu verifies; NULL while pending
    proof_url   text,
    status      text        NOT NULL DEFAULT 'pending',
    created_at  timestamptz NOT NULL DEFAULT now(),
    verified_at timestamptz,
    CONSTRAINT book_payments_status_chk
        CHECK (status IN ('pending', 'verified', 'rejected'))
);

CREATE INDEX IF NOT EXISTS idx_book_payments_order  ON book_payments (order_code);
CREATE INDEX IF NOT EXISTS idx_book_payments_status ON book_payments (status);

-- 3) Server-only table: enable RLS with no policies. The bot connects with the
--    service role (which bypasses RLS); anon/public clients get no access.
ALTER TABLE book_payments ENABLE ROW LEVEL SECURITY;
