-- SCHEMA_v25_wa_message_costs.sql
-- Per-message WhatsApp (Meta Cloud API) cost tracking.
--
-- Meta does NOT return a money amount, but every outbound message gets a status
-- callback whose `pricing` object carries the billing CATEGORY (service /
-- marketing / utility / authentication) and a `billable` flag. We capture that
-- and apply a configurable INR rate card (db_cloud.WA_RATE_CARD_INR) to estimate
-- the spend. `billable` is authoritative for whether a message is charged at all
-- (service + in-window utility are free); the rate card only estimates the ₹.
--
-- One row per sent message, keyed by the WhatsApp message id (wamid). Upserted
-- as statuses arrive (sent -> delivered -> read); pricing usually lands on 'sent'.

create table if not exists public.wa_message_costs (
  wamid           text primary key,
  recipient       text,
  status          text,          -- last status seen: sent / delivered / read / failed
  category        text,          -- service / marketing / utility / authentication
  billable        boolean,
  pricing_model   text,          -- PMP (per-message) / CBP (conversation-based)
  conversation_id text,
  origin_type     text,          -- conversation origin: service / marketing / utility / ...
  est_cost_inr    numeric(10,4) not null default 0,
  created_at      timestamptz   not null default now(),
  updated_at      timestamptz   not null default now()
);

create index if not exists wa_message_costs_created_idx  on public.wa_message_costs (created_at);
create index if not exists wa_message_costs_category_idx on public.wa_message_costs (category);

alter table public.wa_message_costs enable row level security;
-- No public policies: service-role (server) access only, like the other
-- server-owned telemetry tables (pb_api_calls).
