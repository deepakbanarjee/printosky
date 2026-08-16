# Printosky — install guide for a new store

> This replaces the older root-level `INSTALL.md` (which described a
> hot-folder-only Phase 1 system that has since evolved into a full
> multi-store platform). The legacy file is kept for historical
> reference but should not be followed for new installations.

There are **two halves** to spinning up a new Printosky store:

1. **HQ-side prerequisites** — one-time setup that the Printosky owner
   does once for the whole network. Most of this is already done if
   OSP Thrissur is live.
2. **Per-store PC setup** — what you do on each new store's Windows
   PC. This is what `install\bootstrap.ps1` automates.

If HQ-side is done, skip to **Part B**.

---

## Part A — HQ prerequisites (do once per Printosky network)

These don't run on the store PC. They're configured once by the owner.

### A1. Supabase project
- Create at https://supabase.com (free tier is enough to start).
- Apply all `api/migrations/SCHEMA_*.sql` files in numeric order.
- Note the **Project URL** and **service_role key** — store-PCs need them.
- See `docs/SCHEMA.md` for the canonical table list.

### A2. Vercel project (admin API + webhooks)
- Connect the GitHub repo to a new Vercel project.
- Set environment variables in **Vercel → Settings → Environment Variables**:
  - `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_KEY`
  - `META_APP_SECRET`, `META_WEBHOOK_VERIFY_TOKEN`, `META_SYSTEM_USER_TOKEN`, `META_PHONE_NUMBER_ID`
  - `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`
  - `ANTHROPIC_API_KEY`
- Deploy: `vercel --prod` from the repo root.

### A3. Meta WhatsApp Cloud API
- Create a Meta Developer app at https://developers.facebook.com.
- Add the **WhatsApp Cloud API** product.
- Add the store's phone number under WhatsApp → Phone Numbers.
- Set the webhook URL to `https://<your-vercel>.vercel.app/webhook/whatsapp`.
- Subscribe to: `messages`, `message_status`.

### A4. Razorpay
- Create a Razorpay merchant account (live or test).
- Set webhook URL to `https://<your-vercel>.vercel.app/webhook/razorpay`,
  events: `payment.captured`, `payment_link.paid`.
- Note the API keys + webhook secret for the `.env`.

### A5. Cloudflare DNS (optional — only if exposing the store PC)
- Set up a named tunnel from `store.printosky.com` to the store PC.
- The `SETUP_NAMED_TUNNEL.bat` script handles the store-side install.

### A6. Netlify (admin UI hosting)
- Deploy `website/` to Netlify.
- Set these in **Netlify → Site configuration → Environment variables**
  (read by `netlify/functions/auth.js`, which verifies admin/superadmin/
  store/mis logins server-side — these do NOT live in Vercel):
  - `ADMIN_PBKDF2_HASH`, `ADMIN_PBKDF2_SALT`
  - `SUPERADMIN_SHA256_HASH`, `STORE_SHA256_HASH`, `MIS_SHA256_HASH`
  - `ADMIN_PASSWORD_HASH` (separate, simpler SHA256 check used elsewhere)
  - `SUPABASE_URL`, `SUPABASE_KEY` (auth.js also talks to Supabase directly)

When all the above are done, each new store just needs Part B.

---

## Part B — Per-store PC setup (one prompt, then automatic)

### B1. Prerequisites on the store PC

- **Windows 10/11** (PowerShell 5.1+ comes pre-installed)
- **Python 3.12+** — download from https://python.org/downloads.
  **Tick "Add Python to PATH"** during install.
- **Git** — `winget install Git.Git` or download from https://git-scm.com.
- **The store's Windows printer queues** must be set up first:
  - Add the Konica with the network IP, note the queue name.
  - Add the Epson with the network IP, note the queue name.

### B2. Get the HQ secrets file from Printosky HQ

HQ ships **one file** containing all the shared backend secrets:

```
hq-secrets.env
```

This file contains only the keys a store PC actually reads:
`META_*`, `SUPABASE_*`, `RAZORPAY_*`, `ANTHROPIC_API_KEY`.
(`STORE_TOKEN` and `STORE_WHATSAPP_PHONE` are generated / prompted by
the installer, not shipped in this file.)

> **Do NOT put the website login hashes here.** `ADMIN_PBKDF2_HASH`,
> `ADMIN_PBKDF2_SALT`, `SUPERADMIN_SHA256_HASH`, `STORE_SHA256_HASH`,
> `MIS_SHA256_HASH` are **Netlify-only** — they power the website admin
> login (`netlify/functions/auth.js`), not the store PC. No store-PC
> code reads them, and the installer explicitly ignores them.

It is **never** committed to git and must be transferred securely
(encrypted email, USB stick, password-protected zip).

Save it to: `C:\printosky_watcher\hq-secrets.env`

### B3. Clone the repo

```powershell
mkdir C:\printosky_watcher
cd C:\printosky_watcher
git clone https://github.com/deepakbanarjee/printosky.git .
```

(If `hq-secrets.env` was saved before cloning, move it back into the
repo folder now.)

### B4. Run the installer — one prompt block, then automatic

From `C:\printosky_watcher`:

```powershell
powershell -ExecutionPolicy Bypass -File install\bootstrap.ps1
```

The installer asks **only physical-location questions**:

| Prompt | Example | What's used for |
|---|---|---|
| Store name | `Printosky Trivandrum` | shown in admin UI |
| `store_id` | (auto-suggested `PT` from name) | unique short code |
| City / location | `Thrissur` | appended to store_name |
| Konica IP | `192.168.55.110` | LAN IP |
| Epson IP | `192.168.55.214` | LAN IP (OSP; EM-C8100 installed 2026-06-29) |
| Konica Windows queue | `KONICA MINOLTA 1100 PS` | from Devices & Printers |
| Epson Windows queue | `EM-C8100 Series(Network)` | from Devices & Printers |
| WhatsApp # (no `+`) | `919495706405` | inbound webhook routing |
| Epson admin user | `Oxygen` | LAN-only printer login |
| Epson admin password | (your value) | LAN-only printer login |

**Everything else is auto-generated**:

- `store_id` derived from the store name (you can override the suggestion)
- `STORE_TOKEN` (cryptographic 64-hex)
- HQ secrets (`META_*`, `SUPABASE_*`, hashes, etc.) copied from `hq-secrets.env`
- 6-digit PINs for the 5 default staff (written to `.staff_pins_first_login.txt`)
- Windows Startup shortcut for autostart on PC boot
- `C:\Printosky\Data`, `Jobs\Incoming`, `Jobs\Archive` folders
- SumatraPDF portable binary

When the installer finishes you have:

- `C:\Printosky\Data\jobs.db` — local SQLite, 12 tables
- `<repo>\store_config.json` — per-store identity
- `<repo>\.env` — populated with HQ secrets + per-store values
- `<repo>\.staff_pins_first_login.txt` — one-time PINs for staff
- `<repo>\SumatraPDF.exe`
- Windows Startup shortcut → `START_PRINTOSKY.bat`

**Nothing else needs to be filled in.**

### B5. First run

Either reboot the PC (autostart will launch everything) or start now:

```powershell
.\START_PRINTOSKY.bat
```

This launches:
- **watcher.py** on port 3002/3003 — file watcher + Supabase sync
- **print_server.py** on port 3005 — admin API for printing
- **academic_pipeline_worker.py** — Supabase task polling

Three CMD windows open. Leave them running.

### B6. Distribute staff PINs

The installer auto-generated 6-digit PINs for the 5 default staff
and wrote them to `.staff_pins_first_login.txt` in the repo root.

1. Open `.staff_pins_first_login.txt`
2. Hand each PIN to its owner privately (Telegram, in person, sealed envelope)
3. Each staff member changes their PIN on first login
4. **Delete** `.staff_pins_first_login.txt` from the PC

### B7. Smoke test — drop a file in the hot folder

```powershell
copy C:\Windows\System32\license.rtf C:\Printosky\Jobs\Incoming\
```

Within 5 seconds:
- The watcher CMD window should log `NEW JOB REGISTERED: <store_id>-YYYYMMDD-NNNN`
- The file appears in the `jobs` table in `C:\Printosky\Data\jobs.db`
- Within ~5 minutes (one sync cycle) the row appears in Supabase too

### B8. Autostart on PC boot

Already done — the installer dropped `Printosky.lnk` into the Windows
Startup folder. Services launch on next boot. If you want belt-and-
braces machine-wide autostart instead, right-click
`SETUP_AUTOSTART.bat` → Run as administrator.

### B9. Verify in production

- Open `https://printosky.com/admin` (or the Netlify URL)
- Log in as admin
- The new `store_id` should appear in the store filter dropdown
- A test job dropped at this store should appear in the admin UI
  within ~5 minutes

---

## Troubleshooting

### "Python not found"
Re-install Python with "Add to PATH" checked. Close and re-open
PowerShell.

### `bootstrap.ps1` exits at step 4 with pip error
Most often a network proxy issue. Try:
```powershell
python -m pip install -r requirements.txt --proxy http://your.proxy:port
```

### "Print queue NOT found"
The Windows printer queue name in `store_config.json` doesn't match
what's actually installed. Open Devices & Printers, find the exact
name (including capitalisation), and edit `store_config.json`:
```json
"printer_queue_names": { "konica": "...", "epson": "..." }
```
Restart `print_server.py`.

### "Konica/Epson printer NOT reachable"
The printer's LAN IP doesn't match `store_config.json`. Check the
printer's network status page and update the IP. If correct,
check the store PC is on the same VLAN as the printers.

### `.env` missing values
The installer doesn't fill in the shared HQ values — it can't. Get
them from the HQ operator (Vercel env vars are the source of truth).

### Daemon won't start — port 3005 already in use
Something else (a previous instance, or another app) is on port 3005.
Run:
```powershell
Get-NetTCPConnection -LocalPort 3005 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

---

## What's intentionally missing

The installer doesn't try to set up:
- **Cloudflare named tunnel** — use `SETUP_NAMED_TUNNEL.bat` after
  HQ has registered the DNS record for this store.
- **WhatsApp coexistence on the store phone** — see
  `STORE_SETUP_CHECKLIST.md` section A. That's a phone-side install,
  not a PC step.
- **Razorpay merchant subaccount** — needed only if using Razorpay
  Route for multi-store payment splitting.

These are tracked separately in `STORE_SETUP_CHECKLIST.md`.
