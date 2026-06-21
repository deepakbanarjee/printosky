-- SCHEMA_v31: verifier reminder sweep
-- Tracks when a payment_review order was last re-pinged to Anu (the payment
-- verifier), so the /cron/payment-review-reminders sweep nudges at most once per
-- cooldown window and orders never strand silently in payment_review.
--
-- Root cause this supports: the original "Payment to verify" prompt to Anu is
-- fire-and-forget. When she misses it (it lands at night, or stacks behind
-- another prompt) nothing re-surfaces it and the order sits forever while the
-- customer was told "we're verifying".

ALTER TABLE public.book_orders
    ADD COLUMN IF NOT EXISTS verifier_reminder_at timestamptz;

COMMENT ON COLUMN public.book_orders.verifier_reminder_at IS
    'Last time the verification prompt was re-sent to Anu by the payment-review reminder sweep (NULL = never reminded).';
