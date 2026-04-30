# Printosky — Feature Pipeline
_Last updated: 2026-04-30_
_Lives alongside: [MASTER_PLAN.md](MASTER_PLAN.md), [GROWTH_PLAN.md](GROWTH_PLAN.md), [EXPANSION_PLAN.md](EXPANSION_PLAN.md), [MARKETING_KICKOFF.md](MARKETING_KICKOFF.md)_

Priority tiers:
- **P0** — ship next (≤1 week)
- **P1** — current sprint (≤1 month)
- **P2** — next quarter
- **P3** — Phase B / C scope (after franchise / marketplace)

---

## Customer-facing

| ID | P | Feature | Notes |
|----|---|---------|-------|
| C1 | P0 | Referral leaderboard reply ("TOP REFERRERS") | bot returns top 5 by store-credit earned |
| C2 | P0 | Status check via WhatsApp ("STATUS") | bot returns last 3 jobs and their status |
| C3 | P0 | Quick reorder ("REORDER") | re-uses last job's settings, asks only for new file |
| C4 | P1 | Self-serve quote estimator (printosky.com/quote) | upload PDF + select options → live total via rate_card |
| C5 | P1 | Pickup-ready photo notify | when staff marks Ready, push photo of bound output |
| C6 | P1 | Loyalty tiers (Silver/Gold) | cumulative spend → permanent discount % |
| C7 | P2 | Subscription plans | e.g. ₹499/sem unlimited B&W ≤500 pages |
| C8 | P2 | Birthday auto-coupon | annual ₹50 store credit on registered birthday |
| C9 | P2 | WhatsApp Pay-In-Chat (Razorpay Flow) | inline payment without leaving chat |
| C10 | P3 | Customer PWA (track orders, reorder, store credit balance, edit PDF) | base for marketplace UX |

---

## Staff / Operations

| ID | P | Feature | Notes |
|----|---|---------|-------|
| S1 | P0 | Referral leaderboard tab in admin.html | sortable table: code · label · orders · earned · redeemed |
| S2 | P0 | Manual store-credit adjust UI | staff can grant/revoke credit (audit log row) |
| S3 | P1 | WhatsApp broadcast tool | segmented (all customers / academic / B2B), template + send |
| S4 | P1 | Daily reconciliation report | cash collected vs Razorpay vs store-credit redeemed |
| S5 | P1 | Inventory low alerts | toner / paper / spirals — manual count log + threshold push |
| S6 | P2 | Staff performance leaderboard | jobs/staff/day, avg turnaround, customer ratings |
| S7 | P2 | Customer notes pinned to phone | allergies, preferences, special-handling flags |
| S8 | P2 | Shift cash-drawer reconciliation | open shift / close shift / variance flag |

---

## Growth & Marketing

| ID | P | Feature | Notes |
|----|---|---------|-------|
| G1 | P0 | College WhatsApp group seeding kit | A4 poster + QR for ref link, messaging templates |
| G2 | P0 | Counter-poster QR (NEW: store credit) | pinned at shop counter; brings walk-ins into ref loop |
| G3 | P1 | GMB profile completion + 50 reviews target | per [GROWTH_PLAN.md](GROWTH_PLAN.md) Phase A |
| G4 | P1 | Instagram Reel pipeline | "thesis printed in 2 hours", before/after, student testimonial |
| G5 | P1 | Hostel-warden ambassador program | one ambassador per hostel, 5% override on referred orders |
| G6 | P2 | Auto-Instagram post on bound thesis (consent-gated) | uses Canva or generated image |
| G7 | P2 | College department B2B portal | bulk pricing, monthly invoice, dedicated WhatsApp |
| G8 | P2 | SEO landing pages (Thrissur / Kochi / Kozhikode) | per [GROWTH_PLAN.md](GROWTH_PLAN.md) |
| G9 | P3 | Affiliate API for tutoring centres | revenue-share embedded in their student onboarding |

---

## Financial

| ID | P | Feature | Notes |
|----|---|---------|-------|
| F1 | P1 | GST invoice auto-PDF (per order, on demand) | already partial — finish + send via WhatsApp |
| F2 | P1 | Store-credit ledger view per customer | full debit/credit history with order linkage |
| F3 | P2 | B2B credit accounts + monthly invoice | exists in `b2b_manager.py` — wire UI + Razorpay Smart Collect |
| F4 | P2 | Profit-margin per job | rate vs printer cost (toner/sheet) — input cost model |
| F5 | P3 | Razorpay Route (marketplace take rate) | only at Phase C |
| F6 | P3 | TDS 194-O compliance | mandatory at marketplace scale |

---

## Technical & Infrastructure

| ID | P | Feature | Notes |
|----|---|---------|-------|
| T1 | P0 | UptimeRobot on store PC + Vercel + Supabase | already mentioned in [SECURITY.md](SECURITY.md) |
| T2 | P1 | Backup automation (Supabase + SQLite snapshot to Storage) | nightly cron |
| T3 | P1 | Sentry / structured logging | replace logger.info noise with searchable events |
| T4 | P1 | Rename `b2b_manager.py` SQLite store → Supabase | unblocks multi-shop |
| T5 | P2 | `agent.py` (~150 lines) replaces watcher+print_server+webhook_receiver | per [EXPANSION_PLAN.md](EXPANSION_PLAN.md) Phase A3 |
| T6 | P2 | Multi-tenant `shop_id` on all tables | Phase A4 |
| T7 | P3 | Self-serve franchise onboarding | Phase B trigger work |
| T8 | P3 | PostGIS for geospatial shop matching | Phase C |

---

## Security

_All from [SECURITY.md](SECURITY.md) — restating priorities here for cross-reference._

| ID | P | Item |
|----|---|------|
| SEC1 | P0 | Verify `RAZORPAY_ACADEMIC_WEBHOOK_SECRET` set on Vercel + Razorpay dashboard |
| SEC2 | P0 | Verify `ADMIN_PASSWORD_HASH` set on Vercel |
| SEC3 | P0 | Confirm `pin_salt` migration ran on Supabase |
| SEC4 | P1 | Change Epson `192.168.55.202` admin/admin default password |
| SEC5 | P1 | Move STORE_TOKEN from localStorage → httpOnly cookie + CSP |
| SEC6 | P2 | Scoped Supabase role to replace service_role key on store PC |
| SEC7 | P2 | Random suffix on academic project IDs (PROJ-2026-001 → enumerable) |

---

## Done (last 7 days)

- ✅ Referral tracking: capture `ref_CODE`, credit on payment
- ✅ Referral invite to 4-5 star raters
- ✅ Store-credit framing (no cash)
- ✅ MY CREDITS / BALANCE WhatsApp reply
- ✅ Redemption endpoints + admin checkbox UI
- ✅ Phone normalization, redeem idempotency, race-safe atomic update
- ✅ Branch hygiene: collapsed to `main`-only

---

## Operating Rules

- **No feature without metric.** Every P0/P1 entry needs an "instrumented for" note before merge — what's the success signal?
- **Don't pre-build for Phase C.** Anything tagged P3 stays parked until Phase B trigger fires (per EXPANSION_PLAN).
- **Customer-facing copy in Printosky brand voice** — see GROWTH_PLAN tone notes.
