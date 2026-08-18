# Many PCs per store

Every store ends up with more than one machine: a counter PC, a second counter,
an office box. They all run the same agent, so by default they all do the same
work — poll the printers, import the printer's job log, pull paid jobs down and
print them.

That is how Nattika stored **388 printer jobs twice** (both boxes scraping one
Epson), and it is how two boxes eventually print the same paid job twice — on
paper, in front of the customer.

Configuring each box by hand does not scale and drifts the moment a PC is
reimaged. So boxes coordinate at runtime through the one thing they already
share: Supabase.

## Two primitives

### Role lease — for work that only one box should do

`store_role_leases` holds one row per `(store_id, role)`. Every box calls
`device_lease.hold(role)` on each cycle. Exactly one gets `True`; the rest stand
by. The lease carries a TTL (`DEVICE_LEASE_TTL_SECONDS`, default 180 s) and the
holder renews it as it works, so if that box is switched off another takes over
within one TTL — with nobody touching a config file.

| Role | Work |
|---|---|
| `poll_printers` | SNMP/web page counters and supply levels |
| `fetch_epson_log` | Importing the Epson's own job history |
| `print_jobs` | (reserved) pulling paid jobs down to print |

```python
from device_lease import hold, ROLE_POLL_PRINTERS

if hold(ROLE_POLL_PRINTERS):
    poll_the_printers()      # I am this store's poller right now
```

Take-over is a compare-and-set on the row as it was read, so two boxes racing for
an expired lease cannot both win.

### Job claim — for work that must happen exactly once

Printing is not idempotent. Before a job is downloaded or printed, the box claims
it:

```python
if device_lease.claim_job(job_id):
    print_it()
else:
    pass                      # another box at this store already has it
```

The claim is an atomic conditional update — `print_claimed_at IS NULL` — so of
any number of boxes trying at the same instant, exactly one gets a row back. A
failed print calls `release_job()` so the retry can proceed.

`pulled_jobs`, the old guard, is a **local** SQLite table: it stops one box
pulling a job twice and can say nothing about the box next to it. The claim is
what makes printing exactly-once.

## What happens when Supabase is unreachable

Deliberately different for each, because the costs are not symmetric:

| | Behaviour | Why |
|---|---|---|
| Lease | Falls back to the local `poll_printers` flag (default true) | Polling twice is a data annoyance; polling never is a blind store |
| Claim | **Fails closed** — no claim, no print, retry next cycle | Wasted paper cannot be undone. A store that cannot reach Supabase cannot see paid jobs anyway, so this costs nothing offline |

Both report to `ops_watchdog`, so a coordination failure is an alert rather than
a mystery (`docs/FAIL_LOUD.md`).

## Config still wins when you want it to

`poll_printers: false` in `store_config.json` is a hard local veto — that box
never polls, lease or no lease. Use it for a machine that must never touch the
printers. Leave it `true` (the default) everywhere else and let the lease sort
it out.

## Adding a box to a store

Install the agent, drop in the store's `store_config.json` from
`config/stores/`, start it. Nothing else. It registers in `store_devices`,
competes for leases, and either picks up work or stands by. Pull the plug on any
box and its work moves within a TTL.

Each machine keeps a stable `device_id` in `device_id.txt` next to its SQLite DB
(override with `PRINTOSKY_DEVICE_ID`), so leases survive restarts and the console
can say *which* box is doing what.

## Local-first printing

A walk-in printed at the counter used to travel:

```
browser → Supabase Storage → jobs row with file_url → the store PC's puller
        → downloads it back to that same PC → prints
```

A round trip through the internet for a file that never leaves the room, and
counter printing that stopped whenever the line did.

Now, when the browser **is** the fulfilling store's PC (⚙ PC Setup done, and its
`store_id` matches the job's), the order console POSTs the bytes straight to
`print_server /local-print`, which saves them under `Jobs\Local`, creates the job
and prints it. If anything about that is unavailable it silently falls back to
the old cloud path, so a roaming box or an office machine still works.

The job record still syncs to the cloud, so the console sees it. It carries **no
`file_url`** — which is also what stops the puller printing it a second time
(`select_pullable` requires one).

The file is written to `Jobs\Local`, never the hot folder: a drop there would
trigger watcher intake, creating a second job and WhatsApping a quote to a
customer already standing at the counter paying for it.
