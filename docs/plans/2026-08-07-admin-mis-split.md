# Plan / Handoff — Slim the Admin page to store-only; move back-office to MIS

**Date:** 2026-08-07 · **Author:** Claude · **Status:** planned, not started
**Repo:** `D:\PY\printosky` · source of truth `origin/main`

> Owner's ask: *"overhaul the admin page — make it minimal, keep only what's
> required for the store, move everything else to the MIS page."* This handoff
> captures the current state, the keep/move split, the open decisions, and a
> safe execution order. Written because the working session was near its context
> limit — a future session (Claude or Antigravity) can execute from here.
> See [[feedback_multiagent_handoff_docs]] convention.

---

## 1. Why
`website/admin.html` has grown to **7,320 lines / 6 tabs** and mixes two very
different audiences:
- **The store counter operator** (needs: see jobs, print, take payment, hand over).
- **Back-office / MIS** (academic pipeline, book campaign, referrals, transcripts,
  courier ops, settlements, chat inspection).

Loading all of that on the store PC is slow, error-prone, and exposes controls a
counter operator never needs. Goal: **admin = store only; everything else → MIS.**

## 2. Current state (verified 2026-08-07)
- **admin.html** (7,320 lines) — tabs via `switchTab(tab)`:
  1. **Jobs** (`tab-jobs`) — print job queue + job detail panel. *(STORE)*
  2. **Academic Projects** (`tab-academic`). *(MOVE)*
  3. **Referrals** (`tab-referrals`). *(MOVE)*
  4. **Project Orders** (`tab-pb-orders`). *(MOVE)*
  5. **Book Orders** (`tab-book-orders`) + payments-to-verify, dispatch sheet,
     courier slips, import manifest, Divya settlement/statement, walk-in book
     order. *(MOVE)*
  6. **Transcripts** (`tab-transcripts`) — manuscript OCR. *(MOVE)*
  - Plus a **Conversations / chat inspector** (WhatsApp thread viewer). *(MOVE)*
- **mis.html** (913 lines) — **currently a REPORTING dashboard only** (Konica/Epson
  job counts + revenue by Today/Week/Month/Year via `setKJTab`/`setSPTab`). It is
  NOT an operations console today. ← key scoping fact (see Decision A).
- **operator-mode.html** (403 lines), **superadmin.html** (1,239 lines) also exist.
- **Auth tiers already exist** (`/.netlify/functions/auth`, server-side per
  MASTER_PLAN "Admin sub-page auth (superadmin/store/MIS) ✅ Done"). admin.html
  and mis.html each authenticate through it.

## 3. The split

### KEEP in admin (the minimal store set)
- **Jobs tab**: queue table + filters (All/Completed/Pending/Today), **store filter
  (OSP/PRINTK) + `store-diag` badge** (commit d1d1f2c), search.
- **Job detail panel**: specs & quote, colour detection, print items, **Print**,
  **Mark Paid: Cash/UPI** (commit 47103fc — new), **Collect Payment**, **Notify
  Ready**, Save Specs.
- **Printer breakdown** (Konica/Epson pending counts) — what's queued to print.
- **New Job (walk-in)** + **Photocopy** modals.
- **Staff login/logout + badge**, **⚙ PC Setup**, store config / `fetchStoreConfig`.
- Minimal **stats strip** (today's pending count + revenue).

### MOVE to MIS / back-office
- Academic Projects, Referrals, Project Orders tabs.
- Book Orders tab + **all** book-campaign tooling: payments-to-verify, dispatch
  sheet, courier slips, import courier list/manifest, Divya statement/settlement,
  walk-in book order.
- Transcripts tab (manuscript OCR — the PRIOFF office node, see
  [[project_manuscript_transcription]]).
- Conversations / chat inspector.

> Backend note: **no API changes needed to move UI.** The academic/book/transcript
> handlers are separate endpoints (`api/handlers_admin.py`, `handlers_order`,
> `handlers_pb`, academic routes). Moving a tab = moving its HTML+JS to the target
> page; the endpoints it calls stay put.

## 4. Open decisions (need owner input before building)
- **A. What IS "the MIS page"?** Today's `mis.html` is reporting-only. Options:
  1. **Expand `mis.html`** into a full back-office console (reporting + the moved
     ops tabs). One page, MIS-tier auth. *(Recommended — matches owner's words.)*
  2. Keep `mis.html` as pure reporting; create a **new back-office ops page**
     (e.g. `backoffice.html`) for the moved tabs.
  Recommend #1 unless the owner wants reporting kept separate from ops.
- **B. Auth tier for moved features.** They currently gate on `admin_password`.
  Confirm the target page runs under the **MIS tier** (or superadmin) via
  `/.netlify/functions/auth`, and that the store PC's operators do NOT get MIS
  access.
- **C. Shared JS strategy.** admin.html is one monolith; the moved tabs share
  helpers (`sbFetch`, auth, `_convPw`, `_adminPwPrompt`, toast/alert, table
  render utils). Extract shared helpers into a common `admin-shared.js` include
  used by both pages, OR accept duplication. Recommend **extract** to avoid 2×
  maintenance and drift.

## 5. Execution order (incremental, low-risk)
Do NOT big-bang. Move one self-contained tab at a time, verify, then strip.
1. Decide A/B/C above.
2. Stand up the target page skeleton + shared JS include; wire MIS-tier auth.
3. Move **Transcripts** first (most self-contained) → verify end-to-end.
4. Move **Book Orders** (+ its panels) → verify (payments-to-verify, dispatch,
   courier slips, Divya settlement).
5. Move **Academic Projects**, **Project Orders**, **Referrals**, **Conversations**.
6. Only after all move + verify: **strip admin.html down to Jobs-only**, delete
   dead tab HTML/JS, and confirm the store operator flow (login → see jobs →
   print → mark paid → collect → done) is intact on the PRINTK store PC.
7. Update `netlify.toml` / any redirects and the nav links between pages.

## 6. Latest-status notes to fold in
- **Full-fidelity auto-print** shipped (duplex/N-up/mixed) — it's `store_puller`
  side, no admin UI, but the slim admin must keep job **status visibility**
  (Paid→Printed→Completed). See `HANDOFF_AUTOPRINT_FIDELITY.md`.
- **MASTER_PLAN:182** asked for an admin "collation warning when a mixed job is
  split to both printers." That need is now **largely moot**: the HIGH-2 fix
  forces mixed jobs to a single printer (the Epson), so there's no cross-printer
  split to warn about. Revisit only if multi-printer splitting is reintroduced.
- Keep **Mark Paid (cash/UPI)** and the **store-diag** badge in the slim admin —
  both are core store-counter features added this week.
- Pre-existing latent bug to fix opportunistically: `showToast()` is called ~15×
  in admin.html (DTP timer handlers) but defined nowhere → ReferenceError. (A task
  chip was spawned for this.)

## 7. Risks
- admin.html's shared-JS coupling — a naive cut breaks cross-references; that's
  why Decision C (extract shared) comes first.
- Don't disrupt the live store operator flow mid-migration — keep admin working
  until each moved tab is verified on MIS.
- Auth regressions — verify the store tier can't reach MIS ops and vice-versa.

## 8. Key files
- `website/admin.html` (source, 7,320 ln) · `website/mis.html` (913 ln) ·
  `website/superadmin.html` · `website/operator-mode.html`
- Auth: `netlify/functions/auth.js` (server-side tier check)
- Backends (unchanged by the move): `api/handlers_admin.py`, `api/handlers_order.py`,
  `api/handlers_pb.py`, academic routes in `api/index.py`
- Prior context: `docs/MASTER_PLAN.md` (auth tiers, staff MIS dashboard),
  `SPRINT_BACKLOG.md` S8-2 (mis.html never live-tested)

## 9. Progress log
- **2026-08-08 — Decisions locked** (owner input):
  - **A:** Expand `mis.html` into a tabbed back-office console (reporting stays as
    first tab; ops tabs join it).
  - **B:** MIS tier gates the *page*; ops **actions keep the existing admin-pw
    prompt** → zero backend change. Note: the admin password is **not shared with
    any staff yet**, so only the owner performs back-office ops for now — nobody is
    blocked by this. If staff back-office access is ever wanted, either share the
    admin pw or change the backend to accept MIS auth (`handlers_admin.py` currently
    hard-checks `admin_password` against `ADMIN_PASSWORD_HASH`).
  - **C:** Extract shared helpers into `website/admin-shared.js` (both pages include).
- **2026-08-08 — Step 0 done** (branch `claude/session-recap-pending-4vp603`):
  - `fix(admin): define showToast()` (`5e3e212`) — the DTP timer handlers called an
    undefined `showToast`, throwing ReferenceError on every start/pause/resume.
  - `refactor(web): extract shared sbFetch + showToast → admin-shared.js` (`f666449`).
    The two pages' `sbFetch` differed only in the 401 branch; unified into one file,
    with each page supplying a `sbAuthFail()` hook (admin → `logout()`;
    mis → `sessionStorage.clear(); location.reload()`). Behaviour-preserving; verified
    in Chromium that both pages resolve `sbFetch`/`showToast`/`sbAuthFail` cleanly.
- **2026-08-08 — Transcripts tab moved** (`62860d7`). First real tab migration.
  - mis.html gained a top-level tab shell (`.mis-tab`: Reporting | Transcripts) via
    `switchMisTab()`; existing reporting is `#mis-pane-reporting`, transcripts is
    `#mis-pane-transcripts`. Added pdf.js + a global `.ctrl-btn` to mis.
  - `getStoreId()` → `admin-shared.js` (admin's store-diag badge still needs it);
    `changeStore`/`requireStoreId` are transcript-only and moved with the tab.
  - admin.html stripped of the tab button, pane, `tr-edit-modal`, all `tr*` JS, and
    the `switchTab` transcripts branch.
  - NOTE: Transcripts does **not** use the admin-pw cluster (that stays in admin for
    Conversations) — so the modal/`_adminPwPrompt` move is deferred to the first ops
    tab that actually needs it (Conversations / Book Orders).
  - Verified in Chromium (both pages: no errors, tab switching works, transcripts
    renders). Caught + fixed one bug during verification: the moved inner
    `#transcripts-tab` div kept its own `display:none`, so the pane rendered blank
    until the wrapper `<main>` took over visibility.
  - Pre-existing latent bugs carried over verbatim (fix in follow-up): `trScrollToPage`
    and `trEditBalance` are referenced but never defined (both harmless/cosmetic).
- **2026-08-08 — ARCHITECTURE REVISION (owner):** heavy single-purpose ops tools get
  their **own standalone page/window** (like `chat.html`), NOT a tab inside mis.html.
  This refines Decision A: mis.html stays a **reporting-only** dashboard; back-office
  *tools* become dedicated pages. Implications:
  - **Conversations is already done** — it's the standalone `website/chat.html`
    (admin-pw auth). So the admin `#conv-panel` is **deleted** (not moved) at strip time.
  - **Transcripts → standalone `website/transcripts.html`** (`7df5142`). Auto-auths via
    the machine's `storeToken` (auth.js `staff` type → Supabase JWT, no password
    prompt); ⚙ Setup captures storeToken/storeId/storePcUrl. Reuses `admin-shared.js`
    + pdf.js. mis.html was reverted to reporting-only (the tab shell from `62860d7`
    removed). Verified in Chromium.
  - Net: the earlier mis tab-shell approach for Transcripts is **superseded**.
- **2026-08-08 — PIVOT: extract Jobs into a per-store page (owner direction).**
  Instead of the subtractive "strip admin down to Jobs," go **extractive**: pull the
  store console *out* into its own page each store logs into individually. Rationale:
  the store console is the critical path — building it fresh/small is safer than
  reverse-engineering a strip, per-store login is a real correctness win (no wrong-
  store filter), and the back-office monolith stays working untouched meanwhile.
  - **`website/jobs.html`** (`14f13ec`), route `/jobs`. Built as a **stripped copy**
    of admin.html (build-alongside — admin.html left fully intact). Removed all
    back-office (conv/academic/referrals/pb/books/opq tabs + modals + ~2.9k JS lines
    + tab bar); kept the whole store flow (queue, job panel, colour, DTP, quote,
    print, mark-paid/collect, notify, New Job, photocopy, printer breakdown, staff
    PIN login, PC setup) and the admin-pw cluster+modal (store payments use it).
  - **Per-store auto-scope:** store dropdown/diag removed; page scopes to the
    machine's `store_id` (from print_server `/status`). Verified: an OSP PC shows
    only OSP jobs (a PRINTK job is filtered out of both the queue and the breakdown).
  - Verified in Chromium: login→dashboard, scoped queue, job panel, all store fns,
    no errors.
  - **admin.html stays the owner back-office** (unchanged); tools keep peeling off it
    into standalone pages (chat ✓, transcripts ✓). A later cleanup can retire admin's
    now-duplicated Jobs code once jobs.html is proven on the real store PC.
- **FOLLOW-UPS surfaced by the Jobs extraction:**
  - **Payments need the admin password.** `markPaid`/`confirmPayment` send
    `X-Admin-Password` via `_convPw()`/`_adminPwPrompt()`. Since the admin password
    isn't shared with staff, staff currently can't mark-paid/collect without it.
    Decide: switch payment auth to the store/staff token (backend change) so counter
    staff can take payment without the admin pw. (Own workstream.)
  - **Remaining per-store scoping:** the stats strip (Today's Jobs/Revenue) and the
    Printer Job Log come from pre-aggregated `daily_summary` / `konica_jobs`/
    `epson_jobs` tables that aren't store-scoped — they still show all-store totals.
    Scope these to the machine's store (query or client-side) in a follow-up.
  - Pre-existing `trScrollToPage`/`trEditBalance` (now only in transcripts.html).
- **REMAINING back-office (still in admin.html): decide standalone page vs. keep.**
  - **Book Orders** (+ payments-verify/dispatch/courier/Divya): heavy → likely its own
    page (`book-orders.html`?). Uses admin-pw → the `_adminPwPrompt` + `admin-pw-modal`
    cluster goes to `admin-shared.js` when the first admin-pw page is built.
  - **Academic Projects / Project Orders / Referrals**: lighter; decide standalone vs.
    a small back-office page.
  - Then **strip admin.html to Jobs-only** (delete conv-panel + any moved tabs) and fix
    nav links so the store console links out to chat.html / transcripts.html / mis.html.
- **DEFERRED (separate workstream):** admin **New Job → order-v2 fidelity**. The
  `nj*` walk-in wizard posts a thin payload with no `print_spec`, so walk-in jobs
  skip the full-fidelity auto-print pipeline that customer `order-v2.html` orders get.
  To be done as its own branch/PR **before the final admin strip**, not folded into
  the relocation work. (Owner: "tackle it later.")
