# Printosky Sprint Backlog
Last updated: 2026-03-19 (Session 6)

---

## 🔴 CRITICAL / BLOCKERS

| # | Task | Details |
|---|------|---------|
| C1 | **Run SCHEMA_v3 in Supabase** | `CREATE TABLE staff_sessions`, `ALTER TABLE jobs ADD printed_by`, `ALTER TABLE konica_jobs ADD attributed_to` — SQL in project_status.md |
| C2 | **OXYGEN PC server URL** | Currently set to own IP (217). Fix: reset localStorage, enter `192.168.55.212:3005` |

---

## 🟠 SPRINT 7 — Admin Panel & Quoting

| # | Task | Details |
|---|------|---------|
| S7-1 | **Quote endpoint: `colour=col` param** | `/quote?colour=col` ignored — maps nothing. Fix: detect `colour=col` param, set `paper_type=A4_col` |
| S7-2 | **Print panel: deploy & test** | New floating panel inserted under job row — deploy to Netlify + test on all PCs |
| S7-3 | **Print panel: item specs loaded from DB** | When job already has saved specs, load them into editItems (currently resets to defaults) |
| S7-4 | **Outsourced vendor workflow** | When finishing=project/record/lam_roll, show vendor selection; send job to vendor via WhatsApp |
| S7-5 | **Thermal binding** listed in admin but rate not tested | Test `finishing=thermal` in quote endpoint |

---

## 🟡 SPRINT 8 — Staff & MIS

| # | Task | Details |
|---|------|---------|
| S8-1 | **Tell staff their PINs** | Priya=1001, Revana=1002, Bini=1003, Anu=1004, Deepak=1005 |
| S8-2 | **MIS dashboard — live test** | mis.html built but never live-tested. Verify staff sessions syncing to Supabase |
| S8-3 | **Staff session Supabase sync** | `supabase_sync.py` syncs staff_sessions — verify after SCHEMA_v3 applied |
| S8-4 | **Konica job attribution** | `KONICA_USER_PC_MAP` in print_server.py — map Konica usernames to staff. Test attribution flow |
| S8-5 | **Idle logout timer** | session_timeout.py — verify it logs out idle staff correctly |

---

## 🟡 SPRINT 9 — Printer & Hardware

| # | Task | Details |
|---|------|---------|
| S9-1 | **Konica supply levels** | Parse bizhub XML at `192.168.55.110` for toner %. Currently returns None |
| S9-2 | **Konica job export URL** | Auto-discovery failed. Manual: open `192.168.55.110` → job log → CSV export → inspect URL in DevTools → set `KONICA_JOB_EXPORT_URL` in konica_jobs_fetcher.py |
| S9-3 | **Epson ink alerts** | Ink Black 2: 0% (EMPTY), Cyan: 2%. Add WhatsApp alert when ink < 10% |
| S9-4 | **A3 printing** | Test A3 job end-to-end (bot → quote → print) |
| S9-5 | **Receipt printer** | `RECEIPT_PRINTER = None` in print_server.py. Hardware pending. |

---

## 🟢 SPRINT 10 — Bot & Customer Experience

| # | Task | Details |
|---|------|---------|
| S10-1 | **Meta Cloud API migration** | Replace whatsapp-web.js with Meta Cloud API direct (free, zero ban risk). Steps: (1) Deregister 9446903907 from WA app — CONFIRMED no active account. (2) Create Meta Business Manager + WABA (needs GST cert). (3) Register number, get System User token. (4) Add `/whatsapp-webhook` route to webhook_receiver.py, point CloudFlare tunnel. (5) Migrate outbound calls in whatsapp_bot.py from WA Web socket to graph.facebook.com REST API. Monthly cost: ~₹0 (reactive flow = free service window). |
| S10-2 | **Bot conversation flow review** | Run full customer journey test: file → 6 steps → payment → notification |
| S10-3 | **WhatsApp group/channel filter** | Already patched in index.js. Verify after next bot restart |
| S10-4 | **Delivery flow** | Bot asks delivery Y/N. Delivery charge Rs.30. Test end-to-end |
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
