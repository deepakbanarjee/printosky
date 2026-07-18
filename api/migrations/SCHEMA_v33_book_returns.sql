-- SCHEMA_v33: book_returns + replacement linkage
-- Admin-only returns/replacements for the Xtraa book campaign.
--
-- A return records a physically-returned book against an existing order (e.g.
-- customer ordered Hindi, wanted Malayalam). Resolution is a replacement reship,
-- a recorded settlement, or both. A replacement is a NEW book_orders row flagged
-- is_replacement=true so it rides the existing dispatch/deliver pipeline while
-- staying out of the revenue + commission ledgers.
--
-- MONEY: a return can move money EITHER way. Swapping to a pricier book, or the
-- customer bearing inward/outward courier, means the customer OWES the store
-- (settlement_direction='collect'). A refund means the store owes the customer
-- ('refund'). settlement_amount is the net rupees; the price_delta / inward_courier
-- / outward_courier columns keep the breakdown for the record. All settlement is
-- done manually by staff over QR / UPI / Cash (no Razorpay on this account yet) —
-- the system only RECORDS it.

CREATE TABLE IF NOT EXISTS public.book_returns (
    id                     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_code            text NOT NULL UNIQUE,
    order_code             text NOT NULL,                 -- original order returned against
    phone                  text,
    name                   text,
    returned_items         jsonb NOT NULL DEFAULT '{}'::jsonb,  -- {"hindi": 1}
    reason                 text,                          -- wrong_language|damaged|defective|not_needed|other
    condition              text,                          -- resellable|damaged|unopened
    resolution             text NOT NULL DEFAULT 'replacement',  -- replacement|refund|both
    replacement_order_code text,                          -- linked reship book_orders.order_code
    replacement_items      jsonb,                         -- {"malayalam": 1}
    -- money breakdown (rupees) --------------------------------------------------
    price_delta            numeric NOT NULL DEFAULT 0,    -- replacement book value − returned book value
    inward_courier         numeric NOT NULL DEFAULT 0,    -- courier to send the book back
    outward_courier        numeric NOT NULL DEFAULT 0,    -- courier to reship the replacement
    -- net settlement ------------------------------------------------------------
    settlement_direction   text NOT NULL DEFAULT 'none',  -- collect (customer pays) | refund (store pays) | none
    settlement_amount      numeric NOT NULL DEFAULT 0,    -- net rupees, absolute value
    settlement_mode        text,                          -- qr|upi|cash
    settlement_status      text NOT NULL DEFAULT 'none',  -- none|pending|done
    settlement_note        text,
    courier_borne_by       text NOT NULL DEFAULT 'customer',  -- store|customer|na (who bears courier overall)
    status                 text NOT NULL DEFAULT 'requested',  -- requested|item_received|resolved|closed|cancelled
    notes                  text,
    created_by             text,
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS book_returns_order_code_idx  ON public.book_returns (order_code);
CREATE INDEX IF NOT EXISTS book_returns_status_idx      ON public.book_returns (status);

-- Service role (bot/API) bypasses RLS; enabling it keeps anon/public clients out,
-- matching the rest of the schema.
ALTER TABLE public.book_returns ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.book_returns IS
    'Admin-logged book returns/replacements for the Xtraa campaign. One row per return; links to a replacement book_orders row and/or a net money settlement (collect/refund over QR/UPI/Cash).';

-- Replacement linkage on the order table. Existing rows backfill to false/NULL.
ALTER TABLE public.book_orders
    ADD COLUMN IF NOT EXISTS is_replacement    boolean NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS parent_order_code text,
    ADD COLUMN IF NOT EXISTS return_code       text;

COMMENT ON COLUMN public.book_orders.is_replacement IS
    'True for a reship created by a book_returns resolution. Excluded from revenue + Divya commission ledgers (settlement money is tracked on book_returns, not here).';
