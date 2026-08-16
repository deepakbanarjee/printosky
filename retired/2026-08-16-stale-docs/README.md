# Retired 2026-08-16 — stale store checklists

Two operational checklists that had drifted far enough from reality to be
actively misleading at the counter. Both were still sitting in the repo root
where they read as current. Parked here rather than deleted: the *structure* of
the Sunday-maintenance checklist is worth reusing, and the April task list is a
useful record of what the store looked like pre-migration.

The live replacement is [`STORE_SETUP_CHECKLIST.md`](../../STORE_SETUP_CHECKLIST.md).

## What's here

| File | Original location | Why retired |
|---|---|---|
| `STORE_CHECKLIST_TODAY.html` | repo root | Last true in April 2026. Instructed staff to ping the **retired** Epson at `192.168.55.202`, to run the long-since-applied `SCHEMA_v3_migration.sql`, and to fix the "Oxygen PC URL" (C2, closed since April). Its closing line — "then Sprint 8 starts immediately" — refers to a sprint that finished in May. |
| `TASKS_2026-04-13.md` | repo root | A dated one-day task list. Every open item on it is either done or has been carried into `SPRINT_BACKLOG.md` / `docs/OWNER_ACTIONS.md`. Retained references to `192.168.55.202` as the live Epson. |

## Carried forward, not lost

The only items on either file that were still genuinely open on 2026-08-16:

| Item | Now tracked in |
|---|---|
| Change the Epson web-panel default password (`admin`/`admin`) | `STORE_SETUP_CHECKLIST.md` §B3, `docs/FEATURE_PIPELINE.md` SEC4, `docs/SECURITY.md` |
| Verify LibreOffice / PyMuPDF / pikepdf / reportlab present on the store PC | `STORE_SETUP_CHECKLIST.md` §F |
| Run the test suite on the store PC and report pass/fail | `STORE_SETUP_CHECKLIST.md` §F |
| Printer reachability pings | `STORE_SETUP_CHECKLIST.md` §C |

Everything else on both files was verified done before retiring them.

## Why the IP matters

The WF-C21000 at `192.168.55.202` was replaced by the **EM-C8100 at
`192.168.55.214`** on 2026-06-29 (`store_config.py`, `docs/ARCHITECTURE.md`).
Any doc still naming `.202` as the live Epson sends staff to a dead address.
The same commit that retires these files sweeps the remaining `.202` references
out of live code — see `epson_jobs_fetcher.py`, the four `epson_*.py` diagnostic
scripts, `install/INSTALL.md` and `SCHEMA.sql`.
