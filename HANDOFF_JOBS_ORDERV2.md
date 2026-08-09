# Handoff — Per-store jobs console, order-v2 print options, staff order creation

**Date:** 2026-08-08 · **From:** Claude (Opus 4.8, Claude Code web) ·
**To:** Antigravity / Claude Opus 4.6 · **Status:** all work below is **live on `main`**.
**Branch:** `claude/session-recap-pending-4vp603` (== `main` at `3a30ba2`).
**Deploy model:** pushing to `main` auto-deploys **Netlify** (the `website/` dir)
and **Vercel** (the `api/` Python function). `print_planner.py`/`nup_imposer.py`/
`store_puller.py` run on the **store PC**, not Vercel.

---

## 0. TL;DR — pick up here

The next task is **#2 frontend: make the jobs console's "+ New Job" open
`order-v2.html` in a staff mode** that creates a walk-in job with the full
`print_spec`. **The backend is already done and deployed** (`/order/staff-create`).
You only need the frontend. Full spec in §4. After that, **#3: multi-file** (§5).

**Before ANY change under `api/`, read §6 (the outage lesson) and run the import
smoke-test.** I shipped a circular-import bug this session that 500'd the entire
API; there is now a test guarding it — don't remove that guard.

---

## 1. What this session shipped (all on `main`, deployed)

Arc: split the 7.3k-line `admin.html` monolith into focused standalone pages, and
sharpen the order-v2 print flow.

- **Standalone pages architecture.** Heavy single-purpose tools became their own
  pages instead of tabs: `chat.html` (Conversations, already existed),
  **`transcripts.html`** (manuscript OCR, new — auto-auths via the store token),
  and **`jobs.html`** (new — the per-store operating console). `mis.html` stays a
  reporting-only dashboard. **`admin.html` is UNTOUCHED** and remains the owner
  back-office (build-alongside; nothing was cut from it).
- **`admin-shared.js`** (new): `sbFetch`, `showToast`, `getStoreId`, shared by
  `admin.html` + `mis.html` + `jobs.html` + `transcripts.html`. Each page defines
  its own `sbAuthFail()` hook (sbFetch calls it on 401).
- **`jobs.html` = per-store console.** A stripped copy of admin (back-office
  removed). **Auto-scopes to the machine's `store_id`** (from `print_server /status`
  via `fetchStoreConfig` → `localStorage.storeId`), so an OSP PC shows only OSP
  jobs (queue + breakdown filter on `assigned_store_id`). Staff-PIN login. Route:
  `/jobs` (netlify.toml redirect). Full flow kept: queue, job panel, colour detect,
  DTP timers, quote, print, mark-paid/collect, New Job, photocopy.
- **Payments without the admin password** (`c47fa3f`). `/admin/mark-paid` now
  authorizes via `_acad_auth_staff(h)` (valid `X-Staff-Pin` OR `X-Admin-Password`)
  instead of admin-only. `jobs.html` `markPaid()` sends the staff PIN. Owner's
  admin path still works.
- **order-v2 N-up direction + orientation animation** (`96574e4`, `bb1c337`).
  Added a **Horizontal/Vertical** fill-direction toggle; the preview sheet reshapes
  to portrait/landscape and numbers/pops slots in fill order. `print_spec` now
  carries `nup_direction`; the backend already threaded it (`print_planner` →
  `nup_imposer.perform_nup(layout_direction=...)`). **2-up vertical stacks** (1 top,
  2 bottom) — for 2-up the grid flips with direction (planner picks 1×2 portrait).
- **B&W preview is monochrome** (`bb1c337`). `.ov2-thumb.bw-mono canvas {
  filter: grayscale(1) }` now applies to any page that prints B&W (all in `bw`,
  the non-colour pages in `mixed`, none in `col`).
- **Outage + guard** (`4b2995e`, `93e00ef`) — see §6.
- **#2 backend** (`3a30ba2`) — see §4.

Full commit trail in §9.

---

## 2. Architecture now — pages & who operates them

| Page | Purpose | Auth | Notes |
|---|---|---|---|
| `jobs.html` `/jobs` | Per-store operating console (the store's daily driver) | Staff PIN | Auto-scoped to the machine's store |
| `chat.html` | WhatsApp Conversations | Admin pw | Standalone (pre-existing) |
| `transcripts.html` `/transcripts` | Manuscript OCR | Store token (auto) | Standalone |
| `mis.html` `/mis` | Reporting dashboard | MIS pw | Reporting only; has a global Staff Performance panel |
| `admin.html` `/admin` | Owner back-office (Academic/Referrals/Books/OpQ/Conv) | Admin pw | **Untouched** — still fully works |

Decisions ledger + progress: **`docs/plans/2026-08-07-admin-mis-split.md`** (keep
updating it).

---

## 3. Auth cheat-sheet (important for #2/#3)

- **Vercel API** (`api/index.py`): `_acad_auth_staff(h)` (defined **api/index.py
  line ~1937**) returns True for a valid `X-Staff-Pin` (active staff in Supabase)
  **OR** a valid `X-Admin-Password`. This is the "counter staff or owner" gate used
  by `/referrals/*`, `/admin/mark-paid`, and the new `/order/staff-create`.
- **Netlify auth function** (`netlify/functions/auth.js`): verifies tier passwords
  (`admin` PBKDF2, `superadmin`/`store`/`mis`/`staff` SHA256) and returns a Supabase
  JWT. `type:"staff"` with the `storeToken` yields a JWT (how `transcripts.html`
  auto-auths and how `jobs.html` gets its JWT after PIN login).
- **Frontend session**: `sessionStorage` holds `staff_pin`, `staff_id`,
  `supabase_jwt`; `localStorage` holds `storeId`, `storeToken`, `storePcUrl`.
  `localStorage` is shared across tabs; `sessionStorage` is copied into a tab opened
  via `window.open()` (matters for #2 — see §4).

---

## 4. NEXT: #2 frontend — "+ New Job" launches order-v2 staff mode

**Goal:** staff create a walk-in print job with order-v2's rich options (N-up,
direction, orientation, duplex, mixed colour) instead of the thin `nj` wizard.

**Backend is DONE (`3a30ba2`), deployed:** `POST /order/staff-create`
(`api/handlers_order.py` `_handle_order_staff_create`, routed in `api/index.py`
~line 3133). It reuses the `/order/create` pipeline but: **auth = staff PIN/admin**
(`X-Staff-Pin`), **no phone required**, **no customer WhatsApp**, **store from the
request**. Body: `{file_url, file_name, print_spec, store_id, customer_name?,
phone?, operator_note?}` → returns `{job_id, total}`. The job lands Pending; staff
mark-paid + print from `jobs.html` (which auto-prints with the full spec).

**Frontend to build:**

1. **`website/jobs.html`** — change the **+ New Job** button
   (`onclick="openNewJobModal()"`, in the Job Log panel header ~line 1317) to
   `onclick="window.open('order-v2.html?staff=1','_blank')"`. Use `window.open`
   (NOT a plain link) so the new tab **inherits `sessionStorage`** (staff_pin).
   Leave `openNewJobModal()` and the `nj*` wizard defined (dead fallback) for now.

2. **`website/order/order-ui.js`** — add a `STAFF` mode:
   - Detect it: `const STAFF = new URLSearchParams(location.search).get('staff') === '1';`
   - **Store id**: in staff mode use `localStorage.getItem('storeId')` (the machine's
     store) instead of the customer pickup-store picker.
   - **Hide the customer step** (`#ov2-step2` contents: `#ov2-whatsapp`,
     delivery/pickup `[data-delivery]`, `#ov2-address`, payment-mode select,
     `#ov2-identity`). Keep an OPTIONAL customer name + phone if you want; not
     required. Relabel the submit button (`#ov2-submit` → e.g. "Add to queue").
   - **Reroute submit** (`submitOrder()`, ~line 699): the upload steps
     (`/order/upload-sign` → PUT to storage) stay the same; only the create call
     changes. In staff mode, instead of `fetch(API + '/order/create', ...)` (~line
     763), POST `API + '/order/staff-create'` with headers
     `{ 'Content-Type':'application/json', 'X-Staff-Pin': sessionStorage.getItem('staff_pin')||'' }`
     and body `{ file_url: fileUrl, file_name: state.fileName,
     print_spec: buildPrintSpec(state), store_id: localStorage.getItem('storeId'),
     customer_name: <optional>, phone: <optional> }`. On success show "Added to the
     queue" (not "order placed"); offer "New job" (reset) or close.
   - If `staff-create` returns 403, show "Open this from the jobs page (log in with
     your PIN first)."

3. **Verify** (over http — order-v2 uses ES modules, file:// blocks them; see §7):
   serve `website/`, drive the flow, confirm the create call hits `/order/staff-create`
   with `X-Staff-Pin` and the right `store_id` + `print_spec`. Confirm the customer
   customer flow (no `?staff=1`) is UNCHANGED.

**Design already settled:** staff auth via the inherited `staff_pin`; store from
`localStorage`. Payment is NOT captured in order-v2 — staff mark-paid on `jobs.html`
(reuses the flow we built). Inline payment could be a later enhancement.

---

## 5. THEN: #3 — multiple files (same or different print options)

Not started. order-v2 is single-file today (`#ov2-file`, `e.target.files[0]`, no
`multiple`). The owner wants: add several files, with **one shared** setting OR
**per-file** settings.

- Note: a job already supports multiple *print items with different specs within
  ONE file* (page-range based, `print_items`/`editItems`) — but multiple *separate
  files* is genuinely new.
- Likely shape: a file list UI; each file uploads (upload-sign) and gets its own
  `print_spec`; then either N `/order/staff-create` calls (one job per file) or a
  new multi-file job concept. Recommend **one job per file** first (simplest, reuses
  `/order/staff-create`), grouped visually. Confirm with the owner before building —
  this is the biggest piece and has real UX decisions.

---

## 6. ⚠️ THE OUTAGE LESSON — read before touching `api/`

I shipped `c47fa3f` (staff-PIN mark-paid) which added `_acad_auth_staff` to
`handlers_admin.py`'s **top-level** `from api.index import (...)`. But `api/index.py`
imports `handlers_admin` at **line ~1885**, while `_acad_auth_staff` is defined at
**line ~1937** — *below* the import point. On a fresh interpreter that raises
`ImportError: cannot import name '_acad_auth_staff' from partially initialized
module` and **the entire Vercel API fails to boot — every endpoint 500s** (order
quote/upload → "couldn't start the upload" + no live rate; WhatsApp webhook;
Razorpay; staff API; …). It was down for a while before we caught it.

**Rules:**
- `api/index.py` imports its handler modules PART-WAY through its own body
  (`handlers_admin` ~1885, `handlers_referrals` ~1981, `handlers_order` ~2606). A
  handler's **module-top** `from api.index import X` only works if `X` is defined
  **above** that handler's import line. For anything defined lower (like
  `_acad_auth_staff` at ~1937), **import it lazily inside the function** (`from
  api.index import _acad_auth_staff` at call time — the module is fully loaded by
  then). That's how the fix (`4b2995e`) and `/order/staff-create` do it.
- **ALWAYS run the import smoke-test after an `api/` change:**
  `python3 -c "import api.index"` (must exit 0), and/or run
  `tests/test_api_imports.py` (a fresh-subprocess guard — **do not delete it**).
  `py_compile` is NOT enough — it only checks syntax, not import resolution.

---

## 7. Verification playbook (how I tested; reuse it)

- **Chromium/Playwright** is preinstalled. In Node scripts:
  `require('/opt/node22/lib/node_modules/playwright')`,
  `chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })`.
- **Static HTML pages** (jobs/admin/mis/transcripts): can load via `file://`.
  Gotcha: a blocking cdnjs `<script>` (pdf.js) stalls later scripts under
  `file://` with no network — stub external `.js` requests via `page.route` and
  a `pdfjsLib` init-script, or you'll see false "undefined function" results.
- **order-v2 uses ES modules** (`<script type="module">`) → **file:// blocks the
  imports**. Serve over http: `cd website && python3 -m http.server 8899 &`, then
  load `http://localhost:8899/order-v2.html`. Module functions are NOT global; to
  drive the UI, remove `.ov2-hidden` on gated panels and `.click()` the toggle
  elements (their listeners are wired at init).
- **JS syntax:** copy an ESM file to `*.mjs` and `node --check`. For an inline
  `<script>`, extract it and `node --check`.
- **Python:** `python3 -m py_compile <file>` for syntax; **`import api.index`** for
  import resolution (see §6).
- I verified each change in Chromium (screenshots) and via targeted logic checks
  before deploying. Scratchpad test scripts were throwaway (in the session temp dir).

---

## 8. Key files

- `website/jobs.html` — per-store console. `loadAll` renders stats + queue;
  `renderTable`/`renderPrinterBreakdown` scope by `storeFilter` (locked to the
  machine store via `initStoreFilter`); `markPaid` uses `X-Staff-Pin`.
- `website/order-v2.html` + `website/order/{order-ui.js,order-logic.js,order-auth.js}`
  — the rich order page. `buildPrintSpec` (order-logic.js) builds the spec incl.
  `nup_direction`; `renderNupSheet`/`setDirection`/`setOrientation` (order-ui.js)
  drive the preview; `submitOrder` (order-ui.js ~699) uploads + creates.
- `website/admin-shared.js` — `sbFetch`, `showToast`, `getStoreId`.
- `api/handlers_order.py` — `/order/*` incl. new `_handle_order_staff_create`.
- `api/index.py` — routes + `_acad_auth_staff` (~1937) + handler imports.
- `api/handlers_admin.py` — `/admin/mark-paid` (staff-or-admin auth, lazy import).
- `print_planner.py` / `nup_imposer.py` — store-PC N-up imposition; reads
  `print_spec['nup_direction']`, `orientation`; 2-up vertical → 1×2 portrait grid.
- `netlify.toml` — redirects (`/jobs`, `/transcripts`, `/mis`, `/admin`).
- `tests/test_api_imports.py` — the import smoke-test (keep it).
- `docs/plans/2026-08-07-admin-mis-split.md` — the running decisions/progress log.

---

## 9. Commit trail (this session, oldest → newest)

```
5e3e212 fix(admin): define showToast()
f666449 refactor(web): extract shared sbFetch + showToast into admin-shared.js
62860d7 refactor(web): move Transcripts tab from admin.html to mis.html
7df5142 feat(web): make Transcripts a standalone page; revert mis to reporting-only
14f13ec feat(web): add per-store jobs.html console (extracted from admin)
c47fa3f feat(payments): staff PIN can mark-paid — no admin password needed
da40670 feat(jobs): replace queue/printer panels with a per-staff summary
71f6d22 feat(jobs): show per-staff summary AND the full job queue
4a4a65d feat(jobs): remove the By-Staff summary from the jobs window
96574e4 feat(order-v2): N-up fill direction + orientation-aware animation
bb1c337 fix(order-v2): 2-up vertical stacks; B&W preview shows monochrome
4b2995e hotfix(api): fix circular import crashing the whole API
93e00ef test(api): fresh-interpreter import smoke-test
3a30ba2 feat(order): /order/staff-create — walk-in jobs, full print_spec, no phone
   (+ docs(plan) commits interleaved)
```

---

## 10. Deferred / known issues (fix opportunistically)

- **`jobs.html` per-store scoping is partial.** The queue + printer breakdown are
  store-scoped; the **top stats strip** and (removed but if restored) the Printer
  Job Log read pre-aggregated tables (`daily_summary`, `konica_jobs`, `epson_jobs`)
  that are NOT store-scoped — they show all-store totals. Scope them in a follow-up.
- **`transcripts.html`:** `trScrollToPage` and `trEditBalance` are referenced but
  never defined (pre-existing, harmless — page-scroll no-op / static balance).
- **Konica per-staff attribution is dead** (`konica_jobs.attributed_to` 0/4507).
  Any per-staff pages should aggregate from `jobs.printed_by`, not Konica.
- **Staff-create inline payment**: `/order/staff-create` creates a Pending job;
  staff mark-paid separately on `jobs.html`. Optional future: capture cash/UPI in
  order-v2 staff mode and mark paid in one step.
