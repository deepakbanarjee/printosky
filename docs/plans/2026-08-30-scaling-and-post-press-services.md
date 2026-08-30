# Plan — Print scaling (Fit / Actual / Custom) + post-press services without printing

**Date:** 2026-08-30 · **Author:** Claude · **Status:** planned, not started
**Branch:** `claude/post-press-no-print-options-xbnruo` · source of truth `origin/main`

> Owner's ask: *"add option for fit / actual / custom UI for printing. Also plan
> for copy, scan, laminate, foiling without printing — basically all post-press
> without printing. Solid plan without affecting the existing workflow; do not
> change anything already completed."*
> Follow-up: *"everything is baked before sending to printer. Also show the user
> a preview while scaling."*

This document is a plan only. **No code is changed by this commit.**

---

## 0. Two rules this plan is built on

### Rule 1 — nothing already working may change

> A job that does not carry the new field must produce the **byte-identical**
> SumatraPDF command line and the **byte-identical** PDF it produces today.

Enforced by guard tests (§3.9, §4.9), not by good intentions. These stay untouched:

| Locked area | Why |
|---|---|
| `nup_imposer.py` rotation model + `print_planner` orientation handling | All 12 A4 combinations verified on paper, OSP Konica 2026-08-17 (`docs/PRINT_ROTATION_MATRIX.md`) |
| `print_server._konica_queue_for_sides()` + its two call sites | Locked 2026-08-30 duplex/simplex fix — CLAUDE.md says confirm twice before touching |
| `logging.basicConfig()` at the top of `print_server.py` | Same lock; moving it silently breaks file logging |
| `tools/nup_final_test.py` `build()` 2-page-per-combo design | Same lock |
| `handle_new_photocopy` (`print_server.py:1236`) and `/new-photocopy` | Live in both consoles; the service flow is a **parallel** path, not a rewrite |
| Existing `calculate_quote` / `calculate_item_cost` behaviour | Every quote in the system runs through them |

### Rule 2 — everything is baked before it reaches the printer *(owner, 2026-08-30)*

**No geometry is ever left to the driver.** Page size, rotation, imposition and
now scaling are all decided in Python and written into a real PDF; the printer
receives a file whose pages are already exactly what should come out.

This is not a new principle — it is the one the imposer was built on, after the
Konica driver was caught silently ignoring per-job `duplex`/`simplex` overrides
(2026-08-29). Scaling follows it exactly:

- `pdf_scaler.apply_scale()` produces the baked PDF for the pass-through path.
- `nup_imposer.perform_nup()` bakes for the imposed path (it already draws into
  an explicit rect — that *is* baking).
- SumatraPDF's `noscale` is emitted alongside a baked file purely as a **guard**
  — "don't touch what we already decided" — never as the mechanism. If a driver
  ignores it, the output is still correct, because the correctness lives in the
  file.

**Corollary:** the preview (§3.6) renders the baked PDF itself. What the operator
or customer sees is the artifact, not a drawing of it.

---

## 1. Current state (verified in code, 2026-08-30)

### 1.1 How a print job reaches paper

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

- **No scale token is emitted anywhere.** `grep -n "noscale\|shrink\|fit" print_server.py print_planner.py store_puller.py` → nothing. Every job runs on SumatraPDF's *default* (`shrink`: shrink-to-printable-area only when the page is larger; never enlarge). Nothing is baked and nothing is asked for — it is the driver's choice today.
- `nup_imposer.perform_nup` **already accepts** `scale_behavior` (`"Auto-Fit"` | `"Original"` | `"Custom"`), `custom_scale_width/height`, `maintain_aspect`, `is_centered` (`nup_imposer.py:248–253`, applied at `:357–364`). **`print_planner` never passes them** — every imposed job is `"Auto-Fit"`.
- So the imposed path already bakes a fit; the pass-through 1-up portrait path bakes nothing.

### 1.3 What exists for preview today

| Surface | What it shows |
|---|---|
| `website/order-v2.html` (customer) | pdf.js 3.11.174 (UMD global) renders a per-page thumbnail strip client-side (`order-ui.js:150`, `:268–277`); an N-up mini-diagram (`#ov2-nupSheet`) is a CSS grid, not a render |
| `website/jobs.html` + `admin.html` print panel | `<iframe id="jp-preview-iframe">` (`jobs.html:2338`) pointing at the raw file (`file_url`, else the store PC `/file`) — the browser's own PDF viewer |
| Both consoles | A full-screen preview modal over the same raw file |

**Nothing anywhere previews the file as it will actually print.** The iframe shows
the source document; imposition, page skipping and (soon) scaling are invisible.

### 1.4 What exists for services-without-print today

- `handle_new_photocopy` (`print_server.py:1236`) — instant `Completed` job, no file, no quote engine (staff types the amount).
- `handle_create_job` (`print_server.py:1328`) — walk-in entry; jobs.html offers **"Skip — No file (photocopy / service only)"** (`website/jobs.html:1338`, `njSkipFile()` at `:3668`). But it then **always** prices as a print job and **always** inserts a `print_items` row (`print_server.py:1418`, guarded only by `pages > 0`, and `pages` defaults to 1).
- `rate_card.py` already holds service rates nothing can reach: `BINDING_RATES` (`:105`), `LAMINATION_RATES` (`:123`), `SCANNING_RATES` (`:140`), `DTP_RATES` (`:147`), `SOFT_BINDING_WITHOUT_PRINT` (`:89`), `calculate_finishing_cost(..., with_print=False)` (`:327`).
- **Foiling does not exist anywhere** — no rate, no key, no label.
- `/quote` (`print_server.py:1653`) can only price *print + finishing*. There is no way to ask "what does laminating 6 sheets cost".
- Nothing distinguishes a service job from a print job, so a laminate-only job looks like a 1-page print job in every queue, count and panel.

---

## 2. The two features, in one sentence each

- **A. Scaling** — an optional `scale` block in `print_spec` / `print_items`, surfaced as **Fit to page · Actual size · Custom %** for **1-up only**, baked into the PDF, with a live preview of the baked result.
- **B. Post-press services** — an optional `service_kind` on a job, so **copy, scan, laminate, foil, bind-only** can be quoted, queued, worked, billed and reported **without ever touching a printer path**.

---

## 3. Feature A — Fit / Actual / Custom

### 3.1 Decisions

| # | Decision | Why |
|---|---|---|
| A1 | `print_spec.scale = {"mode": "fit"\|"actual"\|"custom", "percent": <int>}`. **Absent = today's behaviour.** | Absent-means-unchanged is the whole safety story |
| A2 | Scaling is **baked into the PDF**, never asked of the driver (Rule 2) | The Konica already proved it ignores per-job overrides. Correctness must live in the file |
| A3 | When a scale mode is set, also emit `noscale` — as a **guard**, not the mechanism | Stops a driver re-scaling a baked file. Never emitted for jobs without `scale`, so existing command lines are unchanged |
| A4 | **Scaling is offered on 1-up only. Every other layout (2/4/6/9-up) always fits to the printable area.** Not a phase — the rule. *(owner, 2026-08-30)* | See §3.2 |
| A5 | `custom` accepts **25–400 %**, clamped, with a live crop warning above 100 % | A silent crop is exactly the failure mode CLAUDE.md forbids |
| A6 | **Price does not change with scale.** Billing stays per sheet, and the UI says so | Owner rule; otherwise 50 % reads as half the bill |
| A7 | `fit` = fill the printable area, aspect kept, centred. `actual` = 100 %, centred, cropped if oversize | Matches what the words mean to a customer, and what Acrobat calls Fit / Actual size |
| A8 | **The preview renders the baked PDF**, on both surfaces, from the same `pdf_scaler` code | A preview drawn by different code than the printer gets is a preview that can lie |

### 3.2 Why scaling is 1-up only — the reasoning

N-up **is** a scale operation. Choosing 4-up already says "shrink each page to a
quarter-sheet slot"; there is no second, independent scale question to ask. The
three modes stop meaning anything sensible there:

- **Fit** is what N-up already does, in every case.
- **Actual** on 4-up means printing a full-size A4 page into a quarter-sheet slot
  — three-quarters of every page is cropped away. A customer choosing "Actual
  size" is asking for their document at true size, not for it to be guillotined.
- **Custom %** would need a reference the customer cannot see: percent of the
  original page, or percent of the slot? Either reading produces overlap into
  neighbouring slots at >100 %, so the imposed sheet stops being readable.

And the slot geometry is the part that was verified on paper across all 12
combinations. Leaving it fixed keeps that proof valid.

So: **`nup == 1` → the three modes. `nup >= 2` → always fit to the printable
area, no control shown, any `scale` value in the spec ignored and logged.**
(`perform_nup`'s existing default is `"Auto-Fit"`, so this is literally today's
behaviour, now stated as a rule rather than an accident.)

> Note on "v1": the earlier draft staged this as "1-up in v1, N-up later". That
> staging is now dropped — the rule above is permanent, which removes a phase, a
> future paper-proof run, and a UI state that could confuse a customer.

### 3.3 New module — `pdf_scaler.py` (new file, ~90 lines)

```python
def scale_rect(page_w: float, page_h: float, sheet: str,
               mode: str, percent: int | None) -> dict | None:
    """Pure geometry: where one page lands on the sheet.
    -> {x0, y0, x1, y1, sheet_w, sheet_h, scale, crops: bool} or None for a no-op.
    The single source of truth for printing AND for both previews."""

def apply_scale(pdf_bytes: bytes, mode: str, percent: int | None,
                paper_size: str = "A4") -> bytes | None:
    """Bake it: one output page per input page at sheet size, content drawn into
    scale_rect()'s rect. Returns None when no transform is needed -> the caller
    prints the original file, byte-for-byte, exactly as today."""
```

`scale_rect` is split out deliberately: the printer path, the store-PC preview
and the customer preview all read the same function, so a preview cannot drift
from the print. It never rotates — rotation belongs to the imposer.

`apply_scale` returns `None` (no-op) for: no mode, unknown mode, `custom` without
a usable percent, or `actual` when the page already equals the sheet.

### 3.4 Wiring — pass-through path (1-up portrait)

In `print_planner.plan_print_job`, after the `pages_included` slice and **before**
the `nup > 1 or nup_orient == "landscape"` branch:

```python
scale = (spec.get("scale") or {}) if nup == 1 else {}
if scale.get("mode") and nup_orient != "landscape":
    scaled = pdf_scaler.apply_scale(read(current_pdf), scale["mode"],
                                    scale.get("percent"), paper_size or "A4")
    if scaled:
        current_pdf = write(temp_dir/"scaled.pdf", scaled)
```

Each returned action gains `"scale_applied": bool`. `send_to_printer` grows one
optional kwarg `scale_applied: bool = False`; when true it appends `noscale`.
Default `False` ⇒ identical settings string to today.

### 3.5 Wiring — imposed path (1-up landscape)

Also baked, by the imposer, with parameters it already has — no imposer change:

| spec | passed to `perform_nup` |
|---|---|
| no `scale` (today) | nothing — defaults `"Auto-Fit"` (**unchanged**) |
| `fit` | `scale_behavior="Auto-Fit"` (explicit, same result) |
| `actual` | `scale_behavior="Original"` |
| `custom` | `scale_behavior="Custom"`, `custom_scale_width/height` from `scale_rect()` |

`nup >= 2` never reaches this table — it always uses the `"Auto-Fit"` default (A4).

### 3.6 Preview — the operator and the customer both see the baked page

**Decision (owner, 2026-08-30): render the baked PDF, one page at a time, switchable.**

#### 3.6.1 Staff console — `GET /scale-preview` (store PC)

```
GET /scale-preview?job_id=OSP-…&page=1&mode=custom&percent=75&paper_size=A4
   -> image/png
```

The handler runs **the same `pdf_scaler.apply_scale()`** the printer path runs,
then `page.get_pixmap(dpi=96)` on the result (PyMuPDF is already a dependency).
The PNG is therefore a photograph of the artifact, not a re-drawing of it.

- Replaces the `#jp-preview-iframe` content **only while a scale mode is active**; with no scale set the panel keeps today's raw-file iframe, untouched.
- Page switcher (`◀ 3 / 24 ▶`) + a sheet outline, so mixed-size documents can be checked page by page. One render per view — a 200-page job renders one page, not 200.
- Debounced 250 ms on percent typing; `AbortController` cancels superseded renders.
- Cached per `(job_id, page, mode, percent)` in a bounded LRU on the store PC; the baked bytes for the *current* settings are reused by the print itself when the operator hits Print, so the preview costs nothing extra at print time.
- **Store PC unreachable → the preview area says so and the scale control disables.** No silent stale image, no fake. (`docs/FAIL_LOUD.md`.)

#### 3.6.2 Customer — order-v2

The customer's file is in the browser and pdf.js already renders every page
(`order-ui.js:268`). Two viable sources for the *placement*; the geometry must be
the printer's either way:

- **Chosen:** `GET /order/scale-rect?page_w=&page_h=&sheet=A4&mode=&percent=` on Vercel returns `scale_rect()`'s numbers (a few hundred bytes, no file upload, no PDF work). The browser draws the pdf.js page image into that rect on a sheet-shaped canvas, with the overflow region hatched red and a live "**4 pages will be cropped**" count. `api/handlers_order.py:88` already does `import rate_card` from the repo root, so `import pdf_scaler` works the same way; PyMuPDF is already in `api/requirements.txt` if a full baked render is ever wanted later.
- Endpoint unreachable → the preview hides and says "preview unavailable" rather than showing an approximation. Same fail-loud rule.

Shown for `nup == 1` only, next to the scale control; for N-up the existing
`#ov2-nupSheet` diagram already communicates "everything fits into slots".

#### 3.6.3 What the preview must show

Sheet outline · printable-area dashes · the page as it will land · hatched crop
region with a page count · the plain line **"Price is per sheet — scaling does
not change it."**

### 3.7 UI

**Customer — `website/order-v2.html` + `website/order/order-logic.js`**
A new `.ov2-card` next to Paper size, hidden while `nup !== 1`:

```
Page size on paper
[ Fit to page (default) ] [ Actual size ] [ Custom % ]
   custom -> number input, 25–400, live preview + crop warning
Price is per sheet — scaling does not change it.
```
`buildPrintSpec()` (`order-logic.js:37`) adds `scale` **only when the customer
picked something other than Fit**, so the default order body is unchanged.
`buildOperatorNote()` gains "Actual size" / "Scaled 75 %". No API change:
`api/handlers_order.py:284` already persists the whole `print_spec` verbatim.

**Staff — print panel in `website/jobs.html` (`#jp-*`, ~:2349) and `admin.html`**
A select (Fit / Actual / Custom) + percent input above the preview pane, saved
through the existing `/update-job` body into the new `print_items` columns, with
the §3.6.1 preview beside it.

**Staff — New Job modal step 3** (`website/jobs.html:1346`): the same select, so a
walk-in gets the same control.

### 3.8 Wiring — staff manual print (`print_items`)

Additive SQLite columns, both nullable (`fix_db.py` `ALTER TABLE ADD COLUMN`
pattern; also add to `install/bootstrap_db.py` DDL):

```sql
ALTER TABLE print_items ADD COLUMN scale_mode    TEXT;    -- NULL = today
ALTER TABLE print_items ADD COLUMN scale_percent INTEGER; -- NULL = today
```

`handle_print_item` bakes via `apply_scale` when `scale_mode` is set, prints the
temp file, appends `noscale`, cleans up in a `finally`. NULL ⇒ today's path.

### 3.9 Tests (new files, no existing test edited)

- `tests/test_pdf_scaler.py` — `scale_rect` geometry per mode, crop detection, percent clamping; `apply_scale` page sizes; `None` for every no-op case.
- `tests/test_print_planner_scale.py` — **the guard test**: a spec *without* `scale` yields an action list identical to today's; the 12 matrix specs plan unchanged; **`nup >= 2` ignores `scale` entirely** (A4).
- `tests/test_print_server_scale_settings.py` — `scale_applied=False` builds the exact current settings string; `True` appends `noscale`.
- `tests/test_scale_preview.py` — `/scale-preview` returns a PNG whose page box equals `scale_rect()`; bad params → 400, not a stack trace; store-PC failure surfaces as an error, never a stale image.
- `tests/order/order-logic.test.mjs` (existing node harness) — `buildPrintSpec` omits `scale` at default, includes it otherwise.
- `tests/test_nup_rotation_matrix.py` — untouched, must still pass.
- Paper proof before release: `python tools/proof_run.py FILE.pdf --send` for Fit / Actual / 75 % / 150 % on the OSP Konica; append the result to `docs/PRINT_ROTATION_MATRIX.md`.

### 3.10 Failure modes → fail loud

| Failure | Handling |
|---|---|
| `apply_scale` raises (corrupt PDF) | `ops_watchdog.guard("print.scale", reraise=False)` → alert, print unscaled rather than not print |
| Custom % out of range | Clamped, alerted once per job |
| `scale` present with `nup >= 2` | Ignored + logged + one watchdog report (it means a UI leak) |
| Preview render fails / store PC offline | Preview area shows the error and the control disables — never a stale or approximated image |

### 3.11 Shipping order

| PR | Contents | Risk |
|---|---|---|
| A-1 | `pdf_scaler.py` (`scale_rect` + `apply_scale`) + tests. Nothing calls it | none |
| A-2 | Planner + `send_to_printer` wiring + guard tests | low — inert without `scale` |
| A-3 | `print_items` columns + `handle_print_item` | low |
| A-4 | `/scale-preview` + `/order/scale-rect` + tests | low — read-only endpoints |
| A-5 | Staff UI + preview pane — **paper proof here** | low |
| A-6 | order-v2 customer UI + preview canvas | low |

---

## 4. Feature B — Copy / scan / laminate / foil / bind, with no printing

### 4.1 Decisions

| # | Decision | Why |
|---|---|---|
| B1 | A service job is a **normal `jobs` row** with a new `service_kind`, not a new table | Revenue, payment, pickup code, WhatsApp notify, daily summary and MIS all already read `jobs`. A new table means re-implementing every one of them |
| B2 | `service_kind` NULL ⇒ print job ⇒ everything behaves exactly as today | Same absent-means-unchanged rule as Feature A |
| B3 | A service job **never** creates a `print_items` row, never enters a printer queue, never auto-prints | The one behaviour that makes "post-press without printing" true |
| B4 | v1 kinds: `copy`, `scan`, `laminate`, `foil`, `bind` (+ `other` as escape hatch) | Covers the owner's list; `other` stops staff forcing a wrong kind |
| B5 | **Foiling and roll-lamination rates are set** (§4.3.1, owner 2026-08-30): per sheet, A4/A3, minimum 10 sheets then piece rate. `other` stays staff-priced and flagged `needs_manual_price` | Real rates, no invented ones; a ₹0 service job still alerts |
| B6 | `/new-photocopy` and its two console buttons stay exactly as they are | Already in daily use. Copy-as-a-service is the richer path; the old one keeps working until the owner retires it |
| B7 | Staff-console first. Customer-facing (order-v2 / WhatsApp) is a later decision | Post-press needs the physical item in hand |
| B8 | Copy is priced off the **existing print rate card** unless the owner says otherwise | No new numbers invented |

### 4.2 Data model (additive only)

Cloud — new `api/migrations/SCHEMA_v29_service_jobs.sql`:

```sql
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_kind TEXT;   -- NULL = print job
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_meta JSONB;  -- per-kind quantities
CREATE INDEX IF NOT EXISTS jobs_service_kind_idx ON jobs (service_kind)
  WHERE service_kind IS NOT NULL;
```

Store SQLite — the same two columns as `TEXT` via the `fix_db.py` ALTER pattern,
plus the DDL in `install/bootstrap_db.py`. `docs/SCHEMA.md` gains the two rows.

`service_meta` shape per kind (JSON, validated in one place):

| kind | meta |
|---|---|
| `copy` | `{sheets, copies, colour, sides, paper_size}` |
| `scan` | `{sheets, colour, dpi, delivery: "whatsapp"\|"email"\|"usb", destination}` |
| `laminate` | `{sheets, paper_size, lam_type: "pouch"\|"roll"\|"cover"\|"id"}` — pouch reads `LAMINATION_RATES`, roll reads `ROLL_LAM_RATES` (§4.3.1) |
| `foil` | `{sheets, paper_size, foil_type: "gold"\|"silver", notes}` — `foil_type` is for the work order; both cost the same |
| `bind` | `{sheets, binding, paper_size, project_cover?}` — reuses `calculate_finishing_cost(..., with_print=False)` |
| `other` | `{description, manual_price}` |

### 4.3 Pricing — `rate_card.py`, **new Section 11 only**

```python
SERVICE_KINDS = {...}      # labels, whether a manual price is required
def calculate_service_quote(kind: str, meta: dict) -> dict:
    """{ total, breakdown[], needs_manual_price, label } — additive; no existing
    function's signature or behaviour changes."""
```

It composes what is already there: `get_print_rate`/`calc_sheets` for `copy`,
`SCANNING_RATES` tiers for `scan`, `LAMINATION_RATES` for pouch lamination,
`calculate_finishing_cost(..., with_print=False)` for `bind` — plus the two new
tables below.

#### 4.3.1 Foiling and roll lamination *(owner rates, 2026-08-30)*

```python
FOILING_RATES  = {"A4": 30, "A3": 50}   # per sheet; gold and silver identical
ROLL_LAM_RATES = {"A4": 15, "A3": 30}   # per sheet
MIN_SHEETS     = {"foil": 10, "lam_roll": 10}

def _min_qty_price(kind, sheets, paper_size) -> dict:
    billable = max(sheets, MIN_SHEETS[kind])      # under the minimum bills as the minimum
    return {"total": billable * RATE[kind][paper_size], "billable": billable}
```

**Minimum 10 sheets, then straight piece rate** — 3 A4 sheets of foil bill as
10 × ₹30 = ₹300; 14 bill as 14 × ₹30 = ₹420. The quote breakdown must name it:

```
Foiling A4: minimum 10 sheets applied (3 brought) — 10 × ₹30 = ₹300
```

so the operator can explain the number before the customer is surprised by it.
The console shows the same line live in the modal, and a "below minimum" hint
appears the moment sheets < 10.

| | A4 | A3 | Minimum | Notes |
|---|---|---|---|---|
| **Foiling** | ₹30/sheet | ₹50/sheet | 10 sheets | Gold and silver priced the same |
| **Roll lamination** | ₹15/sheet | ₹30/sheet | 10 sheets *(assumed — "same principle")* | Outsourced (`FINISHING_OUTSOURCED`) |

> **Do not conflate roll with pouch.** `LAMINATION_RATES` (A4 ₹60, A3 B&W ₹100,
> A3 colour ₹120) is **sheet/pouch** lamination and is unchanged. Roll lamination
> is a different process at a different price — hence its own table. Wiring roll
> lamination to `LAMINATION_RATES` would overcharge by 4×.

### 4.4 Endpoints (`print_server.py`, new handlers beside the existing ones)

| Route | Purpose |
|---|---|
| `GET /service-quote?kind=laminate&sheets=6&lam_type=a4` | live price for the modal; mirrors `/quote`'s shape |
| `POST /new-service` | create a service job: `Queued` (or `Draft` without payment/override — the same gate as `handle_create_job`), **no `print_items` row**, `service_kind` + `service_meta` set, `_jt_log` audit event |
| `POST /complete-job` | **unchanged** — service jobs complete through the existing payment path |

`handle_create_job` gains exactly one guard: skip the `print_items` insert when
`service_kind` is set and the kind does not print. Nothing else in it moves.

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
| Printer-breakdown counts, jobs.html/admin.html "pending print" | Filter `service_kind IS NULL` |
| Print panel | Replace the Print button with a **Service panel** (kind, quantities, quote, Start / Ready / Collect) |
| `handle_print_item` | Refuse a service job with a clear error, not a stack trace |
| Colour detection / `/detect-colour` | Not offered for service jobs |

### 4.7 Console UI

- **jobs.html** — a `+ Service` button beside `+ Photocopy`, opening a 3-step modal (kind → quantities + live quote → payment), reusing the existing `nj-*` payment markup and CSS. Queue rows get a kind pill (`🖨 Print` / `📄 Copy` / `🔍 Scan` / `✨ Laminate` / `🥇 Foil` / `📕 Bind`).
- **admin.html** — the same modal, mirrored (the two consoles already duplicate the photocopy/new-job modals).
- Job detail shows the service panel instead of the print panel when `service_kind` is set.

### 4.8 Reporting + a free reconciliation win

`daily_summary`, `/report` and the MIS revenue panels sum `amount_collected` from
`jobs`, so service revenue lands automatically once these are `jobs` rows — no
change needed. Panels that claim to count *print* jobs get a `service_kind IS
NULL` filter.

Bonus: `konica_jobs.job_type` already records **`Copy`** and **`Scan`** straight
off the printer (`SCHEMA.sql:210`). Once copy/scan service jobs exist, MIS can
compare *counter-recorded* against *machine-counted* copies — the gap is unbilled
walk-in copying. This is the first time that number becomes knowable.

### 4.9 Tests (new files)

- `tests/test_service_quote.py` — every kind, tier boundaries, `needs_manual_price`.
- `tests/test_service_jobs.py` — `/new-service` creates the row with no `print_items`; payment gate; audit event; `handle_print_item` refuses a service job.
- `tests/test_service_isolation.py` — **the guard test**: a service job is never pulled by `store_puller`, never auto-printed, never counted in printer queues.
- Existing `tests/test_local_print.py`, `test_store_puller.py`, `test_print_planner.py`, `test_update_job.py` must pass unchanged.

### 4.10 Fail-loud (per `docs/FAIL_LOUD.md`)

| Condition | Alert |
|---|---|
| Foil/other job completed at ₹0 with no override reason | `report("service.unpriced", False, ...)` |
| Unknown `service_kind` reaching a console or handler | alert, never a silent skip |
| `/service-quote` raises | `guard("service.quote")` → alert; UI shows "enter price manually" |
| A service job sitting in `Queued` > 24 h | daily digest line (reuses `store_digest.py`) |

New code adds **zero** `except Exception: pass` — `tests/test_fail_loud_rule.py`
budgets stay as they are.

### 4.11 Shipping order

| PR | Contents | Risk |
|---|---|---|
| B-0 | **`lam_roll` / `lam_cover` ₹0 fix** (§4.12) — its own PR, before or after the rest | medium — touches live print-job billing |
| B-1 | `rate_card` Section 11 (incl. foiling + roll-lam tables) + tests. Nothing calls it | none |
| B-2 | Migrations (cloud + SQLite + `docs/SCHEMA.md`) | none — additive columns |
| B-3 | `/service-quote` + `/new-service` + `handle_create_job` guard + isolation tests | low |
| B-4 | jobs.html console UI (modal + pills + service panel) | low |
| B-5 | admin.html mirror + MIS `service_kind` filters | low |
| B-6 | Konica copy/scan reconciliation panel | medium (new analysis, no print path) |
| B-7 | Customer-facing post-press ordering — decide after B-4 is live | — |

### 4.12 Known bug this work uncovers — `lam_roll` / `lam_cover` bill ₹0

`calculate_finishing_cost` (`rate_card.py:327–360`) has branches for `none`,
`staple`, `spiral`, `wiro`, `soft`, `project`, `record`, `lam_sheet` and
`thermal` — and **none for `lam_roll`, `lam_cover` or `id_card`**. `cost` stays
at its `0` initialiser, so:

> **Every print job finished with roll lamination or cover lamination has been
> quoted the print cost only, with ₹0 for the lamination.**

Both are offered in the finishing dropdown of both consoles
(`website/jobs.html:1386`, `admin.html:2001`) and both are in
`FINISHING_OUTSOURCED`, so the store pays a vendor for work it did not bill.
`BINDING_RATES` even carries `lam_cover: 50` — the number exists and is simply
never read. `lam_roll` carries `price: None`; the new `ROLL_LAM_RATES` fills it.

**Decision (owner, 2026-08-30): fix it, in its own PR (B-0).** It changes live
print-job quote maths — the one thing the rest of this plan promises not to touch
— so it ships separately and reverts cleanly:

- `lam_roll` → `ROLL_LAM_RATES[paper_size] × max(sheets, 10)` (§4.3.1).
- `lam_cover` → flat ₹50 from `BINDING_RATES` (per book cover, not per sheet).
- `id_card` → still has no rate; stays ₹0 but is flagged `needs_manual_price`
  rather than quietly free.
- Tests pin every other finishing key's price as unchanged, so the blast radius
  is provably the three keys above.

Worth a look at past jobs with `finishing IN ('lam_roll','lam_cover')` before the
PR lands, to size what was under-billed.

---

## 5. Suggested overall sequence

1. **A-1 → A-3** (scaling core, invisible) — ships behind no UI, provable by tests.
2. **B-1 → B-3** (service core, invisible) — same.
3. **A-4 → A-5** preview endpoints + staff UI, then the **paper proof on the OSP Konica**.
4. **B-4/B-5** service console UI — the counter can now take laminate/foil/scan work.
5. **A-6** customer scaling UI + preview.
6. **B-6** reconciliation; **B-7** customer post-press if wanted.

Rough size: Feature A ≈ 450 lines of code + 250 of tests (preview included);
Feature B ≈ 500 + 300, plus console markup. Neither touches a locked file except
through additive, default-off parameters.

---

## 6. Open questions for the owner

| # | Question | Blocks | Status |
|---|---|---|---|
| Q1 | ~~Foiling rates~~ | — | **answered** — per sheet, A4 ₹30 / A3 ₹50, gold = silver, minimum 10 sheets then piece rate (§4.3.1) |
| Q1b | **Roll lamination minimum** — A4 ₹15 / A3 ₹30 per sheet is set; "same principle" is read as *minimum 10 sheets too*. Confirm or correct | `ROLL_LAM_RATES` in B-1 | assumed |
| Q2 | **Custom scale bounds** — is 25–400 % right, and should customers get Custom % at all, or staff only? | A-6 | open |
| Q3 | **Scan** — priced per sheet off `SCANNING_RATES` as-is? Default delivery (WhatsApp / email / USB)? Colour and DPI choices? | B-1 `scan` | open |
| Q4 | **Copy** — should a walk-in copy become a tracked `Queued` job, or keep the current instant-Completed photocopy entry? | B-3/B-4 (both can coexist) | open |
| Q5 | **Lamination** — which sizes are actually offered (A4 / A3 / ID card), and is ID-card lamination its own price? | B-1 `laminate` | open |
| Q6 | **Bind-only** — is `SOFT_BINDING_WITHOUT_PRINT = 100` still current, and do spiral/wiro cost the same without print? | B-1 `bind` | open |
| Q7 | Should customers be able to order post-press online (drop-off first), or is this counter-only? | B-7 | open |

**Answered 2026-08-30:** preview renders the baked PDF, one page, switchable
(§3.6) · scaling is 1-up only, everything else fits to the printable area (A4) ·
foiling and roll-lamination rates set, minimum 10 sheets billed as 10 (§4.3.1) ·
the `lam_roll`/`lam_cover` ₹0 bug is fixed in its own PR (§4.12).

---

## 7. Explicit non-goals

- No change to the imposition/rotation model or the verified 12-combination matrix.
- No change to the Konica dual-queue duplex/simplex fix.
- No change to existing quote maths for print jobs.
- No scaling controls on N-up layouts — those always fit to the printable area.
- No new printer, tray or media handling.
- No retirement of `/new-photocopy` or its buttons.
- No pricing invented where the owner has not given a rate.
- No change to pouch/sheet lamination pricing (`LAMINATION_RATES` is untouched).
- The `lam_roll`/`lam_cover` ₹0 fix is deliberately **outside** this work's
  no-change guarantee — it is a separate, separately revertible PR (§4.12).
