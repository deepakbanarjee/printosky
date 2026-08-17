# CLAUDE.md

**Printosky** — print job management + billing for Oxygen Students Paradise, Thrissur.
WhatsApp → quote → Razorpay → print → done. Runs on a Windows store PC + Vercel API.

## Run
```batch
START_PRINTOSKY.bat
```
Manual start commands + full port map → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

## Architecture (at a glance)
- **Store PC**: `watcher.py` (file watch + threads), `print_server.py :3005` (staff auth + print), `whatsapp_capture/index.js :3001` (WhatsApp Web)
- **Vercel** (`api/index.py`, branch `main`): WhatsApp webhook, Razorpay webhook, staff PIN API, academic orders API
- **Supabase**: cloud DB mirror + academic orders + storage

Full detail → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
Schema reference (28 tables, owners, columns) → [docs/SCHEMA.md](docs/SCHEMA.md)

## Printing / imposition
Every imposed sheet is **portrait**; the printer is told `duplexlong` and no
orientation flag, for every layout. Landscape turns the content 90° and puts
page 1 at the bottom. **All 12 A4 combinations verified on paper — OSP Konica,
2026-08-17.** A3, A5, 9-up and the Nattika Epson are untested.

Rules, the full rotation matrix and the failure decoder →
[docs/PRINT_ROTATION_MATRIX.md](docs/PRINT_ROTATION_MATRIX.md)
Read it before touching `nup_imposer.py` or `print_planner.py`.

```bash
python tools/nup_matrix.py            # every combination, as sheets
python tools/proof_run.py FILE.pdf    # impose all 12; --send to print
```

## Key REPL Commands (`watcher.py`)
```
pending                              → list pending jobs
report                               → today's revenue
done OSP-YYYYMMDD-XXXX AMOUNT MODE  → mark complete (cash/upi)
```

## Staff CLI
```bash
python staff_setup.py seed | list | add | reset PIN
```

## Install
```bash
pip install watchdog gspread google-auth google-auth-oauthlib websockets requests pysnmp
cd whatsapp_capture && npm install
python staff_setup.py seed
```

## Pending Work
See [SPRINT_BACKLOG.md](SPRINT_BACKLOG.md)

## Security & Config
See [docs/SECURITY.md](docs/SECURITY.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#environment-variables)
