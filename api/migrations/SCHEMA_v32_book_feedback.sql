-- SCHEMA_v32: book_feedback
-- Customer review of the Xtraa book after delivery. One row per order_code
-- (rating saved first, comment may follow). Populated when a delivered customer
-- replies to the Malayalam feedback template; mirrored to Anu + admin chat.

CREATE TABLE IF NOT EXISTS public.book_feedback (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_code  text NOT NULL UNIQUE,
    phone       text,
    rating      smallint CHECK (rating BETWEEN 1 AND 5),
    comment     text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Service role (used by the bot/API) bypasses RLS; enabling it keeps anon/public
-- clients out, matching the rest of the schema.
ALTER TABLE public.book_feedback ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE public.book_feedback IS
    'Post-delivery customer review of the Xtraa book (1-5 rating + comment), one row per order_code.';
