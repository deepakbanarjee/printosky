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

## PRIOFF's own address vs. its printer's

The Epson IP in the table above (`192.168.1.250`) is the *printer's* address,
shared with PRINTK. The PRIOFF **box itself** is a separate machine and has its
own address on the same LAN — as of 2026-08-19 it's a static
`192.168.1.240:3005`. That's what the admin/jobs consoles' `storePcUrl`
(⚙ PC Setup) must point at for that machine, and it isn't tracked by
`store_config.py` (the LAN-exempt rule matches on declared `store_id`, not on
this box's own IP) — so if it changes again, update it here.

## Two rules that are easy to get wrong

**1. `poll_printers` is a veto, not the mechanism.**

Which box polls is decided at runtime by a lease, so every PC in a store can run
identical software and exactly one of them polls — see
[docs/MULTI_BOX.md](../../docs/MULTI_BOX.md). Leave `poll_printers: true`
(the default) unless a specific machine must *never* touch the printers; PRIOFF
ships with `false` because it shares the counter's Epson and there is no reason
for the office box to compete for that work.

Before leases, both Nattika boxes polled the same Epson: every printer job
imported twice (all 388 of PRINTK's Epson job rows were also PRIOFF's) and two
sets of page counters for one printer.

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
