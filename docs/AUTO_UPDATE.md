# Store-PC auto-update

Lets a store PC pick up new Printosky code on its own, on a timer, so an
update doesn't need someone physically at the till or remoted in.

## How it works

- `SETUP_AUTO_UPDATE.bat` (run once, per PC) registers a Windows Task
  Scheduler task, `PrintoskyAutoUpdate`, that runs `AUTO_UPDATE.bat` every
  15 minutes.
- Each run: `git fetch origin`, compare the local commit to
  `origin/<branch this PC has checked out>`. Identical → exit immediately,
  nothing disrupted, nothing logged. Different → stop all Printosky
  processes (`taskkill python.exe`, `taskkill node.exe`, same as
  `STOP_PRINTOSKY.bat`), `git reset --hard origin/<branch>` (same as
  `PULL_UPDATE.bat`), then relaunch everything hidden via `START_SILENT.bat`.
- Every check (or at least every update) is appended to
  `logs\auto_update.log` with a timestamp and the before/after commit.

## Why this design

- **No new network exposure.** It only ever reaches *out* to GitHub over
  the connection the PC already uses for every other `git fetch` — nothing
  listens for an inbound trigger, so there is no new attack surface on a
  machine `docs/SECURITY.md` already flags a live risk on (SEC-OPEN-6,
  Supabase service_role key on the store PC).
- **The tradeoff:** not instant. A push to `main` can take up to 15 minutes
  to reach this PC (tune `/mo` in `SETUP_AUTO_UPDATE.bat` if you want it
  tighter — each check itself is cheap, just a `git fetch`). And a restart
  that lands mid-print interrupts it for the few seconds the processes take
  to come back up — there is no "wait for the current job to finish" check.
  Pick the interval/timing with that in mind (a quiet overnight window, or
  accept the occasional mid-shift blip).

## Setup

On the store PC, once:
```batch
SETUP_AUTO_UPDATE.bat
```
Right-click → Run as Administrator if it fails (Task Scheduler at `/rl
highest` needs elevation on some accounts).

## Undo

```batch
REMOVE_AUTO_UPDATE.bat
```
Goes back to needing `PULL_UPDATE.bat` run by hand.

## Check it's working

```cmd
schtasks /query /tn "PrintoskyAutoUpdate" /v /fo list
type logs\auto_update.log
```
