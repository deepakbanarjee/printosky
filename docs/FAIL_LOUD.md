# Fail loud

## The rule

**If something that is expected to be working stops working, a human is told.**

A log line is not "told" — nobody reads the store PC's console. An empty table is
not "told" — it looks exactly like a quiet day. A green dot is not "told".

This is a hard rule. It applies to every layer: store PC threads, the Vercel API,
the WhatsApp bot, and the admin consoles. New code that can fail in a way nobody
would notice is not finished.

## Why (Nattika, 11–18 August 2026)

Nattika's Epson moved to a new IP. For seven days the store's printer pipeline
was dead and **not one alert fired**. Every layer behaved reasonably:

| Layer | What it did | Why nobody knew |
|---|---|---|
| `printer_poller` | TCP probe failed | An unreachable printer out of hours is normal, so the cycle was skipped — for a week |
| `epson_jobs_fetcher` | Gave up after 3 failures | "Disabled for this session"; a store PC runs for weeks |
| `supabase_sync` | `get_sync_status()` existed | Nothing ever called it |
| `print_server` | `/health` knew the printer was unreachable | No console ever read `/health` |
| Cloud cron | Watched store PC liveness | Only for `STORE_PC_MONITOR_ID` (OSP), and Nattika's PC was *up* anyway — it was the printers that were dead |
| `admin.html` | Showed 🟢 Live and zero rows | The dot only means "Supabase answered"; the empty state said "No records for this period" |

Six safety nets, no net. The failure was found by the owner happening to look.

## How to comply

Use `ops_watchdog`. Two calls cover almost everything.

**A thing you can test:**

```python
from ops_watchdog import report

report("printer.epson", reachable,
       f"reachable at {ip}" if reachable else f"UNREACHABLE at {ip} — powered off, or the IP changed")
```

**A thing that can throw** — this is the replacement for `except Exception: pass`:

```python
from ops_watchdog import guard

with guard("epson.weblog"):              # alerts, then re-raises
    rows = fetch_weblog()

with guard("supabase.upload", reraise=False):   # alerts, then continues
    upload(file)
```

Rules of thumb:

- **Name checks `subsystem.thing`** — `printer.epson`, `sync.supabase`,
  `counters.konica`. The name is the dedup key and what the console displays, so
  keep it stable.
- **Put the fix in the detail string.** "UNREACHABLE at 192.168.1.201 — powered
  off, or the IP changed (check the printer panel, then store_config.json)" is
  worth ten "operation failed"s.
- **Report success too.** A check that only ever reports failure can never
  recover, so nobody learns it came back.
- **Never let reporting break the caller.** `ops_watchdog` swallows its own
  errors; keep it that way.

### What the watchdog does with it

| Event | Behaviour |
|---|---|
| First failure | Alerts immediately — no sustain window, no store-hours gate (owner's call, 2026-08-18) |
| Still failing | Re-alerts every `OPS_ALERT_REPEAT_HOURS` (default 6), not every cycle |
| Recovered | Announced once |
| Store PC restarts | State is in SQLite (`ops_health`) — no re-spam, no forgotten outage |
| Alert channel down | Failure is still recorded and still shows in `/health` and the console |

Alerts go to the ops WhatsApp number via `whatsapp_notify.send_staff_alert`.

### Environment knobs

| Variable | Default | Effect |
|---|---|---|
| `OPS_ALERT_REPEAT_HOURS` | `6` | Re-alert cadence while a check stays broken. `0` = one alert per outage |
| `OPS_ALERT_QUIET_HOURS` | *(off)* | e.g. `21-8` holds failure alerts overnight when printers are legitimately off. Held, not dropped: it fires on the first check after the window. Recoveries are never held |
| `OPS_ALERTS_ENABLED` | `1` | `0` on a dev box: keeps the bookkeeping, sends nothing |
| `STORE_PC_MONITOR_IDS` | `OSP,PRINTK,PRIOFF` | Stores the cloud cron watches |
| `STORE_COUNTER_STALE_MIN` | `180` | Minutes of frozen printer counters before the cloud calls a live store PC's pipeline dead |
| `STORE_PC_NO_PRINTER_IDS` | `PRIOFF` | Boxes with no printers of their own — liveness only, never a frozen-counter alert. Never list a store that HAS printers: silence about a real printer is the failure this all exists to prevent |

## Where the checks live

**On the store PC** (`ops_watchdog`, surfaced via `print_server /health` and
`/status`):

| Check | Fires when |
|---|---|
| `printer.konica` / `printer.epson` | The printer does not answer on its configured IP |
| `counters.konica` / `counters.epson` | Polled fine but returned no page counters |
| `poller.cycle` | The poll loop itself threw |
| `epson.weblog` | The Epson job log cannot be fetched (per-job colour tracking is down) |
| `epson.delta` | SNMP delta attribution threw |
| `fetcher.epson` | The fetch loop itself threw |
| `sync.supabase` | A sync cycle failed, or Supabase is not configured — i.e. the console is now stale |
| `config.epson_ip` | `epson_ip` missing from `store_config.json` |
| `store_puller.realtime` / `academic_worker.realtime` / `transcription_worker.realtime` | The Supabase Realtime subscription could not be established, or a live one died — pickup is back on the 15-minute fallback poll until `realtime_liveness.hold` rebuilds it (recovery is announced) |
| `store_puller.realtime_delivery` / `academic_worker.realtime_delivery` / `transcription_worker.realtime_delivery` | The subscription is connected but a job was still found by the fallback poll — events are not being delivered (Realtime not enabled on the table) |
| `transcription_worker.job` | A manuscript transcription failed. Nothing retries a `failed` row, so this is the only signal before someone opens the DTP console |

**In the cloud** (`/cron/store-pc-check`, per store in `STORE_PC_MONITOR_IDS`) —
this is the layer that still works when the store PC is the thing that died:

- PC offline / back online (heartbeat = latest `daily_summary.synced_at`).
- **PC up but printer counters frozen.** The Nattika case: heartbeat green,
  pipeline dead. Liveness alone would have reported "fine" all week.

**In the consoles** (`admin.html`, `jobs.html` via `admin-shared.js`), on every
load and on every location switch — a banner at the top of the page for:

- the store PC not answering,
- any failing watchdog check, with how long it has been failing,
- printer data older than `HEALTH_STALE_MIN` (3 h) for the location on screen.

The sync dot now reads **"Loaded · HH:MM"**, because that is all it ever knew.
Empty tables point at the banner instead of reassuring the reader.

## Enforcement

`tests/test_fail_loud_rule.py` ratchets the number of `except Exception: pass`
handlers per file: it may go down, never up. Adding one fails the suite. The fix
is `guard(...)`; if the swallow is genuinely correct (closing a handle, an
optional import), raise that file's budget in the same commit and say why.

The 80-odd existing handlers are *not* all audited. The ratchet stops the bleeding;
work them down when you are next in the file.
