# Printosky v2 — architecture and upgrade plan

_Written 2026-09-16. Implements the first slice of
[`docs/reviews/2026-09-10-professional-review.md`](reviews/2026-09-10-professional-review.md)
§7 and the P0 work packages in
[`docs/plans/2026-09-10-reliability-and-operations-plan.md`](plans/2026-09-10-reliability-and-operations-plan.md)._

---

## 0. What this is, in one paragraph

Printosky today is a working business on a large, honest, slightly tangled
codebase: a 4,000-line serverless handler, a 3,700-line print server, consoles
of 7,000 lines of HTML each, and three P0 defects an external review reproduced
in September. **v2 is not a rewrite.** It is a domain core with no I/O, a real
HTTP layer in front of it, a schema that can hold money correctly, and one new
console — all of it mounted *beside* the running system, which keeps serving
every request exactly as it does today. The three P0 defects are fixed in the
live code paths now, using the new core's shared routines.

The rule for every change in this upgrade: **nothing that works today may stop
working.** Every legacy route, page, cron and store-PC behaviour is untouched.

---

## 1. Target architecture

```
                        ┌──────────────────────────────┐
  WhatsApp ────────────▶│  Cloud API  (Vercel)          │
  printosky.com ───────▶│                               │
  Razorpay webhook ────▶│   /v2/*   → api/v2 → core/    │   new
  store PC agent ──────▶│   everything else → the        │   unchanged
                        │        existing if-chain       │
                        └──────────────┬────────────────┘
                                       │
                        ┌──────────────▼────────────────┐
                        │  Supabase (Postgres + Storage) │
                        │  orders · payments · inbox ·   │   new tables
                        │  outbox · attempts · identities│
                        │  jobs · bot_sessions · …       │   unchanged
                        └──────────────┬────────────────┘
                                       │ pull / publish
                        ┌──────────────▼────────────────┐
                        │  Store PC agent (Windows)      │
                        │  durable local queue, printers │
                        │  SQLite for counter continuity │
                        └────────────────────────────────┘
```

Deployment units do not change: Vercel for the API, Netlify for the static
consoles, Supabase for data, a Windows agent per store. The review is explicit
that provider count is not the defect, and that Kubernetes, a second broker, a
new database and AI scheduling are all out of scope without a measured need.

### The layering, and why it is enforced by imports

| Layer | Directory | May import | Must not import |
|---|---|---|---|
| Rules | `core/` | the standard library | anything else in the repo |
| Transport | `api/v2/` | `core/` | `db_cloud`, `print_server` |
| Adapters | `api/v2/repos*.py` | `core/`, `db_cloud` | route modules |
| Presentation | `website/app/` | the v2 JSON envelope | Supabase directly |
| Legacy | `api/index.py`, `db_cloud.py`, … | anything, including `core/` | — |

`core/` being unable to reach a database is what makes the rules testable, and
what lets the same rules run on a store PC that is offline. The legacy code may
import `core/` — the P0 fixes do — but nothing in `core/` imports legacy code,
so the dependency only ever points one way.

---

## 2. File structure

```
core/                        # pure rules, no I/O, standard library only
  money.py                   #   integer paise + exact allocation      (F02)
  pricing.py                 #   a price or an explicit refusal        (F04)
  identity.py                #   roles, store scope, fail-closed auth  (F01, F09)
  orders.py                  #   order model + two state machines      (F05, F06)
  payments.py                #   inbox → ledger → allocations → outbox (F03)
  ports.py                   #   repository Protocols
  errors.py                  #   error codes and their HTTP status

api/v2/
  http.py                    # Request/Response, the envelope, CORS, request ids
  router.py                  # method + path-pattern matching, 405 with Allow
  deps.py                    # settings, per-request context, authentication
  repos.py                   # in-memory adapters (tests + reference semantics)
  repos_supabase.py          # cloud adapters, optimistic concurrency, one RPC
  routes_ops.py              # GET /v2/ (live route table), GET /v2/health
  routes_auth.py             # login / logout / me / agent
  routes_orders.py           # quote, create, accept, transition, claim, attempts
  routes_payments.py         # webhook, counter collection, replay, refunds
  app.py                     # the route table + dispatch() entry point

api/migrations/SCHEMA_v44_core_v2.sql      # additive: 11 tables, 2 functions
scripts/seed_v2_identities.py              # staff → identities, idempotent

website/app/
  tokens.css                 # the design tokens, one copy
  api.js                     # v2 client: envelope, token, bounded retries
  ui.js                      # el / money / since / badge / toast / empty states
  console.js                 # the operator queue controller
website/console.html         # the console shell  (Netlify: /console)

tests/
  test_core_money.py         test_core_pricing.py
  test_core_identity.py      test_core_payments.py
  test_v2_api.py             test_p0_review_regressions.py
```

Untouched: `watcher.py`, `print_server.py`, `printer_poller.py`, `store_puller.py`,
`nup_imposer.py`, `print_planner.py`, `rate_card.py`, `db_cloud.py`, every
`website/*.html` that existed before, and every cron.

---

## 3. Database schema

`api/migrations/SCHEMA_v44_core_v2.sql`. Eleven new tables, no `ALTER`, no
`DROP`, no rename. Every table has RLS enabled with **no policy** and grants
revoked from `anon`/`authenticated` — the deny-by-default pattern of SCHEMA_v19
and v29. The Vercel functions use the service role, which bypasses RLS.

| Table | Holds | Why it exists |
|---|---|---|
| `orders` | one order: store, customer, lane, totals, **two** state columns, `version` | Contract #1 and #3. `version` is the optimistic-concurrency check. |
| `order_items` | file + accepted spec + line amount + `source_object` | Contract #2. `source_object` is a storage object id, never a URL (F08). |
| `order_tasks` | print / finishing / transfer / handover, with owner and due time | What the console lists as work. |
| `payment_inbox` | every verified provider event, raw, written **before** the 200 | F03. `state='received'` rows are the crash-recovery queue. |
| `payments` | the ledger, unique on `(provider, provider_payment_id)` | Contract #4. A re-delivered webhook conflicts instead of double-counting. |
| `payment_allocations` | how each payment splits across lines | F02. The split is checked to equal the payment inside the DB. |
| `outbox_events` | messages committed with the money, sent afterwards | Contract #5. WhatsApp cannot decide whether a payment happened. |
| `print_attempts` | one row per submission, with a fencing token and evidence | Contract #6, F05, F06. |
| `identities` | one row per person or device, with role and store scope | F09. Replaces the single shared Supabase Auth user. |
| `identity_sessions` | live sessions, revocable before expiry | F09. |
| `login_failures` | durable throttle state | A per-lambda dict throttles one instance and nothing else. |

Two functions:

* **`v2_apply_payment(...)`** — the whole payment commit in one transaction:
  ledger row, allocations, order balance, outbox. It rejects an allocation set
  that does not equal the payment, and returns `version_conflict` rather than
  half-applying. PostgREST cannot span statements, so atomicity lives where it
  can actually be guaranteed.
* **`v2_outbox_fail(...)`** — retry bookkeeping without read-modify-write.

### Applying it

**Applied to production (`mlhuwlnwwwxdnqafelko`) on 2026-09-17.** All 11 tables
verified RLS-enabled with zero policies and no `anon`/`authenticated` grants;
both functions `SECURITY DEFINER` and executable only by `service_role`; the
`allocation_mismatch` gate in `v2_apply_payment` exercised and rejecting. The
manifest is regenerated at `version: 44`. Steps 2–3 below are still to do.

```bash
# 1. apply (Supabase SQL editor, or the MCP apply_migration)   ← done
# 2. migrate the people who can already sign in
python scripts/seed_v2_identities.py --dry-run
python scripts/seed_v2_identities.py
# 3. set the real roles — the seed is deliberately conservative
#    update identities set role='owner' where identity_id='deepak';
# 4. re-snapshot the schema contract (never hand-edit the manifest)
python scripts/check_schema.py --dump
```

Until step 1 runs, v2 has no tables and answers 503 on `/v2/health?deep=1`,
which is the correct report. Nothing else changes.

---

## 4. API endpoints

All under `/v2/`. One envelope: `{"ok": true, "data": …}` or
`{"ok": false, "error": {"code", "message", "details"}}`, with `X-Request-Id`
on every response. `GET /v2/` returns the live route table, so the deployment
can always be asked what it serves.

| Method | Path | Permission | Notes |
|---|---|---|---|
| `GET` | `/v2/` | — | live route table |
| `GET` | `/v2/health` | — | separate `alive` / `configured` / `database` signals; `?deep=1` probes the DB |
| `POST` | `/v2/auth/login` | — | PIN → session token. Throttled. Never returns a token without a verified credential |
| `GET` | `/v2/auth/me` | `order:read` | identity + the permissions it holds |
| `POST` | `/v2/auth/logout` | `order:read` | revokes this session id now |
| `POST` | `/v2/auth/agent` | `production:claim` | a store PC checks its token, scope and clock at boot |
| `POST` | `/v2/quotes` | `order:price` | prices without creating. **503 `pricing_unavailable`**, never ₹0 |
| `POST` | `/v2/orders` | `order:create` | items + **storage object ids**; priced server-side |
| `GET` | `/v2/orders` | `order:read` | store- and state-filtered, cursor paged |
| `GET` | `/v2/orders/{id}` | `order:read` | order + its payments |
| `POST` | `/v2/orders/{id}/accept` | `order:accept` | freezes the spec; refuses a moved `quote_hash` |
| `POST` | `/v2/orders/{id}/transition` | `production:write` | production state only; refuses handover with a balance |
| `GET` | `/v2/queue` | `order:read` | the console's buckets, exceptions first |
| `GET` | `/v2/agent/queue` | `production:claim` | only genuinely dispatchable orders |
| `POST` | `/v2/orders/{id}/claim` | `production:claim` | returns a fencing `attempt_token` |
| `POST` | `/v2/attempts` | `production:write` | recorded **before** spooling |
| `POST` | `/v2/attempts/{id}/close` | `production:write` | `confirmed` / `failed` / `uncertain` |
| `POST` | `/v2/payments/webhook/razorpay` | HMAC | inbox → 200 → ledger. 503 if the inbox write fails |
| `POST` | `/v2/orders/{id}/payments` | `payment:record` | counter cash/UPI; a reference is required |
| `GET` | `/v2/orders/{id}/payments` | `payment:read` | ledger + balance |
| `POST` | `/v2/orders/{id}/refunds` | `payment:refund` | owner only; its own ledger row |
| `POST` | `/v2/payments/replay` | `payment:record` | finishes interrupted commits; idempotent |

### Roles

`owner` · `manager` · `counter` · `production` · `agent` · `viewer`, with an
explicit store scope (`["OSP"]`, or `["*"]` for the owner). The permission
matrix is a single table in `core/identity.py`; a route declares the permission
it needs, so authorisation is data rather than a line each handler must
remember to copy.

---

## 5. UI architecture

Three layers, no build step. The repo deploys static files to Netlify on push;
adding a bundler to gain nothing would be a new way for a deploy to fail.

```
website/app/tokens.css   the design tokens               (imported by every page)
website/app/api.js       transport: envelope, token, retry, typed ApiError
website/app/ui.js        primitives: el, money, since, badge, toast, empty/error
website/app/console.js   one page's behaviour
website/console.html     the shell
```

Three rules the existing consoles do not follow, and the reason each is here:

1. **One source for "what is urgent".** `GET /v2/queue` returns ordered buckets;
   the page renders them. Priority is a business rule, and a business rule that
   lives in a page's JavaScript is one the next page will get wrong.
2. **Failure is visible.** `ApiError` carries `code`, so the console can say
   "sign-in is not configured on the server" instead of "incorrect PIN". An
   empty list renders an empty *state* that says why. A health banner that only
   appears when things break is indistinguishable from a broken banner, so
   "health unknown" is also shown.
3. **Retries are bounded and only for reads.** A POST that creates an order is
   never retried automatically; a duplicate order is worse than an error.

`admin.html`, `jobs.html`, `mis.html` and `dtp.html` are untouched and keep
using the legacy API. They fold into this shell one screen at a time, when
there is a reason to open each of them anyway.

---

## 6. How this reaches production without breaking anything

### The mount

`api/index.py` gained one statement at the top of `do_GET`, `do_POST` and
`do_OPTIONS`:

```python
if _v2_dispatch(self, body):
    return
# …the existing chain, unchanged…
```

`dispatch()` claims only paths under `/v2/` and returns `False` for everything
else. The import is guarded: if `api/v2` ever fails to import, the error is
logged and `/v2/*` simply 404s through the old chain — a v2 defect cannot take
WhatsApp or Razorpay down. `PRINTOSKY_V2_DISABLED=1` is a kill switch that needs
no deploy.

`tests/test_v2_api.py::test_dispatch_never_claims_a_legacy_path` asserts this
for thirteen real legacy paths, and `test_dispatch_never_raises_even_on_a_broken_request`
asserts the guard.

### Environment variables

| Var | Where | Purpose |
|---|---|---|
| `PRINTOSKY_SESSION_KEY` | Vercel | HMAC key for v2 session tokens. **Unset → `/v2/auth` answers 503** and no session is ever minted |
| `PRINTOSKY_AGENT_TOKEN` | Vercel + each store PC | store-agent credential |
| `PRINTOSKY_SESSION_TTL` | Vercel (optional) | session lifetime, default 12 h |
| `PRINTOSKY_V2_DISABLED` | Vercel (optional) | `1` turns v2 off entirely |

The existing variables are unchanged. v2 reuses `SUPABASE_URL` /
`SUPABASE_SERVICE_KEY`.

### Rollout

| Step | Change | Reversible by |
|---|---|---|
| 1 | Merge. v2 serves `/v2/health` and 503s on auth (no key set yet) | `PRINTOSKY_V2_DISABLED=1` |
| 2 | ~~Apply `SCHEMA_v44`; re-dump the schema manifest~~ **done 2026-09-17** | dropping the new tables (nothing reads them) |
| 3 | Set `PRINTOSKY_SESSION_KEY`; seed identities; set real roles | unsetting the key |
| 4 | Owner uses `/console` read-only for a week beside the existing consoles | closing the tab |
| 5 | Counter takes payments through `/v2/orders/{id}/payments` at one store | the legacy console still works |
| 6 | Store agent reads `/v2/agent/queue` on a canary PC | the agent keeps its existing puller |

Nothing after step 1 is required for the P0 fixes, which are live in the legacy
paths from the moment the merge deploys.

---

## 7. The P0 fixes, in the live code

| Finding | Was | Now | Test |
|---|---|---|---|
| **F01** auth bypass | `_handle_auth_legacy` ended with "if the password is non-empty, return `ok: true` + a JWT" | Verifies against the same env hashes as `netlify/functions/auth.js`; 401 on a mismatch, **503 when nothing is configured**, no trailing success branch | `test_p0_review_regressions.py` (7 tests) |
| **F02** batch over-collection | `update_job_paid(jid, whole_batch_amount)` per job — ₹100 across 2 jobs recorded ₹200 | `core.money.allocate` splits by each job's quoted value; the parts sum to the payment exactly, asserted inside `allocate()` | 4 handler tests + 50 unit tests |
| **F04** ₹0 quote | `except Exception: total = 0.0`, then create the order and message the customer | `_quote_total_or_refuse` answers **503 `pricing_unavailable`** and creates nothing, at both the web and counter call sites | 3 handler tests |

Still open from the review, in priority order: **F03** (the durable path exists
in v2 but the legacy Razorpay route still acknowledges before persisting),
**F05/F06** (attempt recording exists in v2; `store_puller`/`print_server` do not
use it yet), **F07** (sync ownership), **F08** (object ids are enforced in v2,
not in the legacy `/order/create`), **F10** (the schema gate still skips
silently), **F11** (routing inputs), **F12** (module size).

---

## 8. What was deliberately not done

* **No framework.** No FastAPI, no React, no bundler. The review says plainly
  that a rewrite "would not itself fix these defects", and every one of these
  defects is a missing rule, not a missing library.
* **No change to printing.** `nup_imposer.py`, `print_planner.py`, the Konica
  dual-queue workaround and the `logging.basicConfig()` placement are all
  untouched — they are locked by CLAUDE.md and verified on paper.
* **No migration of existing data.** `jobs` is not backfilled into `orders`.
  The two coexist; v2 earns each workflow as it takes it over. A backfill would
  be the largest single risk in this whole upgrade for no immediate benefit.
* **No historical correction.** The F02 fix is forward-only. Producing a
  read-only discrepancy list for past batch payments is a separate, authorised
  piece of work (plan WP02, "Historical data").

---

## 9. Verification

```bash
python -m pytest tests/ -q                       # 3,300+ tests, all green
python -m pytest tests/test_core_*.py -q         # the rules
python -m pytest tests/test_v2_api.py -q         # the API, end to end, no network
python -m pytest tests/test_p0_review_regressions.py -q   # the three P0 fixes
```

The v2 suite runs entirely against in-memory adapters: no Supabase, no network,
no fixtures to tear down. A rule proven there is the rule the Supabase adapter
must satisfy, which is the answer to how F02's tests came to cover a module
nobody deploys.
