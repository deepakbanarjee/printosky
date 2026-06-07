# Store-PC liveness alerts — setup

The owner gets a WhatsApp when the **store PC** (store_id `OSP`) comes up or goes
down, with a day log at closing, a weekly log on Saturday's shutdown, and a
monthly log on the last working day of the month.

## How it works

- The store PC already pushes `daily_summary.synced_at` to Supabase every ~5 min
  (the **heartbeat**).
- A GitHub Actions cron hits **`/cron/store-pc-check`** every 30 min. It compares
  the heartbeat to now (IST):
  - heartbeat fresh again after a gap → **opening** message
  - heartbeat stale (> `STORE_PC_OFFLINE_MIN`, default 20 min) → **closing**
    message + day log (+ weekly on Sat, + monthly on the last working day)
- This catches **every** way the PC goes down — clean shutdown, crash, power
  cut, internet loss — because it watches for the heartbeat *stopping*, not for a
  shutdown event.

**Latency:** the closing message arrives ~20–50 min after the PC actually goes
off (heartbeat threshold + 30-min cron cadence). That's the trade for catching
ungraceful shutdowns. Tighten `STORE_PC_OFFLINE_MIN` / the cron interval if you
want it faster.

Nothing below is required for the alerts to work — the heartbeat watcher is
self-contained. The shutdown ping only adds the **"closed cleanly" vs "went
offline unexpectedly"** wording.

## Optional: clean-shutdown ping (store PC)

`tools/store_pc_shutdown_ping.bat` curls `/cron/pc-shutdown` as the PC powers
down so the closing message can say it was a clean shutdown.

### 1. Set CRON_SECRET on the store PC
Use the same value as the Vercel `CRON_SECRET` env var. As an admin:
```cmd
setx /M CRON_SECRET "the-same-secret-as-vercel"
```
(Re-login or reboot for the machine env var to take effect.)

### 2. Register it as a shutdown script (preferred — runs before network tears down)
1. `gpedit.msc` → **Computer Configuration → Windows Settings → Scripts
   (Startup/Shutdown) → Shutdown**
2. **Add** → Script Name: full path to `store_pc_shutdown_ping.bat`
3. OK. Windows runs it (and waits, up to its timeout) on every shutdown/restart.

### Alternative: Task Scheduler on shutdown event
If `gpedit` isn't available (Windows Home):
1. Task Scheduler → **Create Task** (run with highest privileges, run whether
   logged on or not).
2. **Triggers → New → On an event**: Log `System`, Source `User32`, Event ID
   `1074` (logged when a shutdown/restart is initiated).
3. **Actions → Start a program**: the `.bat` path.

> Note: a shutdown-time ping is inherently racy — the network may already be
> down. That's fine; it's best-effort. The heartbeat watcher is the reliable
> path and needs none of this.

## Test it

```cmd
REM should return {"store_id":"OSP","clean_shutdown":true}
curl -H "Authorization: Bearer %CRON_SECRET%" https://printosky.vercel.app/cron/pc-shutdown

REM run the watcher manually (Actions tab → workflow_dispatch, or:)
curl -H "Authorization: Bearer %CRON_SECRET%" https://printosky.vercel.app/cron/store-pc-check
```

## Config (Vercel env, optional)
- `STORE_PC_MONITOR_ID` — store_id to watch (default `OSP`).
- `STORE_PC_OFFLINE_MIN` — minutes of heartbeat silence before "down" (default 20).
- `OWNER_ALERT_PHONE` — already set; reused for these alerts.
