-- SCHEMA_v44: the v2 core — orders, payment ledger, inbox/outbox, attempts,
-- individual identities.
--
-- Plan: docs/V2_ARCHITECTURE.md
-- Review findings addressed: F01/F09 (identities), F02 (allocations),
-- F03 (inbox + one transaction), F06 (print attempts), F08 (object ids).
--
-- ── This migration is purely additive ───────────────────────────────────────
-- It creates new tables and one function. It ALTERs nothing, DROPs nothing and
-- renames nothing. `jobs`, `job_batches`, `staff`, `processed_webhooks` and
-- every other existing table keep their exact shape and their exact readers.
-- Running this migration alone changes no behaviour anywhere: the v2 API is
-- the only thing that reads these tables, and v2 serves no legacy route.
--
-- ── RLS ─────────────────────────────────────────────────────────────────────
-- Every table below: RLS enabled, NO policy, grants revoked from anon and
-- authenticated. With RLS on and no policy, those roles are denied by default;
-- the Vercel functions use SUPABASE_SERVICE_KEY, which bypasses RLS by
-- Supabase's design. This is the same deny-by-default pattern established by
-- SCHEMA_v19 and SCHEMA_v29, and it is deliberate: the browser must never read
-- the payment ledger or the identity table directly, whatever key it holds.
--
-- ── After applying ──────────────────────────────────────────────────────────
--   1. Apply in the Supabase SQL editor (or via the MCP apply_migration).
--   2. Seed identities:  python scripts/seed_v2_identities.py --dry-run
--   3. Regenerate the schema contract: python scripts/check_schema.py --dump
--      (do NOT hand-edit config/schema_manifest.yaml to silence the drift)

BEGIN;

-- ── Orders ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.orders (
    order_id          text PRIMARY KEY,
    store_id          text NOT NULL,
    customer_phone    text NOT NULL,
    customer_name     text,
    channel           text NOT NULL DEFAULT 'web',
    lane              text NOT NULL DEFAULT 'assisted',
    -- Two independent state machines over one row. A payment may only write
    -- payment_state; production may only write production_state. See
    -- core/orders.py, which is where the legal edges are defined.
    payment_state     text NOT NULL DEFAULT 'unpaid',
    production_state  text NOT NULL DEFAULT 'draft',
    total_paise       bigint NOT NULL DEFAULT 0 CHECK (total_paise >= 0),
    paid_paise        bigint NOT NULL DEFAULT 0,
    quote_hash        text,
    accepted_at       timestamptz,
    promised_at       timestamptz,
    pickup_code       text,
    note              text,
    -- Optimistic concurrency. Every update carries the version it read and
    -- fails if it moved; that is the only thing stopping two consoles from
    -- overwriting each other.
    version           integer NOT NULL DEFAULT 1,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS orders_store_state_idx
    ON public.orders (store_id, production_state, created_at DESC);
CREATE INDEX IF NOT EXISTS orders_phone_idx ON public.orders (customer_phone);
-- The operator console's first screen: what is paid and not yet started.
CREATE INDEX IF NOT EXISTS orders_actionable_idx
    ON public.orders (store_id, updated_at DESC)
    WHERE production_state IN ('queued', 'held', 'output_uncertain', 'awaiting_approval');

CREATE TABLE IF NOT EXISTS public.order_items (
    item_id         text PRIMARY KEY,
    order_id        text NOT NULL REFERENCES public.orders(order_id) ON DELETE CASCADE,
    -- A storage OBJECT ID, never a caller-supplied URL. F08: the old order
    -- handler accepted any file_url and the store puller then fetched it from
    -- inside the shop LAN. The agent resolves this id to a short-lived signed
    -- download; there is nothing here for an attacker to point anywhere.
    source_object   text NOT NULL,
    file_name       text,
    page_count      integer NOT NULL DEFAULT 0,
    spec            jsonb NOT NULL DEFAULT '{}'::jsonb,
    amount_paise    bigint NOT NULL DEFAULT 0 CHECK (amount_paise >= 0),
    document_hash   text,
    preflight_ok    boolean,          -- NULL = not checked yet; false = held
    preflight_note  text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_items_order_idx ON public.order_items (order_id);

CREATE TABLE IF NOT EXISTS public.order_tasks (
    task_id      text PRIMARY KEY,
    order_id     text NOT NULL REFERENCES public.orders(order_id) ON DELETE CASCADE,
    item_id      text,
    kind         text NOT NULL,         -- preflight|print|finishing|transfer|handover
    store_id     text NOT NULL,
    state        text NOT NULL DEFAULT 'open',
    assigned_to  text,
    due_at       timestamptz,
    note         text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS order_tasks_due_idx
    ON public.order_tasks (store_id, state, due_at);

-- ── Payments: inbox → ledger → allocations → outbox ─────────────────────────

CREATE TABLE IF NOT EXISTS public.payment_inbox (
    inbox_id     bigserial PRIMARY KEY,
    provider     text NOT NULL,
    event_id     text NOT NULL,
    payment_id   text NOT NULL,
    order_ref    text NOT NULL,
    amount_paise bigint NOT NULL,
    currency     text NOT NULL DEFAULT 'INR',
    method       text,
    state        text NOT NULL DEFAULT 'received',  -- received|processed
    result       text,                              -- ok|duplicate|unmatched|error
    error        text,
    raw          jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    -- One row per provider event. The webhook writes here BEFORE answering 200,
    -- so a crash between the ack and the ledger leaves a `received` row that
    -- /v2/payments/replay finishes (F03).
    CONSTRAINT payment_inbox_event_uniq UNIQUE (provider, event_id)
);
CREATE INDEX IF NOT EXISTS payment_inbox_pending_idx
    ON public.payment_inbox (created_at) WHERE state = 'received';

CREATE TABLE IF NOT EXISTS public.payments (
    payment_id           text PRIMARY KEY,
    provider             text NOT NULL,
    provider_payment_id  text NOT NULL,
    order_id             text NOT NULL REFERENCES public.orders(order_id),
    amount_paise         bigint NOT NULL,     -- negative for a refund row
    currency             text NOT NULL DEFAULT 'INR',
    method               text,
    kind                 text NOT NULL DEFAULT 'capture',  -- capture|refund
    event_id             text,
    captured_at          timestamptz NOT NULL DEFAULT now(),
    created_at           timestamptz NOT NULL DEFAULT now(),
    -- Contract #4: unique external payment identity. This constraint, not a
    -- marker table, is what makes a re-delivered webhook harmless.
    CONSTRAINT payments_provider_identity_uniq UNIQUE (provider, provider_payment_id)
);
CREATE INDEX IF NOT EXISTS payments_order_idx ON public.payments (order_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS public.payment_allocations (
    allocation_id bigserial PRIMARY KEY,
    payment_id    text NOT NULL REFERENCES public.payments(payment_id) ON DELETE CASCADE,
    order_id      text NOT NULL,
    item_id       text NOT NULL,
    amount_paise  bigint NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS payment_allocations_order_idx
    ON public.payment_allocations (order_id);
-- F02, as an invariant the database itself holds: the allocations of one
-- payment must sum to that payment. Enforced in v2_apply_payment below, which
-- is the only writer.

CREATE TABLE IF NOT EXISTS public.outbox_events (
    outbox_id   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    topic       text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedup_key   text,
    state       text NOT NULL DEFAULT 'pending',   -- pending|sent|dead
    attempts    integer NOT NULL DEFAULT 0,
    last_error  text,
    due_at      timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- Committed with the money, delivered afterwards with retries. A WhatsApp
    -- outage can no longer decide whether a payment was recorded.
    CONSTRAINT outbox_dedup_uniq UNIQUE (dedup_key)
);
CREATE INDEX IF NOT EXISTS outbox_due_idx ON public.outbox_events (due_at) WHERE state = 'pending';

-- ── Print attempts (F05, F06) ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.print_attempts (
    attempt_id      text PRIMARY KEY,
    task_id         text NOT NULL,
    device_id       text NOT NULL,
    printer_queue   text,
    -- The fencing token from /v2/orders/{id}/claim. A write bearing an older
    -- token than the current claim is refused, which is what stops two boxes
    -- from independently releasing the same attempt.
    attempt_token   text NOT NULL,
    state           text NOT NULL DEFAULT 'created',
    spool_id        text,
    sheets_expected integer NOT NULL DEFAULT 0,
    sheets_observed integer,
    failure_reason  text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);
CREATE INDEX IF NOT EXISTS print_attempts_task_idx ON public.print_attempts (task_id, started_at);
-- The exception queue: attempts nobody resolved.
CREATE INDEX IF NOT EXISTS print_attempts_open_idx
    ON public.print_attempts (started_at)
    WHERE state IN ('created', 'submitted', 'uncertain');

-- ── Identities (F01, F09) ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.identities (
    identity_id   text PRIMARY KEY,
    display_name  text NOT NULL,
    role          text NOT NULL,          -- owner|manager|counter|production|agent|viewer
    store_ids     text[] NOT NULL DEFAULT '{}',   -- '{*}' for everywhere
    kind          text NOT NULL DEFAULT 'staff',  -- staff|device|service
    secret_hash   text NOT NULL,
    secret_salt   text,                   -- NULL = legacy unsalted sha256, rehash on login
    active        boolean NOT NULL DEFAULT true,
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS identities_active_idx ON public.identities (kind) WHERE active;

CREATE TABLE IF NOT EXISTS public.identity_sessions (
    session_id   text PRIMARY KEY,
    identity_id  text NOT NULL REFERENCES public.identities(identity_id) ON DELETE CASCADE,
    role         text NOT NULL,
    store_ids    text[] NOT NULL DEFAULT '{}',
    device_id    text,
    issued_at    timestamptz NOT NULL DEFAULT now(),
    expires_at   timestamptz NOT NULL,
    revoked_at   timestamptz
);
CREATE INDEX IF NOT EXISTS identity_sessions_live_idx
    ON public.identity_sessions (identity_id, expires_at) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS public.login_failures (
    failure_id     bigserial PRIMARY KEY,
    identity_hint  text,
    ip             text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
-- Durable, because a serverless in-process dict throttles one lambda instance
-- and nothing else.
CREATE INDEX IF NOT EXISTS login_failures_recent_idx ON public.login_failures (ip, created_at DESC);

-- ── The one transaction a payment needs ──────────────────────────────────────
--
-- PostgREST cannot span statements, so the atomicity lives here. Either the
-- payment, its allocations, the order balance and the outbox rows all land, or
-- none of them do and the caller gets a retryable failure. Returns a JSON
-- result rather than raising, so the adapter can distinguish a lost version
-- race (retry with fresh data) from a real error (alert).

CREATE OR REPLACE FUNCTION public.v2_apply_payment(
    p_order_id          text,
    p_expected_version  integer,
    p_payment           jsonb,
    p_allocations       jsonb,
    p_new_paid_paise    bigint,
    p_new_payment_state text,
    p_outbox            jsonb
) RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_alloc_sum   bigint;
    v_amount      bigint;
    v_updated     integer;
BEGIN
    v_amount := (p_payment ->> 'amount_paise')::bigint;

    SELECT COALESCE(SUM((value ->> 'amount_paise')::bigint), 0)
      INTO v_alloc_sum
      FROM jsonb_array_elements(COALESCE(p_allocations, '[]'::jsonb));

    -- F02 as a hard gate: allocations must equal the payment, exactly. A caller
    -- that got this wrong is rejected here rather than writing money twice.
    IF v_alloc_sum <> v_amount THEN
        RETURN jsonb_build_object('ok', false, 'error', 'allocation_mismatch',
                                  'allocated', v_alloc_sum, 'amount', v_amount);
    END IF;

    INSERT INTO payments (payment_id, provider, provider_payment_id, order_id,
                          amount_paise, currency, method, kind, event_id, captured_at)
    VALUES (p_payment ->> 'payment_id',
            p_payment ->> 'provider',
            p_payment ->> 'provider_payment_id',
            p_order_id,
            v_amount,
            COALESCE(p_payment ->> 'currency', 'INR'),
            p_payment ->> 'method',
            COALESCE(p_payment ->> 'kind', 'capture'),
            p_payment ->> 'event_id',
            COALESCE((p_payment ->> 'captured_at')::timestamptz, now()))
    ON CONFLICT (provider, provider_payment_id) DO NOTHING;

    IF NOT FOUND THEN
        -- Already in the ledger: the money was counted by an earlier delivery.
        RETURN jsonb_build_object('ok', true, 'duplicate', true);
    END IF;

    INSERT INTO payment_allocations (payment_id, order_id, item_id, amount_paise)
    SELECT p_payment ->> 'payment_id', p_order_id,
           value ->> 'item_id', (value ->> 'amount_paise')::bigint
      FROM jsonb_array_elements(COALESCE(p_allocations, '[]'::jsonb));

    UPDATE orders
       SET paid_paise    = p_new_paid_paise,
           payment_state = p_new_payment_state,
           version       = version + 1,
           updated_at    = now()
     WHERE order_id = p_order_id
       AND version  = p_expected_version;
    GET DIAGNOSTICS v_updated = ROW_COUNT;

    IF v_updated = 0 THEN
        -- The order moved under us. Roll the whole thing back by raising; the
        -- adapter retries with a fresh read.
        RAISE EXCEPTION 'version_conflict'
            USING ERRCODE = 'serialization_failure';
    END IF;

    INSERT INTO outbox_events (topic, payload, dedup_key)
    SELECT value ->> 'topic', COALESCE(value -> 'payload', '{}'::jsonb), value ->> 'dedup_key'
      FROM jsonb_array_elements(COALESCE(p_outbox, '[]'::jsonb))
    ON CONFLICT (dedup_key) DO NOTHING;

    RETURN jsonb_build_object('ok', true, 'duplicate', false);
EXCEPTION
    WHEN serialization_failure THEN
        RETURN jsonb_build_object('ok', false, 'error', 'version_conflict');
END;
$$;

-- Outbox retry bookkeeping, so the adapter never does read-modify-write on it.
CREATE OR REPLACE FUNCTION public.v2_outbox_fail(
    p_outbox_id uuid, p_error text, p_retry_after_seconds integer
) RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = public
AS $$
    UPDATE outbox_events
       SET attempts   = attempts + 1,
           last_error = p_error,
           due_at     = now() + make_interval(secs => p_retry_after_seconds),
           state      = CASE WHEN attempts + 1 >= 8 THEN 'dead' ELSE 'pending' END
     WHERE outbox_id = p_outbox_id;
$$;

-- ── RLS: deny by default, service_role only ─────────────────────────────────

ALTER TABLE public.orders              ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_items         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.order_tasks         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_inbox       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payments            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payment_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbox_events       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.print_attempts      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identities          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.identity_sessions   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.login_failures      ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.orders, public.order_items, public.order_tasks,
              public.payment_inbox, public.payments, public.payment_allocations,
              public.outbox_events, public.print_attempts, public.identities,
              public.identity_sessions, public.login_failures
    FROM anon, authenticated;

-- The RPCs run SECURITY DEFINER, so their execute grant is the access control.
REVOKE ALL ON FUNCTION public.v2_apply_payment(text, integer, jsonb, jsonb, bigint, text, jsonb)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.v2_outbox_fail(uuid, text, integer)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.v2_apply_payment(text, integer, jsonb, jsonb, bigint, text, jsonb)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.v2_outbox_fail(uuid, text, integer) TO service_role;

-- ── Documentation that travels with the schema ──────────────────────────────

COMMENT ON TABLE public.orders IS
    'v2 authoritative order. payment_state and production_state are independent machines (core/orders.py); a payment never moves production.';
COMMENT ON COLUMN public.orders.version IS
    'Optimistic concurrency. Every update must match the version it read.';
COMMENT ON COLUMN public.order_items.source_object IS
    'Storage object id. NEVER a caller-supplied URL — see review finding F08.';
COMMENT ON TABLE public.payment_inbox IS
    'Verified provider events, written BEFORE the webhook is acknowledged. state=received rows are the crash-recovery queue for /v2/payments/replay.';
COMMENT ON TABLE public.payment_allocations IS
    'How each payment splits across order lines. Sums to the payment exactly — enforced in v2_apply_payment. See review finding F02.';
COMMENT ON TABLE public.outbox_events IS
    'Side effects committed with the money and delivered afterwards with retries.';
COMMENT ON TABLE public.print_attempts IS
    'One row per submission to a printer, written before spooling. state=uncertain is resolved by a human and never retried automatically (F05/F06).';
COMMENT ON TABLE public.identities IS
    'One row per person or device, with a role and an explicit store scope. Replaces the single shared Supabase Auth user (F09).';

COMMIT;
