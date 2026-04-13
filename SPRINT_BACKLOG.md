# Printosky Sprint Backlog
Last updated: 2026-04-09 (Session 9)

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
| S8-4 | **Konica job attribution** | `KONICA_USER_PC_MAP` in print_server.py — map Konica usernames to staff. Test attribution flow |
| S8-5 | **Idle logout timer** | session_timeout.py — verify it logs out idle staff correctly |

---

## 🟡 SPRINT 9 — Printer & Hardware

| # | Task | Details |
|---|------|---------|
| S9-1 | ~~**Konica supply levels**~~ ✅ | `parse_konica_xml_supplies()` + `poll_konica_xml_supplies()` in printer_poller.py. Parses TnrBlkRmng/DrmBlkRmng tags (and alternates); poll_once() tries XML first, falls back to SNMP. 14 tests in test_konica_supplies.py. |
| S9-2 | **Konica job export URL** | Auto-discovery failed. Manual: open `192.168.55.110` → job log → CSV export → inspect URL in DevTools → set `KONICA_JOB_EXPORT_URL` in konica_jobs_fetcher.py |
| S9-3 | ~~**Epson ink alerts**~~ ✅ | `_send_ink_alerts()` in printer_poller.py:434. Fires on threshold crossing (EMPTY at 0%, LOW at ≤10%). Called in poll_once() for both printers. |
| S9-4 | **A3 printing** | Test A3 job end-to-end (bot → quote → print) |
| S9-5 | **Receipt printer** | `RECEIPT_PRINTER = None` in print_server.py. Hardware pending. |

---

## 🟢 SPRINT 10 — Bot & Customer Experience

| # | Task | Details |
|---|------|---------|
| S10-1 | ~~**Meta Cloud API migration**~~ ✅ | Live on 9495706405 via Vercel. App review submitted. Token rotated 2026-04-09. |
| S10-2 | ~~**Bot conversation flow review**~~ ✅ | Full journey tested: file → 6 steps → payment → notification (2026-04-09) |
| S10-3 | ~~**WhatsApp group/channel filter**~~ ✅ | Filters @g.us, @newsletter, @broadcast, isGroupMsg in index.js:165-170. Confirmed in code. |
| S10-4 | ~~**Delivery flow**~~ ✅ | Verified working (2026-04-09) |
| S10-5 | **B2B bot** | `b2b_bot.py` and `b2b_manager.py` exist. Status unknown. Define scope and test |

---

## 🔵 SPRINT 11 — Infrastructure & Scale

| # | Task | Details |
|---|------|---------|
| S11-1 | **Cloud hosting for WhatsApp bot** | Bot goes offline when PC is off. Options: Hostinger VPS Rs.350/mo, Hetzner CX22 €4/mo, DigitalOcean $6/mo |
| S11-2 | **PM2 for Node process** | Replace manual CMD window start with PM2 for auto-restart on crash |
| S11-3 | **Job Centro DB** | Investigate silent auto-export of Konica job logs from Job Centro local DB |
| S11-4 | **Second store setup** | Multi-store architecture. Each store needs own PC + tunnel + Supabase store_id |
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
