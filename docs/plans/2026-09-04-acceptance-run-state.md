# Acceptance run — where it stands

Live state of the post-press acceptance run. **Phases 0–2 are done; Phase 3 is next.**
Read this first if you are picking the run up in a fresh session.

The full plan (all nine phases, with expectations per step) lives in the
session artifact; the phases not yet run are summarised at the bottom of this
file in enough detail to carry on without it.

---

## Rules that govern the whole run

* **Rule 1 — nothing already working may change.** A spec or body without a new
  field must produce byte-identical output. *Absent means unchanged.* This is
  what Phase 3 exists to prove.
* **Fail loud** (CLAUDE.md hard rule). No silent failures anywhere. A log line,
  an empty table or a green dot is not an alert — use `ops_watchdog.report()` /
  `guard()`. `tests/test_fail_loud_rule.py` ratchets `except Exception: pass`.
* **Baked geometry (Rule 2).** All page geometry is written into the PDF, never
  requested of the driver. The KONICA MINOLTA 1100 PS driver silently ignores
  per-job duplex/simplex overrides in both directions.
* **Locked area — confirm twice before touching:**
  `print_server._konica_queue_for_sides()` and its two call sites, the
  `logging.basicConfig()` placement at the top of `print_server.py`, and
  `tools/nup_final_test.py`'s 2-page-per-combo design.
* Store PCs run whatever they last pulled. `PULL_UPDATE.bat` then
  **`RESTART_WATCHER.bat`** — `app_version` is captured at process start, so a
  pull alone does not change the running code.

## A pattern worth keeping

Three separate faults this run were **a green light computed over nothing**:

* `konica_result_codes.py`'s import fallback reported "everything mapped".
* `test_undeterminable_dirtiness_is_not_reported_as_clean` asserted the clean
  rendering — it passed on the bug its own name warned about.
* `scale_proof` printed "Every check passed" over two failed builds, then
  offered to send 0 jobs.

Before trusting a pass, ask what it was computed *from*, and make the tool
decline to answer rather than reassure.

---

## Store state (2026-09-04)

| Store | Host | Version | Notes |
|---|---|---|---|
| OSP · Thriprayar | DESKTOP-3NJM40G | `main@1d68526`, **clean** | restarted 09:18, ready |
| PRINTK · Nattika | DESKTOP-MMGVTNI | `main@126d2ff+dirty` | offline since ~18:20 on 09-03 |
| PRIOFF · Office | DESKTOP-SFO6ES9 | `main@126d2ff+dirty` | offline since ~18:20 on 09-03 |

* PRINTK and PRIOFF need pull + restart when switched on. **PRINTK is required
  for Phases 4 and 6.**
* A fourth `store_devices` row (PRINTK / DESKTOP-SFO6ES9, last seen 19 Aug) is
  the office box's **old identity** — it was reconfigured from PRINTK to
  PRIOFF. Stale row, not a missing machine. Safe to delete.
* OSP's `+dirty` cleared after #113, which confirms the dirtiness was untracked
  stray files, not hand-patched code.

---

## Phases 0–2: done

**Phase 0** — all boxes on current code, health checks clean.

**Phase 1 — the rate card.** Verified against what the panel actually *posts*,
not against hand-built meta. That distinction found the bug: five of ten
service kinds sent a quantity `calculate_service_quote` never reads.

**Phase 2 — scaling on paper. VERIFIED, OSP Konica, 2026-09-04.** All eight
combinations printed and checked; checks 3 and 4 (A5 at Actual vs the same at
Fit) came off the printer visibly different. `docs/PRINT_ROTATION_MATRIX.md`
records it.

### Merged this run

| PR | What |
|---|---|
| #109 | Konica result codes; the March CSV repair tool |
| #110 | `print_end` checked against bracketing evidence rather than trusted |
| #111 | Five order-v2 service kinds priced on numbers nobody could type |
| #112 | Actual size on a landscape sheet ran the page off the paper |
| #113 | `+dirty` fired on stray files, so it stopped meaning hand-patched |
| #114 | The scaling proof checks the PDFs before anyone commits paper |
| #115 | "Every check passed" over zero sheets |
| #116 | Sheets labelled with their test id; `--duplex` |

---

## Phase 3 — NEXT. The regression that matters most

Ten steps of work touched shared code: the rate card, the payment webhook, the
job row, the sync. The governing rule throughout was that **an ordinary print
job must behave exactly as it did before.** This is the test that proves it,
and it is worth more than any feature check.

Costs paper. Run at OSP.

| # | Do | Expect |
|---|---|---|
| P3-1 | Send a normal PDF through WhatsApp, as a customer | Job in console, quote as before, pickup code issued |
| P3-2 | Pay it, let the store puller take it | Paid → pulled → auto-printed, no manual step |
| P3-3 | Compare the sheet with one printed before this week | Identical. No scaling applied to a job that never asked |
| **P3-4** | **Konica duplex job, then simplex, back to back** | **Each on the sides it asked for** |
| P3-5 | Counter job from the counter PC (local print) | Prints without going to the cloud at all |
| P3-6 | A 2-up and a 4-up job | Imposed correctly, portrait sheet, page order right |

**Do P3-4 first.** It is the locked area: the driver silently ignores per-job
duplex/simplex overrides in both directions, and the dual-queue workaround is
what makes it work. If it fails, say so **before anything is changed** — a
confident fix has already gone wrong here once.

### P3-3 is pre-settled in code

Eleven ordinary job shapes — no scale block anywhere — were planned under
`126d2ff` and under current `main` and compared on sheets, sides, orientation,
`scale_applied` and rendered pixels. **Identical, all eleven.** Still worth one
sheet on paper, but as confirmation rather than an open question.

---

## Phases 4–9, in brief

* **Phase 4 — services at the counter** (free). Put `ZZTEST` in every customer
  name. Both consoles' *+ New Job* must open order-v2 staff mode. Book pouch
  lamination 6×A4 cash ₹420; confirm it never reaches a printer (structural:
  no `file_url`, and the puller only takes rows that have one). Photocopy
  10×3 B&W A4 = ₹90; repeat typing ₹70 and check the note reads "staff set
  Rs.70 over the quoted Rs.90". Photocopies **do** count as Konica work;
  lamination does not. Soft binding: outsourced at Oxygen, in-house at Nattika.
* **Phase 5 — online booking, item not in hand** (sends WhatsApp; use your own
  number). Public page must not offer photocopy, DTP or "Other", and must
  refuse a booking with no phone. *Notify Ready* stays disabled until *Item
  received*; pressing it twice keeps the **original** timestamp.
* **Phase 6 — sending work to the other shop** (needs PRINTK). Money splits at
  the moment of sending. Forward only: sent → at_finisher → returned. A job
  cannot be sent to its own store. With nothing out, the panel is **hidden**,
  not showing "0 jobs".
* **Phase 7 — real money** (live Razorpay, cannot be undone by deleting a row).
  A3 scanning 40 sheets = ₹800, deposit ₹400. **P7-5 is the one to watch:**
  re-check the amount after two minutes — Razorpay fires two events per
  payment, and ₹800 there means it was counted twice. Payments must
  *accumulate*, not replace.
* **Phase 8 — what the numbers say** (free). MIS showed February–March data for
  five months and looked plausible throughout. Today/Week/Month/Year must give
  four **different** numbers. The copy/scan reconciliation gap is real, not a
  panel fault.
* **Phase 9 — clearing up** (free). **Cancel, never delete** — a deleted job is
  one nobody can explain later. Baseline before the run: 481 jobs all time,
  4 today, ₹0 revenue today, 0 service jobs ever, 2 photocopy jobs ever,
  0 open drop-offs, 0 out for finishing, 14,992 Konica rows.

---

## Open items, none blocking Phase 3

* **The March CSV repair has not been applied.** `tools/konica_repair_march_import.py`
  on OSP: 528 undated rows, 22,376 pages recoverable. The dry run now
  corroborates each `print_end` against the bracketing evidence, so its output
  decides the 13 `print_end` rows rather than a judgement call. Run it, read
  the split, then `--apply`.
* Intermittent lease timeouts on all boxes (Phase 0 finding, unaddressed).
* PRIOFF is configured with OSP's `konica_ip` (192.168.55.110).
* Supabase Realtime not delivering to `store_puller` — jobs can wait up to 15
  minutes for the poll fallback.
* `ops_watchdog` alerts raised by **command-line tools** cannot send: the CLI
  shell has no Meta credentials. Console banners and `/health` still show them.
  Meta credentials are only required on PRINTOFF.
* Rates never given, all with working defaults: stamp / postcard / 4×6 photo,
  ID-card lamination, the OSP→Nattika internal rate.
