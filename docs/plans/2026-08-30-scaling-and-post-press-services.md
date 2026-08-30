# Plan — Print scaling (Fit / Actual / Custom) + post-press services without printing

**Date:** 2026-08-30 · **Author:** Claude · **Status:** planned, not started
**Branch:** `claude/post-press-no-print-options-xbnruo` · source of truth `origin/main`

> Owner's ask: *"add option for fit / actual / custom UI for printing. Also plan
> for copy, scan, laminate, foiling without printing — basically all post-press
> without printing. Solid plan without affecting the existing workflow; do not
> change anything already completed."*

This document is a plan only. **No code is changed by this commit.**

---

## 0. The non-negotiable: nothing already working may change

Both features are designed as **opt-in additions that are inert until asked for**.
The rule applied throughout:

> A job that does not carry the new field must produce the **byte-identical**
> SumatraPDF command line and the **byte-identical** imposed PDF it produces today.

That rule is enforced by tests (§3.7, §4.9), not by good intentions. Concretely,
these stay untouched:

| Locked area | Why |
|---|---|
| `nup_imposer.py` rotation model + `print_planner` orientation handling | All 12 A4 combinations verified on paper, OSP Konica 2026-08-17 (`docs/PRINT_ROTATION_MATRIX.md`) |
| `print_server._konica_queue_for_sides()` + its two call sites | Locked 2026-08-30 duplex/simplex fix — CLAUDE.md says confirm twice before touching |
| `logging.basicConfig()` at the top of `print_server.py` | Same lock; moving it silently breaks file logging |
| `tools/nup_final_test.py` `build()` 2-page-per-combo design | Same lock |
| `handle_new_photocopy` (`print_server.py:1236`) and `/new-photocopy` | Live in both consoles; the new service flow is a **parallel** path, not a rewrite |
| Existing `calculate_quote` / `calculate_item_cost` signatures + behaviour | Every quote in the system runs through them |

Where a locked function needs a new capability, the new capability lives in a
**new module or a new additive parameter with a default that reproduces today**.

---

## 1. Current state (verified in code, 2026-08-30)

### 1.1 How a print job actually reaches paper

```
order-v2.html ──buildPrintSpec()──> /order/create ──print_spec (JSON col)──> Supabase jobs
   (website/order/order-logic.js:37)   (api/handlers_order.py:225)
                                                  │
                                       store_puller.auto_print (store_puller.py:345)
                                                  │
                              print_planner.plan_print_job (print_planner.py:9)
                                    │                              │
                       1-up portrait: pass-through        nup>1 or landscape:
                                    │                    nup_imposer.perform_nup
                                    └──────────┬───────────────────┘
                                   print_server.send_to_printer (print_server.py:662)
                                        SumatraPDF -print-settings (built at :722–:749)
```

Staff manual print is a second, separate command builder:
`print_server.handle_print_item` (`print_server.py:1939`, settings at `:2041–:2070`),
reading `print_items` from the store SQLite DB.

### 1.2 What exists for scaling today

- **No scale token is emitted anywhere.** `grep -n "noscale\|shrink\|fit" print_server.py print_planner.py store_puller.py` → nothing. So every job today runs on SumatraPDF's *default* scaling (`shrink`: shrink-to-printable-area only when the page is larger; never enlarge).
- `nup_imposer.perform_nup` **already accepts** `scale_behavior` (`"Auto-Fit"` | `"Original"` | `"Custom"`), `custom_scale_width/height`, `maintain_aspect`, `is_centered` (`nup_imposer.py:248–253`, applied at `:357–364`). **`print_planner` never passes them** — every imposed job is `"Auto-Fit"`.
- So the plumbing for imposed jobs is half-built already; the pass-through 1-up portrait path has nothing.

### 1.3 What exists for services-without-print today

- `handle_new_photocopy` (`print_server.py:1236`) — instant `Completed` job, no file, no quote engine (staff types the amount), fields `service_type='Photocopy'`, pages/colour/copies.
- `handle_create_job` (`print_server.py:1328`) — walk-in entry; jobs.html offers **"Skip — No file (photocopy / service only)"** (`website/jobs.html:1338`, `njSkipFile()` at `:3668`). But it then **always** prices as a print job and **always** inserts a `print_items` row (`print_server.py:1418`, guarded only by `pages > 0`, and `pages` defaults to 1).
- `rate_card.py` already holds unused-by-the-API service rates: `BINDING_RATES` (`:105`), `LAMINATION_RATES` (`:123`), `SCANNING_RATES` (`:140`), `DTP_RATES` (`:147`), `SOFT_BINDING_WITHOUT_PRINT` (`:89`), and `calculate_finishing_cost(..., with_print=False)` (`:327`).
- **Foiling does not exist anywhere** — no rate, no key, no label.
- `/quote` (`print_server.py:1653`) can only quote *print + finishing*; there is no way to ask it "what does laminating 6 sheets cost".
- Nothing distinguishes a service job from a print job, so a laminate-only job today looks like a 1-page print job in every queue, count and panel.

---

## 2. The two features, in one sentence each

- **A. Scaling** — a new optional `scale` block in `print_spec` / `print_items`, surfaced as **Fit to page · Actual size · Custom %** in the customer and staff UIs, baked into the PDF rather than trusted to the driver.
- **B. Post-press services** — a new optional `service_kind` on a job, so **copy, scan, laminate, foil, bind-only** can be quoted, queued, worked, billed and reported **without ever touching a printer path**.

---

## 3. Feature A — Fit / Actual / Custom

### 3.1 Decisions

| # | Decision | Why |
|---|---|---|
| A1 | Scale is expressed as `print_spec.scale = {"mode": "fit"\|"actual"\|"custom", "percent": <int>}`. **Absent = today's behaviour.** | Absent-means-unchanged is the whole safety story |
| A2 | Scaling is **baked into the PDF**, not asked of the driver | The Konica driver already proved it silently ignores per-job overrides (duplex, 2026-08-29). Geometry inside the PDF is the one thing every driver honours — the same principle the imposer is built on |
| A3 | When (and only when) a scale mode is set, also emit `noscale` to SumatraPDF | Stops the driver re-scaling what we baked. Never emitted for jobs without `scale`, so existing command lines are unchanged |
| A4 | v1 offers scale for **1-up only**. For `nup ≥ 2` the control is hidden and any value is ignored + logged | N-up *is* a fit; keeps the verified 12-combination matrix out of scope entirely |
| A5 | `custom` accepts **25–400 %**, clamped, and warns in the UI above 100 % ("content larger than the sheet will be cropped") | A silent crop is exactly the failure mode CLAUDE.md forbids |
| A6 | **Price does not change with scale.** Billing stays per sheet | Owner rule; UI must say so next to the control so nobody expects 50 % to halve the bill |
| A7 | `fit` means *fill the printable area, aspect kept, centred*; `actual` means *100 %, centred, crop if oversize* | Matches what a customer means by the words, and what Acrobat calls "Fit" / "Actual size" |

### 3.2 New module — `pdf_scaler.py` (new file, ~80 lines)

```python
def apply_scale(pdf_bytes: bytes, mode: str, percent: int | None,
                paper_size: str = "A4") -> bytes | None:
    """Return a re-laid-out PDF, or None when no transform is needed.

    None is the important return: mode falsy / unknown -> None -> the caller
    prints the original file, byte-for-byte, exactly as it does today.
    """
```

Implementation: one `fitz` output page per input page at the target sheet size,
one `show_pdf_page()` into a centred rect whose size is the fit-scale, 100 %, or
`percent/100` of the source box. It never rotates — rotation belongs to the
imposer and stays there.

Returns `None` (no-op) for: no mode, unknown mode, `custom` without a usable
percent, or `actual` when the page already equals the sheet.

### 3.3 Wiring — pass-through path (1-up portrait)

In `print_planner.plan_print_job`, immediately after the `pages_included` slice
and **before** the `nup > 1 or nup_orient == "landscape"` branch:

```python
scale = (spec.get("scale") or {}) if nup == 1 else {}
if scale.get("mode") and nup_orient != "landscape":
    scaled = pdf_scaler.apply_scale(read(current_pdf), scale["mode"],
                                    scale.get("percent"), paper_size or "A4")
    if scaled:
        current_pdf = write(temp_dir/"scaled.pdf", scaled)
```

and each returned action gains `"scale_applied": bool`. `send_to_printer` grows
one optional kwarg `scale_applied: bool = False`; when true it appends `noscale`
to `settings_parts`. Default `False` ⇒ identical string to today.

### 3.4 Wiring — imposed path (1-up landscape)

No imposer change. `plan_print_job` passes the parameters `perform_nup` already
has:

| spec | passed to `perform_nup` |
|---|---|
| no `scale` (today) | nothing — defaults `"Auto-Fit"` (**unchanged**) |
| `fit` | `scale_behavior="Auto-Fit"` (explicit, same result) |
| `actual` | `scale_behavior="Original"` |
| `custom` | `scale_behavior="Custom"`, `custom_scale_width/height` = percent × source box |

### 3.5 Wiring — staff manual print (`print_items`)

Additive SQLite columns, both nullable (`fix_db.py` `ALTER TABLE ADD COLUMN`
pattern; also add to `install/bootstrap_db.py` DDL):

```sql
ALTER TABLE print_items ADD COLUMN scale_mode    TEXT;    -- NULL = today
ALTER TABLE print_items ADD COLUMN scale_percent INTEGER; -- NULL = today
```

`handle_print_item` calls `pdf_scaler.apply_scale` when `scale_mode` is set,
prints the temp file, appends `noscale`, and cleans up in a `finally`. NULL
columns ⇒ the current code path, untouched.

### 3.6 UI

**Customer — `website/order-v2.html` + `website/order/order-logic.js`**
A new `.ov2-card` next to Paper size, hidden while `nup !== 1`:

```
Page size on paper
[ Fit to page (default) ] [ Actual size ] [ Custom % ]
   custom -> a number input, 25–400, live "≈ 3 pages will be cropped" warning
Price is per sheet — scaling does not change it.
```
`buildPrintSpec()` (`order-logic.js:37`) adds `scale: {mode, percent}` **only when
the customer picked something other than Fit**, so the default order body is
unchanged. `buildOperatorNote()` gains "Actual size" / "Scaled 75 %" so the
counter sees it. No API change at all: `api/handlers_order.py:284` already
persists the whole `print_spec` blob verbatim.

**Staff — print panel in `website/jobs.html` (`#jp-*`, ~:2349) and the mirror in `admin.html`**
One `<select>` (Fit / Actual / Custom) + a percent input, saved through the
existing `/update-job` body into the new `print_items` columns.

**Staff — New Job modal step 3** (`website/jobs.html:1346`): the same select, so a
walk-in gets the same control.

### 3.7 Tests (new files, no existing test edited)

- `tests/test_pdf_scaler.py` — geometry per mode; `None` for no-op cases; percent clamping.
- `tests/test_print_planner_scale.py` — **the guard test**: a spec *without* `scale` yields an action list identical to today's (same paths, same flags, no `scale_applied`); the 12 matrix specs still produce their current plans.
- `tests/test_print_server_scale_settings.py` — `send_to_printer(..., scale_applied=False)` builds the exact current settings string; `True` appends `noscale`.
- `tests/test_nup_rotation_matrix.py` — untouched, must still pass.
- Paper proof before release: `python tools/proof_run.py FILE.pdf --send` for Fit/Actual/75 %/150 % on the OSP Konica; record the result in `docs/PRINT_ROTATION_MATRIX.md` (append only).

### 3.8 Failure modes → fail loud

| Failure | Handling |
|---|---|
| `apply_scale` raises (corrupt PDF) | `ops_watchdog.guard("print.scale", reraise=False)` → alert + print unscaled rather than not print |
| Custom % out of range | Clamped, alerted once per job |
| Scale asked for on `nup ≥ 2` | Ignored + `logging.info` + one watchdog report (it means a UI leak) |

### 3.9 Shipping order

| PR | Contents | Risk |
|---|---|---|
| A-1 | `pdf_scaler.py` + its tests. Nothing calls it | none |
| A-2 | Planner + `send_to_printer` wiring + guard tests | low — inert without `scale` |
| A-3 | `print_items` columns + `handle_print_item` | low |
| A-4 | Staff UI (jobs/admin) — paper-test here | low |
| A-5 | order-v2 customer UI | low |

---

## 4. Feature B — Copy / scan / laminate / foil / bind, with no printing

### 4.1 Decisions

| # | Decision | Why |
|---|---|---|
| B1 | A service job is a **normal `jobs` row** with a new `service_kind`, not a new table | Revenue, payment, pickup code, WhatsApp notify, daily summary and MIS all already read `jobs`. A new table would mean re-implementing every one of them |
| B2 | `service_kind` NULL ⇒ print job ⇒ everything behaves exactly as today | Same absent-means-unchanged rule as Feature A |
| B3 | A service job **never** creates a `print_items` row, never enters a printer queue, never auto-prints | The one behaviour that makes "post-press without printing" true |
| B4 | v1 kinds: `copy`, `scan`, `laminate`, `foil`, `bind` (`other` as escape hatch) | Covers the owner's list; `other` stops staff forcing a wrong kind |
| B5 | **Foiling has no rate in the system** → it is staff-priced in v1 and flagged `needs_manual_price`; a foil job completed at ₹0 raises an alert | Inventing a rate would be worse than asking. Silent ₹0 is a fail-loud violation |
| B6 | `/new-photocopy` and its two console buttons stay exactly as they are | Already in daily use. Copy-as-a-service is the richer path; the old one keeps working until the owner retires it |
| B7 | Service jobs are staff-console-first. Customer-facing (order-v2 / WhatsApp) is **phase 4**, after the counter flow is proven | Post-press needs the physical item in hand; an online-only order can't start work |
| B8 | Copy is priced off the **existing print rate card** (a photocopy sheet costs what a printed sheet costs) unless the owner says otherwise | No new numbers invented |

### 4.2 Data model (additive only)

Cloud — new `api/migrations/SCHEMA_v29_service_jobs.sql`:

```sql
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_kind TEXT;   -- NULL = print job
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_meta JSONB;  -- per-kind quantities
CREATE INDEX IF NOT EXISTS jobs_service_kind_idx ON jobs (service_kind)
  WHERE service_kind IS NOT NULL;
```

Store SQLite — same two columns as `TEXT` via the `fix_db.py` ALTER pattern, plus
the DDL in `install/bootstrap_db.py`. `docs/SCHEMA.md` gains the two rows.

`service_meta` shape per kind (JSON, validated in one place):

| kind | meta |
|---|---|
| `copy` | `{sheets, copies, colour, sides, paper_size}` |
| `scan` | `{sheets, colour, dpi, delivery: "whatsapp"\|"email"\|"usb", destination}` |
| `laminate` | `{sheets, lam_type: "normal"\|"with_col"\|"a4"\|"a3_bw"\|"a3_col"\|"id"}` |
| `foil` | `{sheets, notes, manual_price}` |
| `bind` | `{sheets, binding, paper_size, project_cover?}` — reuses `calculate_finishing_cost(..., with_print=False)` |
| `other` | `{description, manual_price}` |

### 4.3 Pricing — `rate_card.py`, **new Section 11 only**

```python
SERVICE_KINDS = {...}                       # labels, whether manual price is required
FOILING_RATES = {}                          # empty on purpose — owner input pending
def calculate_service_quote(kind: str, meta: dict) -> dict:
    """{ total, breakdown[], needs_manual_price, label } — additive; no existing
    function's signature or behaviour changes."""
```

It composes what is already there: `get_print_rate`/`calc_sheets` for `copy`,
`SCANNING_RATES` tiers for `scan`, `LAMINATION_RATES` for `laminate`,
`calculate_finishing_cost(..., with_print=False)` for `bind`, and
`needs_manual_price=True` for `foil`/`other`.

### 4.4 Endpoints (`print_server.py`, new handlers next to the existing ones)

| Route | Purpose |
|---|---|
| `GET /service-quote?kind=laminate&sheets=6&lam_type=a4` | live price for the modal; mirrors `/quote`'s shape |
| `POST /new-service` | create a service job: `Queued` (or `Draft` without payment/override, same gate as `handle_create_job`), **no `print_items` row**, `service_kind` + `service_meta` set, `_jt_log` audit event |
| `POST /complete-job` | **unchanged** — service jobs complete through the existing payment path |

`handle_create_job` gains exactly one guard: skip the `print_items` insert when
`service_kind` is set and the kind is not printing. Nothing else in it moves.

### 4.5 Lifecycle

```
Queued ──(staff starts)──> In Progress ──> Ready (pickup code + WhatsApp notify,
        reusing handle_mark_ready) ──> Completed (payment via handle_complete_job)
```
No `Printed` state, no printer, no claim (`jobs.print_claimed_at` untouched).

### 4.6 Where service jobs must be *excluded* — the full list

| Place | Change |
|---|---|
| `store_puller` pull loop | Already safe: no `file_url` ⇒ never pulled. **Add a test that pins it** |
| `watcher.py` auto-print | File-driven; service jobs have no file. Pin with a test |
| Printer-breakdown counts, jobs.html/admin.html queue "pending print" | Filter `service_kind IS NULL` |
| Print panel | Replace Print button with a **Service panel** (kind, quantities, quote, Start / Ready / Collect) |
| `handle_print_item` | Refuse a service job with a clear error, not a stack trace |
| Colour detection / `detect-colour` | Not offered for service jobs |

### 4.7 Console UI

- **jobs.html** — a `+ Service` button beside `+ Photocopy`, opening a 3-step modal (kind → quantities+live quote → payment), reusing the existing `nj-*` payment step markup and CSS. Queue rows get a kind pill (`🖨 Print` / `📄 Copy` / `🔍 Scan` / `✨ Laminate` / `🥇 Foil` / `📕 Bind`).
- **admin.html** — the same modal, mirrored (the two consoles already duplicate the photocopy/new-job modals).
- Job detail shows the service panel instead of the print panel when `service_kind` is set.

### 4.8 Reporting + a free reconciliation win

`daily_summary`, `/report` and the MIS revenue panels sum `amount_collected` from
`jobs`, so service revenue lands automatically once these are `jobs` rows — no
change needed. Panels that claim to count *print* jobs get a `service_kind IS
NULL` filter.

Bonus (phase 3): `konica_jobs.job_type` already records **`Copy`** and **`Scan`**
straight off the printer (`SCHEMA.sql:210`). Once copy/scan service jobs exist,
MIS can compare *counter-recorded copies* against *machine-counted copies* — the
gap is unbilled walk-in copying. This is the first time that number becomes
knowable.

### 4.9 Tests (new files)

- `tests/test_service_quote.py` — every kind, tier boundaries, `needs_manual_price` for foil/other.
- `tests/test_service_jobs.py` — `/new-service` creates the row with no `print_items`; payment gate; audit event; `handle_print_item` refuses a service job.
- `tests/test_service_isolation.py` — **the guard test**: a service job is never pulled by `store_puller`, never auto-printed, never counted in printer queues.
- Existing `tests/test_local_print.py`, `test_store_puller.py`, `test_print_planner.py`, `test_update_job.py` must pass unchanged.

### 4.10 Fail-loud (per `docs/FAIL_LOUD.md`)

| Condition | Alert |
|---|---|
| Foil/other job completed with ₹0 and no override reason | `report("service.unpriced", False, ...)` |
| Unknown `service_kind` reaching a console or handler | alert, never a silent skip |
| `/service-quote` raises | `guard("service.quote")` → alert, UI shows "enter price manually" |
| A service job sitting in `Queued` > 24 h | daily digest line (reuses `store_digest.py`) |

New code adds **zero** `except Exception: pass` — `tests/test_fail_loud_rule.py`
budgets stay as they are.

### 4.11 Shipping order

| PR | Contents | Risk |
|---|---|---|
| B-1 | `rate_card` Section 11 + `tests/test_service_quote.py`. Nothing calls it | none |
| B-2 | Migrations (cloud + SQLite + `docs/SCHEMA.md`) | none — additive columns |
| B-3 | `/service-quote` + `/new-service` + `handle_create_job` guard + isolation tests | low |
| B-4 | jobs.html console UI (modal + pills + service panel) | low |
| B-5 | admin.html mirror + MIS `service_kind` filters | low |
| B-6 | Konica copy/scan reconciliation panel | medium (new analysis, no print path) |
| B-7 *(phase 4, optional)* | Customer-facing post-press ordering in order-v2 / WhatsApp | decide after B-4 is live |

---

## 5. Suggested overall sequence

1. **A-1 → A-3** (scaling core, invisible) — ships behind no UI, provable by tests.
2. **B-1 → B-3** (service core, invisible) — same.
3. **A-4** staff scaling UI + **paper proof on the OSP Konica**.
4. **B-4/B-5** service console UI — the counter can now take laminate/foil/scan work.
5. **A-5** customer scaling UI.
6. **B-6** reconciliation, **B-7** customer post-press — only if the counter flow proves out.

Rough size: Feature A ≈ 350 lines of code + 200 of tests; Feature B ≈ 500 + 300,
plus console markup. Neither touches a locked file except through additive,
default-off parameters.

---

## 6. Open questions for the owner (block only the marked steps)

| # | Question | Blocks |
|---|---|---|
| Q1 | **Foiling rates** — per sheet? per A4? a setup charge? Different for gold/silver? | B-1 pricing for `foil` (ships as manual-price until answered) |
| Q2 | **Custom scale bounds** — is 25–400 % right, and should customers get Custom % at all, or staff only? | A-5 |
| Q3 | **Scan** — priced per sheet off `SCANNING_RATES` as-is? Default delivery (WhatsApp / email / USB)? Colour and DPI choices? | B-1 `scan` |
| Q4 | **Copy** — should a walk-in copy become a tracked `Queued` job, or keep the current instant-Completed photocopy entry? | B-3/B-4 (both can coexist) |
| Q5 | **Lamination** — which sizes are actually offered (A4 / A3 / ID card), and is ID-card lamination its own price? | B-1 `laminate` |
| Q6 | **Bind-only** — is `SOFT_BINDING_WITHOUT_PRINT = 100` still current, and do spiral/wiro cost the same without print? | B-1 `bind` |
| Q7 | Should customers be able to order post-press online (drop-off first), or is this counter-only? | B-7 |

---

## 7. Explicit non-goals for this work

- No change to the imposition/rotation model or the verified 12-combination matrix.
- No change to the Konica dual-queue duplex/simplex fix.
- No change to existing quote maths for print jobs.
- No new printer, tray or media handling.
- No retirement of `/new-photocopy` or its buttons.
- No pricing invented where the owner has not given a rate.
