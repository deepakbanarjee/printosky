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
| OSP · Thriprayar | DESKTOP-3NJM40G | `main@f32ff7f`, **clean** | live |
| PRINTK · Nattika | DESKTOP-MMGVTNI | `main@f32ff7f`, **clean** | live |
| PRIOFF · Office | DESKTOP-SFO6ES9 | `main@f32ff7f`, **clean** | live |

**2026-09-05: all three boxes on the tip, all clean, all heartbeating within
three minutes.** First time in the run this has been true — Phase 0 claimed it,
but PRINTK and PRIOFF were two commits behind and `+dirty` at the time.
**Phases 4 and 6 are unblocked.**

* Read live from `store_devices`. The `+dirty` markers cleared on all three
  once each box pulled — the dirtiness was untracked stray files, as #113 said,
  not hand-patched code on three separate machines.
* ~~A fourth `store_devices` row (PRINTK / DESKTOP-SFO6ES9, last seen 19 Aug)~~
  **deleted 2026-09-05.** It was the office box's old identity, not a missing
  machine: same `device_id` (`DESKTOP-SFO6ES9-5bead75e`), and the timestamps
  show the handover — the PRINTK identity ran 17:36 to 17:52 on 19 Aug and the
  PRIOFF one on the same machine began at 17:56, four minutes later. Nothing
  referenced it: the only leases naming that device are PRIOFF's, both live.
  `store_devices` is now three rows, one per box.
  *Noted on the way past:* the deleted row's `app_version_since` (29 Aug) was
  **later than its `last_seen`** (19 Aug) — something advanced that column for a
  device that had not reported in ten days. Harmless here, but if the heartbeat
  upsert can move `app_version_since` without `last_seen`, the "version since"
  reading is not trustworthy on any row.
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
| ~~**P3-4**~~ ✅ | **Konica duplex job, then simplex, back to back** | **VERIFIED on paper 09-05** — S7 simplex 4 sheets one side, S8 duplex 2 sheets both sides |
| **P3-5** ❌ | **Counter job from the counter PC (local print)** | **FAILS — 5 of 6 attempts fell back to the cloud, silently** |
| P3-6 ✅ | A 2-up and a 4-up job | Imposed and printed correctly — but via the cloud, having fallen back (see P3-5) |

**Do P3-4 first.** It is the locked area: the driver silently ignores per-job
duplex/simplex overrides in both directions, and the dual-queue workaround is
what makes it work. If it fails, say so **before anything is changed** — a
confident fix has already gone wrong here once.

### P3-4 — VERIFIED on paper, OSP Konica, 2026-09-05

The 2026-09-04 evening attempt never happened — no cloud rows, no local rows
(`collect_jobs()` pushes every local job unfiltered), and no `routing to konica`
line in `logs/print_server.log` after 09:54 that morning. Three signals, one
answer: it never reached `send_to_printer`. Re-run at 09:31 on 09-05, on
`main@f32ff7f`, as `scale_proof --only S7 S8 --send --printer konica`.

**The routing half is settled, by the print command itself:**

```
S7  routing to konica_simplex queue for sides='ss'
    -print-to KONICA MINOLTA 1100 PS           -print-settings ...,simplex,...
S8  routing to konica_duplex queue for sides='ds'
    -print-to KONICA MINOLTA 1100 PS (Duplex)  -print-settings ...,duplexlong,...
```

Better evidence than `jobs.printer`, which records the same fact after the
event: this is the queue name and the sides setting in the argv actually handed
to SumatraPDF. The same run printed the resolved map —
`PRINTERS overridden by store_config: {'konica_duplex': 'KONICA MINOLTA 1100 PS
(Duplex)', 'konica_simplex': 'KONICA MINOLTA 1100 PS'}` — which confirms
`config/stores/OSP.store_config.json` against the machine rather than against a
description of it.

**The paper agrees.** Both jobs are the same 4-page source, sent seconds apart:

| | Asked for | Expected | Came off as |
|---|---|---|---|
| S7 | simplex | 4 sheets, one side each | **4 sheets, one side** ✅ |
| S8 | duplex | 2 sheets, both sides | **2 sheets, both sides** ✅ |

That is P3-4 met: a duplex job and a simplex job, back to back on the same
Konica, each on the sides it asked for. The driver is the thing this whole
workaround exists to distrust, and the sheet counts are the only place its
obedience can be read. Counted, not inferred.

The one thing the counts do not speak to is S8's backs registering with its
fronts — that is S8's *scaling* criterion, checked in Phase 2 on 09-04, not a
sides question. Recorded so nobody later reads this pass as covering it.

**Do not mistake the morning pair for this one.** `OSKY-20260904-5f9f-a669`
(`duplex` → `(Duplex)`) and `OSKY-20260904-ea6d-41de` (`single` → plain queue)
sit at the top of the jobs table and read as a clean P3-4 result. They are
ordinary web jobs from 09:24 and 09:26 on 09-04. They show the routing works;
they are not the test.

### P3-1 met, P3-2 FAILED — the puller cannot see a WhatsApp job

> **Superseded in part, 2026-09-09.** The heading was right about the symptom
> and wrong about the cause. `assigned_store_id` was blocker #1 and the backfill
> cleared it; the puller then saw, claimed and downloaded both jobs, and the
> *print* failed. See "2026-09-09, the puller log" below. Kept as written —
> absent means unchanged.

`OSKY-20260905-2033-1326-e8974b`, 2026-09-05. P3-1 passed: quote ₹3 (the A4 B&W
rate), pickup code `P-KK46`, paid by Razorpay (`pay_TYE8IRvTBEAiG5`). P3-2 did
not: 40 minutes after payment the job was still `Paid` with `printer`,
`completed_at`, `printed_by` and `pickup_ready_at` all null, well past the
15-minute poll fallback.

**The Konica's own log is the independent proof.** `konica_jobs` was current to
10:06 IST and holds nothing with that filename anywhere after 09:25. Not a
sync artefact, not a lag: the sheet was never printed. The same log corroborates
P3-4 from the machine's side — `SCALEPROOF-S7` and `-S8`, 4 pages each, `No
Error`, seconds apart.

Two separate blockers, and they are not the same age:

1. **`assigned_store_id` is NULL — because it is set behind a feature flag that
   has never been on.** `store_puller.fetch_assigned_paid()` filters
   `.eq("assigned_store_id", store_id)` (`store_puller.py:149`) and a NULL never
   matches, so the puller has never been able to see a job on this path.

   The only writer for a WhatsApp job is `db_cloud.update_job_paid()`
   (`db_cloud.py:203-218`), inside

   ```python
   if os.environ.get("MULTISTORE_ROUTING_ENABLED", "").lower() in ("1","true","yes"):
       ...
       if decision.chosen_store_id:
           update_payload["assigned_store_id"] = decision.chosen_store_id
   ```

   **It has never executed.** `_routing_record()` is called unconditionally
   inside that block, before a store is chosen, and `routing_decisions` holds
   **0 rows**. The flag is off on Vercel, and with it off a paid WhatsApp job
   never becomes printable.

   The order-v2 path does not use the flag at all — `handlers_order.py` stamps
   `assigned_store_id` **directly at creation** (lines 135, 331, 427, 645, 811),
   with the comment at line 187 saying so: *"we stamp assigned_store_id directly
   (no distance/capacity engine)"*. That is the whole difference:

   | Path | `assigned_store_id` | Jobs | Printed |
   |---|---|---|---|
   | `web` → OSP | stamped at creation | 72 | 62 |
   | `web` → PRINTK | stamped at creation | 40 | 33 |
   | WhatsApp webhook | flag-gated, never set | 104 | **0** |

   Most of those 104 were never paid, so were never eligible anyway — but of
   the three that *were* paid, none could be pulled, and none was. **Six weeks
   old, not a regression** — Rule 1 holds, nothing this week broke it. A flag
   added as a revert switch quietly took one customer path's printability with
   it, and the flag being off is indistinguishable from the feature working.
2. **`file_url` is an empty string** (length 0) on today's job. The two July
   rows carry real 132- and 143-character Storage URLs, so this one is
   *different from* the old fault, not another instance of it. `select_pullable`
   requires a non-empty `file_url` too, so the job is blocked twice over — and
   more to the point, **the customer was quoted, charged ₹3 and given a pickup
   code for a file the cloud never stored.** Whether the store PC's hot folder
   got its own copy is not visible from here; the puller reads this row, and
   this row has nothing.

Not fixed during the run — `store_puller` and the webhook are the path Phase 3
exists to hold still. But (2) is a paying customer with no file, which is a
different clock from the run's.

### Why `file_url` was empty: three silent failures in a row

Diagnosed 2026-09-05. The WhatsApp media path is deliberately two-phase
(`api/index.py:1266-1299`): insert the job with `file_url=""` and answer the
customer immediately, then download from Meta, compress, upload to Storage and
upsert the real URL. An empty `file_url` means phase two did not finish.

**It did not finish, and nothing said so.** Every step on that path swallows its
own failure:

1. `_download_meta_media()` (`api/index.py:98-119`) — any exception, or a
   missing download URL: `logger.error(...)` then `return None`. A log line is
   not an alert; CLAUDE.md says so in as many words.
2. `upload_file()` (`db_cloud.py:423-437`) — any exception: `logger.error(...)`
   then **`return ""`**. It hands the caller an empty string where a URL
   belongs, so a failed upload is indistinguishable from a successful one. This
   is the same shape as the ₹0 rate-card bugs in the BILLING FIX section:
   *failing to the cheapest thing instead of failing loud*.
3. The call site writes that value into the row **without checking it**, so
   `""` overwrites the placeholder and the job looks finished.

And the order compounds it: the receipt and the first quote question go out in
**phase one**, before the file is fetched. So the bot quotes, takes ₹3 and
issues a pickup code for a file the system may never obtain — which is exactly
what happened.

**What the evidence supports.** `storage.objects` holds nothing under the
`918943232033_20260905_*` prefix: the upload never happened. The same document
is in the bucket from **21 July at 13.8 MB**, so the file is large, and large is
the obvious suspect against the 55-second download timeout — but Vercel's Hobby
plan refused the log query for that window (`ExceedsBillingLimitError`), so
**which** of the three fired is not recoverable. Naming one would be a guess.

**Fix direction, after the run:**

* `upload_file()` must not return `""` on failure — raise, or return `None`, so
  a caller cannot mistake failure for a URL.
* Both handlers report through `ops_watchdog` instead of `logger.error`.
* Structural: a job with no `file_url` must not be quotable or payable. An
  instant receipt is fine; the *quote* is what should wait on the file. That is
  the change that stops a customer paying for a file that does not exist.

**For this customer, now:** ask them to resend, or use the 21 July copy of the
same document already in the bucket.

### P3-5 FAILS — the counter path falls back to the cloud, silently

Five of six `/local-print` attempts across 09-04 and 09-06 did not print
locally. They fell back to the cloud round trip the feature exists to remove,
and nothing anywhere said so.

`C:\Printosky\Jobs\Local\` — written by `handle_local_print` and by nothing
else — holds six files. Each cloud row lands 2-5 seconds after its file:

| File (IST) | Outcome |
|---|---|
| 09-04 09:22:52 | ✅ **local** — `OSKY-20260904-0001`, `Walk-in`, filepath set, no `file_url` |
| 09-04 09:24:44 | ❌ fell back → `…5f9f-a669`, `web`, `file_url` set, filepath null |
| 09-04 09:26:35 | ❌ fell back → `…ea6d-41de` |
| 09-06 12:53:38 | ❌ fell back → `…e855-69a9` |
| 09-06 12:55:17 | ❌ fell back → `…082a-7cb9` |
| 09-06 12:55:44 | ❌ fell back → `…bcac-8c61` |

The mechanism is `website/order/order-ui.js:973-977`:

```js
} catch (e) {
  // Never block a counter job on the local path — fall back to the cloud.
  console.warn('local print unavailable, falling back to upload:', e);
  return null;
}
```

The file is written first, so `handle_local_print` was entered and got past auth
every time; then `handle_create_job` returned without a `job_id`, the JS threw
on `if (!data.job_id)`, and the job became an ordinary cloud upload. **The
operator cannot tell.** The only trace is a `console.warn` in a browser console
nobody has open — not `print_server.log`, not `ops_watchdog`, not the job row.
The orphaned file stays in `Jobs\Local\` as the sole physical evidence.

So CLAUDE.md's "a counter job prints from the counter PC without going to the
cloud at all" inverts itself under failure, and the fallback comment says why in
its own words: *never block a counter job on the local path*. Not blocking was
right. Not saying anything was not.

**Corrects the record.** On 09-05 this file called `OSKY-20260904-5f9f-a669` and
`…ea6d-41de` "ordinary web jobs from 09:24 and 09:26" and warned against
mistaking them for the P3-4 pair. They were not ordinary web jobs — they were
failed counter jobs. The P3-4 point drawn from them still stands (their duplex
and simplex routing was correct); their provenance was wrong.

Likewise today's three jobs, recorded above as P3-6 evidence: the imposition
result stands, but they reached the printer through the cloud, not the counter.

**Root cause: the counter job fails locally exactly when the customer pays.**

`website/order/order-ui.js:956-962` builds the `/local-print` body with

```js
amount_collected: 0,                       // always
override_reason: mode === 'hold' ? 'Counter job — payment on collection' : '',
```

and `handle_create_job` (`print_server.py:1935-1937`) guards with

```python
paid = amount_collected > 0 or amount_partial > 0
if not paid and not override_reason:
    return {"ok": False, "error": "Payment or override reason required"}
```

`amount_collected` is hardcoded to `0`, and `override_reason` is set **only**
for `hold`. So choosing **Cash or UPI** — the operator saying money changed
hands — sends "paid by Cash, amount zero, no reason", the guard correctly
refuses it, and the job falls back to the cloud. Choosing **hold** sets an
override reason and the local print succeeds.

Backwards: the counter path works only when payment is *deferred*, and breaks
whenever it is *taken*. That matches the six attempts — 09-04 09:22 succeeded
(booked on hold), the five that took payment did not. It also explains why the
fault reads as intermittent: it tracks which button the operator pressed.

**Predicted, then tested 2026-09-06 13:22. Confirmed, both ways:**

| | Job | Source | `file_url` | `filepath` | Route |
|---|---|---|---|---|---|
| **hold** | `OSKY-20260906-0001` | `Walk-in` | none | `…\Jobs\Local\132232_option 5.pdf` | **local** ✅ |
| **cash** | `OSKY-20260906-4805-5175` | `web` | set | null | **fell back** ✅ |

Thirty-one seconds apart, same operator, same counter, opposite paths — and
both printed, which is why nobody ever noticed. The id shape alone gives it
away: `_next_job_id()` issues a 4-digit daily sequence for a counter job, while
a cloud job carries a hex suffix.

The earlier three jobs all read `payment_mode: cash` with `amount_collected: 3`,
confirming they were Cash counter jobs that fell back too — 5 of 6, as the file
timestamps said.

One thing the test settles beyond the diagnosis: **the cloud fallback records
the money correctly** (`amount_collected: 3`, `payment_mode: cash`) while the
local hold job carries `amount_collected: null`. So the fix must send the real
amount, not merely satisfy the guard — otherwise a working local cash job would
file every counter sale at ₹0 and under-report the till.

*And on the same screen, the timezone split in one glance:* the local row reads
`13:22:32` (store PC, IST) and the cloud row `07:53:03` (Vercel, UTC). Thirty-one
seconds apart in the shop, five and a half hours apart in the column.

Three things kept it invisible, and each is worth fixing on its own:

1. `amount_collected: 0` is sent alongside `payment_mode: 'Cash'` — the client
   already knows the quote, since it sends it as `amount_quoted`.
2. `order-ui.js:970` throws `new Error('no job id')` and **discards
   `data.error`**, so even the browser console never shows the server's reason.
3. Nothing on that server path logs. `print_server.log` holds the 10:53:54
   startup banner and nothing since — not a logging fault (the `basicConfig`
   placement CLAUDE.md protects is correct and the override line proves it ran),
   but a path that reports a refusal to nobody.

And the two paths disagree: the cloud fallback has no such guard, so the
identical unpaid-looking job succeeds through the cloud. One of them is wrong
about what a counter job must carry.

### Backfill: the three paid WhatsApp jobs assigned to OSP

Done 2026-09-08 01:51 IST, on the owner's instruction and with the consequence
stated first. `assigned_store_id` set to `OSP` on the only three paid jobs that
had none:

| Job | Received | Paid | Pickup | Has a file? |
|---|---|---|---|---|
| `OSP-20260725-3907-3022-a51d62` | 25 Jul | ₹10 | `P-ZCCK` | yes |
| `OSP-20260725-3907-0058-e918fc` | 25 Jul | ₹3 | `P-3PSU` | yes |
| `OSKY-20260905-2033-1326-e8974b` | 05 Sep | ₹3 | `P-KK46` | **no — empty `file_url`** |

Scoped on `razorpay_payment_id IS NOT NULL AND assigned_store_id IS NULL AND
status='Paid' AND store_id='OSP'`, so it could not reach an unpaid job or
another store's. **`paid_without_store` is now 0.**

**This doubles as the end-to-end proof of P3-2.** If the two July jobs print
when OSP next starts, `assigned_store_id` was the whole blocker and the
diagnosis is confirmed on paper rather than by reading code. If they do not,
something else is wrong and this run is not finished with the puller.

They will not print tonight: OSP was last seen **364 minutes ago** with no live
lease — the shop is closed. Expect two sheets shortly after the box comes up,
for customers who paid in July. Worth a word to whoever opens.

The third cannot print whatever happens: its `file_url` is empty, which is the
separate webhook fault recorded above. It needs the customer to resend, or the
21 July copy of the same document already in the bucket.

### 2026-09-08 check-in: the backfill did NOT make them print

Checked at 17:31 IST. The two July jobs still read `Paid`, with `printer`,
`completed_at`, `printed_by` and `pickup_ready_at` all null — now *with*
`assigned_store_id = 'OSP'` set. So **`assigned_store_id` was not the whole
blocker**, or the puller never got a fair run at them. The evidence does not yet
separate those two, and it matters which.

**What is established:**

* OSP renewed both leases up to **09:48 IST** and heartbeated at **09:50**, so
  it was alive and working this morning. `konica_jobs` holds 48 rows for today,
  fetched up to 09:38.
* `store_puller` polls every **900 s** and is *deliberately* not gated on store
  hours (`store_puller.py:269` — "docs/FAIL_LOUD.md rejects an hours
  construction"). So a process alive for a quarter of an hour polls at least
  once.
* The backfill landed at 01:51 IST. OSP was off overnight and came up some time
  before 09:48; **how long it ran is not visible from the cloud** — `last_seen`
  and lease `updated_at` keep only the newest value, not a history.

**What that leaves.** `select_pullable()` has exactly three conditions: not in
`pulled_ids`, status in `("Paid",)`, non-empty `file_url`. These jobs satisfy
the last two plainly. **The remaining candidate is `pulled_ids`** — the local
`pulled_jobs` table on the OSP PC, which `record_pulled()` writes to mean "never
download this again". If those two ids are in it, they are excluded for good, no
matter what `assigned_store_id` says.

That would be its own fault worth having: a job recorded as pulled that never
printed is unreachable by design and invisible from the cloud.

**Two checks at the box settle it**, and neither needs the run:

```powershell
python -c "import sqlite3; c=sqlite3.connect(r'C:\Printosky\Data\jobs.db'); print(c.execute('SELECT job_id, pulled_at FROM pulled_jobs WHERE job_id LIKE ''OSP-20260725%''').fetchall())"
findstr /C:"store_puller" /C:"pulled" logs\store_puller.log
```

Present in `pulled_jobs` → that is the second blocker, and the backfill was
necessary but not sufficient. Absent → the puller had no real run this morning,
and this test is simply not finished.

### 2026-09-09, the puller log: the backfill DID work, and three faults behind it

`logs/store_puller.log` from the OSP box settles the question above and
disproves my `pulled_jobs` hypothesis — the user's query returned 63 rows with
**no July ids among them**. The jobs were never excluded. They were *selected,
claimed, downloaded, and handed to the printer*, and the print failed.

```
2026-09-08 09:52 IST
  store_puller: pulled OSP-20260725-3907-0058-e918fc -> ...\Jobs\Assigned\... (43966 bytes)
  PrintFile: file: '...\Temp\OSP-20260725-3907-0058-e918fc.docx',
             printer: 'KONICA MINOLTA 1100 PS'
  cannot recognize version marker
  trying to repair broken xref / repairing PDF document / no objects found
  Error: Couldn't open file '...e918fc.docx' for printing
  Finished printing, exitCode: 1
  store_puller: ...e918fc did not print — leaving un-recorded to retry next poll
```

**So `assigned_store_id` was blocker #1 and the backfill cleared it.** P3-2's
first cause is confirmed fixed. What sat underneath it is three separate faults.

**Fault A — nothing converts a non-PDF before printing, and no gate says so.**
The two jobs are `nithya coverpage.docx` and `919446903907_20260725_063022.jpg`.
SumatraPDF is handed the file as-is; it tries to parse the `.docx` as a PDF
("cannot recognize version marker", "no objects found") and exits 1.
`watcher.py:657` *does* convert Word/PPT to PDF — but only to count pages, into
`tempfile.gettempdir()`, and deletes it in the `finally`. Nothing converts for
the print itself. Every Word doc and every WhatsApp photo that reaches the
puller is an unprintable job.

This is a **permanent** failure, and `pull_once` has only one failure mode:
transient. "leaving un-recorded to retry next poll" is the right answer for a
printer-busy or disk-full failure and the wrong one for a file that will never
print, on this attempt or any other.

**Fault B — the realtime subscription turns a failed print into a hot spin.**
That warning repeats roughly **once per second** from 09:52:16 through
09:52:52+, each cycle re-downloading the same 43966 bytes and re-launching
SumatraPDF. It is not waiting for the 900 s poll, and the reason is a loop that
feeds itself:

```
claim_job()   -> UPDATE jobs SET print_claimed_at=...      (device_lease.py:334)
                    ↳ realtime filter assigned_store_id=eq.OSP matches
                    ↳ _on_change -> _wake_event.set()      (store_puller.py:191)
print fails   -> _unclaim() -> another UPDATE -> another wake
_wake_event.wait(POLL_SECONDS) returns IMMEDIATELY          (store_puller.py:704)
-> next cycle, at once
```

The puller's own bookkeeping writes are changes on the table it subscribes to.
Nothing distinguishes "a job was paid" from "I just wrote to this row". A single
unprintable file therefore pins one core, re-downloads a file every second, and
writes two Supabase updates a second, indefinitely. This immediately precedes
OSP going silent — last heartbeat 09:50, leases last renewed 09:48, the spin
starts 09:52.

**Fault C — the claim never expires, and the log misnames who holds it.** After
the 09-09 restart both jobs are skipped permanently:

```
2026-09-09 07:56:39,548 reconcile — reset 1 stranded Paid job(s) for retry: ...a51d62
2026-09-09 07:56:39,925 ...a51d62 is already claimed by another box — skipping
2026-09-09 07:56:40,123 ...e918fc is already claimed by another box — skipping
```
…every ~5 minutes through 10:09. From the cloud:

| job_id | print_claimed_at (UTC) | print_claimed_by |
|---|---|---|
| `OSP-20260725-3907-3022-a51d62` | 2026-09-08 02:26:38 | `DESKTOP-3NJM40G-34fa500a` |
| `OSP-20260725-3907-0058-e918fc` | 2026-09-08 04:22:52 | `DESKTOP-3NJM40G-34fa500a` |

`DESKTOP-3NJM40G` **is OSP**. There is no other box — PRINTK and PRIOFF do not
serve this store. The claim is the box's own, left behind when the spinning
process died mid-cycle between `claim_job()` and `_unclaim()`. `claim_job()`
requires `print_claimed_at IS NULL` (`device_lease.py:336`) and **nothing ages a
claim out**; `release_job()` only clears a claim it can match to its own
`device_id`, and it is never reached because the claim fails first. A crash
between those two lines makes a paid job unprintable forever.

`reconcile_stranded()` is the startup recovery for exactly this shape of
failure, and it does not cover it — it deletes `pulled_jobs` rows only, never
touches `print_claimed_at`. That is why the 07:56 reset is followed 380 ms later
by the skip.

And the message is a false statement of fact: "already claimed by another box"
when the row says this box. It sent me looking for a second box for a day.

**None of this alerted.** `auto_print()` returns `False` on a failed
`send_to_printer` with a `logger.warning` and no `ops_watchdog.report()`
(`store_puller.py:451`, `:456`); so does `pull_once` at `:594`. A customer's paid
job can fail to print, spin the box for an hour, strand its own claim and be
skipped every five minutes for a day, and the only trace is a log file somebody
has to think to open. CLAUDE.md's hard rule, verbatim: *a log line is not an
alert.* This is the rule's own subject matter — the print step — going unwatched.

Also visible, and separate: `store_puller.missing_print_spec` is firing for both
— "no print_spec or missing sides value — using safe default (single-sided)".
Correct behaviour for a July row that predates the field; noted so it is not
read later as a new fault.

**What this changes for P3-2.** The step is still failed, but the cause is now
three named bugs rather than one open question, and two of them (B and C) can
bite any job, not just these two. Fixes, in the order they matter:

1. **C** — expire a claim, or reclaim one's own: a claim older than N minutes,
   or held by `device_id()` itself, must be takeable. Fix the log line to name
   the holder. Have `reconcile_stranded()` clear this box's own stale claims at
   startup. *Without this, nothing else can be tested — the jobs are frozen.*
2. **B** — do not let the puller's own writes wake it. Ignore a realtime payload
   whose change is `print_claimed_at`/`print_claimed_by`, and floor the retry
   interval so a failing job cannot be retried faster than the poll.
3. **A** — convert Word/PPT/images to PDF before printing (the watcher already
   has the Word path), and separate permanent failure from transient: a file
   SumatraPDF cannot open must alert and stop retrying, not loop.
4. Alert on a failed auto-print at all — `ops_watchdog.report()` in both places.

**Done — branch `claude/stale-print-claim`, off `main`.** Fixes C, B and 4:

* `claim_job()` accepts a claim older than `CLAIM_TTL_SECONDS` (900 s) as well
  as a null one, still as one atomic conditional UPDATE — the loser of a race
  re-checks its WHERE against the row the winner just wrote, so exactly-once
  survives. Tested both ways round: a fresh claim stays exclusive, and a third
  box racing for an expired one still does not print.
* The skip message reads the row and names the holder.
* `store_puller` releases claims bearing this device's id at startup and alerts
  that the previous run died mid-print (`store_puller.stale_claim`).
* A failed print is held back one poll interval, doubling, capped at an hour —
  so the self-inflicted realtime wake finds nothing to do.
* `store_puller.autoprint` alerts on a failed print and reports recovery.
* Ratchets: `claim_job` may never filter on NULL alone; the reconcile query may
  never stop selecting the claim columns (dropping them would make the recovery
  a no-op reporting "no claims left over" — the pattern this file is named for).

147 tests green in `test_store_puller` / `test_device_lease` /
`test_print_retry_pacing` / `test_fail_loud_rule` / `test_autoprint_e2e`.

**Do NOT clear the two stale claims by hand yet.** The OSP box is running
`f32ff7f`, which is the spinning code. Freeing those claims before that box has
pulled this branch just restarts the 1/s loop on a file that still cannot print.
Order: merge → `PULL_UPDATE.bat` at OSP → restart the watcher (which releases
the claims itself, and says so) → then fault A.

**Fault A — done, branch `claude/print-non-pdf-files`, stacked on
`claude/stale-print-claim`.** New `printable.py` in front of every print:
`store_puller.auto_print()` before the planner, and the top of
`print_server.send_to_printer()` for the staff and counter paths (the locked
Konica routing is untouched — the hook sits after it). A PDF passes straight
through with no existence check, so `send_to_printer` keeps its `Jobs\Archive`
fallback. Images become one fitted page, aspect kept, turning the *sheet* for a
landscape photo. Word/PPT/Excel export through the installed application.

A file that cannot be converted at all is **permanent**: set aside, not retried,
and alerted as `store_puller.unprintable` — a different alert from
`store_puller.autoprint`'s "the printer was busy", because they send whoever
reads them to different places. In memory only, so a restart gives it one more
chance.

3074 tests pass; 22 new ones, driving real raster files rather than stubs, and
including the OSP `.docx` itself on a box with no Word — which must come back
marked, never handed to SumatraPDF.

### 2026-09-09 09:52 IST check-in — still not printed, as expected

All three boxes are up and on `main@5cf6df2`, heartbeating within four minutes —
first time since 09-06. Both July jobs are still `Paid`, `printer`,
`completed_at`, `printed_by` and `pickup_ready_at` all null, and
`print_claimed_at` unchanged since 09-08: the puller is skipping them every five
minutes exactly as the log analysis says it must.

The check-in that fired this morning offered the `pulled_jobs` hypothesis as the
branch to take if they had not printed. That branch is dead — the box's own
query returned 63 rows with no July ids — and the log answered the question
instead. Noted here so the instruction is not followed later by someone reading
only the check-in.

**Order to land these.** Both branches are off `main`; the second is stacked on
the first.

1. `claude/stale-print-claim` — the claim TTL, the honest skip message, the
   startup release, the backoff, the alerts.
2. `claude/print-non-pdf-files` — the conversion and the permanent-failure
   split.
3. `PULL_UPDATE.bat` at OSP, restart the watcher. It releases its own two stale
   claims at startup and alerts that it did.
4. Then the two July jobs print: the `.jpg` certainly, the `.docx` if Word is
   installed on that box — and if it is not, the alert will say so by name
   instead of failing every poll in silence.

Only after 3 is it safe to clear those claims by hand, and after 3 there is no
need to.

### The boxes are down, which is the more urgent finding

| Store | Last seen (IST) | Ago | Version |
|---|---|---|---|
| PRIOFF | 2026-09-08 17:31 | **now** | `main@497ff63` |
| **OSP** | 2026-09-08 09:50 | **7.7 h** | `main@f32ff7f` |
| **PRINTK** | 2026-09-06 19:39 | **45.9 h** | `main@f32ff7f` |

It is a Tuesday afternoon. OSP has been silent since mid-morning and PRINTK
since Sunday evening, and nothing said so — the console health banner needs a
console open, and the alerts these boxes would raise are raised *by* the boxes.
Two days ago this file recorded all three on the tip and heartbeating within
three minutes, "first time in the run this has been true". It lasted a day.

`main` has also moved four commits past `f32ff7f` (#117, #118, #119 and a
marketing feature) — none of them this run's, and OSP is still on `f32ff7f`, so
the P3-2 test premise held. PRIOFF has auto-pulled `497ff63`, which is
`SETUP_AUTOSTART.bat` working as designed.

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
* **`print_planner.scale_actual_landscape` can go red but never green.**
  Found 2026-09-05 during the P3-4 re-run, which raised it:
  *"STILL FAILING — 24.0 h"*. The alert itself is correct and by design — S7
  asks for Actual size on a landscape A4 sheet, which cannot fit, so the
  imposer fits it at 66% and says so rather than shrinking the customer's job
  in silence. The defect is the mechanism, not the message:
  * `print_planner.py:269` is the **only** call site for that check name, and
    it always passes `ok=False`. `ops_watchdog.report()` clears a check only on
    an explicit `ok=True` (`ops_watchdog.py:314`). So the check latches on the
    first landscape-Actual job the store ever prints and stays red for good,
    re-alerting every 6 hours.
  * Both console health banners, `/status` and `/health` therefore read
    unhealthy for a store where nothing is wrong. **That is a live risk to the
    rest of this run**: a genuine alert raised during P3-1…P3-6 arrives on a
    banner that is already red, and nobody looks twice at a light that has been
    on for a day. It is the mirror of the green-light-over-nothing pattern at
    the top of this file, and it corrodes the alerting just as fast.
  * The tests pin that it fires on a downgrade and stays quiet on a page that
    fits (`tests/test_print_planner_scale.py:243-268`). Neither pins recovery,
    so the latch is unintended rather than decided.
  * Diagnosis: a **per-job event modelled as a system health state**. Nothing a
    later job does can repair "that job got downgraded", so no future report can
    ever clear it. It belongs against the job — its notes, its console row — or
    as a one-shot notice, not in the latching health set.
  * **Not fixed during the run**: `print_planner` is the path Phase 3 exists to
    prove unchanged. Same rule as `received_at` above. Note which checks are
    already red (`/health`) before starting P3-1, so a new one can be told apart.
* **Do not merge the `handlers_notes` import fix on its own — the flow it wakes
  sends dicts to a text sender.** `handlers_notes` builds full Meta payloads
  (`_send_text()` returns `{"messaging_product": ..., "text": {"body": ...}}`),
  and `api/index.py` dispatches them with `whatsapp_notify._send(phone,
  message)`, which takes plain TEXT and builds its own payload — putting the
  dict in the message body. The same applies to the inline dict fallbacks at
  `api/index.py:1080-1086` and `:1257`.
  It has never shown because the import has never resolved, so the flow has
  never run. **Fixing the import turns it on.** Checked before claiming it:
  `book_bot` returns *strings* and sends its own interactive messages directly,
  so the live book flow is unaffected — this is confined to notes.
  Either fix the dispatch (send by shape: `str` → `_send`, `type: text` →
  `_send(body)`, `type: interactive` → `_post_interactive`) or hold the import
  fix. Not built here: it is a dormant feature, and the live faults come first.
* Intermittent lease timeouts on all boxes (Phase 0 finding, unaddressed).
* PRIOFF is configured with OSP's `konica_ip` (192.168.55.110).
* Supabase Realtime not delivering to `store_puller` — jobs can wait up to 15
  minutes for the poll fallback.
* `ops_watchdog` alerts raised by **command-line tools** cannot send: the CLI
  shell has no Meta credentials. Console banners and `/health` still show them.
  Meta credentials are only required on PRINTOFF.
* Rates never given, all with working defaults: stamp / postcard / 4×6 photo,
  ID-card lamination, the OSP→Nattika internal rate.
