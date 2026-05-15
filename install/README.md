# Printosky — new-store installer

This directory packages everything needed to bring up a fresh store PC.

```
install/
├── bootstrap.ps1     # main installer (run this)
├── bootstrap_db.py   # creates the local SQLite schema
├── INSTALL.md        # full end-to-end install guide (HQ ops + per-store)
└── README.md         # you are here
```

## Quick start (on the new store PC)

1. Clone the repo to `C:\printosky_watcher`.
2. Open PowerShell **in the repo folder**, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File install\bootstrap.ps1
   ```

3. Answer the prompts (store_id, printer IPs, etc.).
4. When the installer finishes, open `.env` and fill in the shared
   secrets from Printosky HQ (Meta keys, Supabase keys, hashes).
5. Double-click `START_PRINTOSKY.bat` to launch services.

## What the installer does — fully automated

The operator only enters **physical-location data**. Everything else is
generated or copied from an HQ secrets source.

| # | Step | What the operator sees |
|---|---|---|
| 1 | Verify Python / pip / internet | nothing — auto-pass/fail |
| 2 | Create `C:\Printosky\Data`, `Jobs\Incoming`, `Jobs\Archive` | nothing — auto |
| 3 | Download SumatraPDF portable (~20 MB) | nothing — auto |
| 4 | `pip install -r requirements.txt` | nothing — auto |
| 5 | Detect HQ secrets source | nothing — auto |
| 6 | **Ask: store_name, city, printer IPs + queue names, WhatsApp #, Epson password** | the ONE prompt block |
| 7 | Write `store_config.json` (store_id auto-derived from name) | confirms overwrite if exists |
| 8 | Build `.env` (HQ secrets copied; STORE_TOKEN auto-random; STORE_WHATSAPP_PHONE, EPSON_USER, EPSON_PASS set from inputs) | confirms overwrite if exists |
| 9 | Bootstrap SQLite schema (12 tables/views) | nothing — auto |
| 10 | Auto-generate 6-digit PINs for 5 staff, write to `.staff_pins_first_login.txt` | nothing — auto |
| 11 | Create user-scope Windows Startup shortcut | nothing — auto |
| 12 | Verify (config / SumatraPDF / print queues / printer ping) | nothing — auto |
| 13 | Print summary with file paths | — |

### HQ secrets source — auto-detected

The installer looks for HQ shared secrets (META_*, SUPABASE_*,
ANTHROPIC_API_KEY, ADMIN_PBKDF2_*, etc.) in this order:

1. `<repo>/hq-secrets.env` — explicit, HQ-shipped (gitignored)
2. `<repo>/.env` — existing repo config
3. `C:\printosky\.env`
4. `C:\printosky_watcher\.env`
5. `C:\PY\printosky\.env`

First match wins. If none found, the installer fails with clear
instructions to obtain `hq-secrets.env` from HQ.

## What the installer does NOT do

These are out of scope because they're one-time HQ ops, not per-store:

- Vercel project + env vars
- Supabase project + RLS policies + service-role key
- Meta WhatsApp Cloud API app
- Razorpay merchant account + webhook URL
- Cloudflare DNS / named tunnel
- Netlify deploy for the admin UI

For all of the above, HQ generates one `hq-secrets.env` file and
ships it to each new store. The installer copies its contents
verbatim into the new store's `.env`. See `install/INSTALL.md` for
the HQ-side prerequisites.

## Testing the installer on the office PC

The installer is fully idempotent. You can run it on an already-
configured PC and it will only act on what's missing. Concretely:

- If `store_config.json` exists with `store_id="OFFICE"` (this PC's
  current state), step 5 will ask before overwriting.
- If `.env` exists, step 6 will never touch it.
- `bootstrap_db.py` only adds tables that aren't there yet (every
  CREATE has `IF NOT EXISTS`).
- The SumatraPDF download in step 3 is skipped if the binary is
  already in place.

So `powershell -ExecutionPolicy Bypass -File install\bootstrap.ps1`
on this office PC should breeze through almost every step as a
no-op, then exit clean.

## Rolling back

The installer doesn't have an "undo" mode. If you want to start
fresh:

```powershell
# Remove the per-store config + .env + DB:
Remove-Item -Force store_config.json, .env
Remove-Item -Recurse -Force "C:\Printosky"
```

Then re-run `bootstrap.ps1`. (Folders + downloaded SumatraPDF stay
unless you delete them too.)
