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
- **Netlify** (`website/`, branch `main`): admin + jobs consoles, order-v2, marketing site
- **Supabase**: cloud DB mirror + academic orders + storage

Everything deploys from `main` on push — **except the store PCs**, which keep
running whatever they last pulled (`PULL_UPDATE.bat` + restart the watcher).

Full detail → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
Schema reference (28 tables, owners, columns) → [docs/SCHEMA.md](docs/SCHEMA.md)

## Many PCs per store
Boxes coordinate at runtime, not by per-machine config: a **lease** picks the one
box that polls the printers, and an atomic **claim** (`jobs.print_claimed_at`)
makes printing exactly-once. A counter job prints from the counter PC without
going to the cloud at all (`print_server /local-print`).

Design, failure modes and how to add a box → [docs/MULTI_BOX.md](docs/MULTI_BOX.md)

## Hard rule: fail loud
**If something is not working as expected, alert. No silent failures — anywhere.**

A log line, an empty table or a green dot is not an alert. Use `ops_watchdog`:

```python
from ops_watchdog import report, guard
report("printer.epson", ok, f"UNREACHABLE at {ip} — powered off, or the IP changed")
with guard("epson.weblog"):        # exception -> alert (use reraise=False to continue)
    rows = fetch_weblog()
```

First failure alerts immediately; repeats every 6h; recovery is announced. Health
shows on `print_server /health`, `/status`, and as a banner on the admin and jobs
consoles. `tests/test_fail_loud_rule.py` fails the build if a new
`except Exception: pass` appears.

Full contract, check list and knobs → [docs/FAIL_LOUD.md](docs/FAIL_LOUD.md)

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
