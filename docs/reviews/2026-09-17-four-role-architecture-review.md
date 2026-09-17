# Printosky — Whole-Repo Architecture Review

**Date:** 17 September 2026
**Repository:** `deepakbanarjee/printosky`
**Reviewed commit:** `bb17c8a513869d487eec8e9296be0db017ff620d` ("Find the CAPI dataset id by asking Meta, on both edges (#130)")
**Method:** four-role pass — Architect (design), Engineer (implementation plan), Reviewer (quality control), Optimizer (performance).
**Changes made:** none. This document is the only artefact. No code, config, database, deployment or business record was touched.

---

## 0. How to read this, and what it adds

A thorough external review already exists: [`docs/reviews/2026-09-10-professional-review.md`](2026-09-10-professional-review.md),
dated one week before this one, with findings F01–F12. This review does **not**
restate it. It does three things instead:

1. **Re-verifies its P0 findings against today's tree.** Three of the four P0s
   are still present, unchanged, at the same call sites. That is the most
   important thing in this document and it is in §3.1.
2. **Adds findings the prior review did not cover** — an unauthenticated admin
   endpoint returning student phone numbers, fail-open cron authentication, and
   a measured gap in the fail-loud ratchet that explains the BILLING FIX bug
   class in `SPRINT_BACKLOG.md`.
3. **Proposes a target architecture and a sequenced plan** that is compatible
   with the prior review's §7, expressed in terms of this repository's actual
   modules rather than a greenfield design.

### Evidence standard

Every claim below carries a `file:line` citation and was read in the working
tree at the reviewed commit. Where a risk is structural rather than observed,
it is labelled **structural** and the precondition is stated. Nothing here was
exploited, executed against production, or reproduced with a live payment,
message, printer or database query.

**Limit, stated plainly:** the test suite was **not executed while this review
was written**. `pytest` is not installed in this environment and could not be
installed (the package proxy timed out on PyPI). 137 test files exist under
`tests/`. CI has since run the suite green on the commit carrying this
document, but no finding below was derived from a test result — see §6.

---

# Part 1 — Architect

## 1.1 The system as built, not as documented

`docs/ARCHITECTURE.md` describes four deployment targets. The code describes
something slightly different, and the difference is where the structural risk
lives.

| Layer | Artefact | Size | Who writes business state |
|---|---|---|---|
| Store PC | `watcher.py`, `print_server.py`, `printer_poller.py`, `store_puller.py` | ~7,900 lines | SQLite `jobs.db` |
| Cloud API | `api/index.py` + 7 handler modules | ~10,500 lines, one lambda | Supabase |
| Consoles | `website/admin.html`, `jobs.html` | 12,141 lines of HTML+JS | Supabase (direct) **and** the cloud API |
| Shared core | `db_cloud.py`, `rate_card.py`, `book_bot.py`, `whatsapp_notify.py` | ~8,000 lines | both sides import these |

The fourth row is the one the architecture doc does not name, and it is the
defining structural fact of this codebase: **the store PC modules and the
serverless API are not two systems talking over a contract — they are one
codebase imported twice, into two runtimes with different lifecycles.**
`db_cloud` alone is imported 140 times across `api/`.

Three consequences follow, and most findings in this review are instances of
one of them.

### Consequence A — there is no owner for a row

`jobs` is written from both runtimes with no field ownership and no version
column:

- The store PC writes local SQLite, then `supabase_sync.py:450` upserts the
  **latest 500 local rows every 300 seconds** (`SYNC_INTERVAL`,
  `supabase_sync.py:38`) with `on_conflict=job_id` and
  `Prefer: resolution=merge-duplicates` (`supabase_sync.py:48`). The payload
  includes `status`, `amount_collected` and `payment_mode`
  (`supabase_sync.py:139-146`).
- The cloud writes the same columns on payment
  (`api/index.py:1745` `_process_razorpay_payment` → `db_cloud.update_job_paid`).

There is no reverse sync of payment state. The only path by which a store PC
learns that a job was paid is `webhook_checker.py`, which polls the **Razorpay
API** — not Supabase — every 600 seconds (`webhook_checker.py:29`) and only for
jobs it considers stale (`webhook_checker.py:41`).

So for a job that exists in both stores, the cloud can hold `Paid` while the
store PC holds `Quoted`, and the 5-minute sync will push `Quoted` over it.
This is F07 in the prior review; what this pass adds is the precondition and
the blast radius:

> **Precondition (structural).** The job must exist in *both* stores. Jobs
> created in the cloud are safe: `store_puller.py` records pulled jobs in a
> separate `pulled_jobs` table (`store_puller.py:160-184`) and never inserts
> into local `jobs`, so they are never collected by `collect_jobs`. The
> exposure is therefore **locally-originated jobs paid online** — hot-folder
> drops, counter walk-ins and WhatsApp-Web intake that are then sent a Razorpay
> link. The window is up to ~10 minutes wide, and there is no trigger or
> conflict check to close it (`api/migrations/*.sql` defines two triggers, both
> `updated_at` touchers).

### Consequence B — authorization is a convention, not a boundary

`db_cloud._client()` prefers `SUPABASE_SERVICE_KEY` (`db_cloud.py:32`), so
every cloud handler runs with RLS bypassed. The database therefore provides no
second line of defence, and the *only* thing standing between the public
internet and the data is a three-line guard copy-pasted into each handler:

```python
if not _auth_admin_pw(_admin_pw_from_request(h)):
    _json_response(h, 403, {"error": "Unauthorized"})
    return
```

That block appears ~25 times in `api/handlers_admin.py`. It is opt-in: a
handler that omits it is **public by default**, and `vercel.json` routes
`/admin/(.*)` wholesale to the lambda. One handler has already omitted it —
see §3.2, R-01.

### Consequence C — the router is a 91-branch if-chain with an import cycle

`do_GET` and `do_POST` (`api/index.py:3528`, `:3752`) dispatch by 91 sequential
`self.path ==` / `startswith` comparisons. Matching cost is irrelevant at this
volume; the coupling is not. Handler modules are imported at **module scope
partway down** `api/index.py` (lines 2335, 2432, 2448, 3484, 3501, 3516), and
those modules import back from `api.index` while it is still executing:

```python
# api/handlers_pb.py:20
from api.index import (  # noqa: E402  (api.index is mid-import; names below are defined above the import site)
```

The cycle works only because every name a handler needs happens to be defined
above line 2335. Moving a helper downward in `api/index.py` breaks the import
at deploy time, not at review time — and the first symptom is a cold-start
`ImportError` on every route in the lambda.

## 1.2 Target architecture

The prior review's §7 proposes the right destination. This is the same
destination expressed as changes to modules that exist today, so that each step
is independently shippable and none of them is a rewrite.

**Principle 1 — one owner per field, enforced at the sync boundary.**
Money and order state are **cloud-owned**. Print execution and printer metering
are **store-owned**. `supabase_sync.collect_jobs` stops sending `status`,
`amount_collected` and `payment_mode`; the store PC learns those by reading,
not by writing. This single change closes F07's overwrite path without a
schema migration or a state-machine rewrite.

**Principle 2 — authorization is a router concern, not a handler concern.**
An allowlist in `do_GET`/`do_POST` declares the required principal per route
prefix (`admin`, `staff`, `cron`, `webhook`, `public`). A route absent from the
table is denied. Handlers keep their guards during the transition, then shed
them. This converts "public by default" into "denied by default" — the
property that would have prevented R-01 and would prevent the next one.

**Principle 3 — the shared core becomes a package with a stated contract.**
`db_cloud.py` is 3,272 lines, 119 functions and touches 27 tables. Split it by
domain (`jobs`, `book_orders`, `contacts`, `notes`, `wallet`) behind the same
function names, so call sites do not move in the same commit that moves the
code. This is the precondition for ever testing the cloud paths in isolation.

**Principle 4 — fail-loud governs defaults, not just `pass`.** See R-03: the
ratchet currently governs 83 sites and is blind to the 160 that produced the
BILLING FIX bug class.

---

# Part 2 — Engineer

Sequenced so that each step is shippable alone, is reversible, and does not
depend on the step after it. No step requires a framework change. Effort is
relative, not calendar.

### Step 0 — stop the bleeding (hours, not days)

Three edits, each self-contained, each addressing a still-open P0:

| Edit | Site | Change |
|---|---|---|
| Reject unmatched credentials | `api/index.py:2143-2152` | Delete the fall-through grant. When `store_hash` is set and does not match, and when it is unset, return `401`. |
| Never price an exception | `api/handlers_order.py:307`, `:403` | Replace `total = 0.0` with a `503 {"error": "pricing unavailable"}`, or an explicitly unpriced draft that staff must approve. Alert via `ops_watchdog.report`. |
| Close the open admin route | `api/handlers_admin.py:1709` | Add the standard guard to `_handle_admin_notes_queue`. |

Each is a few lines. Each is independently testable with a request-level test
against the handler. None of them changes a schema.

### Step 1 — deny by default at the router

Add a route→principal table beside `do_GET`/`do_POST` and check it before
dispatch. Ship it in "log-only" mode first (record what *would* have been
denied for one deploy cycle), then enforce. This is the change that makes
Step 0's third edit structurally unnecessary rather than individually correct.

Cron endpoints fold into the same table, which also fixes the fail-open pattern
at `api/index.py:2568, 2672, 3118, 3157, 3184, 3212, 3243, 3340, 3408` — see
R-02.

### Step 2 — field ownership at the sync boundary

In `supabase_sync.collect_jobs` (`supabase_sync.py:123`), drop `status`,
`amount_collected` and `payment_mode` from the `SELECT` and the upsert payload.
Then give the store PC a downward read for payment state so it stops depending
on `webhook_checker`'s 10-minute Razorpay poll — `store_puller.py` already
holds a Supabase Realtime subscription on `jobs` (`store_puller.py:21-27`) and
is the natural place for it.

Sequencing matters: **drop the writes before adding the reads.** The write is
the destructive half; the read is an improvement to latency.

### Step 3 — typed sync results

`collect_jobs` returns `[]` on exception (`supabase_sync.py:157-160`), and
`sync_once` cannot distinguish "nothing to sync" from "could not read the
source" (F07). Return `(rows, ok)` and make a failed read report unhealthy
through `ops_watchdog`. Small change, removes a whole class of silent staleness.

### Step 4 — break the import cycle

Move the names that `api/handlers_*.py` import from `api.index` into a
dependency-free `api/_shared.py`. Mechanical, no behaviour change, and it
removes a deploy-time failure mode that no test can currently catch.

### Step 5 — split `db_cloud.py` by domain

Last, because it is the largest diff and the least urgent. Keep the public
function names stable; re-export from `db_cloud` during the transition so no
call site changes in the same commit.

---

# Part 3 — Reviewer

## 3.1 Verification of the 2026-09-10 P0 findings

Re-read against the working tree at `bb17c8a`. **This is the section to act on.**

| Prior finding | Status today | Evidence |
|---|---|---|
| **F01 — legacy auth accepts incorrect passwords** | 🔴 **Still open, unchanged** | `api/index.py:2143-2152` |
| **F02 — batch payment overstates job collections** | 🔴 **Still open, unchanged** | `api/index.py:1771-1772` |
| **F04 — pricing failure becomes a ₹0 quote** | 🔴 **Still open, at two sites** | `api/handlers_order.py:307`, `:403` |
| **F07 — sync failures look like empty successes** | 🔴 **Still open** | `supabase_sync.py:157-160`, `:450` |
| **F10 — green workflow ≠ schema verification ran** | 🔴 **Still open — confirmed live, see below** | `.github/workflows/schema-drift.yml:27-55` |
| F03, F05, F06, F08, F09, F11, F12 | Not re-verified in this pass | — |

### F01 deserves restating, because its severity is higher than the prior review could confirm

`_handle_auth_legacy` grants on **any non-empty password**:

```python
# api/index.py:2143-2152
if password:
    store_hash = os.environ.get("STAFF_TOKEN_HASH") or os.environ.get("STORE_SHA256_HASH")
    if store_hash:
        p_hash = hashlib.sha256(password.encode()).hexdigest()
        if p_hash == store_hash:
            _json_response(h, 200, {"ok": True, "supabase_jwt": _mint_supabase_jwt()})
            return
    _json_response(h, 200, {"ok": True, "supabase_jwt": _mint_supabase_jwt()})   # ← grants anyway
    return
```

Note the control flow: configuring `STAFF_TOKEN_HASH` does not help. A
**non-matching** hash falls straight through to the unconditional grant on the
next line.

What the prior review flagged as depending on "live RLS", this pass can narrow.
`_mint_supabase_jwt()` (`api/index.py:2034-2063`) is not a mock. It performs a
real Supabase Auth password grant against a shared service account
(`SUPABASE_AUTH_EMAIL` / `SUPABASE_AUTH_PASSWORD`) and returns a genuine
`authenticated`-role `access_token`. `SEC-4` in `SPRINT_BACKLOG.md` records that
RLS was deliberately tightened to `auth.role() = 'authenticated'`.

Those two facts compose. If `SUPABASE_AUTH_EMAIL`/`_PASSWORD` are set in the
Vercel environment, then `POST /auth {"password":"anything"}` — a route
`vercel.json` exposes publicly at both `/auth/(.*)` and
`/.netlify/functions/auth` — returns a token that satisfies every
`authenticated`-role RLS policy in the project.

**This was reported as P0 on 10 September and is unchanged on 17 September.**
Whether the env vars are set in production is the one fact this review cannot
see from the repository; it is checkable in the Vercel dashboard in under a
minute, and it decides whether this is a latent bug or a live exposure.

### F10 was confirmed by this review's own pull request

The `drift` check on the PR carrying this document reported **success after 3
seconds**. It verified nothing. `.github/workflows/schema-drift.yml:27-55` gates
every real step on `SUPABASE_DB_URL` being present; the secret is not set, so
the job skips setup, install and `scripts/check_schema.py`, and still reports a
green check.

The workflow is honest about this in a `::notice::` and in its header comment —
but a notice is not a status, and what a reviewer sees on the PR is a passing
check named "drift". This is F10 exactly, observed rather than inferred.

**It is also the same shape as F01 and R-02**, which is the point worth
carrying away from this section:

| Missing configuration | Degrades to |
|---|---|
| `STAFF_TOKEN_HASH` unset *or non-matching* | authentication **grants** (`api/index.py:2143-2152`) |
| `CRON_SECRET` unset | cron endpoints **open** (9 sites) |
| `SUPABASE_DB_URL` unset | schema drift check **passes** (`schema-drift.yml:27`) |

Three independent subsystems, one habit: an absent secret makes the check
disappear instead of making it fail. The project already has the right
instinct written down — `docs/FAIL_LOUD.md` — and applies it rigorously to
runtime pipelines while the *configuration* layer does the opposite. A single
rule would cover all three: **a security or verification control that cannot
find its configuration reports unhealthy, never healthy.**

## 3.2 New findings

### R-01 — `GET /admin/notes-queue` is unauthenticated and returns student phone numbers

**Severity: high · code-verified**

An AST pass over every `_handle_admin_*` function in `api/index.py` and
`api/handlers_admin.py` found exactly two without an admin-password guard:

- `_handle_admin_mark_paid` (`api/handlers_admin.py:749`) — **not a defect.**
  It deliberately accepts a staff PIN instead, via `_acad_auth_staff`
  (`:763-766`), and the docstring says so. Counter staff record cash payments
  without the admin password.
- `_handle_admin_notes_queue` (`api/handlers_admin.py:1709`) — **a defect.** It
  has no guard of any kind.

It calls `db_cloud.pending_notes_queue` (`db_cloud.py:2989`), which is
`select("*")` on `notes`. Per `api/migrations/SCHEMA_v28_notes_marketplace.sql:20-44`,
that row set includes `uploader_phone` and `storage_path` for every pending
upload. `vercel.json` routes `/admin/(.*)` to the lambda, so the endpoint is
publicly reachable.

Impact: an unauthenticated GET returns the phone number of every student with a
pending note upload, plus the private-bucket path of their file. Adjacent
handlers in the same module (`_handle_admin_notes_moderate`, `:1720`) are
guarded, which is why this reads as an omission rather than a decision.

**Fix:** the standard guard. **Structural fix:** Step 1 — deny by default.

### R-02 — cron authentication is fail-open

**Severity: medium · code-verified**

Nine cron handlers share this shape:

```python
expected = os.environ.get("CRON_SECRET", "")
if expected and h.headers.get("Authorization", "") != f"Bearer {expected}":
```

Sites: `api/index.py:2568, 2672, 3118, 3157, 3184, 3212, 3243, 3340, 3408`.

If `CRON_SECRET` is unset or set to `""`, every cron endpoint is open to the
public internet. One docstring acknowledges this and calls it "harmless"
(`api/index.py:2668-2670`), which is true of a read-only digest and not true of
the set as a whole: `/cron/abandoned-carts`, `/cron/payment-review-reminders`
and `/cron/referral-credits` send WhatsApp messages to customers and move
credit. An attacker who can trigger them repeatedly can spam customers on the
store's own number and burn its Meta messaging allowance.

This is the same anti-pattern as F01 — a missing configuration value degrades
to *grant*. The fix is to fail closed: absent `CRON_SECRET`, refuse, and report
the misconfiguration through `ops_watchdog`.

### R-03 — the fail-loud ratchet governs 83 sites and is blind to 160 more

**Severity: medium · measured**

`tests/test_fail_loud_rule.py` is a genuinely good piece of engineering: a
per-file budget that can go down but never up. Its detector counts handlers
"whose entire body is `pass`" (`tests/test_fail_loud_rule.py:78`). That is 83
sites today, matching S9-8 in `SPRINT_BACKLOG.md`.

An AST sweep for the *adjacent* shape — a handler whose entire body assigns or
returns a default, with no log and no alert — finds **160 sites** outside
`tests/` and `retired/`:

| Count | File |
|---|---|
| 26 | `print_server.py` |
| 15 | `api/handlers_admin.py` |
| 13 | `tools/pdf_tools_server.py` |
| 7 | `watcher.py` |
| 7 | `api/index.py` |
| 6 | `device_lease.py` |
| 6 | `book_bot.py` |

Not all 160 are bugs — `except ValueError: x = default` around user input is
legitimate, and this is a population to triage, not a defect list. But the
class is ungoverned, and the class is exactly what the BILLING FIX section of
`SPRINT_BACKLOG.md` diagnoses in its own words:

> "a value the UI offers that the rate card does not know, **failing to the
> cheapest thing instead of failing loud**"

F04 is the same shape and is still open: `except Exception: total = 0.0`
(`api/handlers_order.py:307`) is not a `pass`, so the ratchet does not see it,
and a pricing crash becomes a free order.

**Fix:** extend the detector to flag handlers that neither log, re-raise, nor
call `ops_watchdog`, and freeze a second budget at today's count. The ratchet
mechanism already works; it is pointed at the narrower half of the problem.

### R-04 — the staff PIN is stored in the browser and replayed as a bearer token

**Severity: medium · code-verified**

`website/admin.html:2519` writes the raw PIN to `sessionStorage`
(`sessionStorage.setItem("staff_pin", pin)`), alongside a `session_id` issued by
the same login response (`:2518`). The PIN, not the session id, is what
subsequent API calls present.

A PIN is 4–8 digits (`api/index.py:2120`), shared across a shift, changes
rarely, and cannot be revoked without changing it for everyone. A session id
can be revoked, scoped to a store, and expired. The repository already issues
one; it is simply not the credential. F09 in the prior review points the same
direction; this adds the specific storage site.

### R-05 — `docs/ARCHITECTURE.md` has drifted, in the direction that costs debugging time

**Severity: low · code-verified**

The doc is stamped "Last updated 2026-04-29" and is wrong in ways a newcomer
would act on:

| Doc says | Tree says |
|---|---|
| "Store PC (hardcoded — migration pending): Razorpay live keys → `razorpay_integration.py`" | Env-based since some point after: `razorpay_integration.py:23-25` reads `os.environ[...]`. The doc understates the project's own security posture. |
| Supabase "Tables:" lists 10 | `docs/SCHEMA.md` documents 28; `db_cloud.py` touches 27 |
| Route table lists ~12 routes | 91 dispatch branches; 33 `vercel.json` entries |

The file itself carries a note about how a stale branch name in this same table
"was wrong for long enough to cost real debugging time." The same failure mode
has recurred in the same file. Worth adding the route table and the Supabase
table list to whatever regenerates `docs/SCHEMA.md`, so they cannot drift by
hand.

---

# Part 4 — Optimizer

Ordered by measured impact. None of these is urgent relative to Part 3; the
first is the only one with a plausible production symptom today.

### O-01 — `/admin/book-orders/payments-to-verify` makes up to ~800 Supabase round trips

`api/handlers_admin.py:680-712` loops over two statuses × up to 200 orders, and
for **each order** issues two further queries — `get_book_payments(code)` and
`book_amount_paid(code)`:

```
2 list queries + (400 orders × 2 round trips) = up to 802 sequential requests
```

On a 300-second lambda (`vercel.json`), each round trip carrying full network
latency to Supabase. At today's order volumes this is likely a slow panel
rather than a timeout, but it degrades linearly with success and the failure
mode is a dashboard that stops loading exactly when the business gets busy.

**Fix:** one `book_payments` query with `.in_("order_code", codes)`, grouped in
memory. Two queries total, independent of order count. `book_amount_paid` is
then a sum over the same rows rather than a separate query.

### O-02 — the 5-minute sync re-uploads 500 unchanged rows forever

`collect_jobs` selects the newest 500 jobs unconditionally
(`supabase_sync.py:139-147`) and upserts all of them every 300 seconds,
per store PC. Nothing tracks which rows changed. Two costs:

- Steady-state bandwidth and Supabase write load proportional to history, not
  to activity — permanently, on every box, whether or not anything happened.
- A job that changes while sitting outside the 500-row window is never synced
  at all (also noted in F07).

**Fix:** a `synced_at` / dirty flag on the local row, or a change cursor on
`received_at`. This is the same edit as Step 2/3 in Part 2 — correctness and
efficiency have the same fix here, which is why it is worth doing once, properly.

### O-03 — `select("*")` on 23 of `db_cloud`'s queries

23 sites in `db_cloud.py` select every column. Two of them feed endpoints that
serialize the result straight to a browser, which is how R-01 turns an auth
omission into a phone-number leak rather than a row count. Narrowing the
projections is a small change with both a bandwidth and a blast-radius payoff.

### O-04 — cold-start weight

`api/index.py` imports all seven handler modules at module scope (lines 2335,
2432, 2448, 3484, 3501, 3516), so every cold start parses ~10,500 lines before
serving any route. This is **already well handled** in one respect worth
crediting: the genuinely heavy dependencies (`docx`, `format_fix_v3`,
`db_cloud`, `book_bot`) are imported lazily *inside* functions throughout, so
the parse cost is Python source, not library initialisation. The remaining win
is modest and would fall out of Step 4 rather than justifying its own change.

### O-05 — `get_job()` per job in the batch payment loop

`api/index.py:1786-1789` fetches each job individually to collect pickup codes
for a staff alert. Bounded by batch size and therefore small — listed for
completeness, not for action. Note that this loop is also the site of F02
(`:1771-1772`), so it will be touched anyway when that is fixed.

---

## 5. Summary

**Act on this week, in order:**

1. `api/index.py:2143-2152` — F01. Reject unmatched credentials. Then check
   whether `SUPABASE_AUTH_EMAIL`/`SUPABASE_AUTH_PASSWORD` are set in Vercel;
   that answer decides whether this was latent or live, and whether the shared
   auth user's password needs rotating.
2. `api/handlers_admin.py:1709` — R-01. Add the guard.
3. `api/handlers_order.py:307`, `:403` — F04. Never price an exception.
4. `api/index.py:1771-1772` — F02. One payment, allocated across lines.

**Then the structural work,** in the order given in Part 2: deny-by-default
routing (which retires the whole class R-01 belongs to), field ownership at the
sync boundary (which retires F07's overwrite path), typed sync results, the
import cycle, and finally the `db_cloud` split.

**What is genuinely good here,** and should survive any refactor: the lease and
atomic-claim design in `MULTI_BOX.md`, the on-paper verification behind
`PRINT_ROTATION_MATRIX.md`, the ratchet mechanism in `test_fail_loud_rule.py`
(R-03 asks it to cover more, not to change), the honest retirement notes in
`SPRINT_BACKLOG.md`, and the fact that `ops_watchdog` exists at all. Most
codebases this size have none of these. The findings above are about boundaries
and defaults, not about competence.

## 6. Not verified

- The test suite was not executed **in this review environment** (`pytest`
  unavailable; PyPI unreachable through the proxy). It *was* run by CI on the
  commit carrying this document — the `test` workflow runs the full suite minus
  two Playwright files and passed. No finding in this document was derived from
  a test result either way; note that a green suite did not catch F01, F02, F04
  or R-01, which is itself the argument for the request-level auth tests
  proposed in Part 2.
- Live environment variables, Vercel and Netlify configuration, Supabase RLS
  policies as deployed, and storage bucket visibility.
- F03, F05, F06 and F08–F12 from the prior review were not re-verified.
- Runtime measurements. O-01 and O-02 are counted from code paths, not profiled.
- The Node WhatsApp client (`whatsapp_capture/`) and the `website/` consoles
  beyond the auth surfaces cited in R-04.
