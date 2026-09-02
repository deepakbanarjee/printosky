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
| A9 | **Percent is of the original page**, not of the sheet *(owner, 2026-08-30)* | 100 % means "the document's own size" wherever it lands. An A5 PDF at 100 % prints A5-sized content centred on an A4 sheet; at 200 % it roughly fills it. This is what Acrobat means by scale — and it makes **Custom 100 % ≡ Actual size**, one fewer thing to explain |
| A10 | **Custom % is staff-only.** Customers get Fit and Actual *(owner, 2026-08-30)* | Two clear choices on the order page, and nobody orders 40 % text they cannot read and blames the print. Staff keep the full range for the odd job |
| A11 | Over 100 % is **allowed, warned, and shown** — never silently clamped | "Enlarge this map to fill the page" is a real request. The preview hatches what falls off and counts the pages affected; the operator proceeds knowingly |
| A12 | Input is **presets + free entry**: 50 · 75 · 90 · 125 · 150 · 200, plus a number box | Fastest at a counter, still exact when it matters |
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
| `custom` | **not supported — dropped with an alert** (see below) |

> **Scope reduction found while building A-2 (2026-08-30).** The plan assumed
> `custom` on a landscape sheet was just `scale_behavior="Custom"` plus a target
> box from `scale_rect()`. It is not: `perform_nup` takes ONE absolute box for
> every slot, and on a landscape layout the page is rotated 90°, so the box has
> to be transposed — and which way is a question the verified rotation matrix
> answers only for the fitted case. Rather than guess at geometry that has never
> been on paper, **1-up landscape supports `fit` and `actual`; `custom` there is
> dropped with an ops_watchdog alert** telling the operator to use portrait or
> fit/actual. `custom` is staff-only (A10) and landscape is the rarer half of
> 1-up, so the gap is narrow. Reinstating it needs its own paper proof, not more
> code.

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

**Customer — `website/order-v3.html` + `website/order/order-logic.js`**

> **Ships as a trial page (owner, 2026-08-30):** *"merge it to live as v3 and
> after successful testing we will rewire it to the latest."* `order-v3.html`
> carries the scaling; `order-v2.html` stays byte-identical to what is live and
> remains the page customers reach. v3 is `noindex` with its canonical pointing
> at v2, so it cannot be found or indexed as the real order page. The two share
> `order-ui.js` — the scaling code no-ops without the v3 markup, and
> `tests/test_scale_ui.py::TestOrderV2IsUntouched` is what keeps that true.
> Rewiring means pointing the site's order links at v3 (or renaming it over v2),
> which is a separate, deliberate change.
A new `.ov2-card` next to Paper size, hidden while `nup !== 1`. **Two choices —
no Custom % for customers (A10):**

```
Page size on paper
[ Fit to page (default) ] [ Actual size ]
Price is per sheet — scaling does not change it.
```
The preview still earns its place here: for an A5 or Letter file on an A4 sheet,
Fit and Actual are visibly different, and that is exactly the case a customer
gets wrong. `buildPrintSpec()` (`order-logic.js:37`) adds `scale` **only when the
customer picked Actual**, so the default order body is unchanged.
`buildOperatorNote()` gains "Actual size" / "Scaled 75 %". No API change:
`api/handlers_order.py:284` already persists the whole `print_spec` verbatim.

**Staff — print panel in `website/jobs.html` (`#jp-*`, ~:2349) and `admin.html`**
A select (Fit / Actual / Custom) above the preview pane; choosing Custom reveals
the preset row (50 · 75 · 90 · 125 · 150 · 200) and a free number box clamped to
25–400 %. Saved through the existing `/update-job` body into the new
`print_items` columns, with the §3.6.1 baked preview beside it. **This is the
only place Custom % exists** (A10).

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

> **Found while building A-3 (2026-08-30): the migration cannot be a
> prerequisite.** Store PCs update by pulling code and restarting the watcher
> (`docs/AUTO_UPDATE.md`) — **nothing runs `fix_db.py` for them**. A box running
> new `print_server.py` against a database written before today would hit
> `no such column: scale_mode` on the INSERT, and spec-saving would break at the
> counter. So `handle_update_job` calls `_ensure_print_item_scale_columns()`
> first: a cheap idempotent PRAGMA-and-ALTER that makes `fix_db.py` the tidy-up
> rather than the prerequisite. `handle_print_item` already reads the columns
> defensively. Worth remembering for the B-2 migrations, which have the same
> exposure.

### 3.9 Tests (new files, no existing test edited)

- `tests/test_pdf_scaler.py` — `scale_rect` geometry per mode, crop detection, percent clamping; `apply_scale` page sizes; `None` for every no-op case; **`custom` at 100 % equals `actual`** (A9), and an A5 page at 100 % on an A4 sheet stays A5-sized.
- `tests/test_print_planner_scale.py` — **the guard test**: a spec *without* `scale` yields an action list identical to today's; the 12 matrix specs plan unchanged; **`nup >= 2` ignores `scale` entirely** (A4).
- `tests/test_print_server_scale_settings.py` — `scale_applied=False` builds the exact current settings string; `True` appends `noscale`.
- `tests/test_scale_preview.py` — `/scale-preview` returns a PNG whose page box equals `scale_rect()`; bad params → 400, not a stack trace; store-PC failure surfaces as an error, never a stale image.
- `tests/order/order-logic.test.mjs` (existing node harness) — `buildPrintSpec` omits `scale` at default, includes it for Actual, and **never emits `custom` from the customer UI** (A10).
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
| A-6 | order-v2 customer UI (Fit / Actual only) + preview canvas | low |

---

## 4. Feature B — post-press without printing

### 4.1 What the owner's answers changed

Two answers turned this from "add service jobs" into something larger, and the
plan says so rather than hiding it in an estimate:

1. **Capability is per store, not per service.** *"Binding, roll lamination and
   foiling happens in Printosky Nattika. For all other stores it is outsourced.
   We need to account that properly."* `FINISHING_OUTSOURCED` is a module-level
   global today (`rate_card.py:160`) — a single list that is simply wrong for one
   of the two live stores.
2. **Nattika is not a vendor, it is us.** OSP work finished at Nattika is an
   **internal inter-store transfer**, with revenue **split print vs finishing**
   and Nattika booking an **internal rate**. That needs per-line store
   attribution the `jobs` table does not have.

Neither is hard, but together they are their own phase (B-7…B-9 below), and the
service work should not wait on them.

### 4.2 Decisions

| # | Decision |
|---|---|
| B1 | A service job is a normal `jobs` row with a new `service_kind`, not a new table — revenue, payment, pickup codes, WhatsApp notify, daily summary and MIS all already read `jobs` |
| B2 | `service_kind` NULL ⇒ print job ⇒ everything behaves exactly as today |
| B3 | A service job **never** creates a `print_items` row, never enters a printer queue, never auto-prints |
| B4 | Kinds: `copy`, `scan`, `laminate`, `foil`, `bind`, `cut`, `punch`, `photo`, `dtp`, `other` |
| B5 | Rates in §4.4 are the owner's, given 2026-08-30. `other` stays staff-priced and flagged |
| B6 | `/new-photocopy` and its buttons stay — but stop asking staff to type a price; the button quotes from the rate card like everything else |
| B7 | Services are orderable **online as drop-off bookings**; an un-received booking auto-expires (§4.8) |
| B8 | Payment: on collection, except **part payment upfront above a threshold** |
| B9 | Capability is per store; **outsourced is the default** so a new store never silently claims it can finish (§4.7) |

### 4.3 Data model (additive only)

`api/migrations/SCHEMA_v38_service_jobs.sql` *(built 2026-08-31 — v29 was
already taken by `processed_webhooks_rls`; the next free number is v38)*:

```sql
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_kind  TEXT;   -- NULL = print job
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS service_meta  JSONB;  -- per-kind quantities
-- inter-store finishing (§4.7)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finishing_store_id TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finishing_status   TEXT;   -- sent | at_finisher | returned
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS print_amount       REAL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finishing_amount   REAL;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS finishing_internal_amount REAL;
-- drop-off bookings (§4.8)
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS item_received_at timestamptz;
CREATE INDEX IF NOT EXISTS jobs_service_kind_idx ON jobs (service_kind)
  WHERE service_kind IS NOT NULL;
```

Store SQLite gets the same columns as `TEXT`/`REAL` via the `fix_db.py` ALTER
pattern, plus `install/bootstrap_db.py` DDL and `docs/SCHEMA.md` rows.

> **Built 2026-08-31 (B-2).** The one list lives in `db_migrations.SERVICE_JOB_COLUMNS`
> and `watcher.setup_database()` applies it on every start — the A-3 lesson above,
> acted on rather than recorded again. `fix_db.py` and `install/bootstrap_db.py`
> import the same list, so a fresh box and a three-year-old one end up identical.
> A failed ALTER reports through `ops_watchdog` (`db.migrate.jobs`) instead of a
> log line; it does not raise, because the statement that actually needs the
> column will, with a better message.
>
> **Applied to Supabase 2026-08-31** and verified from the database itself: all
> eight columns present, every one nullable with no default, `service_meta` as
> `jsonb`, `item_received_at` as `timestamptz`, and `jobs_service_kind_idx` in
> place.
>
> **B-2 missed `config/schema_manifest.yaml`**, and applying the migration is
> what exposed it: the live table had eight columns the manifest did not, which
> is exactly the "columns deployed before code" drift `scripts/check_schema.py`
> exists to catch (an extra live column is a drift, so the check would have gone
> red on `main` the moment `SUPABASE_DB_URL` was set). Fixed and pinned by a
> test; the manifest and the live `jobs` table now agree on all 56 columns.
> A migration is not finished until that file moves with it. `supabase_sync.collect_jobs()` began pushing `service_kind` /
> `service_meta` with B-4, once there was somewhere for them to land.
>
> Two things made the SQLite half safe to ship before the Supabase half was run:
> nothing reads these columns yet, and `supabase_sync.collect_jobs()` names its
> columns explicitly — so a migrated store PC physically cannot push
> `service_kind` to a cloud that has not got it. `tests/test_service_job_columns.py`
> pins both. **The cloud migration still has to be run by hand in the Supabase SQL
> Editor** (repo convention, `docs/SCHEMA.md` §Migrations) before B-3 writes a
> service job.

### 4.4 Rates *(owner, 2026-08-30)*

**Per-piece services — `max(pieces, minimum) × rate`, one formula for all:**

| Service | A4 | A3 | Cover | Minimum |
|---|---|---|---|---|
| Foiling | ₹30/sheet | ₹50/sheet | ₹50/piece | 10 pieces (cover floor = ₹500) |
| Roll lamination | ₹15/sheet | ₹30/sheet | — | 10 sheets |
| Pouch lamination | ₹70 | ₹120 B&W · ₹140 colour | — | — |
| Cover lamination | ₹50 flat | | | |
| ID card | ₹100/card, **printing included** | | | |
| Cutting | ₹20 per pass | | | ₹100 |
| Punching | ₹20 per pass | | | ₹100 |

A *pass* is one press of the machine, however many sheets it takes at a time — so
a 60-sheet job punched in 4 passes is 4 × ₹20, floored to ₹100. **Cutting and
punching are free when the job is one we printed or bound**; the rate applies only
to a customer's own sheets.

**Binding:**

| Key | With print | Bind-only (customer's sheets) |
|---|---|---|
| Spiral A4 | ≤30 ₹30 · ≤70 ₹40 · ≤100 ₹50 · ≤130 ₹60 · ≤150 ₹80 · ≤170 ₹90 · ≤200 ₹120 · ≤250 ₹150 | **+₹20** |
| Spiral A3 | ₹80 · ₹110 · ₹130 · ₹160 · ₹210 · ₹240 · ₹320 · ₹400 *(A4 × 2.67, rounded to ₹10)* | **+₹20** |
| Wiro | ≤30 ₹50 · ≤70 ₹100 · ≤100 ₹150 · ≤130 ₹200 · ≤150 ₹250 — **refused above 150 sheets** | **+₹20** |
| Soft | ≤70 ₹80 · ≤100 ₹110 · ≤130 ₹120 · ≤150 ₹140 · ≤200 ₹160 · ≤250 ₹180 | ₹100 (= ₹80 + ₹20) |
| Perfect | **same as soft** | **+₹20** |
| Project | ₹220 white/pink/blue/green · ₹250 gold/silver/custom | same |
| Thesis | **₹500 binding line**, printing charged on top as normal | project **+ ₹100** → ₹320 / ₹350 |
| Record | ₹400 | same |
| ~~Thermal~~ | **withdrawn — no longer offered** (§4.9) | — |

> **The bind-only premium is one rule: +₹20.** It is not a per-binding special
> case — and it was already in the code without being named: the existing
> `SOFT_BINDING_WITHOUT_PRINT = 100` is exactly soft's ₹80 tier plus ₹20. Perfect
> binding is priced the same as soft; the "+₹20 for binding only" the owner
> mentioned is this same rule, not a perfect-specific premium.

**Other services:**

| Service | Rate |
|---|---|
| Copy | the print rate card (A4 B&W ₹3/sheet), **student rates apply** |
| Scan A4 | ≤50 ₹10 · 51–100 ₹7 · >100 ₹5 per sheet |
| Scan A3 | **double the A4 tiers** — ₹20 · ₹14 · ₹10 |
| Photos | print from a **supplied soft copy — no shooting**. Set of 5 ₹50 · full sheet ₹100 · stamp / postcard / 4×6 *(rates pending)* |
| DTP | per page, **typing only, printing extra**: Malayalam ₹40 · English ₹40 · Hindi ₹60 |
| Urgent | **₹20 on any service**, not just soft/project binding as today |

**Withdrawn:** the ₹2 "special" scan rate (Sini/Ujjwala) is removed — a
per-customer override left in the code is an override applied by accident.

Student rates (₹2/sheet under 100 sheets, ₹1.50 over) extend to **photocopying**
and nothing else.

### 4.5 Pricing code — `rate_card.py`, new Section 11 only

```python
SERVICE_KINDS  = {...}                              # labels, manual-price flags
FOILING_RATES  = {"A4": 30, "A3": 50, "cover": 50}
ROLL_LAM_RATES = {"A4": 15, "A3": 30}
HANDWORK_RATES = {"cut": 20, "punch": 20}           # per pass
MIN_CHARGE     = {"cut": 100, "punch": 100}
MIN_PIECES     = {"foil": 10, "lam_roll": 10}
PHOTO_RATES    = {"set5": 50, "sheet": 100}         # + stamp/postcard/4x6 pending
BIND_ONLY_PREMIUM = 20

def calculate_service_quote(kind: str, meta: dict) -> dict:
    """{ total, breakdown[], needs_manual_price, label } — additive; no existing
    function's signature or behaviour changes."""
```

Every minimum names itself in the breakdown, so the operator can explain the
number before the customer is surprised by it:

```
Foiling A4: minimum 10 sheets applied (3 brought) — 10 × ₹30 = ₹300
Punching: 4 passes × ₹20 = ₹80 → minimum ₹100
```

### 4.6 Endpoints

| Route | Purpose |
|---|---|
| `GET /service-quote?kind=laminate&sheets=6&lam_type=pouch&paper_size=A4` | live price for the modal |
| `POST /new-service` | create the job — no `print_items` row, `service_kind` + `service_meta` set, audit event |
| `POST /complete-job` | **unchanged** — services collect through the existing payment path |

`handle_create_job` gains one guard: skip the `print_items` insert when
`service_kind` is set and the kind does not print. Nothing else in it moves.

> **Built 2026-08-31 (B-3).** Three decisions worth recording:
>
> * **No service job gets a `print_items` row — no exceptions.** The plan said
>   "the kind does not print", but every kind here is work on paper the customer
>   already has; even `copy` runs on the copier, not through our print path. One
>   rule is easier to keep true than ten.
> * **`/create-job` stamps `service_kind` in a second `UPDATE`, not in its
>   `INSERT`.** A print job's INSERT stays the exact statement it has always
>   been, which is Rule 1 held to literally rather than approximately. Same
>   reason the v38 migration only runs when a service job is actually being
>   filed: a print job never touches it.
> * **The deposit gate is live with the plan's defaults** (N1: ₹500 threshold,
>   50 %) as `SERVICE_DEPOSIT_THRESHOLD` / `SERVICE_DEPOSIT_FRACTION` at the top
>   of the section. Under the threshold, payment is on collection (B8); over it
>   and unpaid, the job waits in `Draft` — an `override_reason` starts it anyway.
>   Two numbers to change when the owner settles it.
>
> `/service-quote` runs on every keystroke in the modal, so a *healthy* quote is
> announced to `ops_watchdog` once per process rather than per keypress; every
> failure is reported, and the next success re-announces recovery.

### 4.7 Per-store capability and inter-store finishing

**Capability lives in `store_config.json`**, the file that already decides store
behaviour (a blank `konica_ip` is how a store declares it has no Konica):

```json
"capabilities": { "binding": false, "foiling": false, "roll_lam": false }
```

**Absent or false = outsourced**, so a new store never silently claims it can
finish. PRINTK (Nattika) sets all three true. `FINISHING_OUTSOURCED` stays as the
fallback for callers with no store context, behind a new
`is_outsourced(finishing, store_id)`.

> **Built 2026-09-01 (B-7).** Three things worth recording:
>
> * **`FINISHING_CAPABILITY` covers exactly `FINISHING_OUTSOURCED`, no more.** A
>   finishing we already do everywhere — spiral, wiro, staple, pouch lamination,
>   ID cards — is deliberately *not* capability-gated. Gating it would let a
>   store lose work it has always done simply by not writing a claim down. A
>   test asserts the two sets stay equal in both directions.
> * **A box will not answer for a store it cannot see.** `is_outsourced(f,
>   store_id="PRINTK")` on the OSP machine returns the safe default rather than
>   OSP's own capabilities under another store's name.
> * **Parsing is defensive, and the warning is a log line on purpose.**
>   `ops_watchdog` resolves its store id through `store_config`, so reporting
>   from inside the parser recurses — the same loop that already bit
>   `store_config.missing_file`. The alerting check belongs where capabilities
>   are *read*, which is B-8.
>
> **Found while building it:** both consoles hardcode `soft` binding as
> outsourced (`OUTSOURCED_FINISHING`, 6 entries) while `FINISHING_OUTSOURCED`
> does not (5). One side is wrong, and which is a business question — making the
> consoles match removes soft binding's "Send to Vendor" button; making the rate
> card match changes what `outsourced` means in every quote. Pinned by a test
> until the owner settles it.

**A job OSP sells and Nattika finishes** is an internal transfer, not a vendor
job: `finishing_store_id = 'PRINTK'`, `finishing_status` walks
`sent → at_finisher → returned`, and Nattika's console grows an **incoming
finishing work** queue. Pickup stays wherever the customer expects it.

**Revenue splits**: `print_amount` books to the selling store, `finishing_amount`
to the finishing store — at `finishing_internal_amount`, a **configurable
per-service internal rate, editable from the console and seeded at 100 %** until
the owner sets real numbers. Nothing blocks on those numbers.

### 4.8 Online drop-off bookings

A customer books lamination/foiling/binding on the site and brings the item in.
The job exists before the item does, so:

- `item_received_at` NULL = booked but not in hand. It is **not** work-ready and does not appear in the counter's active queue.
- A WhatsApp reminder goes out, and an un-received booking **auto-expires after 3 days** *(day count to confirm)* — cancelled with a reason, not silently deleted.
- Above the payment threshold the booking takes **part payment upfront** *(threshold and deposit pending)*, which also makes an abandoned booking cost the customer rather than the shop.

> **Built 2026-09-02 (B-9).** `item_received_at` is the whole distinction, and
> it is set by *who booked it*, not by a flag anyone has to remember:
>
> | | |
> |---|---|
> | staff at the counter (`/order/staff-service`) | the customer is standing there holding the paper — `item_received_at` is **now**, and the booking never enters the sweep |
> | online (`/order/book-service`, public, no PIN) | the item is in their bag — **NULL**, not work-ready, and the sweep counts down |
>
> One `_create_service_job()` builds both rows, so a price, a deposit or a
> status cannot depend on which door the booking came in through.
>
> **The four rules `dropoff.py` will not bend:**
>
> 1. **A reminder always comes before a cancellation.** A booking a month old
>    with no reminder sent is *reminded*, not cancelled — a missed cron run
>    delays the cancellation rather than skipping the warning. This is why
>    `dropoff_reminded_at` is a column (`SCHEMA_v41`) and not a derived age: a
>    marker survives an outage, arithmetic does not. A failed WhatsApp send does
>    **not** set it.
> 2. **Money stops the sweep.** Anything collected and the booking goes to the
>    owner instead of being auto-cancelled. Refunds, disputes and part payments
>    are not a nightly script's call.
> 3. **An arrived item is untouchable.** Once `item_received_at` is set this
>    module has no opinion about the job at all.
> 4. **Cancelling says why.** `Cancelled` plus a reason in `notes`, never a
>    delete — a job that vanishes is one nobody can explain to the customer who
>    asks next week.
>
> A booking with no phone number is **refused at creation**: it could not be
> reminded, so it would be cancelled in three days with no warning, which rule 1
> exists to prevent.
>
> The console disables **Notify Ready** while the item is missing — telling a
> customer their own paper is waiting for them is the failure that gate is for —
> and offers *"Item received — start work"*, which is idempotent: a second tap
> reports the recorded time rather than restarting an expiry clock.
>
> Customers do not get `copy`, `dtp` or `other`: a photocopy needs the machine
> and the paper at the same moment, so there is nothing to leave behind.
>
> **Not built:** taking the part payment online. The deposit is *computed* and
> shown, and the status rule already applies, but no money changes hands on the
> site — a booking is unpaid until the item arrives. Wiring Razorpay into the
> booking flow is its own piece of work.

### 4.9 Withdrawals — things to remove, deliberately

Thermal binding is **no longer offered**. Remove it from `BINDING_RATES`,
`FINISHING_OUTSOURCED`, `FINISHING_DISPLAY`, `THERMAL_BINDING_TIERS`,
`get_thermal_binding_rate`, and the finishing dropdowns in both consoles
(`jobs.html:1386`, `admin.html:2001`). This closes backlog **S7-5** ("thermal
listed in admin but rate never tested") by deletion rather than by testing.

Also removed: the ₹2 scan special rate (§4.4).

### 4.10 Where service jobs must be excluded

| Place | Change |
|---|---|
| `store_puller` pull loop | Already safe (no `file_url`) — **add a test that pins it** |
| `watcher.py` auto-print | File-driven; pin with a test |
| Printer-breakdown counts, "pending print" filters | Filter `service_kind IS NULL` |

> **Built 2026-09-01 (B-5).** Only one place actually needed the filter:
> `renderPrinterBreakdown()` in both consoles, which buckets every one of the
> day's jobs into a printer panel via `guessprinter()` — a function with no idea
> what a service job is. A lamination was being counted as a Konica job.
>
> **MIS needed no filter at all**, and that is worth writing down rather than
> adding a no-op: every `jobs` read there is gated on `printed_by=not.is.null`,
> which nothing sets on a service job, and the page-count breakdown reads
> `printer_counters` / `konica_jobs` — machine data a service job cannot appear
> in. Both properties are pinned by tests, including one asserting
> `handle_new_service` never writes `printed_by`, because that is what the
> structural exclusion rests on.
>
> The stat cards (Jobs / Completed / Pending / Revenue) deliberately **do** count
> services: money taken for lamination is money taken, and pending work is
> pending work. Those are job counts, not print counts.
| Print panel | Service panel instead (kind, quantities, quote, Start / Ready / Collect) |
| `handle_print_item` | Refuse a service job with a clear error, not a stack trace |
| `/detect-colour` | Not offered for service jobs |

### 4.11 Reporting + the reconciliation win

`daily_summary`, `/report` and MIS revenue panels sum `amount_collected` from
`jobs`, so service revenue lands automatically. Panels claiming to count *print*
jobs get a `service_kind IS NULL` filter. Per-store revenue reads the new
`print_amount` / `finishing_amount` split.

`konica_jobs.job_type` already records **`Copy`** and **`Scan`** off the printer
(`SCHEMA.sql:210`). Once copy/scan service jobs exist, MIS can compare
counter-recorded against machine-counted copies — the gap is unbilled walk-in
copying, knowable for the first time.

> **Built 2026-09-02 (B-10).** The **Copy & Scan Reconciliation** panel in MIS,
> per window, with the first reading it produced:
>
> | | machine | counter |
> |---|---|---|
> | Copy, since 2026-04-13 | 3,640 jobs · 19,837 pages | — |
> | Scan, since 2026-04-13 |   811 jobs ·  7,612 pages | — |
> | Photocopy sales, ever  | — | **2 jobs · 2 pages** |
>
> Building it turned up why nobody could have noticed sooner. `konica_jobs` has
> had **two writers that never agreed**, and nothing ever compared them:
>
> | column | CSV importer (Feb–Mar) | SOAP fetcher (Apr →) |
> |---|---|---|
> | `job_type` | `Print` / `Copy` / `Scan` | `PRINT` / `COPY` / `SCAN` |
> | `result` | `No Error` / `Canceled` | `OK` / `USERCANCEL` |
> | `job_date` | `2026-03-16 09:46:14` | `2026/09/02 09:18:59` |
>
> MIS filtered `result=eq.No Error`, so from 2026-04-13 it matched **only the
> 1,980 rows the retired importer wrote**: the Konica Job Details and Staff
> Performance panels have been showing February–March data for five months,
> looking entirely plausible. `/` (0x2F) sorts above `-` (0x2D), so all 12,864
> slash-dated rows passed *every* `job_date=gte` filter — today, this week, this
> month and this year were one query. And `renderKJPeriod()` bucketed on
> `job_type === "Print"`, so those rows counted as neither a print nor a copy.
>
> Three divergences, each plausible alone, together freezing a panel while it
> kept rendering numbers. `konica_normalize.py` is the fix — one shape, written
> by both writers, tolerated at read time by the console, backfilled in the
> cloud by `SCHEMA_v39` and on each store PC by the fetcher itself (nothing runs
> `fix_db.py` for a counter).
>
> The reconciliation's own honesty rules: **pages, not jobs**, because one sale
> routinely covers several machine jobs; and a window with no machine data
> reports `blind`, never `0 unbilled` — as does a machine log over 24 h stale.
> A reconciliation that has quietly stopped reconciling is the exact failure it
> exists to catch, so it is the one clean state that still speaks.

### 4.12 Fail-loud

| Condition | Alert |
|---|---|
| A service job completed at ₹0 with no override reason | `report("service.unpriced", False, …)` |
| Unknown `service_kind` reaching a console or handler | alert, never a silent skip |
| `/service-quote` raises | `guard("service.quote")` → alert; UI says "enter price manually" |
| A wiro job over 150 sheets | refused at the counter with "offer spiral or soft instead" |
| A drop-off booking expiring | WhatsApp reminder first, then a cancellation with a reason |
| A job sent to a finishing store and not returned in 48 h | daily digest line (`store_digest.py`) |

> **Built 2026-09-01 (B-8).** Both the digest line and the console queue are
> **silent when there is nothing to say** — no "0 jobs overdue" line, no empty
> panel. A green tick shown every day is one people stop reading, which is the
> failure mode `ops_watchdog` exists to prevent, not an example of it. The
> threshold is one constant (`FINISHING_OVERDUE_HOURS = 48`) and a test asserts
> the console's copy of it matches.
>
> **Repaired 2026-09-02.** The digest line was dead from the day it shipped, in
> three independent ways — and being *silent by design* is exactly what hid a
> section that was silent on every day:
>
> 1. `supabase_sync.collect_jobs()` never selected the finishing columns, so a
>    job sent to Nattika for binding was invisible in the cloud.
> 2. `finishing_sent_at` was read by `overdue_finishing()` and **written by
>    nothing**. The age fell back to `received_at`, which measures a different
>    interval — a job taken in a month ago and sent to the finisher an hour ago
>    read as 720 h late. The fallback was the only branch that ever ran, so the
>    number would have been wrong every single time.
> 3. The cron called `compose_closing_message()` without `finishing_rows`.
>
> All three fixed: the columns sync (defensively — a PC that has not restarted
> does not have them, and a `SELECT` naming a missing column takes the whole
> sync down), `/finishing-send` writes the send time (`SCHEMA_v40`, self-applying
> on the store PC), and the cron fetches the open transfers and passes them.
>
> The `received_at` fallback is **gone**, not repaired. A row with no send time
> is not aged at all — but it is not dropped either: it is reported as *"out for
> finishing, age unknown, that PC has not restarted"*. Silently discarding it
> would hide a job sitting at another shop, which is the one thing the section
> exists for.
>
> A row with no usable timestamp is **skipped rather than aged by guesswork**,
> in the digest and in the console alike: a wrong age is worse than no age.

New code adds **zero** `except Exception: pass`.

### 4.13 Tests

- `tests/test_service_quote.py` — every kind, every tier boundary, both minimum rules, the +₹20 bind-only premium, wiro's 150-sheet refusal.
- `tests/test_service_jobs.py` — `/new-service` creates the row with no `print_items`; payment threshold; audit event; `handle_print_item` refuses a service job.
- `tests/test_service_isolation.py` — **guard test**: never pulled by `store_puller`, never auto-printed, never counted in printer queues.
- `tests/test_store_capabilities.py` — capability defaults to outsourced; PRINTK is in-house; `is_outsourced` agrees with `store_config.json`.
- `tests/test_dropoff_expiry.py` — a booking with no `item_received_at` expires, one with an item never does.

### 4.14 Billing gaps this work uncovered — verified against production

Queried Supabase `jobs` on 2026-08-30: **476 jobs** since 2026-03-12, **109 with a
finishing value** (`none` 104, `spiral` 2, `perfect` 2, `soft` 1).

**Nothing has been under-billed through these gaps.** No `lam_roll`, `lam_cover`,
`id_card`, `thermal`, `project` or `record` job has ever existed in the cloud
table, and the two `perfect` jobs were never collected. The gaps are latent.

> Caveat: this is the **cloud** table. Walk-ins entered on a store PC that never
> sync are invisible here, and `lam_cover`/`lam_roll` are in the store consoles'
> dropdown. Nothing lost *through the cloud path* is not the same as nothing lost.

**Gap 1 — `lam_roll`, `lam_cover`, `id_card` bill ₹0.** `calculate_finishing_cost`
(`rate_card.py:327–360`) has no branch for any of them; `cost` stays at its `0`
initialiser. `BINDING_RATES` even carries `lam_cover: 50` — never read.

**Gap 2 — `perfect` and `thesis` are orderable but unpriced.** Reachable from the
live order page: `order-v2.html:418,421` offers both, `_VALID_FINISHING`
(`api/handlers_order.py:68`) accepts both, and `rate_card` has no such keys at
all. The production rows confirm it — 1 colour page + Perfect quoted **₹10**, the
page and nothing for the binding.

**Gap 3 — A5 and Letter have no rates and silently bill as A4 B&W.** This is
what the ₹10-vs-₹3 pair turned out to be, and it is not drift at all: the ₹3 job
was **A5 colour**. `_VALID_SIZE` (`api/handlers_order.py:180`) accepts
`{A4, A3, A5, Legal, Letter}` and order-v2's paper dropdown offers all five, but
`PRINT_RATES` has keys only for A4, A3 and Legal. `get_print_rate` ends with
`PRINT_RATES.get(paper_type, PRINT_RATES["A4_BW"])` — so **A5 and Letter, colour
included, are billed at ₹3/sheet**. An A5 colour page bills ₹3 instead of ₹10;
Letter is larger than A4 and bills at A4 rates. Same class of bug as the
finishing gaps: a value the UI offers that the rate card does not know, failing
to the cheapest thing instead of failing loud.

**The audit — run 2026-08-30, `tools/quote_drift_audit.py`.** Every stored
`print_spec` recomputed through `rate_card` and compared with what was charged:

| | |
|---|---|
| Jobs with a stored quote | 104 |
| Match the rate card exactly | 70 |
| Drift | 32 |
| Not recomputable (spec pre-dates `pages_included`) | 2 |
| Unrated paper type | 1 (the A5 job above) |

**There is no live drift.** Every drift row is dated **2026-08-07 → 08-13**;
everything from 08-16 on matches exactly. Commit `6afb9b5` (2026-08-14,
*"calc_sheets returns exact physical sheet count without odd-rounding bug"*)
deliberately removed the old rounding, and **28 of the 32 drift rows are exactly
reproduced by the pre-fix rule**. The other 4 are mixed-colour jobs from the same
window, explained by colour moving to per-page billing. On those 32 rows
customers paid ₹512 against ₹332 at today's rates — historically *over*-charged
by ₹180, never under.

> **One real leftover:** `rate_card.py:20` still documents the rule that commit
> removed — *"Sheets for DS = ceil(pages/2), rounded UP to next even number."*
> The code and its own docstring disagree, which is how the next person gets
> this wrong. One-line fix, folded into B-0.

**The fix — B-0, its own PR**, because it changes live print-job quote maths:

- `perfect` → soft tiers · `thesis` → ₹500 / project + ₹100 · bind-only → +₹20
- `lam_roll` → `ROLL_LAM_RATES × max(sheets, 10)` · `lam_cover` → ₹50 · `id_card` → ₹100/card
- `lam_sheet` → by paper size (₹70 / ₹120 / ₹140) instead of the hardcoded `LAMINATION_RATES["a4"]`
- Spiral A3 → tiered · wiro → its own tiers · thermal → removed
- **`A5_*` / `Letter_*` added** so no offered size falls back to A4 B&W *(owner, 2026-08-30)*:

  | | B&W | Colour (≤30 / ≤50 / >50 sheets) |
  |---|---|---|
  | A4 *(reference)* | ₹3 | ₹10 / ₹9 / ₹8 |
  | **A5** — half of A4 | **₹1.50** | **₹5 / ₹4.50 / ₹4** |
  | **Letter** — same as A4 | **₹3** | **₹10 / ₹9 / ₹8** |

  **No discounts on either.** The student rate stays A4 B&W only — which is
  what `get_print_rate` already does (its student branch tests
  `paper_type == "A4_BW"`), so this is a comment against a future "helpful"
  extension rather than a change. A5 at ₹1.50 already equals the >100-sheet
  student A4 rate.
- A **guard test that every size in `_VALID_SIZE` has a rate**, the same shape as the finishing-key guard — the pair of tests that make this class of bug impossible to reintroduce
- `rate_card.py:20` docstring corrected to match `calc_sheets`
- Add every missing key to `BINDING_RATES` / `FINISHING_DISPLAY` and the in-house/outsourced lists
- **A guard test that every key in `_VALID_FINISHING` and every `data-binding` in order-v2 resolves to a non-zero, non-`None` price** — the test that would have caught all of this
- Every other finishing key's price pinned unchanged, so the blast radius is provable

### 4.15 Shipping order

| PR | Contents | Risk |
|---|---|---|
| B-0 | Unpriced-finishing fix + thermal withdrawal (§4.14, §4.9) | medium — live billing |
| B-1 | `rate_card` Section 11 + tests. Nothing calls it | none |
| B-2 ✅ | Migrations (cloud + SQLite + `docs/SCHEMA.md`) — built 2026-08-31, cloud SQL not yet run | none — additive |
| B-3 ✅ | `/service-quote` + `/new-service` + `create_job` guard + isolation tests — built 2026-08-31 | low |
| B-4 ✅ | jobs.html console UI (modal, kind pills, service panel) — built 2026-08-31 | low |
| B-5 ✅ | admin.html mirror + print-count exclusion — built 2026-09-01 | low |
| B-6 ✅ | Photocopy button quotes from the rate card (B6) — built 2026-09-01 | low — one live button |
| B-7 ✅ | Per-store capabilities + `is_outsourced()` — built 2026-09-01, inert until B-8 | low |
| B-8 ✅ | Inter-store transfer + revenue split + Nattika's incoming queue — built 2026-09-01 | medium |
| B-9 | ~~Online drop-off bookings + expiry sweep~~ ✅ | medium |
| B-10 | ~~Konica copy/scan reconciliation panel~~ ✅ | medium |

---

## 5. Suggested overall sequence

1. **B-0** — the billing fix. It is the only item where money is currently wrong, and it is independent of everything else.
2. **A-1 → A-3** (scaling core) and **B-1 → B-3** (service core) — both invisible, both provable by tests.
3. **A-4 → A-5** preview endpoints + staff UI, then the **paper proof on the OSP Konica**.
4. **B-4 → B-6** service console — the counter can take laminate/foil/scan/cut/punch work.
5. **A-6** customer scaling UI.
6. **B-7 → B-9** per-store capability, inter-store finishing, online drop-off.
7. **B-10** reconciliation.

Rough size: Feature A ≈ 450 lines + 250 tests. Feature B ≈ 900 + 500 across ten
PRs — larger than first estimated, because per-store capability and inter-store
finishing (§4.1) are real subsystems rather than a field on a row.

## 6. Open questions

Everything material is answered, and every remaining item has a working default —
nothing blocks B-0, B-1 or A-1.

| # | Still needed | Blocks | Working default |
|---|---|---|---|
| N1 | **Upfront-payment threshold + deposit** for services (part payment above the limit) | B-3's payment gate | ₹500 threshold, 50 % deposit |
| N2 | **Photo rates** for stamp / postcard / 4×6 (set of 5 ₹50 and full sheet ₹100 are set) | `photo` in B-1 | those two only; others quoted by hand |
| N3 | **Drop-off expiry** — how many days before an un-received booking cancels | B-9 | 3 days, WhatsApp reminder first |
| N4 | **OSP→Nattika internal rates** | nothing — deliberately configurable | 100 % (Nattika books the full finishing amount) |
| N5 | ~~Quote-drift audit~~ | — | **run 2026-08-30** — no live drift; see §4.14 |
| N6 | ~~A5 and Letter print rates~~ | — | **answered** — A5 = half A4, Letter = A4, no discounts on either (§4.14) |

**Answered 2026-08-30:** preview renders the baked PDF, one page, switchable
(§3.6) · scaling 1-up only, everything else fits the printable area (A4) · all
service and binding rates (§4.4) · thermal withdrawn · scan ₹2 special dropped ·
urgent extends to all services · student rates cover printing and photocopy ·
capability is per store, default outsourced · Nattika is an internal transfer,
not a vendor · revenue splits print vs finishing at an internal rate · services
orderable online as drop-off bookings · payment on collection below a threshold.

## 7. Explicit non-goals

- No change to the imposition/rotation model or the verified 12-combination matrix.
- No change to the Konica dual-queue duplex/simplex fix.
- No change to existing quote maths for print jobs.
- No scaling controls on N-up layouts — those always fit to the printable area.
- No new printer, tray or media handling.
- Thermal binding is removed, not re-tested — an explicit withdrawal (§4.9).
- No retirement of `/new-photocopy` or its buttons.
- No pricing invented where the owner has not given a rate (§6 lists what is still missing, with the working default each will use until it arrives).
- No change to pouch/sheet lamination pricing (`LAMINATION_RATES` is untouched).
- The unpriced-finishing fixes are deliberately **outside** this work's
  no-change guarantee — a separate, separately revertible PR (§4.12).


---

## 5. The New Job flow moved to order-v2 (2026-09-02)

**Owner, on the consolidation shipped the day before:** *"I absolutely hated the
dark version of the jobs platform. That is why we created the order v2 version.
It is more clear and interactive. I just want you to add the missing features to
v2 instead of taking me back to the previous version."*

The 2026-09-01 change (#99) was right that the two consoles must not disagree,
and wrong about which one to keep. Standardising on the dark modal was argued
from how short the wiring was — it lived in the console, needed no page hop, and
already spoke to `print_server` — and none of those is a reason that belongs to
anyone at the counter.

### What that mistake cost, concretely

`order-ui.js:42` carried `scale: 'fit', // Custom % is staff-only, never emitted
here`. Custom % was excluded from order-v2 back when v2 was customer-only. But
v2 has had a full staff mode since `0f5f85c` (`?staff=1` → `/order/staff-create`),
and that is the staff portal the owner actually uses. So Custom % was absent
from **both** places he looked, and the two rounds of "I still don't see custom
scale" were answered by fixing a label and a 404 in a console he does not work
in. The lesson is the one from the first round, unlearned and relearned: find
the failing path, do not answer from the code's intent.

### What shipped

| | |
|---|---|
| `+ New Job` | Both consoles open `order-v2.html?staff=1`. |
| The dark modal | **Deleted** from both — 218 lines of markup and 257 of wizard JS each. Not hidden: an unreachable second implementation is exactly how these two drifted apart, and jobs.html had already carried one for months. |
| Custom % | In order-v2, revealed by `syncStaffScale()` in staff mode only. `scaleBlock()` emits nothing for `fit`, nothing for a 100% custom (100% *is* unscaled), and nothing for a percentage that is not a number — so the "absent means unchanged" property holds. |
| Services | A no-file mode in staff mode: the ten rate-card kinds, live quote, deposit, waiver-with-a-reason. |

### Where services post, and why

**Owner:** *"use the vercel api so staff can work off-site."*

So `/order/service-quote`, `/order/staff-service` and `/order/staff-photocopy`
on Vercel — not `print_server` on the shop LAN. A staff member at the other
store, or at home, can book a lamination.

That makes the cloud the **second** caller of the service logic, which is the
whole reason `service_jobs.py` exists: the deposit threshold, the Queued/Draft
rule, the meta parsing, the typed-amount override and the payment-mode fallback
live there, both callers import them, and `tests/test_service_parity.py` asserts
the two paths agree. A price or a status that depends on which machine the
counter used is the konica_jobs split all over again.

Three properties the cloud path preserves structurally rather than by policy:

* **no `file_url`** — `store_puller` pulls only rows that have one, so a service
  job can never be downloaded or auto-printed;
* **no `printed_by`** — which is what keeps services out of the MIS printer and
  staff panels;
* **a photocopy gets no `service_kind`** — it is work the Konica actually did,
  so it stays inside the printer counts that B-10's reconciliation compares
  against. Giving it one would remove it from the comparison built to catch it.

`override_reason`, `amount_partial` and `queued_at` exist only in the store PC's
SQLite. PostgREST rejects the *whole* insert on one unknown column, so the cloud
row maps them instead of carrying them: the waiver goes into `notes` (where the
operator reads it), money taken is `amount_collected` below `amount_quoted`
(which is what a deposit is), and `status` already says Queued. A test pins
every written column against `config/schema_manifest.yaml`, because that class
of bug is a 500 on every call.
