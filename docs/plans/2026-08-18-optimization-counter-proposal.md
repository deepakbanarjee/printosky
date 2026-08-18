# Counter-Proposal — Optimization & Scalability

**Date:** 2026-08-18 · **Author:** Claude · **Status:** proposed, not started
**Responds to:** "Printosky Optimization & Scalability Proposal" (12 phases, 4 months)
**Evidence:** production Supabase `mlhuwlnwwwxdnqafelko`, queried 2026-08-18 (queries in §8)

> The original proposal is a competent, well-organized roadmap whose sequencing
> is wrong for where Printosky actually is. This document keeps what it gets
> right, drops what is already shipped, and re-orders the rest behind the one
> problem that gates every number in it: **the system records ~1.6% of what the
> store prints.**

---

## 1. What the original proposal gets right

Credit where it is due — these are real contributions, not politeness:

**1. Phase 0 is the best idea in the document.** "Establish current performance
metrics before making changes" is exactly correct, and it is the one thing the
codebase genuinely lacks. `job_events` has **0 rows**. `daily_summary` reports
zero for every store, every day. There is no reprint counter, no turnaround
timer, no failed-print tally. The proposal insists on measurement first; this
counter-proposal adopts that principle wholesale and makes it Stage 1 rather
than a one-week preamble.

**2. It names drawbacks on every phase.** Every section carries an honest
"Drawbacks" heading — processing delay, tuning cost, Meta conversation charges,
reconciliation complexity, OCR inaccuracy. That is more intellectual honesty
than most roadmaps carry, and the format is worth keeping for future planning
docs.

**3. Its routing rules are smarter than what we shipped.** Current routing is
one dimension only — colour→Epson, B&W→Konica, hardcoded in five places
(`print_server.py:950`, `store_puller.py:173`). The proposal adds a **volume**
dimension: bulk to the Konica, small-volume to the Epson. That is a genuine
refinement we do not have, and it matters — the Konica did 39,565 pages in a
fortnight while the Epson did 1,453, so small jobs queueing behind bulk runs is
a plausible real bottleneck.

**4. Preflight beyond colour is new and cheap.** `pdf_scanner.py` detects colour
vs B&W and nothing else. Encryption, blank pages, page-size mismatch and
resolution checks are not implemented anywhere. An encrypted PDF today fails at
the printer, in front of the customer.

**5. Consumables beyond toner are new.** `printer_supplies` (70,508 rows) and
threshold alerts cover toner and ink well. **Paper, spirals and binding
materials are tracked nowhere.** This matches S5 already sitting in
`docs/FEATURE_PIPELINE.md`; the proposal independently arrived at a known gap.

**6. The closing principle is sound.** "Prioritize operational efficiency before
adding advanced AI features" is the right instinct. It is simply aimed at the
wrong bottleneck.

**Adopted from the original, essentially unchanged:** Phase 0 (as Stage 1),
Phase 1's extra preflight checks, Phase 2's volume dimension, Phase 3's
paper/binding tracking, Phase 7's priority levels (deferred until there is a
queue to prioritize).

---

## 2. Why the sequencing has to change

### 2.1 The capture gap

Printer meters vs. recorded jobs, OSP, 2026-08-04 → 2026-08-18 (identical window,
both from production):

| Source | Impressions |
|---|---|
| Konica meter delta (`printer_counters`) | 39,565 |
| Epson meter delta | 1,453 |
| **Actually printed** | **41,018** (~2,900/day) |
| Recorded in `jobs` (pages × copies, 104 jobs) | **636** |
| **Capture rate** | **1.6%** |

Money over the same fortnight: **₹1,135 quoted, ₹647 collected**, and **2
Razorpay payments total since March**.

The proposal's targets — 50–60% workload reduction, double capacity, 70% fewer
print errors — are all ratios over a denominator the system cannot see. Software
that touches 1.6% of the work cannot halve staff workload no matter how good it
is. Every efficiency gain in the roadmap is multiplied by 0.016 before it
reaches the business.

### 2.2 The measurement layer is broken, silently

`daily_summary` has reported **0 jobs / ₹0 revenue for OSP across 38 consecutive
days**, while `synced_at` updates every evening. Two root causes, both confirmed:

1. **Wrong data source.** Every OSP job in August is `source='web'` — created
   cloud-side (`api/handlers_order.py` `/order/staff-create` and friends) and
   never written to the store PC's SQLite. `collect_daily_summary()`
   (`supabase_sync.py:272`) reads **SQLite only**, so it correctly reports zero
   for a store whose jobs all live in the cloud.
2. **Wrong status literal.** The same query counts `status='Completed'`.
   Production statuses are `Printed`, `Pending`, `Received`, `Paid`,
   `Cancelled` — **`Completed` never occurs**. The `completed` column is
   structurally always 0.

This is a textbook violation of `docs/FAIL_LOUD.md`: the sync heartbeat reports
healthy, the table fills with rows, and every row is zero. A quiet day and a
dead pipeline look identical. Note the irony — **the proposal's own Phase 0
cannot be executed until this is fixed**, because there is nothing trustworthy
to baseline against.

### 2.3 Two of three stores are not really on the system

- **PRINTK (Nattika):** 838 Epson pages in the window, **zero** rows in `jobs`,
  ever. Known as S9-9. Prints straight from Windows.
- **PRIOFF:** 130 August "jobs" from **one** sender, 0 impressions, ₹0. Machine
  noise, not customers.
- **OSP:** the only store with real Printosky traffic, at 1.6% capture.

Phase 12 ("Multi-Branch Readiness") proposes preparing for expansion. We already
run three store IDs; two of them do not feed the system. The readiness problem
is adoption, not schema.

### 2.4 Nine of twelve phases already exist

| Phase | Status in repo |
|---|---|
| 1. PDF preflight | **Partial** — `pdf_scanner.py` (colour only) |
| 2. Printer routing | **Shipped** — colour→Epson / B&W→Konica, 5 call sites |
| 3. Inventory | **Partial** — toner/ink live (S9-3); paper & binding absent |
| 4. Customer portal | **Shipped** — `website/track.html`, `pickup_code.py`, same stages |
| 5. WhatsApp Business API | **Shipped April** — Meta Cloud API, 4,076 billed messages |
| 6. Auto-print after payment | **Shipped** — `store_puller.auto_print()`, e2e tests |
| 7. Smart queue | **Table only** — `pb_operator_queue`, 0 rows |
| 8. Direct UPI | **Redundant** — Razorpay already settles UPI |
| 9. Cloud-first | **Shipped** — Supabase + Vercel + leases (S9-11) |
| 10. OCR | **Shipped/active** — `manuscript_transcripts`, confidence scoring |
| 11. AI academic | **Live** — `academic_pipeline_worker.py`, 38 orders |
| 12. Multi-branch | **Shipped** — 3 store IDs, `store_config.py`, `docs/MULTI_BOX.md` |

Phase 5 is the proposal's flagship Month-3 item and it went live in April. The
truly new surface is preflight depth, paper inventory and priority levels —
roughly two weeks, not four months.

### 2.5 One phase contradicts a decision we already made

Phase 6 proposes removing manual intervention after payment, listing "requires
strong validation controls" as a one-line drawback. We already chose the other
way deliberately: S10-6 made book-order payment **owner-verified** specifically
to block screenshot fraud. Reopening that needs an argument about fraud
exposure, not a bullet point.

---

## 3. The counter-roadmap

Three stages, gated. **Do not start a stage before its exit criterion is met.**

### Stage 1 — Make the system tell the truth (Week 1)

Nothing here is a feature. It is the instrumentation the original Phase 0 asked
for, made actually possible.

| # | Work | Why |
|---|---|---|
| 1.1 | Fix `collect_daily_summary()` — read the same store the jobs live in (cloud for web-sourced), drop the `'Completed'` literal | It has reported zero for 38 days |
| 1.2 | Add an `ops_watchdog` check: meter delta ≫ recorded impressions → alert | The capture gap should have alerted itself months ago; per `docs/FAIL_LOUD.md` this is exactly the "green dot over a dead pipeline" case |
| 1.3 | Start writing `job_events` (received / quoted / paid / printed / delivered, with timestamps) | 0 rows today; without it, turnaround and reprint metrics are unmeasurable and every % target is unfalsifiable |
| 1.4 | Daily capture-rate line in MIS: meter pages vs. recorded pages, per store | One number that says whether any of this is working |

**Exit criterion:** a dashboard line showing capture rate per store, and an alert
that fires when it drops.

### Stage 2 — Close the capture gap (Weeks 2–4)

This is the whole ball game. 41,000 pages a fortnight walk past the system.

| # | Work | Why |
|---|---|---|
| 2.1 | **Floor study, not code.** Sit at the OSP counter. Where do those 39,565 Konica pages come from — walk-in photocopy, USB sticks, staff printing from Windows? | Decides everything downstream. Cannot be answered from the repo |
| 2.2 | Classify the volume: *should-be-captured* vs. *legitimately outside scope* (e.g. pure walk-in xerox) | Sets the honest denominator. If most of it is xerox, the workload targets must be rebased on the slice Printosky owns — the original proposal never defines that slice |
| 2.3 | Make the fast path the recorded path — whatever the counter does today must be quicker through Printosky than around it | Staff bypass systems that cost them time. `/order/staff-create` exists; the question is whether anyone can use it in under 10 seconds |
| 2.4 | Decide S9-9 (Nattika): counter on the console, or PRINTK is printer-metering only and stops pretending to be a store | It has been ambiguous since 4 Aug |

**Exit criterion:** capture rate above an agreed floor (suggest 60% of
in-scope volume) sustained for two weeks.

### Stage 3 — Then, and only then, optimize (Month 2+)

With a real denominator, the original proposal's good ideas become worth
building — and measurable:

| # | Work | From |
|---|---|---|
| 3.1 | Preflight: encryption, blank pages, page-size mismatch, low resolution — extend `pdf_scanner.py`, surface at quote time | Original Phase 1 |
| 3.2 | Routing v2: add the volume dimension — bulk→Konica, small/photo→Epson; replace the 5 hardcoded call sites with one function | Original Phase 2 |
| 3.3 | Paper / spiral / binding stock with auto-deduction on completion and low-stock push | Original Phase 3 + S5 |
| 3.4 | Priority levels (express / urgent / normal) once `pb_operator_queue` has traffic | Original Phase 7 |

**Measured against Stage 1's baseline**, with a stated target per item and a
before/after read. An improvement we cannot measure does not ship.

---

## 4. Not doing, with reasons

| Item | Reason |
|---|---|
| Phase 5 — WhatsApp API migration | Live since April (S10-1) |
| Phase 8 — Direct UPI | Razorpay already handles UPI; adds reconciliation load for marginal gain |
| Phase 9 — Cloud-first refactor | Already the architecture. And **S11-1 first**: the bot still dies when the store PC sleeps. More cloud on top of that is backwards |
| Phase 12 — Multi-branch schema | Shipped. The real blocker is S11-4 (hardware) and adoption |
| Phase 6 — Auto-print after payment | Mechanism exists; the gate is deliberate (S10-6 fraud control). Needs a fraud argument, not a schedule slot |

---

## 5. Backlog items the proposal never mentions

These cost more today than anything in the twelve phases:

- **S11-1** — WhatsApp bot offline whenever the PC sleeps. Every missed message
  is a lost order, and no phase addresses it.
- **S9-8** — 82 remaining `except Exception: pass` sites. The capture gap and
  the `daily_summary` bug are both this failure mode.
- **S9-9** — Nattika off-system entirely.
- **S11-4** — Binding store blocked on hardware.

---

## 6. On the numbers in the original

The Expected Results table (50–60% workload, 70–80% error reduction, 20–30%
payment speed) has no source. Nothing in the repo tracks reprints, failed prints
or turnaround — `job_events` is empty — so the figures could be neither derived
beforehand nor verified afterwards.

This counter-proposal deliberately publishes **no** percentage targets until
Stage 1 produces a baseline. The one number offered is the capture rate, because
it is the only one measurable today.

---

## 7. Summary

The original proposal is a good roadmap for a business whose jobs flow through
its software. Printosky's do not yet. Fix the measurement layer, find out where
41,000 pages a fortnight are going, get them onto the system — then spend the
optimization effort, which will be worth roughly sixty times more per unit of
work than it is today.

Keep from the original: measure-first, honest drawbacks, volume-aware routing,
deeper preflight, paper inventory. Change: the order.

---

## 8. Appendix — evidence queries

```sql
-- Capture gap (run 2026-08-18)
select store_id, printer, max(total_pages) - min(total_pages) as pages
from printer_counters
where polled_at > (now() - interval '30 days')::text
group by 1, 2;
-- OSP: konica 39565, epson 1453 | PRINTK: epson 838

select store_id, count(*) jobs,
       sum(coalesce(page_count,0) * greatest(coalesce(copies,1),1)) as impressions,
       sum(coalesce(amount_quoted,0))::int  quoted,
       sum(coalesce(amount_collected,0))::int collected
from jobs where received_at >= '2026-08-04' group by 1;
-- OSP: 104 jobs, 636 impressions, ₹1135 quoted, ₹647 collected
-- PRIOFF: 130 jobs, 0 impressions, ₹0

-- Broken reporting
select store_id, count(*) days, sum(total_jobs) jobs, sum(revenue) revenue
from daily_summary where date > (now() - interval '60 days')::text group by 1;
-- OSP: 38 days, 0 jobs, ₹0

-- Empty instrumentation
select count(*) from job_events;          -- 0
select count(*) from pb_operator_queue;   -- 0
```

Code references: `supabase_sync.py:272` (`collect_daily_summary`),
`print_server.py:950` + `store_puller.py:173` (routing), `pdf_scanner.py:121`
(`scan_pdf`), `api/handlers_order.py:296` (walk-in creation).
