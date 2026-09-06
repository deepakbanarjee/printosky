# Printosky — Context Handoff

_A self-contained briefing for an engineer or agent working in a **different repo** who needs
to understand what Printosky is, what problem it solves, and how the machinery fits together.
Written 2026-09-06 against `main` @ `954d6a4`. Nothing here requires access to the printosky
repo to read._

---

## 1. The problem

**Oxygen Students Paradise (OSP)** is a print + stationery shop in Thrissur, Kerala. Its
customers are overwhelmingly college students: assignments, lab records, seminar reports,
final-year project theses. The shop also serves walk-ins and a handful of small businesses.

Before Printosky, the shop ran the way every Indian print shop runs:

| Step | How it worked | What broke |
|---|---|---|
| Customer sends file | WhatsApp message to the shop's number | Files buried in chat, lost, re-sent |
| Quote | Staff eyeballs page count, quotes from memory | Inconsistent pricing; colour pages billed as B&W; margin leaks |
| Payment | Cash / "pay when you collect" | Unpaid pickups, no reconciliation, no record |
| Print | Staff opens the file, picks a printer, prints | Wrong printer, wrong colour mode, wrong collation |
| Binding | Spiral/soft/project — some in-house, some at a vendor | No tracking once it leaves the shop |
| Collection | "Is my thing ready?" phoned in all day | Staff interrupted constantly |
| Books | Nothing | No revenue per day, per staff, per machine; no idea which jobs lose money |

The shop was **one WhatsApp inbox pretending to be an order-management system**. Printosky
replaces that inbox with a real pipeline while keeping WhatsApp as the only interface the
customer ever sees — because in this market, asking a student to install an app or visit a
portal loses the order.

### The second problem (adjacent, higher-margin)

Final-year students must submit project reports formatted to *exact* university specifications:
margins in centimetres, Times New Roman 12pt, 1.5 line spacing, a certificate page with the
university's precise boilerplate, roman-numeral front matter, chapter-wise figure numbering.
Every university differs. Students get their submissions rejected over margins.

Historically the shop did this manually — a staff member retyping/reformatting in Word for
₹500-1500 per project, taking hours. That's the **Project Builder / academic** product line:
automate university-spec formatting and sell it as a self-serve digital product at ₹49-149,
with a human operator queue as the fallback when automation can't produce a publishable result.

### The business thesis

1. Fix one shop until it's bulletproof and profitable. *(current phase)*
2. Turn the store software into an agent thin enough that a franchise shop installs it in 30 minutes.
3. Sell it as SaaS to 20-50 shops (₹1,500-3,000/mo).
4. Become a print marketplace: customer orders, platform routes to the nearest capable shop,
   platform takes a cut (Razorpay Route, ~10%).

Phases A→B→C are documented in `docs/EXPANSION_PLAN.md`. Multi-store plumbing (partner
registry, routing engine, pickup codes, take-rate columns) is **already built and merged**,
but only `store_id='OSP'` is live.

---

## 2. What exists today — the two product lines

### Line 1: Print job pipeline (the shop's core business)

```
WhatsApp file  →  bot asks 6 questions  →  quote  →  Razorpay link
      →  customer pays  →  webhook flips job to Paid
      →  staff prints from admin panel  →  binding  →  Ready
      →  WhatsApp "ready for collection"  →  collected  →  review request
```

### Line 2: Project Builder / academic (digital, self-serve, no printing required)

```
Student uploads .docx  →  free chapter detection (structure preview)
      →  picks university + tier  →  Razorpay order  →  pays
      →  docx_engine reformats to that university's spec  →  Supabase Storage
      →  download link + WhatsApp delivery
      →  (if automation can't parse the structure → operator queue → human finishes it)
```

Tiers: `format_fix` (Standard, ₹49 — reformat what the student wrote, in place, preserving
images/tables) and `generate` (Premium, ₹99 under ~50 pages / ₹149 over — build the report from
a structured form). A free blank university template is a lead magnet.

There is also a legacy, staff-mediated academic order flow (`academic_orders`, `PROJ-YYYY-NNN`,
advance + balance payments, phase-1/phase-2 docx) owned by a **sibling repo, `osp-academics`**.
Project Builder is the self-serve successor to it; both currently coexist.

---

## 3. Engineering — the shape of the system

Three runtimes. The design constraint that explains everything: **the printers are physical
machines on a LAN in a shop**, so *something* must run on-premises, but everything that can
live in the cloud has been migrated there because the store PC gets switched off.

```
┌─────────────────────────── CLOUD ────────────────────────────┐
│                                                              │
│  Vercel — api/index.py (single Python BaseHTTPRequestHandler) │
│    · Meta WhatsApp Cloud API webhook (the bot brain)         │
│    · Razorpay webhooks (print + academic)                    │
│    · Staff PIN auth, admin endpoints, operator queue         │
│    · Project Builder: analyse / order / process / retrieve   │
│    · Public order tracker (/api/track/<pickup-code>)         │
│                                                              │
│  Supabase — Postgres (28 tables) + Storage + Auth            │
│    · System of record. Storage buckets hold uploaded files   │
│      and generated DOCX outputs.                             │
│                                                              │
│  Netlify — website/*.html (static)                           │
│    · admin.html (staff job console), mis.html, superadmin,   │
│      project-builder.html, track.html, operator-mode.html    │
│    · netlify/functions/auth.js does server-side password     │
│      verification and mints a Supabase JWT                   │
└──────────────────────────────────────────────────────────────┘
                              ▲
                              │ HTTPS / Supabase client
                              ▼
┌──────────────────── STORE PC (Windows) ──────────────────────┐
│  watcher.py        — watchdog on C:\Printosky\Jobs\Incoming\ │
│                      + spawns sync / poller / timeout threads│
│                      + an operator REPL (pending/report/done)│
│  print_server.py   :3005 — staff auth + drives SumatraPDF    │
│  whatsapp_capture/ :3001/:3004 — legacy WhatsApp Web client  │
│  dashboard.py      :5000 HTTP / :5001 WS — live job board    │
│  printer_poller.py — SNMP + web-scrape of the two printers   │
│                                                              │
│  LAN: Konica (B&W, 192.168.55.110) · Epson WF-C21000         │
│       (colour, 192.168.55.202)                               │
│  SQLite at C:\Printosky\Data\jobs.db — offline cache/mirror  │
└──────────────────────────────────────────────────────────────┘
```

**Deploy:** push to `main` → Vercel auto-deploys the API. Netlify auto-deploys the static site.
The store PC is updated by `PULL_UPDATE.bat` (a `git pull` + restart). CI is a single GitHub
Actions workflow running pytest on every push (`.github/workflows/test.yml`); Playwright browser
tests are excluded from it.

**Direction of travel:** the store PC is being hollowed out. The target is `agent.py` (~150 lines:
watch folder → upload to Storage → listen on Supabase Realtime → print) replacing
`watcher.py` + `print_server.py` + `webhook_receiver.py` (~6,000 lines). `webhook_receiver.py`
is already dead — its Razorpay handling moved to Vercel in May 2026.

---

## 4. The print pipeline, in engineering terms

### 4.1 Intake

Two paths converge on the same `jobs` row:

- **WhatsApp.** Meta Cloud API posts to `POST /whatsapp-webhook` on Vercel (HMAC-verified with
  `META_APP_SECRET`). Media is downloaded from Meta with a system-user token, pushed to Supabase
  Storage, and a `jobs` row is created. Every inbound/outbound message is written to
  `conversation_log` — the admin panel has a full chat view per customer.
- **Walk-in.** Staff drop a file into the hot folder; `watcher.py`'s watchdog observer picks it
  up and creates the job locally, tagging `file_source` (`USB` / `Email` / `Drive` / `Hot folder`).

Job IDs are `OSP-YYYYMMDD-NNNN`, generated under a `threading.Lock` (there was a real race here).

### 4.2 The bot: a 6-step state machine

State lives in `bot_sessions`, keyed by phone. Steps:

```
size → colour → layout → [multiup_per → multiup_sided] → copies → finishing → delivery
```

with a `prev_step` column powering a back button, and `customer_profiles` pre-filling the
answers from the customer's last order. Multiple files sent within a 30-60s window are collected
into a `job_batches` row and quoted as one payment link. Group / newsletter / broadcast messages
are filtered out. Typing `help` sets `bot_sessions.needs_human` and alerts staff — the escape
hatch that keeps a stuck state machine from losing an order.

### 4.3 Pricing (`rate_card.py`)

Pure, unit-tested, no I/O. The key idea is **sheets, not pages**:

```
pages → calc_sheets(pages, sides, layout)   # double-sided halves it, 2-up halves it again
      → per-sheet rate from a slab table (rate drops as volume rises)
      → × copies
      → + finishing (spiral by sheet-count tier, soft/thermal binding by tier,
                     lamination flat, project/record binding fixed)
      → + DTP / scanning / graphs / editing-minutes line items
      → + delivery (₹30) + urgent surcharge (₹20, soft & project binding only)
```

Finishing splits into `FINISHING_INHOUSE` (staple, spiral, wiro, lam_sheet, id_card) and
`FINISHING_OUTSOURCED` (lam_roll, lam_cover, project, record, thermal) — the outsourced set
triggers a vendor dispatch step and a staff-mediated quote. A `rate_card` table in Supabase
mirrors the Python constants and can override them at boot; the two are kept in sync **manually**
(a known wart).

### 4.4 Colour detection

`colour_detector.py` opens the PDF with PyMuPDF and classifies each page as colour or B&W,
producing `jobs.colour_page_map` (JSON). This matters commercially: colour pages cost several
times more, and a 200-page thesis with 6 colour figures billed entirely at colour rates (or
entirely at B&W rates) is the difference between profit and loss on the job. Staff confirm or
override the detection before printing (`colour_confirmed`).

Mixed jobs may be **split into sub-jobs** (`parent_job_id`, `is_sub_job`, `sub_job_type`):
B&W pages to the Konica, colour to the Epson. Only the Epson preserves page sequence reliably,
so a split job raises `collation_warning` and the admin UI warns staff to collate by hand.

### 4.5 Payment

`razorpay_integration.py` creates a payment link (or a Razorpay *order* for Project Builder's
inline checkout). Razorpay calls `POST /webhook/razorpay` on Vercel; the handler verifies the
HMAC signature with `RAZORPAY_WEBHOOK_SECRET`, dedupes on `processed_webhooks.event_id`
(webhooks retry — idempotency is mandatory), flips the job to `Paid`, and notifies staff.
Cash/UPI at the counter is recorded by staff instead, with `payment_mode`.

### 4.6 Print

`print_server.py` (port 3005, LAN-only) authenticates the staff member (PBKDF2, 260k iterations,
per-user 16-byte salt, `hmac.compare_digest`, 5-attempts-per-IP-per-60s rate limit), resolves the
target printer, and shells out to **SumatraPDF** with the right flags. Job status is pushed
straight to Supabase rather than waiting for the 5-minute sync.

`printer_poller.py` polls both machines for page counters and toner/ink levels — SNMP for the
Epson, Konica's XML web endpoint with an SNMP fallback — writing `printer_counters`,
`printer_supplies`, and `supply_changes` (detected by a level jumping *up*). Threshold crossings
fire ink/toner alerts to staff WhatsApp. `konica_jobs` / `epson_jobs` hold the raw per-job logs
scraped from the machines, used to reconcile "what we billed" against "what the printer
actually produced".

### 4.7 Fulfilment and collection

Status ladder: `Pending → Paid → Printed → Ready → Delivered`, with every transition appended to
`job_events` (staff id, from/to, duration). `work_sessions` tracks per-job hands-on time
(start/pause/resume/end). When staff mark Ready, `whatsapp_notify.py` sends the collection
message with a **pickup code** (`P-XXXX`, `secrets.choice` over a 30-char ambiguity-free
alphabet — no `0 1 I L O Q` — uniqueness-checked at claim time). The public tracker at
`/api/track/<code>` deliberately does **not** reveal the store name/address until status is
`Ready`, so a code alone can't be used to shop-surf.

30 minutes after collection the bot requests a rating. 4-5★ → Google Maps review link + a 10%
discount code (`discount_codes`) + a referral invite. 1-3★ → "what went wrong?" routed to staff.
B2B customers and jobs with no phone are excluded.

### 4.8 Referrals

A `ref_CODE` in a greeting message is captured onto `bot_sessions.referral_code` and credited
(₹20 default, `referrers.credit_amount`) on payment into `referral_credits`. Customers reply
`MY CREDITS` / `BALANCE`; staff redeem via `POST /referrals/redeem`, which is idempotent and
does a race-safe atomic partial-redemption walk over the oldest unredeemed credits. Framed as
**store credit, never cash**. This is the only foreign key in the entire database.

### 4.9 Multi-store (built, not switched on)

`partners` (hub/spoke registry, capabilities JSON, capacity/day, geo, take-rate %, Razorpay
Route sub-merchant id) + `routing/engine.py` + `store_dispatch.py`.

The routing engine is deliberately dumb-by-design: filter to stores that are KYC-active, capable
of the spec, and open right now; score
`capacity_remaining*w1 − queue_depth*w2 − distance_km*w3`; highest wins; ties break to whoever
fulfilled fewest jobs in 24h. Every decision is written to `routing_decisions` with the full
score breakdown — a fairness audit trail and future ML training set. It takes queue depth and
capacity as *inputs* so it stays unit-testable, and it does not send the message.

Dispatch is over WhatsApp, not an API: the partner shop owner gets a message and replies
`ACCEPT` / `REJECT` / `READY` / `DELIVERED` / `QUERY` in free text, which `parse_store_reply`
turns into a state transition. No software install at the partner shop — that's the whole point
for a 1-3 store MVP. A 60-second ack timeout re-routes and increments `reroute_count`.

---

## 5. Project Builder, in engineering terms

`docx_engine.py` (~110KB, the single largest module) plus `university_configs/*.json`
(Calicut, MG, CUSAT, KTU, IGNOU).

A university config is a **declarative formatting spec**: margins in cm, body font/size, per-level
heading rules, line spacing, front-matter order, roman vs arabic front-matter numbering,
figure/table caption position and numbering scheme, reference style, cover colours, and the exact
certificate/declaration boilerplate with `[TITLE]` / `[GUIDE_NAME]` placeholders. Adding a
university is a JSON file, not code.

Three product surfaces:

| Function | Product | Payment |
|---|---|---|
| `generate_free_template()` | blank .docx skeleton for a university | free (lead magnet) |
| `format_fix()` | reformat a student's own .docx **in place** — preserving images, tables, structure | ₹49 |
| `generate_from_form()` | build the whole report from structured form data | ₹99 / ₹149 |

**Structure detection is the hard part** and uses a 3-pass cascade: (1) real Word heading styles,
(2) heuristics on text shape, (3) Claude for metadata/ambiguous cases. When all three fail,
`docx_engine` raises `StructureDetectionError` rather than returning something plausible-looking.
That exception is load-bearing — the API layer must catch it and either upsell (pre-payment) or
push the order into the **operator queue** (post-payment). The docstring records why: silently
swallowing it once shipped a document literally titled "FORMATTED DOCUMENT" to a paying customer.

**Back-pressure:** the operator queue has a capacity threshold (`PB_OPERATOR_CAPACITY`, default
10). Above it, `GET /project-builder/availability` reports Premium as paused and
`POST /project-builder/create-order` returns 503 with an upsell to Standard — *before* the
customer pays. Failure to query the queue is non-blocking (`paused=False`), so a DB hiccup never
freezes paid conversions. PDF upload is refused outright with instructions to save as .docx;
accepting PDFs produced unusable results.

Free preview is generated and stored under a UUID path before payment, so the customer sees the
real output before being charged.

---

## 6. Data model — what another repo needs to know

Supabase project `mlhuwlnwwwxdnqafelko`. 28 tables, 2 views, and — this is not a typo — **one
foreign key** in the entire database (`referral_credits.referrer_code → referrers.code`).
The schema is heavily denormalised; referential integrity is enforced in application code or not
at all. `docs/SCHEMA.md` in the printosky repo is the canonical column-by-column reference and
is generated from live `information_schema`.

### Ownership model (this is the cross-repo contract)

| Owner | Tables | Rule |
|---|---|---|
| 🟦 printosky | 24 — `jobs`, `job_batches`, `job_events`, `job_reviews`, `bot_sessions`, `conversation_log`, `whatsapp_contacts`, `customer_profiles`, `konica_jobs`, `epson_jobs`, `printer_counters`, `printer_supplies`, `supply_changes`, `staff`, `staff_sessions`, `work_sessions`, `rate_card`, `daily_summary`, `routing_decisions`, `partners`, `processed_webhooks`, `discount_codes`, `referrers`, `referral_credits`, `b2b_*` | printosky owns the migrations |
| 🟪 osp-academics | `academic_orders` | sibling repo owns it; its `academic_pipeline_worker.py` reads/writes it |
| 🟧 shared | `project_builder_orders` (+ `b2b` grey area) | **both** products write — changes need coordination in both repos |

**If you are working in another repo against this database:**

1. Check the owner before reading a column. Ownership encodes who is free to break it.
2. Schema changes land as `printosky/api/migrations/SCHEMA_vNN_*.sql` **and** a `docs/SCHEMA.md`
   update in the same PR — even when the change is driven by the other repo.
3. Apply the migration to live Supabase *before* merging the code that depends on it. Both repos
   read the schema at module load, so the column must exist first.
4. `scripts/check_schema.py` catches doc-vs-live drift.

### Conventions that will bite you

- **Timestamps are inconsistent.** Newer columns are `timestamptz`; older ones are `text` holding
  ISO-8601 strings, a fossil from when these tables were SQLite. Use `timestamptz` for new columns;
  don't rewrite the old ones casually.
- **Booleans-as-int.** Older tables use `0`/`1` integer columns (`jobs.colour_confirmed`,
  `is_sub_job`, `collation_warning`, `discount_codes.used`). New columns must use real `boolean`.
- **Printer name casing differs by table.** `printer_counters` / `printer_supplies` /
  `supply_changes` use lowercase (`konica`/`epson`); `jobs.printer` uses TitleCase
  (`Konica`/`Epson`). `mis.html` filters on the lowercase form. Do not unify without auditing
  every read site.
- **`store_id` defaults to `'OSP'`** everywhere. It's the multi-tenant partition key.
- **US vs British spelling is inconsistent on purpose**: `konica_jobs.color_pages` matches
  Konica's own XML; the rest of the codebase uses `colour`.
- **No cascades.** Deleting from `jobs`, `staff`, or `partners` orphans children silently.

---

## 7. Security posture

Done: PBKDF2+salt PIN hashing with zero-downtime legacy upgrade (NULL salt = old SHA-256, upgraded
on next login); `hmac.compare_digest` on every credential comparison; login rate limiting;
`os.path.basename()` on all incoming filenames (path traversal); webhook HMAC verification that
**fails closed** when a secret is unset (an empty `META_APP_SECRET` logs an error and rejects,
rather than silently accepting everything); webhook idempotency; staff auth on academic order
creation; RLS enabled across the DB including the three tables that were missed.

Known open items (in `docs/SECURITY.md`): `STORE_TOKEN` lives in `localStorage` and Netlify has no
CSP; the store PC holds a Supabase `service_role` key that bypasses all RLS if `.env` leaks;
academic project IDs are sequential and therefore enumerable; the Epson's web panel was on
`admin/admin` on the store LAN.

Secrets are env vars on Vercel and Netlify. **Store-PC config is still partly hardcoded**
(folder paths, printer IPs) — a known migration debt.

---

## 8. Current state and where the work is

**Live and working:** WhatsApp bot end-to-end on Meta Cloud API, pricing engine, Razorpay
payments both directions, printing to both machines, staff auth + sessions, admin panel, printer
telemetry + ink alerts, referrals (smoke-tested end-to-end), Project Builder self-serve across
all-India universities, operator queue with back-pressure.

**Built but not switched on:** everything multi-store — `partners`, routing engine, pickup codes,
take-rate and Razorpay Route columns. Only `OSP` runs.

**The big in-flight refactor:** collapse the store PC to `agent.py`. Until that lands, the shop's
bot goes offline when the PC is off — which is the single most-cited operational pain and the
reason the Vercel migration happened at all.

**Deliberately killed** (code preserved under `retired/2026-05-12-graveyard/` with a documented
revival path): Konica job attribution (0/4507 rows ever attributed), the B2B bot (zero rows in
production, no owner), the receipt printer (hardware never bought — the stub returned "not
configured" on every call). The project has an explicit habit of retiring features that don't
earn their keep rather than carrying them.

**Operating rules the team holds itself to:** no feature ships without a stated success metric;
nothing gets pre-built for a later phase; customer-facing copy stays in brand voice. Backlog and
priorities live in `SPRINT_BACKLOG.md` and `docs/FEATURE_PIPELINE.md`.

---

## 9. Design decisions worth inheriting

These are the non-obvious calls, with their reasoning — the things most likely to be
accidentally "fixed" by someone who doesn't know why they're that way.

1. **WhatsApp is the entire customer interface.** No app, no login, no portal. Every added step
   loses orders in this market. The web pages exist for *staff*, and for Project Builder's
   file-upload flow where a browser is unavoidable.
2. **The routing engine is intentionally stupid.** Explicit instruction in its docstring: don't
   build v2 (ML on historical SLA, dynamic take-rate, surge) until v1 has run 200+ jobs across
   2+ stores.
3. **Partner shops integrate over WhatsApp text replies, not an API.** Zero install at the partner.
4. **Fail closed on every webhook secret**, and dedupe every webhook by event id.
5. **Never return a plausible-looking bad document.** `StructureDetectionError` exists so failure
   routes to a human instead of shipping garbage to a paying customer.
6. **Back-pressure before payment, not after.** Refuse the order while the customer can still
   choose a cheaper tier; never take money for something that can't be delivered in SLA.
7. **Log the decision, not just the outcome** — `job_events`, `routing_decisions.scores_json`,
   `conversation_log`. Almost every disputed situation in a print shop is resolved by replaying
   what was said and what was chosen.
8. **Sheets, not pages, are the billing unit.** Every pricing bug so far has come from conflating
   the two.
9. **Retire features that don't earn.** Code to `retired/` with a revival note; don't carry
   dead weight.

---

## 10. Glossary

| Term | Meaning |
|---|---|
| OSP | Oxygen Students Paradise — the physical shop; also the default `store_id` |
| Job | One print order. `OSP-YYYYMMDD-NNNN` |
| Batch | Multiple files from one customer sharing a single payment link |
| Sub-job | A split of a mixed-colour job — B&W half to Konica, colour half to Epson |
| Sheets | Physical pieces of paper (pages ÷ sides ÷ n-up). The billing unit |
| Finishing | Binding/lamination. In-house vs outsourced-to-vendor |
| Pickup code | `P-XXXX` shown at the counter to claim a job |
| Format-fix | Reformat the student's own document to a university spec (Standard tier) |
| Operator queue | Human fallback when Project Builder automation can't produce a publishable result |
| Hub / Spoke | Partner store roles in the multi-store model |
| Take rate | Platform's % of a job fulfilled by a partner store (default 10%) |

---

## 11. Where to look in the printosky repo

| Question | File |
|---|---|
| Runtime processes, ports, routes, env vars | `docs/ARCHITECTURE.md` |
| Every table and column, with owners | `docs/SCHEMA.md` |
| Security posture and open items | `docs/SECURITY.md` |
| Full target-state customer journey (incl. covers, vendors) | `docs/MASTER_PLAN.md` |
| Phase A/B/C business plan | `docs/EXPANSION_PLAN.md` |
| Prioritised feature backlog | `docs/FEATURE_PIPELINE.md`, `SPRINT_BACKLOG.md` |
| All cloud API handlers | `api/index.py` (~3,100 lines, one handler class) |
| Pricing | `rate_card.py` (pure, well-tested) |
| Bot state machine | `whatsapp_bot.py` |
| Document formatting | `docx_engine.py` + `university_configs/*.json` |
| Multi-store | `routing/engine.py`, `store_dispatch.py`, `pickup_code.py` |
| Tests | `tests/` (~35 files, pytest, run in CI on every push) |
