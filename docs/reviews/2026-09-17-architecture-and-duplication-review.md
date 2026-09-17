# Printosky — Architecture, Duplication and Maintainability Review

**Review date:** 17 September 2026
**Repository:** deepakbanarjee/printosky
**Base commit:** `bb17c8a`
**Branch:** `claude/codebase-architecture-review-k3si73`
**Scope:** Code structure, duplication, performance and maintainability. Complements the
[2026-09-10 professional review](2026-09-10-professional-review.md), which covered security,
payments and operations; this one deliberately does not re-tread that ground.

**Status:** Analysis **and** changes. Four commits on the branch above. Every change is
behaviour-preserving except one bug fix, called out explicitly in §5. Verified against the
full suite: **3188 passing before, 3220 passing after, zero failures.**

---

## 1. Architecture summary

Printosky is a print-shop operating system split across three runtimes that do not share a
process, a language runtime, or a deployment cadence.

```
  CUSTOMER                    CLOUD (deploys from main on push)        STORE PC (pulls by hand)
  ────────                    ────────────────────────────────         ────────────────────────
  WhatsApp ──────────────────▶ api/index.py  (Vercel, one lambda)
                               ├─ /whatsapp-webhook   (HMAC)
                               │    └─▶ whatsapp_bot / book_bot / routing.intent
                               ├─ /webhook/razorpay   (HMAC)
                               ├─ /staff/* /admin/*   (PBKDF2 PIN)
                               ├─ /academic/* /order/* /notes/*
                               └─ /cron/*  (GitHub Actions, 8 schedules)
                                        │
  Razorpay ─────────────────────────────┤
                                        ▼
                                  Supabase ──── cloud mirror ────▶ store_puller.py
                                  (28 tables)                         │
  Netlify (website/)                    ▲                             ▼
  admin.html / jobs.html ───────────────┘                      watcher.py (threads)
  order-v2, marketing                                          ├─ print_server.py :3005
                                                               │    └─▶ SumatraPDF ─▶ Konica
                                                               │                    └▶ Epson
                                                               ├─ printer_poller.py (SNMP)
                                                               ├─ supabase_sync.py (5 min)
                                                               └─ whatsapp_capture :3001 (Node)
                                                                        │
                                                          SQLite  C:\Printosky\Data\jobs.db
```

**The load-bearing idea** is that the store PCs are semi-autonomous. Vercel and Netlify update
themselves within a minute of a merge; a store PC keeps running whatever it last pulled. Multiple
boxes per store coordinate at runtime rather than by per-machine config — a **lease**
(`device_lease.py`) elects the one box that polls printers, and an atomic **claim** on
`jobs.print_claimed_at` makes printing exactly-once. A counter job prints from the counter PC
without touching the cloud at all.

**The quality spine is `ops_watchdog`.** The stated hard rule — *if something is not working as
expected, alert; no silent failures anywhere* — is genuinely enforced, not aspirational:
`tests/test_fail_loud_rule.py` holds a per-file budget of silent `except: pass` handlers that may
only ratchet downward. This is the most valuable piece of engineering culture in the repository,
and the review leaned on it: two of the four commits are written in its idiom.

**Scale.** 164 production Python files, ~55,500 lines (excluding tests, `retired/`, and vendored
skills), plus a 134-file, ~3,200-test suite that runs in about 20 seconds.

### Observations that reflect well on the design

- **Cold start is cheap despite a 4,100-line lambda.** `api/index.py` carries only 23 top-level
  imports against 137 deferred inside function bodies, so importing the module measures ~75 ms.
  The heavy dependencies (`db_cloud`, `whatsapp_bot`, `razorpay_integration`) load on first use.
  That is the right trade for serverless and it was clearly done on purpose.
- **Comments carry incident history.** Many of the sharpest comments record a specific outage and
  its date. This is unusually good practice and it made the review faster — several findings below
  came from reading a comment that documented a problem instead of fixing it.
- **Route ordering is currently correct.** All 111 path tests in the dispatch chain were checked;
  no route is shadowed today (§2.1).

---

## 2. Problem areas

### 2.1 Structural — the dispatch chain is correct by luck, not by construction

`api/index.py` routes by walking a flat `if self.path == ...` / `.startswith(...)` chain: 56 tests
in `do_GET`, 55 in `do_POST`, tried in source order, first match wins. `do_POST` is a single
355-line function; `do_GET` is 218.

No route is unreachable today — verified by AST analysis of every path test. The problem is that
nothing keeps it that way. A prefix test swallows every later route beneath it, and the failure is
silent: the request still returns 200, just from the wrong handler. It produces wrong data rather
than an error.

This has already been hit once. The comment above the book-orders route is the scar:

> *Exact path (+ optional query) only, so a GET to a sub-route like
> `/admin/book-orders/<code>/confirm` is not swallowed by the list handler.*

That is a correctness invariant maintained by a comment and reviewer vigilance.

### 2.2 Duplication — the same logic, in up to four places

Detected by AST-normalised clone analysis across the tree, then confirmed by reading each pair.
Every item below was **verbatim identical**, docstrings included.

| Logic | Copies | Where |
|---|---|---|
| PBKDF2 staff-PIN hash + verify | **3** | `print_server.py`, `api/index.py`, `staff_setup.py` |
| `_epson_ip()` resolver | **4** | `check_epson_snmp`, `epson_find_pages`, `epson_snmp_discover`, `epson_scrape_status` |
| `split_malayalam_english` + chillu table | **3** | `api/index.py`, `tools/cloud_transcription_worker.py`, `tools/pdf_tools_server.py` |
| `_normalize_phone` | 2 | `api/index.py`, `review_manager.py` |
| `_as_datetime` | 2 | `store_digest.py`, `dropoff.py` |
| `build_logo.py` (whole file) | 2 | `brand-kit/logo/`, `website/assets/` |

Two of these were **annotated as duplicates rather than shared**:

- `review_manager._normalize_phone` — *"Match api/index.py's normalization"*
- `dropoff._as_datetime` — *"Deliberately the same permissive parse as store_digest._as_datetime"*

That is what hand-synchronisation looks like right up until someone edits one side.

Severity is not uniform. Three cases are materially worse than the rest:

- **PIN hashing is a cross-deployment contract.** A PIN seeded by `staff_setup` on the store PC
  must verify in `print_server` at the counter *and* in `api/index` in the cloud. Three
  independent literal `260_000`s merely happened to agree. Drift in any one copy locks staff out
  of that surface with nothing to point at, and the failure appears at the till.
- **`_epson_ip` is a repeat of a known outage.** Its docstring records the 2026-06-29 EM-C8100
  swap, when hardcoded addresses left the scripts talking to a printer that was gone. The fix was
  then applied *by copy-paste*, leaving four copies of the fallback IP — so the next swap was
  set up to be the same outage in four places.
- **The chillu table is wrong** (§5).

### 2.3 Performance — mostly fine; the real costs are elsewhere

Scanned for queries inside loops across the tree: 36 sites. Most are legitimate — per-row updates
with race guards in cron sweeps, where one statement per row is the point.

Two genuine inefficiencies, both low-volume today:

- `api/handlers_referrals.py:313` issues **two extra queries per candidate job** purely to detect
  whether `_credit_referrer` created a row (count before, count after). At current volumes this is
  invisible; it becomes the sweep's dominant cost as referral traffic grows. The clean fix is for
  `_credit_referrer` to return whether it credited.
- `api/index.py` `/cron/dropoff-sweep` updates one row per reminder. Correct, but a batched
  update keyed on the job-id set would collapse it.

The linear dispatch chain (§2.1) is O(n) string comparisons per request. With n≈111 and Python
string comparison, this is not worth optimising for speed — it matters for correctness and
readability, not latency.

**No evidence was found of the classic problems**: the Supabase client is constructed once and
cached (`db_cloud._client`), and cold start is well managed (§1).

### 2.4 Maintainability — concentration and invisible files

**Five modules carry a third of the codebase.**

| Module | LOC | Longest function |
|---|---:|---|
| `api/index.py` | 4,115 | `do_POST` — 355 |
| `print_server.py` | 3,693 | `handle_print_item` — 285 |
| `db_cloud.py` | 3,272 | — |
| `docx_engine.py` | 3,205 | `format_fix_pdf` — 318 |
| `book_bot.py` | 2,850 | `maybe_handle_soc` — 160 |

`api/index.py` in particular mixes HTTP dispatch, WhatsApp conversation state, Razorpay
settlement, referral accounting, staff auth, DOCX generation and eight cron handlers in one file.
The `api/handlers_*.py` split has clearly begun — seven modules, ~5,960 lines — but the parent
still holds more than any child.

**A quieter problem: five modules begin with a UTF-8 BOM** — `db_cloud.py`, `watcher.py`,
`api/handlers_admin.py`, `book_catalog.py`, `dispatch_render.py`. Python's import machinery
handles it, so nothing breaks at runtime. But any tool that reads sources as plain `utf-8` fails
on `U+FEFF` with a `SyntaxError`, and tools that skip unparseable files then skip these five
**silently**. This review hit exactly that: the first version of the new duplicate-scanners was
quietly excluding all five, including the two largest modules after the API and print server
(§4, commit 4). Anything that lints, greps-by-AST, or audits this tree should read `utf-8-sig`.

**Scripts execute on import.** `check_epson_snmp.py` and its siblings run queries at module level
with no `if __name__ == "__main__":` guard, so they cannot be imported for testing — which is why
they have none.

---

## 3. Refactoring strategy

The strategy applied was deliberately narrow, because the codebase is in production across three
deployment targets and one of them updates by hand.

**Principle: consolidate what is provably identical; enforce what is currently true; report what
needs a human.**

1. **Prove identity before merging.** Nothing was consolidated on the strength of looking similar.
   Each extraction was differentially fuzzed against the original implementation — 20,000 random
   inputs per helper for phone and timestamp, 30,000 through the full chillu-and-split pipeline —
   with zero mismatches, *then* rewired.
2. **Extract by concern, not into a junk drawer.** Five focused modules (`pin_crypto`,
   `printer_endpoints`, `phone_format`, `timestamps`, `malayalam`) rather than one `utils.py`.
   Each is named for what it owns and carries the reasoning that was previously scattered across
   the copies' comments.
3. **Preserve every documented edge case.** The legacy SHA-256 path for pre-v15 NULL-salt PIN rows,
   the guarded `store_config` import with its literal fallback, the `EPSON_IP` override and its
   `.strip()` — all carried across unchanged.
4. **Ratchet, don't just fix.** Deduplication decays. Both new guard tests follow the existing
   `test_fail_loud_rule.py` idiom so the invariants hold without reviewer vigilance.
5. **Leave behaviour changes to the owner.** The chillu defect (§5) alters text in documents
   customers have already received. It is documented and staged, not applied.

### Recommended next, not done here

| # | Action | Why |
|---|---|---|
| R1 | **Decide the chillu fix (§5).** | Customer-facing text is wrong today. |
| R2 | Replace the dispatch chain with an ordered route table (`(method, matcher, handler)`), most-specific-first. | Turns §2.1's invariant into structure. The new test makes this safe to attempt. |
| R3 | Continue the `api/handlers_*` split; move the cron handlers and the transcript DOCX builder out of `api/index.py`. | The parent is still the largest module. |
| R4 | Give `_credit_referrer` a return value; drop the before/after counting. | Removes 2 queries per candidate job. |
| R5 | Strip the BOMs, or standardise every source-reading tool on `utf-8-sig`. | Removes a whole class of silent tooling blind spots. |
| R6 | Add `if __name__ == "__main__":` guards to the Epson diagnostic scripts. | Makes them importable and therefore testable. |
| R7 | De-duplicate `build_logo.py` between `brand-kit/logo/` and `website/assets/`. | Two full copies of a build script. |

---

## 4. Changes made

All four commits are on `claude/codebase-architecture-review-k3si73`. The suite was run green
after each.

| # | Commit | Change |
|---|---|---|
| 1 | `145acc7` | `pin_crypto.py` — one PBKDF2 implementation for all three surfaces. `printer_endpoints.py` — one Epson resolver for four scripts. **Plus one bug fix (§5.2).** |
| 2 | `1878089` | `phone_format.py`, `timestamps.py`, `malayalam.py` — the remaining verified-identical helpers, each fuzz-checked before rewiring. |
| 3 | `5a9af2e` | `tests/test_no_redundant_copies.py` and `tests/test_route_dispatch.py` — ratchets for §2.1 and §2.2. |
| 4 | `f40c519` | Fixed the BOM blind spot the new scanners had (§2.4), plus a test that fails loudly if they ever go quiet again. |

Both guards in commit 3 were validated by **reintroducing the regressions they exist to catch** — a
`/referrals` prefix above `/referrals/balance`, a second `_normalize_phone`, a second
`pbkdf2_hmac` call — and confirming each fails with a message naming the offending file. The
working tree was restored afterwards.

**Test count: 3188 → 3220.** The increase is 5 reentrancy tests, 22 guard tests, and 5 picked up
automatically by existing per-file parametrised checks now seeing the two new modules.

One existing assertion was relaxed, and it is worth stating plainly rather than burying:
`test_ops_watchdog.py`'s memory-fallback test asserted the failing-check list was *exactly*
`["printer.epson"]`. After the §5.2 fix, a second genuinely-failing check
(`store_config.missing_file`) is correctly recorded alongside it. The old assertion held only
because that second alert was being lost. It now asserts membership; the test's actual subject —
memory fallback, and no stray Windows-path file — is untouched.

---

## 5. Two bugs found

### 5.1 The Malayalam chillu table is wrong — **not fixed, needs your decision**

`CHILLU_MAP` converts atomic chillu letters to base-consonant + virama + ZWJ. Its **keys sit one
codepoint off the values they should carry**, so five of the six letters decompose to the wrong
consonant:

| Input | Produces today | Should produce | |
|---|---|---|---|
| അവൻ (*avan*, "he") | അവണ്‍ | അവന്‍ | wrong |
| അവൾ (*aval*, "she") | അവന്‍ | അവള്‍ | wrong |
| വർഷം (*varsham*, "year") | വള്‍ഷം | വര്‍ഷം | wrong |
| കാൽ (*kaal*, "leg") | കാര്‍ | കാല്‍ | wrong |
| വാൿ (*vaak*) | വാല്‍ | വാക്‍ | wrong |
| മാൺ | മാണ്‍ | മാണ്‍ | correct |

`ൿ` (CHILLU K) has no correct entry at all — it picks up `ൽ`'s value. Only `ൺ` (CHILLU NN) is right.

The trailing comments are the tell: each names the letter its **value** decomposes to, which is
what the key was meant to be. The values and comments were written correctly and the keys were
then written one codepoint off.

This reaches customers through `/api/transcripts/export-docx` and both transcription tools — all
three of which carried an identical copy of the bad table.

**Deliberately not fixed.** It changes text in transcripts already delivered, and it is a call for
someone who reads Malayalam. The table ships exactly as before; the corrected one sits beside it
as `CORRECTED_CHILLU_MAP` in `malayalam.py`, fully documented. **Swapping the name in
`replace_chillus` is the entire change** once you confirm.

### 5.2 The fail-loud system had a silent failure of its own — **fixed**

On a store PC with **no `store_config.json`**, the alert saying so never sent.

```
get_store_config()              # @lru_cache — has not returned yet
  └─▶ report("store_config.missing_file")
        ├─▶ _store_id()              ─┐ both need the store config
        └─▶ _configured_db_path()    ─┘
              └─▶ get_store_config()  # cache still empty → re-enters → …
```

`@lru_cache` publishes a result only when the call *returns*, so the nested lookup found the cache
empty, took the same missing-file branch, and reported again — until the stack blew. `report()`
caught the `RecursionError` and logged *"could not send alert"*.

So the one alert announcing that a machine has no configuration was the one alert that never
arrived — on exactly the freshly-provisioned store PC that most needs it. Reproduced on
unmodified `main`.

A comment in `ops_watchdog._store_id` asserted this could not happen — *"Measured: it does not —
that nested report never re-enters `_store_id`, so the nesting depth stays at 1."* That holds only
when a config file exists or the store id is already warm; neither is true on a cold, unconfigured
box.

Fixed at the source with a thread-local re-entrancy guard in `store_config.get_store_config()`,
which covers **both** re-entry paths — a guard in `_store_id` alone would have missed
`_configured_db_path`. The alert now sends once and names the store instead of `?`.
`tests/test_store_config_reentrancy.py` pins it and fails with `RecursionError` against the
unfixed code.

---

## 6. Summary

The architecture is sound and the operational instincts behind it are better than the file sizes
suggest. The `ops_watchdog` fail-loud rule with its ratcheting test is a genuinely strong piece of
engineering culture, and the habit of recording incident history in comments paid for itself
repeatedly during this review.

The recurring weakness is not the design — it is that **fixes get applied by copy-paste, and
observations get written into comments instead of into structure**. The Epson resolver is the
clearest case: a real outage, correctly diagnosed, fixed four times. Two other helpers carry
docstrings explaining that they are duplicates. A route-ordering rule lives in a comment. A claim
that recursion was impossible lives in a comment, and was wrong.

The changes here convert six of those comments into single implementations with tests that hold
them there. The highest-value remaining item is §5.1, which needs your judgement, not more code.
