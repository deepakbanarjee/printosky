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

## What the installer does

| # | Step | Idempotent? |
|---|---|---|
| 1 | Verify Python / pip / Node.js / internet | yes |
| 2 | Create `C:\Printosky\Data`, `Jobs\Incoming`, `Jobs\Archive` | yes |
| 3 | Download SumatraPDF portable (~20 MB) to repo root | yes |
| 4 | `pip install -r requirements.txt` | yes |
| 5 | Interactively write `store_config.json` (skipped if exists) | yes |
| 6 | Copy `.env.example` → `.env`, auto-generate fresh `STORE_TOKEN` | yes (never overwrites existing `.env`) |
| 7 | Bootstrap SQLite schema (12 tables/views) | yes |
| 8 | Seed default staff PINs (optional) | yes |
| 9 | Verify config loads, SumatraPDF found, print queues + printer ping | yes |
| 10 | Print "what's next" reminder | — |

## What the installer does NOT do

These are out of scope because they're one-time HQ ops, not per-store:

- Vercel project + env vars
- Supabase project + RLS policies + service-role key
- Meta WhatsApp Cloud API app
- Razorpay merchant account + webhook URL
- Cloudflare DNS / named tunnel
- Netlify deploy for the admin UI

See `install/INSTALL.md` for the HQ-side prerequisites and where to
find each value the installer will ask you to paste in.

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
