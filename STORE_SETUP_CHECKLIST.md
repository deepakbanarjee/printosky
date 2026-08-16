# Store PC Checklist
## Printosky — Oxygen Students Paradise (`OSP`), Thriprayar

Tasks that need physical access to the store PC, the printers, or the store
phone. Everything here is blocked on being *at the store* — the rest of the
backlog lives in [SPRINT_BACKLOG.md](SPRINT_BACKLOG.md) and
[docs/OWNER_ACTIONS.md](docs/OWNER_ACTIONS.md).

_Last verified against the repo: 2026-08-16 (`main` @ `57ad33b`)._

> **Hardware as of 2026-08-16**
> Konica Bizhub Pro 1100 — `192.168.55.110`
> Epson **EM-C8100** — `192.168.55.214` (replaced the WF-C21000 `.202` on 2026-06-29)
> Windows queues: `KONICA MINOLTA 1100 PS` · `EM-C8100 Series(Network)`
> All of these come from `store_config.json`; nothing should be hardcoded.

---

## A. 🔴 The store PC is running stale code — do this first

`main` has 8 commits (2026-08-12 → 08-15) that change code running **on this
PC**. None of them are live until the workers are pulled and restarted. As of
2026-08-16 `watcher.log` had not been written since **2026-08-12** and
`cloud_worker.log` did not exist — i.e. the workers have been down for four
days, which matches the `store_puller.py` startup crash fixed in #67 on that
exact date.

- [ ] **Pull:** `PULL_UPDATE.bat` (or `git pull origin main` in `C:\PY\printosky`)
- [ ] **Restart:** `STOP_PRINTOSKY.bat` → `START_PRINTOSKY.bat`
      (`START_SILENT.bat` is what Windows Startup runs; it launches all six services)
- [ ] **Confirm each worker is actually up** — `STATUS_PRINTOSKY.bat`, then check
      that these have *fresh* timestamps:
  - `logs/store_puller.log`
  - `logs/watcher.log`
  - `cloud_worker.log`  ← did not exist on 2026-08-16; if it's still missing,
    `cloud_transcription_worker.py` never started — check `START_SILENT.bat`
- [ ] **Note any red lines** in the terminal windows and report them back.

**What the restart brings live:**

| Commit | Effect on this PC |
|---|---|
| #67 | `store_puller.py` module-level `logging.handlers` import — the startup crash |
| #66 | retry failed prints, recover stranded jobs, faster poll |
| #65 | rotating file log at `logs/store_puller.log` |
| #68 | **Supabase poll intervals cut** — store_puller 15s→45s, transcription 10s→60s, academic 30s→90s |
| #64 · #62 · `9105d81` | N-up orientation, short-edge bind for landscape, 2-up duplex 180° fix |
| #61 | colour billed per page (duplex no longer changes a colour price) |

> Because `watcher.py` starts `printer_poller.py` and `epson_jobs_fetcher.py` as
> daemon threads, a dead watcher also means **no SNMP readings, no ink alerts and
> no Epson job attribution** for the whole outage window. Expect a gap in
> `printer_counters`.

---

## B. 🟠 Supabase quota — restricted from 11 Sep 2026

Billing cycle 12 Aug – 12 Sep, checked 2026-08-16:

| Metric | Reading | Note |
|---|---|---|
| Egress | 2.903 / 5 GB (58%) | **4 days into a 31-day cycle** — projects to ~22 GB |
| Storage | 0.722 / 1 GB (72%) | was 441 MB when #68 was written; still climbing |
| Database | 0.107 / 0.5 GB (21%) | fine |

- [ ] **§A is the egress fix.** The #68 interval changes do nothing until the
      workers restart. Re-check the usage graph a day later and confirm the
      daily REST read count actually drops (it was 15,917/day).
- [ ] **Reclaim storage** — needs the service key in this PC's `.env`:
      ```
      python tools/storage_cleanup.py            REM dry run — read the report first
      python tools/storage_cleanup.py --apply    REM deletes; writes a CSV manifest first
      ```
      Safe by design: never touches a file referenced by `jobs.file_url`, never
      touches `book-payments/`, age-gated per tier.
- [ ] If egress is still tracking over after both, the remaining source is not
      the store PC — check the Vercel function logs next.

---

## C. 🖨️ Printers

- [ ] **Ping both** — `ping 192.168.55.110` and `ping 192.168.55.214`
- [ ] **Poll them** — `python printer_poller.py` should print Konica toner and
      Epson ink levels with no errors
- [ ] **Epson job log reachable** — `python epson_jobs_fetcher.py` (Tier 1 web
      log needs `EPSON_USER` / `EPSON_PASS` in `.env`)
- [ ] **TASK-004 — check and replace Epson cartridges** as needed
- [ ] **Re-verify the SNMP OID layout on the EM-C8100.** The vendor OIDs and the
      supply index→colour mapping in `printer_poller.py` were confirmed on the
      *retired* WF-C21000 and have never been re-walked:
      ```
      python epson_snmp_discover.py
      ```
      If ink alerts name the wrong colour, this is why — update the mapping at
      `printer_poller.py` §"Supply level polling".
- [ ] **Confirm the exact Windows queue name.** `print_server.py:405` assumes
      `EM-C8100 Series(Network)`. Check Devices & Printers; if it differs, set
      `printer_queue_names.epson` in `store_config.json` rather than editing code
      — delta attribution reads the same value.

### C1. Epson still has its default password (SEC4 / B3)
- [ ] Browser → `http://192.168.55.214` → log in → Security → Change Password
- [ ] Put the new value in `.env` as `EPSON_PASS` (it is read from there already;
      the credential fallback list in `epson_jobs_fetcher.py` should then be trimmed)

---

## D. 🧾 Print-fidelity smoke tests (real paper — only possible here)

The August auto-print work shipped with unit tests but **no physical
verification**. Print one of each and check the sheets:

- [ ] **2-up duplex, long edge** — `9105d81` added slot-orientation detection and
      a 180° back-page rotation. Confirm the backs are not upside down.
- [ ] **Landscape N-up** — should bind short-edge (#64).
- [ ] **Mixed B&W + colour document** — must come out pre-collated in document
      order, from a single printer (#62/#64).
- [ ] **Colour duplex quote** — #61 changed colour to bill per page; run a quote
      and check the total against the rate card.
- [ ] **S9-4 — A3 end-to-end** (bot → quote → print). Never tested.
- [ ] **S7-5 — thermal binding** (`finishing=thermal`). Listed in admin, rate
      never tested; if it's wrong, decide whether to fix or drop it.

---

## E. 👥 Staff & sessions

- [ ] **S8-1 — give staff their PINs:** `python staff_setup.py list`
      (never commit PIN values)
- [ ] **S8-5 — idle logout:** confirm `session_timeout.py` logs out an idle staff session
- [ ] **S8-2 / S8-3 — MIS live test:** open `mis.html`, confirm staff sessions are
      syncing to Supabase

---

## F. 🔵 Environment health

- [ ] **Run the suite:** `python -m pytest tests/ -q` — `main` is green as of #69
      (three stale assertions were fixed there; if those three fail you have not
      pulled)
- [ ] **Dependencies present:**
      ```
      python -c "import fitz;      print('PyMuPDF',  fitz.__version__)"
      python -c "import pikepdf;   print('pikepdf OK')"
      python -c "import reportlab; print('reportlab OK')"
      where soffice
      ```
- [ ] **Hot folder works** — drop a PDF into `C:\Printosky\Jobs\Incoming\`;
      `watcher.py` should log it immediately and it should reach the admin panel
      within ~30s. (Note: `*_transcript.docx/.txt/.pdf` are deliberately skipped
      since #68 — that's the DTP export guard, not a bug.)

---

## G. 🟡 Optional while you're at the machine

- [ ] **S11-2 — PM2 for the Node WhatsApp process** so it auto-restarts instead of
      dying with the CMD window
- [ ] **Clean-shutdown ping** — needs admin on this box, full steps in
      [docs/STORE_PC_SHUTDOWN_PING.md](docs/STORE_PC_SHUTDOWN_PING.md):
      `setx /M CRON_SECRET "<same value as Vercel>"`, then register
      `tools/store_pc_shutdown_ping.bat` via `gpedit.msc` → Shutdown scripts.
      The heartbeat watcher works without it; this only improves the wording.

---

## Not store-only (do from anywhere)

Tracked in [docs/OWNER_ACTIONS.md](docs/OWNER_ACTIONS.md) — repeated here so
nothing gets lost between visits:

- **TASK-001 — repoint the Razorpay webhook.** 0 Razorpay webhooks have ever been
  processed; no online payment is being recorded.
- **TASK-003 — submit the Meta pickup templates** (category UTILITY).
- **PR #51** (`feat/store-scoped-jobs`) — open and stale since 2026-08-05, likely
  superseded by the `jobs.html` work in #54–#60. Close or rebase.

---

## Reference

- Deploy: push to `main` auto-deploys Netlify (`website/`) and Vercel (`api/`).
  Store-PC code (`watcher.py`, `store_puller.py`, `print_server.py`,
  `printer_poller.py`) only updates when you pull **here**.
- Supabase project: `mlhuwlnwwwxdnqafelko` · Meta App ID: `922193850568204`
- Print server: `http://localhost:3005` · WhatsApp capture: `:3001`
- Store PC LAN address for remote admin: `192.168.55.212:3005`
