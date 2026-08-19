# Store-PC auto-update

A store PC picks up new Printosky code by itself, once a day, when it boots —
so an update doesn't need anyone at the till or remoted in.

## How it works

The boot chain, registered once per PC by `SETUP_AUTOSTART.bat`:

```
Windows login
  → boot_delay.vbs      (15s, waits for the network)
  → BOOT_PRINTOSKY.bat
      → AUTO_UPDATE.bat   git fetch + hard reset to origin/<branch>
      → START_SILENT.bat  every service, hidden, each to its own log
```

**Update before start, not stop-update-restart.** At boot nothing is running
yet, so there is nothing to kill and no restart to perform — the freshly
pulled code is simply what gets launched. That means this can never interrupt
a print, which a mid-day timer-based update could.

`AUTO_UPDATE.bat` therefore never touches processes. Run it by hand mid-day
and the new code lands on disk but does **not** go live — Python has already
loaded the old modules into memory. Reboot, or `STOP_PRINTOSKY.bat` +
`START_SILENT.bat`, to make it take effect.

## Where the detail goes

Everything runs silently, so the logs are the record:

| Log | What's in it |
|---|---|
| `logs\boot.log` | The boot sequence — when it ran, each step, exit codes |
| `logs\auto_update.log` | Every check: branch, before/after commit, or "already up to date" |
| `logs\watcher.log`, `print_server.log`, `store_puller.log`, … | One per service, as before |

**But a log is not an alert.** Per [FAIL_LOUD.md](FAIL_LOUD.md), the update
also reports to `ops_watchdog` as `store_pc.boot_update` — so a failed fetch
or reset (a store PC quietly running stale code) raises an alert instead of
waiting for someone to remember to open a log file. A successful boot is not
news and stays quiet.

## Latency

Up to one business day: a change pushed to `main` at noon reaches the store
PC the next morning when it boots. Fine for ordinary changes. For anything
urgent, the PC still needs a manual `PULL_UPDATE.bat` + restart, or a reboot.

## Setup

On the store PC, once:
```batch
SETUP_AUTOSTART.bat
```
Right-click → Run as Administrator if the registry write fails. This is the
same script that already set up auto-start; re-running it is safe and simply
repoints the boot chain at `BOOT_PRINTOSKY.bat`.

## Check it worked

On the PC itself:

```cmd
type logs\boot.log
type logs\auto_update.log
git log --oneline -3
STATUS_PRINTOSKY.bat
```

**Or from anywhere** — every box reports the commit it is running to
`store_devices.app_version` on each sync cycle (~5 min), so "is OSP on the
latest code?" no longer requires being in front of OSP:

```sql
select store_id, device_id, hostname, app_version, last_seen
from store_devices
order by last_seen desc;
```

`app_version` reads `main@a1b2c3d`, or `…+dirty` if that box has uncommitted
local edits — worth seeing before you debug why one machine behaves
differently. `unknown` means the version could not be read at all (not a git
checkout, or `.git` unreadable). See `app_version.py`.
