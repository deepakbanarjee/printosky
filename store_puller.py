"""
store_puller.py — pull routed jobs down to a store PC for printing.

A multi-store job is created in Supabase with an ``assigned_store_id``.
The file lives in Supabase Storage, not on the assigned store's PC. This
script runs on the store PC, polls Supabase for jobs routed to *this*
store that are paid and not yet pulled, and downloads each file into
``Jobs/Assigned/`` so staff can print it.

Why NOT the hot folder: dropping a file into ``Jobs/Incoming`` triggers
``watcher.log_new_file`` -> a brand-new local job **and** a customer
"file received, here's your quote" WhatsApp message. For a job that has
already been ordered and paid, that would double-message the customer and
create a duplicate order. So routed jobs bypass intake entirely and land
in a dedicated folder.

Idempotency: every downloaded job_id is recorded in a local ``pulled_jobs``
table, so a job is never downloaded twice even if it stays in the same
status across poll cycles.

Pickup is event-driven: a Supabase Realtime subscription on the ``jobs``
table wakes the loop the instant a row for this store changes (a job routed
here, or paid), so a job is normally pulled within a second or two rather
than waiting for the next scheduled cycle. POLL_SECONDS is now a fallback
safety net only — it still runs every cycle in case the realtime connection
drops, so nothing is lost, just slower. Set STORE_PULLER_REALTIME=0 to fall
back to poll-only.

Run:  python store_puller.py            # loop, poll every POLL_SECONDS
      python store_puller.py --once     # single pass then exit
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone

from ops_watchdog import report as _report_health

logger = logging.getLogger("store_puller")

# Fallback poll interval - only matters when realtime is down or disabled, since
# a live subscription wakes the loop immediately on every change. 15 minutes is
# plenty for a safety net (it used to be the primary pickup path at 45s, costing
# ~1.9k requests/day against the Supabase egress quota for a queue that is empty
# most of the time). Lower it via STORE_PULLER_POLL_SECONDS if a store wants a
# tighter fallback.
POLL_SECONDS = int(os.environ.get("STORE_PULLER_POLL_SECONDS", "900"))

# Realtime is on by default; STORE_PULLER_REALTIME=0 disables the subscription
# and falls back to polling only (e.g. an older supabase-py on a store PC that
# has not been updated, or Realtime not enabled on the `jobs` table yet).
REALTIME_ENABLED = os.environ.get("STORE_PULLER_REALTIME", "1").lower() not in ("0", "false", "no")

# Set by the realtime callback (a different thread); the main loop waits on it
# instead of a flat sleep so a change wakes it immediately.
_wake_event = threading.Event()

# Housekeeping: a printed download is deleted immediately; anything left behind
# (a job whose auto-print failed and was kept for manual printing) is purged
# after this many days so the disk cannot silently fill. Set 0 to disable.
KEEP_DAYS = int(os.environ.get("STORE_PULLER_KEEP_DAYS", "7"))

# Only these statuses mean "assigned to us and ready to print". A job that
# has not yet been paid, or is already delivered, must not be pulled.
PULLABLE_STATUSES = ("Paid",)

# Columns we need off each job row (colour/copies drive auto-print).
_JOB_COLUMNS = "job_id,filename,file_url,status,assigned_store_id,pickup_code,colour,copies,size,orientation,print_spec"


# -- local tracking table ------------------------------------------------------

def ensure_pulled_table(conn: sqlite3.Connection) -> None:
    """Create the local pulled_jobs tracking table if absent."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pulled_jobs (
            job_id     TEXT PRIMARY KEY,
            pulled_at  TEXT NOT NULL,
            dest_path  TEXT
        )
        """
    )
    conn.commit()


def load_pulled_ids(conn: sqlite3.Connection) -> set[str]:
    """Return the set of job_ids already pulled on this PC."""
    ensure_pulled_table(conn)
    rows = conn.execute("SELECT job_id FROM pulled_jobs").fetchall()
    return {r[0] for r in rows}


def record_pulled(conn: sqlite3.Connection, job_id: str, dest_path: str) -> None:
    """Mark a job_id as pulled so it is never downloaded again."""
    ensure_pulled_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO pulled_jobs (job_id, pulled_at, dest_path) "
        "VALUES (?, ?, ?)",
        (job_id, datetime.now(timezone.utc).isoformat(), dest_path),
    )
    conn.commit()


# -- pure selection logic ------------------------------------------------------

def safe_filename(job_id: str, filename: str | None) -> str:
    """Build a collision-safe local filename: ``<job_id>__<sanitised name>``."""
    base = re.sub(r"[^A-Za-z0-9._-]", "_", (filename or "file").strip()) or "file"
    return f"{job_id}__{base}"


def select_pullable(rows: list[dict], pulled_ids: set[str]) -> list[dict]:
    """Filter job rows down to those that should be downloaded now.

    A row is pullable when it has a job_id we have not pulled, a pullable
    status, and a non-empty file_url. The assigned_store_id match is done in
    the query, but callers may pass unfiltered rows -- this stays permissive
    about that and strict about the rest.
    """
    out: list[dict] = []
    for r in rows:
        job_id = (r.get("job_id") or "").strip()
        if not job_id or job_id in pulled_ids:
            continue
        if (r.get("status") or "") not in PULLABLE_STATUSES:
            continue
        if not (r.get("file_url") or "").strip():
            continue
        out.append(r)
    return out


# -- Supabase + download I/O (isolated so the logic above stays testable) ------

def fetch_assigned_paid(client, store_id: str) -> list[dict]:
    """Query Supabase for paid jobs routed to this store."""
    res = (
        client.table("jobs")
        .select(_JOB_COLUMNS)
        .eq("assigned_store_id", store_id)
        .eq("status", "Paid")
        .execute()
    )
    return getattr(res, "data", None) or []


def _realtime_thread(store_id: str, stop: threading.Event) -> None:
    """Run the Supabase Realtime subscription on its own asyncio loop.

    supabase-py 2.15 exposes Realtime through the **async** client only — the
    sync client's ``channel.subscribe()`` raises ``NotImplementedError`` ("use
    the realtime feature in the async client only"), which is exactly what
    silently dropped every store back to the slow fallback poll. So the
    subscription has to live on an asyncio event loop; we give it a dedicated
    daemon thread rather than making the whole puller async — the main loop
    stays a plain blocking poll, and this thread's only job is to set
    ``_wake_event`` the instant a matching ``jobs`` row changes.

    Best-effort by design: this is a latency optimisation, not the source of
    truth. Any failure reports to ops_watchdog and returns, leaving the
    fallback poll to keep the store running — just slower. A socket that dies
    later is rebuilt by realtime_liveness.hold, which also reports both edges,
    because the client's own auto-reconnect does not cover every way a
    connection can go and a dead socket used to leave this check green.

    We do not inspect the payload: any change on our store's rows might mean a
    job just went Paid, and pull_once's own status/file_url filter is the
    single source of truth for what actually gets pulled.
    """
    import asyncio

    import realtime_liveness

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")
    if not (url and key):
        logger.warning("store_puller: realtime disabled — SUPABASE_URL / key not set")
        _report_health("store_puller.realtime", False,
                       "SUPABASE_URL / key not set", store_id=store_id)
        return

    def _on_change(_payload):
        # Runs on the asyncio thread; threading.Event.set is thread-safe.
        _wake_event.set()

    async def _run() -> None:
        from supabase import create_async_client

        # create_async_client passes `key` to the realtime socket as its token,
        # so the connection authorises as service_role and RLS lets every change
        # on our store's rows through — no separate set_auth needed.
        client = await create_async_client(url, key)

        async def _subscribe() -> None:
            channel = client.channel(f"store-{store_id}-jobs")
            # on_postgres_changes(event, callback, table=, schema=, filter=) is
            # synchronous and chainable; subscribe() is the coroutine.
            channel.on_postgres_changes(
                "*", callback=_on_change, table="jobs", schema="public",
                filter=f"assigned_store_id=eq.{store_id}",
            )
            await client.realtime.connect()
            await channel.subscribe()

        await _subscribe()
        logger.info("store_puller: realtime subscription active for store %s", store_id)
        _report_health("store_puller.realtime", True, "subscribed", store_id=store_id)

        # Hold the loop open so the client's background listen + heartbeat
        # tasks keep pumping, and rebuild the socket if it dies — the client's
        # own auto-reconnect does not cover every way it can go (see
        # realtime_liveness). On return, asyncio.run cancels those tasks and
        # closes the socket.
        await realtime_liveness.hold(
            client.realtime, stop,
            resubscribe=_subscribe,
            on_status=lambda ok, detail: _report_health(
                "store_puller.realtime", ok, detail, store_id=store_id),
            label=f"store_puller[{store_id}]",
        )

    try:
        asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            "store_puller: realtime subscription failed (%s) — falling back to "
            "polling every %ss", exc, POLL_SECONDS,
        )
        _report_health("store_puller.realtime", False,
                       f"{type(exc).__name__}: {exc}", store_id=store_id)


def start_realtime(store_id: str) -> threading.Event:
    """Start the Realtime subscription in a background daemon thread.

    Returns a stop Event; set it to tear the subscription down. Callers that
    run forever can ignore the return value. Non-blocking — the subscription
    is a latency optimisation over the fallback poll, never the source of
    truth, so it must never delay or block startup.
    """
    stop = threading.Event()
    threading.Thread(
        target=_realtime_thread, args=(store_id, stop),
        name="store-puller-realtime", daemon=True,
    ).start()
    return stop


def _check_realtime_delivery(pulled: list[str], woken_by_realtime: bool, store_id: str) -> None:
    """Secondary signal: is the subscription actually delivering, not just
    connected? ``start_realtime`` can report "subscribed" successfully even
    when Realtime is not enabled on the `jobs` table in Supabase — the
    channel opens fine, it just never gets a postgres_changes event.

    The fallback poll should only ever be the one to find a job when realtime
    failed to wake the loop first. So: if this cycle pulled jobs AND the wait
    that preceded it timed out rather than being woken, the subscription is
    silently not delivering.

    Deliberately not gated on store hours (docs/FAIL_LOUD.md rejects an hours
    gate — that is what hid the Nattika outage). It does not need one: this
    only ever fires when a job was actually pulled, so it is silent by
    construction outside business activity.
    """
    if not pulled:
        return
    if woken_by_realtime:
        _report_health("store_puller.realtime_delivery", True,
                        f"pulled {len(pulled)} job(s) via realtime wake", store_id=store_id)
        return
    _report_health(
        "store_puller.realtime_delivery", False,
        f"pulled {len(pulled)} job(s) via the {POLL_SECONDS}s fallback poll with no prior "
        "realtime wake — subscription is connected but not delivering events "
        "(check Realtime is enabled on the `jobs` table in Supabase)",
        store_id=store_id,
    )


def download_url(url: str, dest_path: str) -> int:
    """Download ``url`` to ``dest_path``. Returns bytes written."""
    import requests

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "wb") as fh:
        fh.write(resp.content)
    return len(resp.content)


def purge_old_files(dest_dir: str, max_age_days: int) -> int:
    """Delete files in ``dest_dir`` older than ``max_age_days``. Returns the
    count removed. Best-effort — never raises. A ``max_age_days`` of 0 disables
    purging. Only top-level files are touched (the planner's temp_* dirs clean
    up themselves), so a job kept for manual printing survives its grace period.
    """
    if max_age_days <= 0 or not os.path.isdir(dest_dir):
        return 0
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for name in os.listdir(dest_dir):
        path = os.path.join(dest_dir, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info("store_puller: purged %d file(s) older than %dd from %s",
                    removed, max_age_days, dest_dir)
    return removed


# ── auto-print (best-effort) ──────────────────────────────────────────────────

def printer_key_for(colour: str | None) -> str:
    """Colour → epson; everything else → konica. A store with no Konica has its
    print server redirect konica→epson, so this is safe on Epson-only nodes."""
    c = (colour or "").strip().lower()
    return "epson" if c in ("col", "colour", "color") else "konica"


def colour_mode_for(colour: str | None) -> str:
    """Map the job's colour field to a SumatraPDF colour mode."""
    c = (colour or "").strip().lower()
    if c in ("col", "colour", "color"):
        return "colour"
    if c in ("bw", "b&w", "mono", "monochrome"):
        return "bw"
    return "auto"


def auto_print(job_id: str, dest_path: str, colour: str | None, copies,
               paper_size: str | None = None, orientation: str | None = None,
               print_spec: dict | None = None) -> bool:
    """Print a freshly-pulled job on this store's printer. Best-effort — never
    raises. On failure the file is left in Jobs/Assigned for manual printing.

    Reuses print_server.send_to_printer, which resolves the printer (incl. the
    no-Konica konica→epson redirect), prints via SumatraPDF, and marks the job
    Printed in Supabase. print_server only starts its HTTP server under
    __main__, so importing it here has no side effects.

    ``paper_size`` and ``orientation`` come off the cloud jobs row so the Epson
    gets the right sheet/orientation instead of the queue default.
    """
    temp_dir = None
    try:
        try:
            n = int(copies)
        except (TypeError, ValueError):
            n = 1

        # Check for missing or incomplete print_spec FIRST, before any imports.
        # This ensures the alert is sent even if imports fail.
        if not print_spec or not print_spec.get("sides"):
            # Map legacy colour format ('bw' -> 'bw', 'col'/'colour' -> 'col', 'mixed' -> 'mixed')
            c_mode = "bw"
            c = (colour or "").strip().lower()
            if c in ("col", "colour", "color"):
                c_mode = "col"
            elif c == "mixed":
                c_mode = "mixed"

            # ALERT: Missing/incomplete print_spec — this job lacks full print settings.
            # Happens for jobs created before SCHEMA_v35 or from non-web sources.
            # FIXED: Default to "simplex" (single-sided), not "duplex" (was the bug).
            # Safer to upgrade to duplex later than downgrade from duplex.
            # This fix applies to BOTH Konica and Epson printers (printer routing
            # happens later, after print_spec is processed).
            from ops_watchdog import report as _report_alert
            _report_alert(
                "store_puller.missing_print_spec",
                False,
                f"Job {job_id}: no print_spec or missing sides value — using safe default (single-sided). "
                "If duplex is needed, operator should re-print with correct settings.",
            )
            logger.warning(
                "store_puller: MISSING/INCOMPLETE PRINT_SPEC for job %s — defaulting to SIMPLEX "
                "(was previously DUPLEX — this was the bug). File: %s",
                job_id, dest_path
            )

            print_spec = {
                "copies": max(1, n),
                "paper_size": paper_size,
                "orientation": orientation,
                "colour_mode": c_mode,
                "sides": "simplex"  # ← FIXED: was "duplex" (caused single→duplex bug)
            }

        import print_planner
        from print_server import send_to_printer

        # Mixed-colour jobs are split into ordered B&W/colour sub-jobs. Each
        # sub-job routes to its NATURAL device — B&W -> Konica, colour -> Epson —
        # so B&W stays on the cheaper Konica (a no-Konica store redirects
        # konica->epson, so this is safe there too). print_planner keeps each
        # printer's sections in document order; the operator interleaves the two
        # trays, and the Konica's offset setting separates the B&W batches.
        # (Previously mixed was forced entirely to the Epson to keep everything in
        # one tray/order; Oxygen prefers the split.)

        dest_dir = os.path.dirname(os.path.abspath(dest_path))
        actions, temp_dir = print_planner.plan_print_job(job_id, dest_path, print_spec, dest_dir)

        success = True
        printed_actions = 0
        n_actions = len(actions)

        for idx, action in enumerate(actions):
            sub_pdf = action["pdf_path"]
            sub_colour = action["colour_mode"]
            sub_copies = action["copies"]
            sub_sides = action["sides"]
            sub_paper = action["paper_size"]
            sub_orient = action["orientation"]
            # .get(): a planner fallback action predates the key on old rows.
            sub_scaled = action.get("scale_applied", False)

            send_colour = "colour" if sub_colour == "colour" else ("bw" if sub_colour == "bw" else colour_mode_for(colour))
            printer_key = printer_key_for(sub_colour)

            # Mark the job "Printed" only on the FINAL sub-job. The loop breaks on
            # any failure, so this last (status-updating) call is reached only when
            # every prior sub-job succeeded — a mid-order failure leaves the job
            # un-marked for manual attention instead of falsely showing Printed.
            is_last = (idx == n_actions - 1)

            ok, msg = send_to_printer(
                job_id, sub_pdf, printer_key,
                copies=max(1, sub_copies), colour_mode=send_colour, staff_id=None,
                sides=sub_sides, paper_size=sub_paper, orientation=sub_orient,
                update_status=is_last, scale_applied=sub_scaled,
            )
            if ok:
                printed_actions += 1
                logger.info("store_puller: auto-printed sub-job %d/%d (%s)", idx + 1, n_actions, msg)
            else:
                success = False
                logger.warning("store_puller: auto-print sub-job %d/%d failed: %s", idx + 1, n_actions, msg)
                break

        return success and printed_actions > 0
    except Exception as exc:
        logger.warning(
            "store_puller: auto-print error for %s: %s (file in %s for manual print)",
            job_id, exc, dest_path,
        )
        return False
    finally:
        # Always remove the planner's temp working dir (sliced/imposed/sub PDFs) —
        # even on failure. It never holds the original download, so the original
        # stays in Jobs/Assigned for manual printing.
        if temp_dir:
            try:
                import print_planner as _pp
                _pp.cleanup_temp_dir(temp_dir)
            except Exception:
                pass


# -- orchestration -------------------------------------------------------------

def reconcile_stranded(client, store_id: str, conn: sqlite3.Connection) -> int:
    """Startup recovery: forget any job the cloud still reports as Paid but that
    is already in ``pulled_jobs``. Such a job was downloaded yet never printed
    (a transient disk-full / printer-busy failure under the old code), so drop
    its record and let the next poll pull + print it again. Returns the count
    reset. Best-effort — never raises.
    """
    try:
        rows = fetch_assigned_paid(client, store_id)
    except Exception as exc:
        logger.error("store_puller: reconcile fetch failed: %s", exc)
        return 0
    ensure_pulled_table(conn)
    pulled_ids = load_pulled_ids(conn)
    strand = [r["job_id"] for r in rows
              if (r.get("job_id") or "") and r["job_id"] in pulled_ids]
    for jid in strand:
        conn.execute("DELETE FROM pulled_jobs WHERE job_id=?", (jid,))
    if strand:
        conn.commit()
        logger.info("store_puller: reconcile — reset %d stranded Paid job(s) for retry: %s",
                    len(strand), ", ".join(strand))
    return len(strand)


def _claim(job_id: str) -> bool:
    """Claim `job_id` so that exactly one box at this store prints it.

    Every PC at a store runs this puller, and each keeps its own local
    `pulled_jobs` table — which one box cannot see in another. Without a shared
    claim, two boxes both see the same paid job and both print it. The claim is
    an atomic conditional update in Supabase: the first box wins.

    Fails CLOSED (no claim, no print) — wasted paper cannot be undone, and the
    job is retried next cycle. If the coordination module is missing entirely
    (an older deployment), fall through to the previous single-box behaviour
    rather than refusing to print at all.
    """
    try:
        from device_lease import claim_job
    except ImportError:
        return True
    return claim_job(job_id)


def _unclaim(job_id: str) -> None:
    """Hand a job back after a failed print so the retry can proceed."""
    try:
        from device_lease import release_job
        release_job(job_id)
    except ImportError:
        logger.debug("store_puller: device_lease not deployed — nothing to release "
                     "for %s (single-box behaviour)", job_id)


def pull_once(
    client,
    store_id: str,
    dest_dir: str,
    conn: sqlite3.Connection,
    downloader=download_url,
    on_pulled=None,
) -> list[str]:
    """Run one poll cycle. Returns the job_ids downloaded this pass.

    ``downloader`` is injectable so tests can drive this without real HTTP.
    A download failure for one job is logged and skipped; it will be retried
    next cycle because it is only recorded as pulled on success.

    ``on_pulled(row, dest_path)`` is an optional best-effort hook run after a
    successful download (used for auto-print). It returns True when the job
    printed, False when it failed. A job is recorded as pulled only when there is
    no hook (download-only) or the hook reports success — so a failed print
    retries next cycle rather than stranding at Paid.
    """
    ensure_pulled_table(conn)
    try:
        rows = fetch_assigned_paid(client, store_id)
    except Exception as exc:
        logger.error("store_puller: fetch failed for store %s: %s", store_id, exc)
        return []

    todo = select_pullable(rows, load_pulled_ids(conn))
    if not todo:
        return []

    os.makedirs(dest_dir, exist_ok=True)
    pulled: list[str] = []
    for row in todo:
        job_id = row["job_id"]
        # Claim before the download: no point spending bandwidth, let alone
        # paper, on a job another box at this store is already printing.
        if not _claim(job_id):
            continue
        dest = os.path.join(dest_dir, safe_filename(job_id, row.get("filename")))
        try:
            n = downloader(row["file_url"], dest)
        except Exception as exc:
            logger.error("store_puller: download failed for %s: %s", job_id, exc)
            _unclaim(job_id)          # we are not printing it; let a retry have it
            continue
        logger.info("store_puller: pulled %s -> %s (%s bytes)", job_id, dest, n)
        printed = None
        if on_pulled:
            try:
                printed = on_pulled(row, dest)
            except Exception as exc:
                logger.warning("store_puller: on_pulled hook failed for %s: %s", job_id, exc)
                printed = False
        # Record as done only when nothing remains to do: no autoprint hook
        # (download-only mode), or the print actually succeeded. A FAILED print is
        # left un-recorded so the next poll retries it — the job stays 'Paid' in
        # the cloud — instead of stranding at Paid forever (which is what happened
        # when a disk-full/printer-busy print failed after the download).
        if on_pulled is None or printed:
            record_pulled(conn, job_id, dest)
            pulled.append(job_id)
        else:
            logger.warning("store_puller: %s did not print — leaving un-recorded to retry next poll", job_id)
            _unclaim(job_id)
    return pulled


def _load_runtime():
    """Resolve store_id, download dir and a DB connection from the environment.

    Kept out of the tested logic because it touches store_config / dotenv /
    the real filesystem.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)
    except Exception:
        pass

    from store_config import get_store_config
    import db_cloud

    cfg = get_store_config()
    dest_dir = os.path.join(os.path.dirname(cfg.db_path), "..", "Jobs", "Assigned")
    dest_dir = os.path.normpath(dest_dir)
    conn = sqlite3.connect(cfg.db_path)
    client = db_cloud._client()
    return cfg.store_id, dest_dir, conn, client


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    once = "--once" in argv
    # Log to the console AND a rotating file, so the auto-print history survives
    # after the "Printosky Job Puller" window is closed. This is the process that
    # runs the imposition + auto_print, so its Print command lines land here (not
    # in print_server.log). 2 MB x 5 backups. Best-effort on the file handler.
    _handlers = [logging.StreamHandler()]
    try:
        _log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
        os.makedirs(_log_dir, exist_ok=True)
        _handlers.append(logging.handlers.RotatingFileHandler(
            os.path.join(_log_dir, "store_puller.log"),
            maxBytes=2_000_000, backupCount=5, encoding="utf-8",
        ))
    except Exception:
        pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=_handlers,
    )
    # The realtime socket carries the service_role key in its URL and in every
    # join frame; at INFO those libraries write it into store_puller.log. See
    # realtime_liveness.quiet_transport_loggers. Imported here, like the
    # subscription thread does, so the module stays importable without it.
    import realtime_liveness
    realtime_liveness.quiet_transport_loggers()
    try:
        store_id, dest_dir, conn, client = _load_runtime()
    except Exception as exc:
        logger.error("store_puller: startup failed: %s", exc)
        return 1

    # Auto-print each pulled job on this store's printer. On by default; set
    # STORE_PULLER_AUTOPRINT=0 to only download (staff print manually).
    autoprint = os.environ.get("STORE_PULLER_AUTOPRINT", "1").lower() in ("1", "true", "yes")
    on_pulled = None
    if autoprint:
        def on_pulled(row, dest):
            printed = auto_print(
                row.get("job_id"), dest, row.get("colour"), row.get("copies"),
                paper_size=row.get("size"), orientation=row.get("orientation"),
                print_spec=row.get("print_spec"),
            )
            # Printed successfully → the download is no longer needed; delete it
            # so Jobs/Assigned cannot fill the disk (root cause of the WinError
            # 112 that aborted a mid-run job). A failed print keeps the file for
            # manual printing; purge_old_files sweeps it up after KEEP_DAYS.
            if printed:
                try:
                    os.remove(dest)
                except OSError:
                    pass
            # Report success back to pull_once so a failed print is NOT recorded
            # as pulled and retries next cycle.
            return printed

    logger.info(
        "store_puller: store=%s dest=%s fallback_poll=%ss realtime=%s mode=%s autoprint=%s",
        store_id, dest_dir, POLL_SECONDS, REALTIME_ENABLED, "once" if once else "loop", autoprint,
    )
    # Recover any jobs stranded at Paid by an earlier failed print (pre-fix).
    reconcile_stranded(client, store_id, conn)

    if not once and REALTIME_ENABLED:
        start_realtime(store_id)

    # None on the first cycle: there is no preceding wait to judge yet, so the
    # delivery check (which needs to know whether realtime or the timeout woke
    # the loop) stays quiet until the second cycle.
    woken_by_realtime = None
    while True:
        purge_old_files(dest_dir, KEEP_DAYS)
        pulled = pull_once(client, store_id, dest_dir, conn, on_pulled=on_pulled)
        if REALTIME_ENABLED and woken_by_realtime is not None:
            _check_realtime_delivery(pulled, woken_by_realtime, store_id)
        if once:
            return 0
        # Woken immediately by the realtime callback on a matching row change;
        # otherwise falls through to the next scheduled cycle after POLL_SECONDS.
        woken_by_realtime = _wake_event.wait(POLL_SECONDS)
        _wake_event.clear()


if __name__ == "__main__":
    raise SystemExit(main())
