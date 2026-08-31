# Printosky Sprint Backlog
Last updated: 2026-06-02 — Xtraa book-order flow, new-customer welcome, admin conversations fix all shipped (S10-6/7/8)

---

## 🔐 SPRINT SEC — Security Hardening

| # | Task | Details |
|---|------|---------|
| SEC-1 | ~~**Remove staff PINs from SPRINT_BACKLOG.md**~~ ✅ | PIN values removed from file; staff can look up via `python staff_setup.py list` |
| SEC-2 | ~~**Move Supabase credentials to env vars**~~ ✅ | Moved to `.env`; `supabase_sync.py` now uses `load_dotenv` + `os.environ` |
| SEC-3 | ~~**Admin password hash exposed in Netlify JS**~~ ✅ | All password hashes moved to Netlify env vars; `netlify/functions/auth.js` verifies server-side. Covers admin (PBKDF2), superadmin, store, and MIS |
| SEC-4 | ~~**Supabase anon key in admin.html**~~ ✅ | All `sbFetch` calls now use Supabase JWT from sessionStorage; `SCHEMA_v5_migration.sql` tightens RLS to `auth.role() = 'authenticated'`; `supabase_sync.py` uses `SUPABASE_SERVICE_KEY` |
| SEC-5 | ~~**Sequential staff PINs**~~ ✅ | Reset all staff PINs to random non-sequential values on store PC (2026-04-09) |

---

## 🔴 CRITICAL / BLOCKERS

| # | Task | Details |
|---|------|---------|
| C1 | ~~**Run SCHEMA_v3 in Supabase**~~ ✅ | Done |
| C2 | ~~**OXYGEN PC server URL**~~ ✅ | Fixed: `192.168.55.212:3005` |

---

## 🟠 SPRINT 7 — Admin Panel & Quoting

| # | Task | Details |
|---|------|---------|
| S7-1 | ~~**Quote endpoint: `colour=col` param**~~ ✅ | Fixed in print_server.py handle_quote(); `colour=col/colour/color` → `paper_type=A4_col`. Tests in test_quote_endpoint.py |
| S7-2 | **Print panel: deploy & test** | New floating panel inserted under job row — deploy to Netlify + test on all PCs |
| S7-3 | ~~**Print panel: item specs loaded from DB**~~ ✅ | Fixed: handle_update_job derived paper_type from colour when not sent by frontend; was always defaulting to A4_BW so colour quotes were billed at B&W rates. 6 tests in test_update_job.py. |
| S7-4 | **Outsourced vendor workflow** | When finishing=project/record/lam_roll, show vendor selection; send job to vendor via WhatsApp |
| S7-5 | **Thermal binding** listed in admin but rate not tested | Test `finishing=thermal` in quote endpoint |

---

## 🟡 SPRINT 8 — Staff & MIS

| # | Task | Details |
|---|------|---------|
| S8-1 | **Tell staff their PINs** | Run `python staff_setup.py list` to view current PINs — do not commit PIN values to this file |
| S8-2 | **MIS dashboard — live test** | mis.html built but never live-tested. Verify staff sessions syncing to Supabase |
| S8-3 | **Staff session Supabase sync** | `supabase_sync.py` syncs staff_sessions — verify after SCHEMA_v3 applied |
| S8-4 | ~~**Konica job attribution**~~ 🗑️ | RETIRED 2026-05-12 — 0/4507 attribution rate; see `retired/2026-05-12-graveyard/konica_attribution.py` for code + revival path. |
| S8-5 | **Idle logout timer** | session_timeout.py — verify it logs out idle staff correctly |

---

## 🟡 SPRINT 9 — Printer & Hardware

| # | Task | Details |
|---|------|---------|
| S9-1 | ~~**Konica supply levels**~~ ✅ | `parse_konica_xml_supplies()` + `poll_konica_xml_supplies()` in printer_poller.py. Parses TnrBlkRmng/DrmBlkRmng tags (and alternates); poll_once() tries XML first, falls back to SNMP. 14 tests in test_konica_supplies.py. |
| S9-2 | ~~**Konica job export URL**~~ ✅ | Jobs loading correctly — confirmed 2026-04-20 |
| S9-3 | ~~**Epson ink alerts**~~ ✅ | `_send_ink_alerts()` in printer_poller.py:434. Fires on threshold crossing (EMPTY at 0%, LOW at ≤10%). Called in poll_once() for both printers. |
| S9-4 | **A3 printing** | Test A3 job end-to-end (bot → quote → print) |
| S9-7 | ~~**Fail-loud alerting**~~ ✅ | `ops_watchdog.py` + checks across poller/fetcher/sync/print server, multi-store cloud cron, console health banner. Built after Nattika's printer pipeline died 11–18 Aug 2026 behind six layers of silence. Rule: [docs/FAIL_LOUD.md](docs/FAIL_LOUD.md) |
| S9-9 | **Nattika records no jobs** ⏳ | `PRINTK` has **zero** rows in `jobs`, ever, and `daily_summary` reports 0 jobs every day since 4 Aug; no staff has ever logged in as PRINTK. Meanwhile its Epson's own weblog shows real printing (388 jobs to 14 Aug, Windows user `Oxygen`). So Nattika prints straight from Windows and the counter never touches Printosky — no hot-folder drops, no walk-in entries, no quotes, no revenue. Decide: put the Nattika counter on the console, or accept PRINTK as printer-metering only and stop showing it a jobs console. |
| S9-11 | ~~**Multi-box coordination**~~ ✅ | Leases (`store_role_leases`) elect one poller per store; `jobs.print_claimed_at` makes printing exactly-once across boxes; `/local-print` keeps counter jobs off the cloud. Migration `SCHEMA_v28_device_coordination.sql` (applied 2026-08-18). See [docs/MULTI_BOX.md](docs/MULTI_BOX.md). |
| S9-12 | **Retire the duplicate backup tables** ⏳ | `backup_20260818_nattika_epson_jobs` / `_counters` hold the pre-dedup snapshot (811 + 339 rows). Drop them once the consolidated PRINTK history has been eyeballed. |
| S9-10 | ~~**Nattika double-polling**~~ ✅ | Both Nattika PCs polled the same Epson: all 388 PRINTK Epson job rows are also PRIOFF's, and two sets of counters for one printer. Fixed by leases (S9-11), with `poll_printers` as a veto. The 388 historic duplicate rows were merged and removed 2026-08-18; Nattika's printer history is now one series under PRINTK. |
| S9-8 | **Work down the silent handlers** ⏳ | 82 `except Exception: pass` sites remain; `tests/test_fail_loud_rule.py` ratchets the count so it cannot grow. Convert to `ops_watchdog.guard()` when next in each file, then lower that file's budget. |
| S9-5 | ~~**Receipt printer**~~ 🗑️ | RETIRED 2026-05-12 — hardware never purchased, stub returned "not configured" on every call. See `retired/2026-05-12-graveyard/receipt_printer.py` for code + revival path. |
| S9-6 | **Epson per-job mono/colour tracking** ⏳ | Re-scoped to **Epson EM-C8100** (OSP unit, IP `192.168.55.214`, installed 2026-06-29). Prior work: (a) ✅ delta dedup on Supabase; (b) ✅ `source='spec'` rows on Epson dispatch in print_server.py; (c) ✅ delta attribution now matches the EM-C8100 queue name — it looked for "epson"/"wf"/`.202`, none of which appear in `EM-C8100 Series(Network)`, so every delta since the swap was filed unattributed. Remaining: (d) ⏳ store-PC redeploy after new printer install; (e) ⏳ admin-UI spec ↔ weblog reconciliation. Note: binding store gets its own separate EM-C8100 (see S11-4). |

---

## 🟢 SPRINT 10 — Bot & Customer Experience

| # | Task | Details |
|---|------|---------|
| S10-1 | ~~**Meta Cloud API migration**~~ ✅ | Live on 9495706405 via Vercel. App review submitted. Token rotated 2026-04-09. |
| S10-2 | ~~**Bot conversation flow review**~~ ✅ | Full journey tested: file → 6 steps → payment → notification (2026-04-09) |
| S10-3 | ~~**WhatsApp group/channel filter**~~ ✅ | Filters @g.us, @newsletter, @broadcast, isGroupMsg in index.js:165-170. Confirmed in code. |
| S10-4 | ~~**Delivery flow**~~ ✅ | Verified working (2026-04-09) |
| S10-5 | ~~**B2B bot**~~ 🗑️ | RETIRED 2026-05-12 — 0 b2b_clients rows in production, no owner. See `retired/2026-05-12-graveyard/{b2b_bot.py,b2b_manager.py,test_b2b*.py}` for code + revival path. Live Supabase tables `b2b_clients` + `b2b_payments` left in place. |
| S10-6 | ~~**Xtraa book-order flow**~~ ✅ | Live 2026-06-02 (commit c0cbb74). WhatsApp flow: enquiry → catalog (ML ₹200 / HI ₹150 / EN ₹200 / set ₹549 + ₹75 courier) → qty per book → address → phone confirm → summary → branded UPI QR → payment screenshot → owner 1-tap confirm. Separate `book_orders` table (RLS service_role); `book_bot.py` + `book_catalog.py`; admin **Book Orders** tab + `/admin/book-orders` & `/confirm` endpoints. Owner-verified payment (no auto-confirm) to block screenshot fraud. 62 tests. **Open:** confirm real book titles (placeholders) + live WhatsApp smoke-test. |
| S10-7 | ~~**New-customer welcome auto-reply**~~ ✅ | Live 2026-06-02 (commit ae95ba5). A brand-new contact (no prior `conversation_log` rows) whose first message isn't a file/book/help/command gets a welcome + menu (printouts / Xtraa books / staff). `db_cloud.is_new_contact()`; fires once, returning customers unaffected. 2 tests. |
| S10-8 | ~~**Admin conversations 404 fix**~~ ✅ | Fixed 2026-06-02 (commit 6126b6a/1ccd3f1). `/admin/conversations` + `/admin/thread` were 404ing — `vercel.json` only registered `/admin/reset-pin` + `/admin/send`; replaced with `/admin/(.*)` wildcard + `/referrals/(.*)`. Conversations panel now shows visible error messages instead of silent failure. |
| S10-9 | **Admin: place book order on behalf of customer** | Add a "New Book Order" button in the admin Book Orders tab. Form fields: customer name, phone, book qty per title, address, payment mode (cash/UPI/divya-collected), delivery method. Creates a `confirmed` order directly (no WhatsApp flow). Needed for walk-in customers and teacher-forwarded orders (e.g. Divya). |
| S10-10 | **Divya ledger: show own-book deductions** | `divya_ledger` currently only sums `via_divya=true` orders (commission earned). It must also surface `via_divya=false` rows from Divya's own phone (books she took for herself) as a deduction line, so the net settlement figure is accurate. Add `books_taken` and `books_cost` to the ledger response and display it in the admin Divya settlement view. |

---

## 🔵 SPRINT 11 — Infrastructure & Scale

| # | Task | Details |
|---|------|---------|
| S11-1 | **Cloud hosting for WhatsApp bot** | Bot goes offline when PC is off. Options: Hostinger VPS Rs.350/mo, Hetzner CX22 €4/mo, DigitalOcean $6/mo |
| S11-2 | **PM2 for Node process** | Replace manual CMD window start with PM2 for auto-restart on crash |
| S11-3 | **Job Centro DB** | Investigate silent auto-export of Konica job logs from Job Centro local DB |
| S11-4 | **Binding store setup** ⏳ | Binding store opens 2026-07-02. Needs own PC + Cloudflare tunnel + Supabase store_id. **Blocked** until computers are installed on-site. Multi-store architecture already in place (`store_config.json` per PC). Console side is ready: leave `konica_ip` blank and the admin/jobs pages drop every Konica panel, `/status` reports `has_konica: false`, and B&W routes to the Epson. |
| S11-5 | **Netlify OXYGEN team credit** | Monitor plan limit. Upgrade if needed or keep deploying via personal account |

---

## 🟣 SPRINT 12 — Advanced Print Automation (WFManager Port)

| # | Task | Details |
|---|------|---------|
| S12-1 | **Operator Dashboard GUI** | Build a local Tkinter desktop dashboard on the store PC to view incoming jobs, DB status, and control the watcher. |
| S12-2 | **Rule-Based Auto-Print** | Bypass the holding queue and auto-print specific workflows (e.g., B2B drops) directly to the OS spooler using `lpr` or shell commands. |
| S12-3 | **Filename Auto-Pricing** | Match specific module filenames (e.g., "PHYSICS MODULE 1") to fixed-price packages in the DB to skip per-page counting. |
| S12-4 | **Live Hardware Web Scraper** | Integrate `printer_poller.py` with Konica XML scraping to actively read printer meters before/after jobs to verify completion. |

---

## ⚪ SPRINT 13 — Print scaling + post-press services

Plan: [docs/plans/2026-08-30-scaling-and-post-press-services.md](docs/plans/2026-08-30-scaling-and-post-press-services.md)
— full rate tables, design and shipping order. Two rules: **nothing already
working changes**, and **everything is baked into the PDF before it reaches the
printer** (the driver is never asked to scale, only told `noscale` as a guard).

| # | Task | Details |
|---|------|---------|
| S13-0 | ~~**Unpriced finishing keys bill ₹0**~~ ✅ | `calculate_finishing_cost` (`rate_card.py:327`) has no branch for `lam_roll`, `lam_cover` or `id_card`, and `rate_card` has **no key at all for `perfect` or `thesis`** — both offered on the live order page and accepted by `_VALID_FINISHING`. All five quote ₹0 for the finishing. `lam_sheet` also hardcodes the A4 rate, so A3 pouch bills as A4. Verified latent, not bleeding: no such job exists in the cloud DB and the two `perfect` orders were never collected. **Fixed 2026-08-30.** All five priced, `lam_sheet` now by size, spiral A3 tiered, wiro given its own tiers and a 150-sheet refusal, thermal withdrawn, A5/Letter rates added. `calculate_finishing_cost` returns `unpriced`/`refused` so a rate it does not know is flagged and alerted, never quoted at ₹0. 49 guard tests read the real API whitelist, the real order-v2 buttons and the real console dropdowns. 2024 tests green |
| S13-1 | ~~**`pdf_scaler.py` + tests**~~ ✅ | **Built 2026-08-30.** `scale_rect()` (pure geometry — one source of truth for print *and* both previews), `apply_scale()` (bakes the PDF), plus `count_cropped_pages()` for the preview's "N pages will be cropped" line. Custom % is of the original page, so Custom 100% returns exactly what Actual returns — asserted, not assumed. Never rotates. 46 tests. **Nothing imports it yet** — inert until S13-2 wires it |
| S13-2 | ~~**Planner + print server wiring**~~ ✅ | **Built 2026-08-30.** `print_spec.scale` → `resolve_scale()` → baked by `pdf_scaler` on the pass-through path, or by `perform_nup`'s own `scale_behavior` on 1-up landscape; `scale_applied` flows through `store_puller` to `send_to_printer`, which appends `noscale` as a guard. Every dropped combination alerts through `ops_watchdog` rather than half-applying: `nup ≥ 2`, unknown mode, junk percent, and **custom % on 1-up landscape** (deferred — the rotated target box is unproved on paper). 46 tests, incl. the sheets-identical-without-scale guard across 7 layouts |
| S13-3 | ~~**`print_items.scale_mode/scale_percent`**~~ ✅ | **Built 2026-08-30.** Additive nullable columns (`fix_db.py` + `install/bootstrap_db.py`); `handle_update_job` persists them, `handle_print_item` bakes via `pdf_scaler` and appends `noscale`, cleaning up in a `finally`. **Self-migrating**: store PCs pull code and restart the watcher without ever running `fix_db.py`, so `handle_update_job` adds the columns itself if the DB predates them — otherwise spec-saving would break at the counter. 21 tests |
| S13-4 | ~~**Preview endpoints**~~ ✅ | `GET /scale-preview` returns a PNG of the **baked** page (same `apply_scale` the printer gets), one page, switchable; `GET /order/scale-rect` gives the customer canvas the same geometry. Offline → say so, never approximate |
| S13-5 | **Staff scaling UI + paper proof** ⏳ | Fit / Actual / Custom in the print panel and New Job modal — **Custom % is staff-only**, presets 50/75/90/125/150/200 plus free entry, clamped 25–400%, percent **of the original page** (so Custom 100% ≡ Actual). Over 100% is allowed, warned and shown cropped, never silently clamped. **UI built 2026-08-30** — control in both consoles' print panels, baked preview with page switcher and crop warning, saved through `/update-job`, defaulting to no scaling. **Paper proof still outstanding**: the 8-check list is written into `docs/PRINT_ROTATION_MATRIX.md` and needs running on the OSP Konica before the control is used in anger |
| S13-6 | ~~**Customer scaling UI + preview**~~ ✅ *(ships as order-v3)* | order-v2 card (1-up only) + sheet canvas with hatched crop region and "N pages will be cropped" |
| S13-7 | **Service rate engine** | `rate_card.py` Section 11: `calculate_service_quote()` over the rates below. Every minimum names itself in the breakdown |
| S13-8 | **`jobs.service_kind` + `service_meta`** + transfer/booking columns | `SCHEMA_v29_service_jobs.sql` + SQLite ALTERs + `docs/SCHEMA.md`. NULL = print job |
| S13-9 | **`/service-quote` + `/new-service`** | Service jobs never create `print_items`, never enter a printer queue, never auto-print. Isolation tests pin it |
| S13-10 | **Service console UI** | `+ Service` modal + kind pills + service panel in jobs.html, mirrored in admin.html; MIS print counts filtered to `service_kind IS NULL` |
| S13-11 | **Photocopy button quotes properly** | Keep the one-tap button, but price it from the rate card instead of asking staff to type a number |
| S13-12 | **Per-store capabilities** | `store_config.json` gains `capabilities {binding, foiling, roll_lam}`, **default false = outsourced** so a new store never silently claims it can finish. PRINTK sets all three true. `FINISHING_OUTSOURCED` becomes `is_outsourced(finishing, store_id)` |
| S13-13 | **Inter-store finishing + revenue split** | OSP work finished at Nattika is an **internal transfer, not a vendor job**: `finishing_store_id`, `finishing_status`, an incoming-work queue in Nattika's console, and revenue split `print_amount` / `finishing_amount` at a configurable internal rate (seeded 100%) |
| S13-14 | **Online drop-off bookings** | Customers book finishing-only work on the site and bring the item in. `item_received_at` NULL = not work-ready; WhatsApp reminder then auto-expire after ~3 days; part payment upfront above a threshold |
| S13-15 | **Copy/scan reconciliation** | Compare counter-recorded copy/scan service jobs against `konica_jobs.job_type IN ('Copy','Scan')` — first visibility into unbilled walk-in copying |
| S13-16 | ~~**Quote drift audit**~~ ✅ | Run 2026-08-30 via `tools/quote_drift_audit.py`: 104 quoted jobs, **70 match, 32 drift, all dated 08-07→08-13**, none after. Commit `6afb9b5` (08-14) deliberately removed the odd-sheet rounding and reproduces 28 of the 32 exactly; the other 4 are mixed-colour jobs from the same window. Customers were over-charged ₹180 historically, never under. **No live drift.** Two real findings fell out → S13-17, and `rate_card.py:20` still documents the removed rounding rule |
| S13-17 | ~~**A5 / Letter bill as A4 B&W**~~ ✅ | `_VALID_SIZE` and the order-v2 paper dropdown offer A4/A3/A5/Legal/Letter, but `PRINT_RATES` has keys only for A4, A3 and Legal. `get_print_rate` falls back to `PRINT_RATES["A4_BW"]`, so **A5 and Letter bill at ₹3/sheet — colour included**. An A5 colour page bills ₹3 instead of ₹10; Letter is bigger than A4 and bills at A4 rates. This is the real explanation of the ₹10-vs-₹3 pair. Live under-billing. Rates set 2026-08-30: **A5 = half the A4 rate** (B&W ₹1.50; colour ₹5/₹4.50/₹4 by tier), **Letter = the A4 rate**, **no discounts on either** — the student rate stays A4 B&W only. **Fixed 2026-08-30** as part of S13-0, with a guard test that every size in `_VALID_SIZE` resolves to its own rate and that colour is always dearer than B&W |

### Rates locked by owner 2026-08-30

| Per-piece | A4 | A3 | Cover | Min |
|---|---|---|---|---|
| Foiling | ₹30 | ₹50 | ₹50 | 10 pieces (cover floor ₹500) |
| Roll lamination | ₹15 | ₹30 | — | 10 sheets |
| Pouch lamination | ₹70 | ₹120 B&W · ₹140 col | — | — |
| Cover lamination | ₹50 flat | | | |
| ID card | ₹100/card, printing included | | | |
| Cutting · Punching | ₹20 per machine pass, min ₹100 — **free on our own print/bind jobs** | | | |

**Binding:** spiral A4 unchanged; **spiral A3 now tiered** (₹80/110/130/160/210/240/320/400
= A4 × 2.67); **wiro ₹50 → ₹250 in ₹50 steps, refused above 150 sheets**; perfect =
soft; thesis = **₹500** binding line with printing on top, or project **+₹100**
bind-only; **bind-only is +₹20 across the board** (the existing ₹100 soft-no-print
constant is exactly ₹80 + ₹20); **thermal withdrawn — no longer offered** (closes S7-5).

**Other:** copy at print rates with student discount · scan A4 ₹10/₹7/₹5, **A3 double**
· photos printed from a supplied file, **no shooting** — set of 5 ₹50, full sheet ₹100
· DTP per page typing only (ML ₹40 · EN ₹40 · HI ₹60) · **urgent ₹20 now applies to
any service** · the ₹2 Sini/Ujjwala scan rate is removed.

**Still needed — all have working defaults, none blocking:** service payment threshold +
deposit (₹500 / 50%) · stamp, postcard, 4×6 photo rates · drop-off expiry days (3) ·
OSP→Nattika internal rates (100%).

**Locked by owner 2026-08-30:** scaling is offered on **1-up only** — every other
layout always fits to the printable area (N-up *is* a fit; "Actual size" on 4-up
would crop three-quarters of every page, and it keeps the verified slot geometry
untouched). Previews render the baked PDF, one page at a time. **Custom % is staff-only and
measured against the original page** — 100% is the document's own size wherever it
lands, so Custom 100% and Actual size are the same thing.

---

## ✅ COMPLETED (Session 1–6 reference)

- WhatsApp bot + file capture
- Multi-file batch timer (30s/60s)
- Razorpay payment link + webhook
- Named Cloudflare tunnel (store/pay subdomains)
- Print server (SumatraPDF, Konica + Epson)
- Admin panel (login, job list, print panel, quote, payment modal)
- Staff login/logout (PIN-based, per-PC)
- Phone column in job list
- Print preview iframe in panel
- storePcUrl key mismatch fix
- Auto-start via Windows Startup folder
- Floating print panel under clicked job row
- Home/remote PC access via store.printosky.com
- Troubleshooting playbook created
# Sprint 7
