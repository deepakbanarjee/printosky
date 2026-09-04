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
| OSP · Thriprayar | DESKTOP-3NJM40G | `main@efb42b1`, **clean** | live, both leases held, ready |
| PRINTK · Nattika | DESKTOP-MMGVTNI | `main@126d2ff+dirty` | offline since ~18:20 on 09-03 |
| PRIOFF · Office | DESKTOP-SFO6ES9 | `main@126d2ff+dirty` | offline since ~18:20 on 09-03 |

* Read live from `store_devices` at 10:22 IST on 09-04. OSP renewed both
  `store_role_leases` (`poll_printers`, `fetch_epson_log`) minutes before, so it
  is polling, not merely up.
* OSP is one commit behind `main` (`f32ff7f`, #116). That commit touches only
  `docs/`, `tests/` and `tools/scale_proof.py` — **no runtime code** — so it does
  not change what Phase 3 exercises. Pull and restart anyway so the run is
  recorded against the tip rather than one short of it.
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

### Where P3-4 stands: attempted 2026-09-04 evening, NOT verified

A duplex/simplex pair was sent at the counter around 19:15 IST, just after OSP
closed. **Nothing about it reached the cloud and nothing has been confirmed.**
Start here tomorrow; do not record P3-4 as run.

* `jobs` still holds exactly **486** rows — the same count taken before the
  pair was sent — and the newest `received_at` is still the 09:22 walk-in.
* OSP stopped renewing at 13:26 UTC (18:56 IST): heartbeat stale, and both
  `store_role_leases` past their 3-minute TTL. That is closing time, not the
  intermittent lease fault in the open items below. Do not let this evening's
  expiry be read as evidence for that bug.
* A job created through the web / order-v2 path is written by the Vercel API
  and appears in `jobs` whatever the store PC is doing — that is how this
  morning's two web jobs got there. Nothing appeared, so the pair was created
  **locally**. If it printed, its rows are in `C:\Printosky\Data\jobs.db` and
  will push when the watcher next starts.

**Do not mistake the morning pair for this one.** `OSKY-20260904-5f9f-a669`
(`duplex` → `(Duplex)`) and `OSKY-20260904-ea6d-41de` (`single` → plain queue)
sit at the top of the table and read as a clean P3-4 result. They are ordinary
web jobs from 09:24 and 09:26, hours before P3-4 was attempted. They show the
routing works; they are not the test.

Tomorrow, in this order:

1. Start the box. `PULL_UPDATE.bat` then `RESTART_WATCHER.bat` — pending local
   rows survive the restart and push on start.
2. `logs/print_server.log` — `routing to konica_duplex queue` and `routing to
   konica_simplex queue`, one line each. Written by the function under test at
   the moment it decides, so this is better evidence than `jobs.printer`, and
   it needs no cloud. No lines means the pair never printed; re-send it.
3. The two sheets, if they are still by the printer. Sides are settled on
   paper and nowhere else.

### Before the paper: which queue simplex uses, and how OSP is wired

**Decided (2026-09-04): OSP's simplex queue is the original
`KONICA MINOLTA 1100 PS`.** Only duplex got a second Windows queue,
`KONICA MINOLTA 1100 PS (Duplex)`. The jobs are wired to that:

```json
"printer_queue_names": {
  "konica_duplex":  "KONICA MINOLTA 1100 PS (Duplex)",
  "konica_simplex": "KONICA MINOLTA 1100 PS"
}
```

That is a valid wiring, not a half-installed one. `_konica_queue_for_sides()`
only asks whether `PRINTERS[variant]` is set, never whether the two names
differ, so mapping `konica_simplex` back to the original queue routes simplex
there deliberately. `config/stores/OSP.store_config.json` now carries it — it
did not before, so a rebuild from the template would have dropped the duplex
queue in silence.

Say it explicitly even though leaving `konica_simplex` unset routes to exactly
the same queue: a fall-through is indistinguishable from nobody having
configured anything, which is precisely the confusion recorded below.

`jobs.printer` stores the queue a job actually reached, after
`_konica_queue_for_sides()` has chosen. Every Konica row ever written:

| Queue | Jobs | First | Last |
|---|---|---|---|
| `KONICA MINOLTA 1100 PS (Duplex)` | 3 | 2026-08-30 | 2026-09-04 09:24 |
| `KONICA MINOLTA 1100 PS` | 53 | 2026-08-11 | 2026-09-04 09:26 |
| `KONICA MINOLTA 1100 PS (Simplex)` | 0 — the name is not in use | — | — |

The first `(Duplex)` row is dated the day the fix went in, so duplex routing is
real and working. Two of this morning's web jobs went down both paths back to
back — `OSKY-20260904-5f9f-a669` (`sides: duplex`) to `(Duplex)`,
`OSKY-20260904-ea6d-41de` (`sides: single`) to the original queue.

**This table cannot tell you how the store is wired, and an earlier revision of
this file said it could.** A simplex job lands on `KONICA MINOLTA 1100 PS`
whether `konica_simplex` names that queue or is unset entirely — same row,
either way — so "`(Simplex)` never appears" was read as "the simplex half was
never installed" when it supports no such conclusion. One more green light
computed over nothing, this time in the file that warns about them. What does
distinguish the two, on the box:

* `http://localhost:3005/status` → `printers` — is there a `konica_simplex` key?
  **Checked on the box, 2026-09-04: it is there.** The wiring is what this
  section describes, and `config/stores/OSP.store_config.json` now matches the
  machine.
* the print_server log: `routing to konica_simplex queue for sides=...` is
  written only when the variant resolves. No line, no wiring.

What the wiring cannot settle either way is the **queue's persisted Printing
Preferences default**. The original queue serves simplex only while its default
is 1-sided; a preference silently moving is the exact fault the dual-queue
workaround exists to survive, and it is visible from no code, database or
console. It also means a job that names no sides at all lands on the same queue
and so comes out 1-sided. P3-4 is the test of that checkbox as much as of the
routing.

After the pair is sent, `jobs.printer` proves where each was *routed*. Only the
sheet proves how many sides came out. Do not let the first stand in for the
second. If the simplex sheet comes out 2-sided, the fix is **not** to touch
`_konica_queue_for_sides()` — it is that queue's default, or a separate
`(Simplex)` queue per `install/INSTALL.md`.

### Baseline, taken 10:22 IST before P3-4

486 jobs all time · 3 today · ₹9 collected today · 1 service job ever
(`OSKY-20260903-1688`, `other`, ₹499, still `Queued` — created during this run,
and one for Phase 9 to cancel rather than delete).

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
* **`jobs.received_at` is written in two different timezones.** Found while
  waiting on the P3-4 pair, 2026-09-04. Both writers call naive
  `datetime.now()` — `watcher.py:574` on the store PC, so IST, and
  `db_cloud.insert_job_from_webhook()` (`db_cloud.py:356`) on Vercel, so UTC.
  Same column, no offset stored; `SCHEMA.md` calls it "ISO-8601 string (legacy
  from SQLite)" and names no zone. Today's rows show it: the walk-in reads
  `09:22:52` and the two web jobs `03:54`/`03:56`, minutes apart in reality and
  5½ hours apart in the column. One row currently reads **1h16m in the future**
  against UTC now, which is the cheap way to see it.
  * **Affects P3-1**, whose job arrives through the WhatsApp webhook: expect
    its `received_at` to read ~5½ hours behind the counter clock. The quote,
    the pickup code and the print are unaffected — this is the timestamp only.
  * **Affects Phase 8.** `_sd_jobs_range()` (`api/index.py:2694-2701`) bounds
    that column with plain string comparison and no normalisation. For
    shop-hours jobs the date still lands right and only the clock reads wrong;
    a cloud-written job received between 00:00 and 05:30 IST is stamped the
    previous day and counted there. Some already exist.
  * **Do not fix during the run.** It is shared code on the payment/job path,
    and Phase 3 exists to prove that path unchanged. Note it, finish the run,
    fix it in its own PR — and decide the column's zone once, rather than
    patching whichever caller is in front of you.
* Intermittent lease timeouts on all boxes (Phase 0 finding, unaddressed).
* PRIOFF is configured with OSP's `konica_ip` (192.168.55.110).
* Supabase Realtime not delivering to `store_puller` — jobs can wait up to 15
  minutes for the poll fallback.
* `ops_watchdog` alerts raised by **command-line tools** cannot send: the CLI
  shell has no Meta credentials. Console banners and `/health` still show them.
  Meta credentials are only required on PRINTOFF.
* Rates never given, all with working defaults: stamp / postcard / 4×6 photo,
  ID-card lamination, the OSP→Nattika internal rate.
