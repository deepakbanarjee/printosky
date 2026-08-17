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

## Printing / N-up imposition
**Read [docs/PRINT_IMPOSITION.md](docs/PRINT_IMPOSITION.md) before touching
`nup_imposer.py`, `print_planner.py`, or the print path in `print_server.py`.**
It carries the rules and, more importantly, the models that were tried and are
wrong. Short version:
- Every imposed sheet is **portrait**; landscape layouts are composed transposed.
- A **portrait page is never rotated** — it reads without turning the sheet.
- The printer is only ever told `paper=<size>, duplexlong`. Nothing else.
- One constant absorbs the printer's back-side behaviour:
  `store_config.duplex_back_rotation` (0 or 180, currently **180**).

```bash
python tools/nup_doctor.py --nup 2      # what this PC would actually print
python tools/nup_doctor.py --calibrate  # A/B a printer in one duplex print
```
Run the doctor before spending paper. If the doctor is right and the paper is
wrong, it's the printer — calibrate, don't change code.

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
Store-only tasks (needs physical access) → [STORE_SETUP_CHECKLIST.md](STORE_SETUP_CHECKLIST.md)
Owner/dashboard tasks → [docs/OWNER_ACTIONS.md](docs/OWNER_ACTIONS.md)

## Security & Config
See [docs/SECURITY.md](docs/SECURITY.md) · [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#environment-variables)
