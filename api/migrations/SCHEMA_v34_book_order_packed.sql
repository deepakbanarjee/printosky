-- SCHEMA_v34: packed_at on book_orders — closes the confirmed→dispatched gap
--
-- Bug: the dispatch sheet lists every confirmed, undispatched order. A box that
-- was packed but not yet couriered stayed 'confirmed', so the next day's sheet
-- reprinted its slip and it shipped twice. packed_at marks "packed, awaiting
-- courier": such orders drop off the pick list (can't be re-picked) and instead
-- appear in a reminder banner. Status stays 'confirmed' until dispatched, so the
-- revenue + commission ledgers are unaffected.

ALTER TABLE public.book_orders
    ADD COLUMN IF NOT EXISTS packed_at timestamptz;

COMMENT ON COLUMN public.book_orders.packed_at IS
    'Set when the order is packed and waiting for courier pickup. Drops it off the dispatch-sheet pick list to prevent double-shipping; status stays confirmed until dispatched.';
