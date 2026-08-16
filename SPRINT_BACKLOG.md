# Printosky Sprint Backlog
Last updated: 2026-08-16 — refreshed against `main` @ `57ad33b`. The June–August
arc (order-v2, per-store jobs console, full-fidelity auto-print, DTP/OCR
pipeline, Supabase egress work) is now reflected here; it had been missing since
the 2026-06-02 entry.

**Store-only tasks live in [STORE_SETUP_CHECKLIST.md](STORE_SETUP_CHECKLIST.md).**
Owner/dashboard tasks live in [docs/OWNER_ACTIONS.md](docs/OWNER_ACTIONS.md).

---

## 🔥 OPEN NOW — top of the list

| # | Task | Why it's urgent |
|---|------|-----------------|
| **N1** | **Store PC is running code from before 2026-08-12** | `watcher.log` stale since 08-12, no `cloud_worker.log`. Eight commits (#62–#69) that run on that PC are not live, including the `store_puller` startup-crash fix (#67). While `watcher.py` is down there are no SNMP readings, no ink alerts and no Epson attribution. → checklist §A |
| **N2** | **Supabase quota — projects restricted from 2026-09-11** | 2.903/5 GB egress **4 days into** the 12 Aug–12 Sep cycle; storage 0.722/1 GB. The #68 interval cuts only take effect after N1; `tools/storage_cleanup.py --apply` still needs running. → checklist §B |
| **N3** | **Print fidelity never verified on paper** | The whole August auto-print arc (N-up, duplex, mixed colour, per-page colour billing) has unit tests only. → checklist §D |
| **N4** | **TASK-001 — Razorpay webhook still not repointed** | 0 Razorpay webhooks have ever been processed; no online payment is recorded anywhere. → OWNER_ACTIONS |
| **N5** | **PR #51 stale** | `feat/store-scoped-jobs`, open since 08-05, likely superseded by the `jobs.html` work in #54–#60. Close or rebase. |

---

## 🔐 SPRINT SEC — Security Hardening

| # | Task | Details |
|---|------|---------|
| SEC-1 | ~~**Remove staff PINs from SPRINT_BACKLOG.md**~~ ✅ | PIN values removed; look them up via `python staff_setup.py list` |
| SEC-2 | ~~**Move Supabase credentials to env vars**~~ ✅ | `.env` + `load_dotenv` in `supabase_sync.py` |
| SEC-3 | ~~**Admin password hash exposed in Netlify JS**~~ ✅ | Hashes moved to Netlify env vars; `netlify/functions/auth.js` verifies server-side |
| SEC-4 | ~~**Supabase anon key in admin.html**~~ ✅ | `sbFetch` uses the Supabase JWT from sessionStorage; RLS tightened in `SCHEMA_v5_migration.sql` |
| SEC-5 | ~~**Sequential staff PINs**~~ ✅ | Reset to random values 2026-04-09 |
| SEC-6 | **Epson default password** ⏳ | `192.168.55.214` still `admin`/`admin`. Store-only → checklist §C1. Also drop the hardcoded credential-fallback list in `epson_jobs_fetcher.py` once `EPSON_PASS` is authoritative. |
| SEC-7 | **STORE_TOKEN in localStorage** | Move to an httpOnly cookie + CSP (FEATURE_PIPELINE SEC5) |

---

## 🟠 SPRINT 7 — Admin Panel & Quoting

| # | Task | Details |
|---|------|---------|
| S7-1 | ~~**Quote endpoint: `colour=col` param**~~ ✅ | Fixed in `handle_quote()`; tests in `test_quote_endpoint.py` |
| S7-2 | ~~**Print panel: deploy & test**~~ ✅ | Superseded by the standalone `jobs.html` console (#54–#60), live on Netlify at `/jobs` |
| S7-3 | ~~**Print panel: item specs loaded from DB**~~ ✅ | `handle_update_job` derives `paper_type` from colour; 6 tests in `test_update_job.py` |
| S7-4 | **Outsourced vendor workflow** | finishing=project/record/lam_roll → vendor selection + WhatsApp dispatch. Not started. |
| S7-5 | **Thermal binding rate untested** ⏳ | `finishing=thermal` shows in admin, rate never verified. Needs a live job → checklist §D |

---

## 🟡 SPRINT 8 — Staff & MIS

| # | Task | Details |
|---|------|---------|
| S8-1 | **Tell staff their PINs** ⏳ | `python staff_setup.py list` — store-only |
| S8-2 | **MIS dashboard — live test** ⏳ | `mis.html` built, never live-tested |
| S8-3 | **Staff session Supabase sync** ⏳ | Verify `supabase_sync.py` syncs `staff_sessions` |
| S8-4 | ~~**Konica job attribution**~~ 🗑️ | RETIRED 2026-05-12 — 0/4507 attribution rate; `retired/2026-05-12-graveyard/konica_attribution.py` |
| S8-5 | **Idle logout timer** ⏳ | Verify `session_timeout.py` |
| S8-6 | ~~**Staff PIN mark-paid without admin password**~~ ✅ | `/admin/mark-paid` accepts `X-Staff-Pin` (`c47fa3f`) |
| S8-7 | ~~**Cloud staff-PIN login (off-LAN)**~~ ✅ | `/staff/login` mints the Supabase JWT (#57); LAN-reject fallback (#58) |

---

## 🟡 SPRINT 9 — Printer & Hardware

| # | Task | Details |
|---|------|---------|
| S9-1 | ~~**Konica supply levels**~~ ✅ | XML parse + SNMP fallback; 14 tests |
| S9-2 | ~~**Konica job export URL**~~ ✅ | Confirmed 2026-04-20 |
| S9-3 | ~~**Epson ink alerts**~~ ✅ | `_send_ink_alerts()`, fires on threshold crossing |
| S9-4 | **A3 printing** ⏳ | Never tested end-to-end → checklist §D |
| S9-5 | ~~**Receipt printer**~~ 🗑️ | RETIRED 2026-05-12 — hardware never purchased |
| S9-6 | **Epson per-job mono/colour tracking** ⏳ | On the **EM-C8100** (`192.168.55.214`, installed 2026-06-29). Done: delta dedup, `source='spec'` rows on dispatch, multi-credential fallback + HTML scraper (`a6d28d2`), **queue-name matching fix (2026-08-16 — the delta query had matched the retired unit's name and silently attributed nothing)**. Remaining: (c) store-PC redeploy → checklist §A; (d) admin-UI spec ↔ weblog reconciliation. |
| S9-7 | **Re-walk EM-C8100 SNMP OIDs** ⏳ | Vendor OIDs and the supply index→colour map in `printer_poller.py` were confirmed on the *retired* WF-C21000 and never re-verified. → checklist §C |

---

## 🟢 SPRINT 10 — Bot & Customer Experience

| # | Task | Details |
|---|------|---------|
| S10-1 → S10-4 | ~~Meta Cloud API, bot flow, group filter, delivery flow~~ ✅ | Live since April |
| S10-5 | ~~**B2B bot**~~ 🗑️ | RETIRED 2026-05-12 |
| S10-6 | ~~**Xtraa book-order flow**~~ ✅ | Live 2026-06-02. **Still open:** confirm real book titles (placeholders) + live WhatsApp smoke test |
| S10-7 | ~~**New-customer welcome auto-reply**~~ ✅ | Live 2026-06-02 |
| S10-8 | ~~**Admin conversations 404 fix**~~ ✅ | Fixed 2026-06-02 |
| S10-9 | **Admin: place book order on behalf of customer** | "New Book Order" in the Book Orders tab — walk-ins and teacher-forwarded orders |
| S10-10 | **Divya ledger: show own-book deductions** | Surface `via_divya=false` rows from her own phone as a deduction; add `books_taken` / `books_cost` |

---

## 🔵 SPRINT 11 — Infrastructure & Scale

| # | Task | Details |
|---|------|---------|
| S11-1 | **Cloud hosting for WhatsApp bot** | Bot dies with the PC. Hetzner CX22 €4/mo or similar. **Raised in priority by the 4-day outage.** |
| S11-2 | **PM2 for the Node process** ⏳ | Auto-restart on crash — store-only → checklist §G |
| S11-3 | **Job Centro DB** | Silent auto-export of Konica job logs |
| S11-4 | ~~**Multi-store: second store live**~~ ✅ | `PRINTK` (Nattika) on `192.168.1.0/24`, `PRIOFF` dev box exempted; subnet→store mapping in `store_config.py`; jobs scoped by `assigned_store_id` (#59, #60) |
| S11-5 | **Netlify OXYGEN team credit** | Monitor plan limit |
| S11-6 | ~~**Store-PC liveness alerts**~~ ✅ | Heartbeat watcher + `/cron/store-pc-check`; see `docs/STORE_PC_SHUTDOWN_PING.md`. **Note: this did not surface the 08-12 → 08-16 outage — check whether the cron is firing.** |
| S11-7 | **Supabase cost control** ⏳ | Egress + storage → N2 |

---

## 🟣 SPRINT 12 — Advanced Print Automation

| # | Task | Details |
|---|------|---------|
| S12-1 | **Operator Dashboard GUI** | Local Tkinter dashboard on the store PC |
| S12-2 | ~~**Rule-based auto-print**~~ ✅ | `store_puller.py` `auto_print()` + `print_planner.plan_print_job()` — paid orders print unattended. See [HANDOFF_AUTOPRINT_FIDELITY.md](HANDOFF_AUTOPRINT_FIDELITY.md) |
| S12-3 | **Filename auto-pricing** | Match module filenames to fixed-price packages |
| S12-4 | ~~**Live hardware scraper**~~ ✅ | Konica XML + Epson HTML table scraper (`a6d28d2`) |

---

## 🟤 SPRINT 13 — Order v2, DTP & CV Builder (August arc)

| # | Task | Details |
|---|------|---------|
| S13-1 | ~~**order-v2 customer print options**~~ ✅ | Duplex, N-up + fill direction, mixed colour, page ranges → `jobs.print_spec` (JSONB) |
| S13-2 | ~~**Full-fidelity auto-print**~~ ✅ | `print_planner.py` + `nup_imposer.py`; mixed jobs split B&W→Konica / colour→Epson |
| S13-3 | ~~**Per-store jobs console**~~ ✅ | `jobs.html`, auto-scopes to the machine's `store_id` |
| S13-4 | ~~**Staff walk-in order creation**~~ ✅ | `/order/staff-create`, multi-file staff mode (#54), Cash/UPI/Hold at creation (#63) |
| S13-5 | ~~**Malayalam manuscript OCR / DTP**~~ ✅ | `cloud_transcription_worker.py` + `transcripts.html` (renamed DTP), output to `C:\DTP\<ddmmyy>` |
| S13-6 | ~~**AI CV Builder (operator-only)**~~ ✅ | 9 templates, ATS scanner, admin-gated AI endpoints |
| S13-7 | **jobs.html per-store scoping is partial** | Top stats strip + printer job log read `daily_summary` / `konica_jobs` / `epson_jobs`, which are **not** store-scoped — they show all-store totals. Per [HANDOFF_JOBS_ORDERV2.md](HANDOFF_JOBS_ORDERV2.md) §10. |
| S13-8 | **`transcripts.html` dead references** | `trScrollToPage` / `trEditBalance` referenced but never defined (harmless) |
| S13-9 | **Staff-create inline payment** | Optionally capture cash/UPI in order-v2 staff mode and mark paid in one step |

---

## 🧹 Housekeeping

| # | Task | Details |
|---|------|---------|
| H1 | ~~**Retire stale store checklists**~~ ✅ | 2026-08-16 — `STORE_CHECKLIST_TODAY.html` + `TASKS_2026-04-13.md` → `retired/2026-08-16-stale-docs/`. Both still pointed staff at the retired Epson `.202`. |
| H2 | ~~**Sweep retired-printer references**~~ ✅ | 2026-08-16 — `.202` / `WF-C21000` removed from live code paths; the four `epson_*.py` diagnostic scripts now read the IP from `store_config` |
| H3 | **`docs/FEATURE_PIPELINE.md` is stale** | Last updated 2026-04-30; its "Done (last 7 days)" list is from May |
| H4 | **`make_arch_pdf.py` credentials** | Confirm whether `Printosky@1234` / `@MIS2026` / `@Super2026` are real; if so rotate and move to `.env` |

---

## ✅ COMPLETED (Sessions 1–6 reference)

WhatsApp bot + file capture · multi-file batch timer · Razorpay payment link +
webhook · named Cloudflare tunnel · print server (SumatraPDF, Konica + Epson) ·
admin panel · staff PIN login · print preview · auto-start via Windows Startup ·
remote access via store.printosky.com · troubleshooting playbook
