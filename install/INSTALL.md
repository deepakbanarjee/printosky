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
  - `ADMIN_PBKDF2_SALT`, `ADMIN_PBKDF2_HASH`
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

### A6. Netlify (admin UI hosting — optional)
- Deploy `website/` to Netlify.
- Set `ADMIN_PASSWORD_HASH`, `SUPERADMIN_SHA256_HASH`, etc. in
  Netlify env vars.

When all the above are done, each new store just needs Part B.

---

## Part B — Per-store PC setup

### B1. Prerequisites on the store PC

- **Windows 10/11** (PowerShell 5.1+ comes pre-installed)
- **Python 3.13+** — download from https://python.org/downloads.
  **Tick "Add Python to PATH"** during install.
- **Git** — `winget install Git.Git` or download from https://git-scm.com.
- **The store's Windows printer queues** must be set up first:
  - Add the Konica with the network IP, note the queue name.
  - Add the Epson with the network IP, note the queue name.
  - The installer will ask you to enter these queue names.

### B2. Clone the repo

```powershell
mkdir C:\printosky_watcher
cd C:\printosky_watcher
git clone https://github.com/deepakbanarjee/printosky.git .
```

### B3. Run the installer

From `C:\printosky_watcher`:

```powershell
powershell -ExecutionPolicy Bypass -File install\bootstrap.ps1
```

You'll be prompted for:

| Field | Example | Notes |
|---|---|---|
| `store_id` | `TVM` | short uppercase code, unique per store |
| `store_name` | `Trivandrum Branch` | shown in admin UI |
| Konica IP | `192.168.55.110` | LAN IP of the Konica |
| Epson IP | `192.168.55.202` | LAN IP of the Epson |
| hot folder | `C:\Printosky\Jobs\Incoming` | default is fine |
| db_path | `C:\Printosky\Data\jobs.db` | default is fine |
| Konica queue | `KONICA MINOLTA 1100 PS` | from Windows Devices & Printers |
| Epson queue | `WF-C21000 Series(Network)` | from Windows Devices & Printers |
| Seed default staff PINs? | Y | resets to temporary PINs, **reset before going live** |

When it finishes, you'll have:
- `C:\Printosky\Data\jobs.db` — local SQLite, 12 tables ready
- `<repo>\store_config.json` — per-store identity
- `<repo>\.env` — secrets template with a freshly generated `STORE_TOKEN`
- `<repo>\SumatraPDF.exe` — for silent PDF dispatch

### B4. Fill in the `.env` shared secrets

The installer auto-generated `STORE_TOKEN` (unique to this store) but
you still need to paste in shared values from HQ. Open `.env` and
replace all `xxxxxxxx...` placeholders with the real values:

| Key | Source |
|---|---|
| `META_APP_SECRET` | Meta dev portal → Settings → Basic |
| `META_WEBHOOK_VERIFY_TOKEN` | matches what's set in Vercel/Meta |
| `META_SYSTEM_USER_TOKEN` | Meta dev portal → System Users |
| `META_PHONE_NUMBER_ID` | Meta dev portal → WhatsApp → Phone Numbers |
| `STORE_WHATSAPP_PHONE` | this store's WhatsApp number (no `+`) |
| `RAZORPAY_*` | Razorpay dashboard → Settings → API Keys |
| `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_KEY` | Supabase → Project Settings → API |
| `SUPABASE_AUTH_EMAIL`, `SUPABASE_AUTH_PASSWORD` | Supabase → Authentication → Users |
| `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| `ADMIN_PBKDF2_*`, `*_SHA256_HASH` | same value as the Vercel/Netlify env vars |
| `EPSON_USER`, `EPSON_PASS` | the Epson's web admin credentials |

### B5. First run

```powershell
.\START_PRINTOSKY.bat
```

This launches:
- **watcher.py** on port 3002/3003 — file watcher + Supabase sync
- **print_server.py** on port 3005 — admin API for printing
- **academic_pipeline_worker.py** — Supabase task polling

Three CMD windows open. Leave them running.

### B6. Smoke test — drop a file in the hot folder

```powershell
copy C:\Windows\System32\license.rtf C:\Printosky\Jobs\Incoming\
```

Within 5 seconds:
- The watcher CMD window should log `NEW JOB REGISTERED: <store_id>-YYYYMMDD-NNNN`
- The file appears in the `jobs` table in `C:\Printosky\Data\jobs.db`
- Within ~5 minutes (one sync cycle) the row appears in Supabase too

### B7. Autostart on PC boot

Right-click `SETUP_AUTOSTART.bat` → **Run as administrator**.

From now on Printosky services launch when the PC boots — no manual
intervention needed.

### B8. Reset staff PINs from defaults

Default seeded PINs are documented (and weak). Reset each one via the
admin API before the store goes live — see `STORE_SETUP_CHECKLIST.md`
section E for the exact `curl` commands.

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
