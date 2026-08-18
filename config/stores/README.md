# Per-location store config

One file per machine. Copy the right one to the repo root as `store_config.json`
(gitignored) on that PC and restart the watcher — config is read once at process
start.

```batch
copy config\stores\PRINTK.store_config.json store_config.json
RESTART_WATCHER.bat
```

`store_config.py` also accepts `%PRINTOSKY_STORE_CONFIG%` (absolute path) or
`~/.printosky/store_config.json`. Delete the `_readme` key or leave it — unknown
keys are ignored.

## The machines

| File | Machine | Konica | Epson | Polls printers |
|---|---|---|---|---|
| `OSP.store_config.json` | Oxygen counter, Thriprayar | `192.168.55.110` | `192.168.55.214` | yes |
| `PRINTK.store_config.json` | Printosky counter, Nattika | none | `192.168.1.250` | yes |
| `PRIOFF.store_config.json` | Printosky office, Nattika | none | `192.168.1.250` | **no** |

## Two rules that are easy to get wrong

**1. Only one machine per physical printer sets `poll_printers: true`.**

Both Nattika boxes were polling the same Epson. The result: every printer job
imported twice (all 388 of PRINTK's Epson job rows are also PRIOFF's) and two
sets of page counters for one printer. The counter PC owns polling; the office
box sets `poll_printers: false` and simply doesn't run the poller or the Epson
job fetcher.

**2. The office box must declare `store_id: PRIOFF` itself.**

Any PC on `192.168.1.*` is forced to `PRINTK` by the LAN rule in
`store_config.py`, so a shop's machines cannot drift apart. `PRIOFF` is on the
exempt list — but only if the file says so. A Nattika machine with no config
falls back to the legacy Oxygen defaults and is then swept into `PRINTK`.

## No Konica

`konica_ip: null` is how a store says "finishing/collection only". It makes the
store PC route B&W to the Epson (`print_server._effective_printer_key`), stop
polling a printer that isn't there, report `has_konica: false` on `/status`, and
the admin and jobs consoles hide every Konica panel.

## After a change

Restart the watcher, then confirm on that PC:

```
curl http://localhost:3005/status     # store_id, has_konica, printer_ips, watchdog
curl http://localhost:3005/health     # every check, and what is failing
```

A wrong IP now alerts within one poll cycle instead of going quiet for a week —
see [docs/FAIL_LOUD.md](../../docs/FAIL_LOUD.md).
