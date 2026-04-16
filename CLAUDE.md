# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**Printosky** is a print job management and billing system for a physical print shop (Oxygen Students Paradise, Thrissur). It auto-captures print files, quotes customers via WhatsApp, collects Razorpay payments, and tracks all jobs on a live dashboard. It runs entirely on a Windows store PC.

## How to Run

```batch
START_PRINTOSKY.bat
```

This launches all three services in parallel. To run them manually:

```bash
# Terminal 1 — file watcher + integrations
python watcher.py

# Terminal 2 — print server + staff auth
python print_server.py

# Terminal 3 — WhatsApp bot (Node.js)
cd whatsapp_capture
node index.js

# Optional: local live dashboard
python dashboard.py    # http://localhost:5000
```

## Installation

```bash
# Python deps
pip install watchdog gspread google-auth google-auth-oauthlib websockets requests pysnmp

# Node deps
cd whatsapp_capture && npm install

# Seed default staff PINs
python staff_setup.py seed

# Supabase schema (run in Supabase SQL Editor, in order)
# SCHEMA.sql → SCHEMA_v2_migration.sql → SCHEMA_v3_migration.sql
```

## Key Commands (watcher.py REPL)

```
pending                          → list pending jobs
report                           → today's revenue summary
done OSP-YYYYMMDD-XXXX AMOUNT MODE  → mark job complete (mode: cash/upi)
```

## Staff CLI

```bash
python staff_setup.py list       # show all staff
python staff_setup.py add        # add staff member
python staff_setup.py reset PIN  # reset a PIN
```

## Architecture

The system has three runtime processes:

1. **watcher.py** — Python; monitors `C:\Printosky\Jobs\Incoming\` via watchdog; logs new files to SQLite (`C:\Printosky\Data\jobs.db`); starts background threads for Supabase sync, printer polling, session timeout, and Konica CSV import.

2. **print_server.py** (port 3005) — Python HTTP server; handles `/print`, `/quote`, `/staff-login`, `/staff-logout`, `/printers`; authenticates staff via SHA-256 PIN; drives SumatraPDF.

3. **whatsapp_capture/index.js** (port 3001) — Node.js; runs WhatsApp Web client; saves incoming files to hot folder; delegates conversation state to `whatsapp_bot.py` (Python module, not a separate process).

Supporting modules (all imported/threaded, not standalone services):

| Module | Role |
|---|---|
| `rate_card.py` | Pricing engine — paper × sides × layout × copies × finishing; tiered colour rates |
| `razorpay_integration.py` | Creates payment links; verifies webhook signatures |
| `webhook_receiver.py` | HTTP handler (port 3002) for Razorpay payment confirmations |
| `supabase_sync.py` | Background thread; upserts jobs + counters to Supabase every 5 min |
| `printer_poller.py` | SNMP/HTTP polling for Konica (192.168.55.110) and Epson (192.168.55.202) |
| `konica_jobs_fetcher.py` | Pulls CSV job log from Konica web admin; deduplicates |
| `session_timeout.py` | Flags idle WhatsApp bot sessions; sends staff alerts |
| `b2b_manager.py` | B2B credit accounts, per-client discounts, monthly invoice PDF |
| `dashboard.py` | HTTP + WebSocket (ports 5000/5001); pushes live job stats to browser |

**Admin UI** (`website/admin.html`) — static HTML deployed to Netlify (printosky.com/admin); reads from Supabase via anon key; staff PIN and admin password are SHA-256 checked client-side.

**Payment webhooks** reach the store PC via CloudFlare Tunnel (exposes localhost:3002).

## Data Flow

```
File arrives in hot folder
  → watcher.py logs to SQLite
  → Customer quotes via WhatsApp → rate_card.py calculates price
  → razorpay_integration.py creates payment link → customer pays
  → webhook_receiver.py marks job "Paid"
  → Staff sees green job in admin.html → clicks Print
  → print_server.py sends to SumatraPDF → job "Completed"
  → supabase_sync.py pushes to cloud → admin page reflects update
```

## Database

SQLite at `C:\Printosky\Data\jobs.db`. Key tables: `jobs`, `bot_sessions`, `staff`, `staff_sessions`, `printer_counters`, `b2b_clients`, `b2b_payments`. Supabase mirrors `jobs`, `printer_counters`, `daily_summary`, `staff_sessions`.

## Pending Work

See `SPRINT_BACKLOG.md` for all queued items. Critical items at top of file: run SCHEMA_v3 migration, fix Oxygen PC server URL.

## Config Locations

All secrets and paths are hardcoded directly in source files:
- Folder paths: `watcher.py`
- Supabase URL/key: `supabase_sync.py` and `website/admin.html`
- Razorpay live keys: `razorpay_integration.py`
- Printer IPs: `printer_poller.py`, `konica_jobs_fetcher.py`
- Admin password hash: `website/admin.html`
