-- SCHEMA_v24_book_commission.sql
-- Divya teacher (Xtraa coordinator) commission tracking on book_orders.
--
-- Business rules (owner, 2026-06-03):
--   * Every book order accrues a flat ₹50 per physical book to Divya teacher.
--   * Courier is NOT commissionable.
--   * Oxygen's net is always grand_total - commission. Only the cash direction
--     differs by who collected payment:
--       - payment_collected_by in ('oxygen','pending') -> Oxygen owes Divya `commission`.
--       - payment_collected_by = 'divya'               -> Divya owes Oxygen `grand_total - commission`.
--     ('pending' = customer hasn't paid yet but the order still ships — Divya's
--      orders are never held for payment.)
--   * Delivery is usually couriered (₹75); sometimes hand-delivered to the
--     Xtraa office (no courier charge).
--
-- Additive + idempotent — safe to re-run.

alter table public.book_orders
  add column if not exists via_divya            boolean     not null default true,
  add column if not exists commission           numeric     not null default 0,
  add column if not exists payment_collected_by text        not null default 'oxygen',
  add column if not exists delivery_method      text        not null default 'courier',
  add column if not exists divya_settled         boolean     not null default false,
  add column if not exists divya_settled_at      timestamptz;

-- Constrain enumerated columns to known values.
alter table public.book_orders
  drop constraint if exists book_orders_payment_collected_by_chk;
alter table public.book_orders
  add constraint book_orders_payment_collected_by_chk
  check (payment_collected_by in ('oxygen', 'divya', 'pending'));

alter table public.book_orders
  drop constraint if exists book_orders_delivery_method_chk;
alter table public.book_orders
  add constraint book_orders_delivery_method_chk
  check (delivery_method in ('courier', 'xtraa_office'));

-- Backfill commission for existing orders from their item quantities (₹50/book).
update public.book_orders
set commission = 50 * (
      coalesce((items->>'malayalam')::int, 0)
    + coalesce((items->>'hindi')::int, 0)
    + coalesce((items->>'english')::int, 0)
  )
where commission = 0;
