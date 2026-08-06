# Handoff — Full-Fidelity Auto-Printing

**Status:** live on `main`, deployed to Vercel · **Last updated:** 2026-08-05 · **Latest commit:** `c0fee1e`

The store-PC auto-print pipeline prints a paid order **exactly as the customer
specified** — duplex, N-up imposition, and mixed B&W/colour — with the sections
kept in document order, minimising human handling. This doc is the durable,
version-controlled record; an out-of-repo copy also lives in Antigravity's brain
folder.

---

## 1. How it works (end to end)

```
order-v2.html  →  api/handlers_order.py  →  jobs.print_spec (JSONB, Supabase)
   (customer picks settings + store)        (full per-job spec persisted)
                                                     │  Razorpay → status "Paid"
                                                     ▼
store_puller.py (store PC, polls 60s)  →  print_planner.plan_print_job()
   download file_url → Jobs/Assigned          → ordered list of print actions
                                                     ▼
   per action → print_server.send_to_printer() → SumatraPDF → Epson
```

- **`print_spec`** (JSONB on `jobs`, migration `api/migrations/SCHEMA_v35_autoprint_spec.sql`) carries
  `{sides, layout, nup, colour_pages, paper_size, orientation, copies, pages_included}`.
  Verified in production: the column exists and real order rows match the planner's keys.
- **`print_planner.py`** decomposes `(pdf, print_spec)` into ordered actions: slices
  `pages_included`, imposes N-up via `nup_imposer.py`, and splits **mixed** colour docs
  into consecutive same-mode sections so they spool pre-collated in order.
- **`nup_imposer.py`** — sequential N-up chunking (pages 1..N on sheet 1, etc.), duplex
  back-page slot mirroring, horizontal/vertical layout direction.
- **`store_puller.py` `auto_print()`** runs each action through `send_to_printer`.

## 2. Print-settings facts (SumatraPDF, verified)
- Valid `-print-settings` tokens: `<N>x`, `color`/`monochrome`, `duplex`/`duplexlong`/`simplex`,
  `paper=A4|A3|A5|legal|letter|…`, `portrait`/`landscape`, bare page ranges (`1-5,10`), `even`/`odd`.
- **No N-up token** → N-up is pre-imposed into the PDF by `nup_imposer.py`, then printed 1-up.
- `paper=`/duplex are honoured by the Windows driver → tray/driver-dependent; also set the
  queue default paper to A4 as a backstop.

## 3. Key files
| File | Role |
|---|---|
| [print_planner.py](print_planner.py) | spec + PDF → ordered print actions (slice / impose / mixed-split) |
| [nup_imposer.py](nup_imposer.py) | N-up imposition engine (sequential, duplex-aware) |
| [store_puller.py](store_puller.py) | polls Supabase, downloads, runs the planner + prints |
| [print_server.py](print_server.py) | `send_to_printer` (SumatraPDF), `_sumatra_paper`, admin print path |
| [api/handlers_order.py](api/handlers_order.py) | writes `print_spec` on order create/reorder |
| [api/migrations/SCHEMA_v35_autoprint_spec.sql](api/migrations/SCHEMA_v35_autoprint_spec.sql) | adds `jobs.print_spec` |
| [tests/test_print_planner.py](tests/test_print_planner.py) · [tests/test_autoprint_e2e.py](tests/test_autoprint_e2e.py) · [tests/test_store_puller.py](tests/test_store_puller.py) | 43/43 print+order tests pass |

## 4. Correctness fixes (review pass, commit `c0fee1e`)
1. **False "Printed" status (fixed).** `send_to_printer` marked the job `Printed` on every
   spool, so a multi-section mixed job flipped to Printed after the *first* section — a later
   failure was invisible. `send_to_printer` now takes `update_status` (default `True`);
   `store_puller` sets it `True` only on the **final** sub-job, and the loop breaks on any
   failure, so a mid-order failure leaves the job un-marked for manual attention.
2. **Mixed jobs split across two printers (fixed).** B&W→Konica / colour→Epson routing put a
   mixed doc's sections in two trays on stores with a Konica. Mixed jobs now force **every**
   sub-job to the **Epson** (prints both), preserving one-tray order. Non-mixed jobs route normally.
3. **Temp-dir leak (fixed).** The planner's `temp_<job>` dir is now cleaned in a `finally`
   (even on failure). It never holds the original download, so that stays in `Jobs/Assigned`.

## 5. Still open
- **MEDIUM** — the raw frontend order `spec` is persisted verbatim as `print_spec`; the planner
  depends on those exact keys, so a frontend key rename would silently degrade to the `auto`
  fallback. Consider a normalize/validate step in `api/handlers_order.py` before persisting.
- **LOW** — ensure the store PC installs deps from `requirements.txt` (PyMuPDF is declared there;
  the older manual pip line in `CLAUDE.md` omits it).

## 6. Deploy / verify on a store PC
1. `git pull` on the store checkout; restart `START_PRINTOSKY.bat` (or `python store_puller.py`).
2. Preflight: `python -c "from print_server import send_to_printer"`; `Get-Printer` name vs `store_config.json`.
3. Place one live **mixed + duplex + N-up** order; confirm it prints in order on the Epson and
   the job is marked `Printed` exactly once, only after all sections spool.

> Note: the store-hardware E2E was reported successful by the implementing session; the review
> above was code-level + prod-data validation. Because the fixes changed dispatch behaviour
> (mixed→single printer, once-only status), re-run step 6 per store after pulling `c0fee1e`.
