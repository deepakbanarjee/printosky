# Printosky Architecture
_Last updated: 2026-04-29 — reflects commit 8930eaf_

---

## Runtime Processes

### Store PC (Windows)
| Process | File | Port | Role |
|---------|------|------|------|
| File watcher | `watcher.py` | — | Watchdog on `C:\Printosky\Jobs\Incoming\`; logs to SQLite; spawns sync/poller/timeout threads |
| Print server | `print_server.py` | 3005 | Staff auth (PBKDF2+salt PIN), `/print` `/quote` `/staff-login` `/staff-logout` `/printers`; drives SumatraPDF |
| WhatsApp client | `whatsapp_capture/index.js` | 3001 (send), 3004 (doc) | WhatsApp Web session; saves incoming files to hot folder; delegates state to `whatsapp_bot.py` |
| Dashboard | `dashboard.py` | 5000 (HTTP), 5001 (WS) | Live job stats browser push |
| Webhook receiver | `webhook_receiver.py` | 3002 | Razorpay print payment confirmations (store PC local only) |

### Vercel (`api/index.py`)
All routes → single Python handler. Deployed from `main` branch.

| Route | Handler | Auth |
|-------|---------|------|
| `GET /` | health check | none |
| `POST /whatsapp-webhook` | `_process_meta_webhook` | HMAC `META_APP_SECRET` |
| `GET /whatsapp-webhook` | webhook verify | `META_WEBHOOK_VERIFY_TOKEN` |
| `POST /webhook/razorpay` | `_process_razorpay_payment` | HMAC `RAZORPAY_WEBHOOK_SECRET` |
| `POST /staff/set-pin` | `_handle_staff_set_pin` | current PIN (PBKDF2) |
| `POST /staff/resume` | `_handle_staff_resume` | PIN (PBKDF2) |
| `POST /admin/reset-pin` | `_handle_admin_reset_pin` | `ADMIN_PASSWORD_HASH` |
| `POST /admin/send` | `_handle_admin_send` | `ADMIN_PASSWORD_HASH` |
| `GET/POST /academic/orders` | `_handle_acad_orders_*` | staff PIN |
| `GET /academic/orders/:id` | `_handle_acad_order_get` | staff PIN |
| `POST /academic/razorpay-webhook` | `_handle_acad_razorpay_webhook` | HMAC `RAZORPAY_ACADEMIC_WEBHOOK_SECRET` |
| `POST /academic/orders/:id/*` | state transitions | staff PIN |

### Supabase (cloud DB)
Tables: `jobs`, `job_batches`, `bot_sessions`, `customer_profiles`, `conversation_log`, `staff`, `staff_sessions`, `printer_counters`, `daily_summary`, `academic_orders`
Storage bucket: `academic-outputs` (public)

---

## Supporting Modules (store PC, imported/threaded)

| Module | Role |
|--------|------|
| `rate_card.py` | Pricing: paper × sides × layout × copies × finishing |
| `razorpay_integration.py` | Creates payment links; verifies Razorpay webhook sigs |
| `supabase_sync.py` | Background thread; upserts jobs + counters every 5 min |
| `printer_poller.py` | SNMP/HTTP poll: Konica `192.168.55.110`, Epson `192.168.55.202` |
| `konica_jobs_fetcher.py` | Pulls CSV job log from Konica web admin |
| `session_timeout.py` | Flags idle WhatsApp sessions; sends staff alerts |
| `b2b_manager.py` | B2B credit accounts, per-client discounts, monthly invoice PDF |
| `db_cloud.py` | Supabase CRUD (jobs, sessions, storage upload) |
| `db_cloud_academic.py` | Supabase CRUD for academic orders; generates `PROJ-YYYY-NNN` IDs |
| `academic_db.py` | SQLite mirror of academic schema (store PC path) |

---

## Database

**SQLite** at `C:\Printosky\Data\jobs.db`
Tables: `jobs`, `bot_sessions`, `staff` (+`pin_salt` col, schema v15), `staff_sessions`, `printer_counters`, `b2b_clients`, `b2b_payments`

**Schema migrations** apply in order: `SCHEMA.sql` → `SCHEMA_v2` → ... → `SCHEMA_v15_pin_salt.sql`
v15 Supabase: `ALTER TABLE staff ADD COLUMN IF NOT EXISTS pin_salt TEXT;` (run in SQL Editor)

---

## Environment Variables

### Vercel only (`api/index.py` reads these)
| Var | Purpose | Status |
|-----|---------|--------|
| `META_APP_SECRET` | Meta webhook HMAC | ✅ Set |
| `META_WEBHOOK_VERIFY_TOKEN` | WhatsApp hub verify | check dashboard |
| `META_SYSTEM_USER_TOKEN` | Media download from Meta | check dashboard |
| `ADMIN_PASSWORD_HASH` | SHA-256 of admin password | check dashboard |
| `RAZORPAY_WEBHOOK_SECRET` | Print Razorpay webhook HMAC | check dashboard |
| `RAZORPAY_ACADEMIC_WEBHOOK_SECRET` | Academic Razorpay webhook HMAC | ⚠️ open — see SECURITY.md |
| `SUPABASE_URL` | Supabase project URL | ✅ Set at deploy |
| `SUPABASE_KEY` | Supabase anon key | ✅ Set at deploy |
| `SUPABASE_SERVICE_KEY` | Supabase service role key | ✅ Set at deploy |

### Store PC (hardcoded — migration pending)
- Folder paths → `watcher.py`
- Supabase URL/key → `supabase_sync.py`, `website/admin.html`
- Razorpay live keys → `razorpay_integration.py`
- Printer IPs → `printer_poller.py`, `konica_jobs_fetcher.py`
- Admin password hash → `website/admin.html` (client-side check)

---

## Data Flow

```
File arrives in C:\Printosky\Jobs\Incoming\
  → watcher.py logs to SQLite
  → WhatsApp bot quotes via rate_card.py
  → razorpay_integration.py creates payment link → customer pays
  → Vercel /webhook/razorpay marks job "Paid" → Supabase updated
  → Staff sees job in admin.html → clicks Print
  → print_server.py sends to SumatraPDF → "Completed"
  → supabase_sync.py pushes counters/summary to Supabase

Academic orders:
  → Student submits → POST /academic/orders (staff PIN required)
  → Stored in Supabase academic_orders (PROJ-YYYY-NNN ID)
  → Pipeline worker on store PC generates document
  → Uploaded to academic-outputs bucket → student notified via WhatsApp
```

---

## Deploy Pipeline

| Branch | Platform | Trigger |
|--------|----------|---------|
| `main` | Vercel | auto on push |
| `sprint/session-9` | Netlify | auto on push |

**Rule:** Cherry-pick every API change to `main` after committing to sprint branch.

---

## Admin UI

`website/admin.html` — static HTML on Netlify (`sprint/session-9`).
Reads Supabase via anon key. Staff PIN + admin password checked client-side (SHA-256).
Includes academic orders tab (added session 9).

---

## What Changed (session history)
| Session | Change |
|---------|--------|
| Session 9 (Apr 29) | WhatsApp + Razorpay webhooks moved to Vercel; academic orders API added |
| Session 10 (Apr 29) | PBKDF2+salt PIN hashing (all 3 files); rate limiting on /staff-login; path traversal fix; Razorpay academic webhook secret separated |
