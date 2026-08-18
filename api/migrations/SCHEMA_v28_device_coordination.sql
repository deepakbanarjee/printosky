-- v28 — device coordination: many PCs per store, one owner per job of work.
--
-- Every store will run several boxes (counter, second counter, office). Today
-- each one independently polls the printers and independently pulls paid jobs
-- to print, because the only guard is a per-machine config flag and a LOCAL
-- pulled_jobs table. That produced Nattika's 388 duplicated printer-job rows,
-- and would produce duplicate PAPER the moment two boxes both auto-print.
--
-- Fix: coordinate through the one thing every box already shares — Supabase.
--   * store_role_leases — a short TTL lease per (store, role). Whoever holds it
--     does that work; everyone else stands by and takes over automatically if
--     the holder dies. No per-box configuration.
--   * jobs.print_claimed_at — an atomic claim, so a job prints exactly once
--     even if every box tries at the same instant.
--
-- Safe to re-run.

create table if not exists store_devices (
    store_id    text        not null,
    device_id   text        not null,
    hostname    text,
    app_version text,
    first_seen  timestamptz not null default now(),
    last_seen   timestamptz not null default now(),
    primary key (store_id, device_id)
);

comment on table store_devices is
  'Every PC that has ever run the Printosky agent, per store. last_seen is the box''s heartbeat.';

create table if not exists store_role_leases (
    store_id     text        not null,
    role         text        not null,   -- 'poll_printers', 'print_jobs', 'fetch_epson_log'
    owner_device text,
    acquired_at  timestamptz,
    expires_at   timestamptz,
    updated_at   timestamptz not null default now(),
    primary key (store_id, role)
);

comment on table store_role_leases is
  'Who currently does each singleton job of work at a store. A lease is taken for a short TTL and renewed; if the holder dies it expires and another box picks it up.';

create index if not exists idx_role_leases_expiry on store_role_leases (expires_at);

-- Exactly-once printing. Claimed before the file is sent to the printer, so a
-- second box attempting the same job finds the row already taken and skips.
alter table jobs add column if not exists print_claimed_at timestamptz;
alter table jobs add column if not exists print_claimed_by text;

create index if not exists idx_jobs_print_claim on jobs (assigned_store_id, print_claimed_at);

-- RLS enabled with no policy, matching v27: only the service role (which
-- bypasses RLS) touches these. The anon key used by the browser consoles gets
-- nothing, which is right — leases are agent bookkeeping, not console data.
alter table store_devices     enable row level security;
alter table store_role_leases enable row level security;
