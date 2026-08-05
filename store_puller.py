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

Run:  python store_puller.py            # loop, poll every POLL_SECONDS
      python store_puller.py --once     # single pass then exit
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

logger = logging.getLogger("store_puller")

POLL_SECONDS = int(os.environ.get("STORE_PULLER_POLL_SECONDS", "60"))

# Only these statuses mean "assigned to us and ready to print". A job that
# has not yet been paid, or is already delivered, must not be pulled.
PULLABLE_STATUSES = ("Paid",)

# Columns we need off each job row (colour/copies drive auto-print).
_JOB_COLUMNS = "job_id,filename,file_url,status,assigned_store_id,pickup_code,colour,copies"


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


def download_url(url: str, dest_path: str) -> int:
    """Download ``url`` to ``dest_path``. Returns bytes written."""
    import requests

    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(dest_path, "wb") as fh:
        fh.write(resp.content)
    return len(resp.content)


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


def auto_print(job_id: str, dest_path: str, colour: str | None, copies) -> bool:
    """Print a freshly-pulled job on this store's printer. Best-effort — never
    raises. On failure the file is left in Jobs/Assigned for manual printing.

    Reuses print_server.send_to_printer, which resolves the printer (incl. the
    no-Konica konica→epson redirect), prints via SumatraPDF, and marks the job
    Printed in Supabase. print_server only starts its HTTP server under
    __main__, so importing it here has no side effects.
    """
    try:
        try:
            n = int(copies)
        except (TypeError, ValueError):
            n = 1
        from print_server import send_to_printer

        ok, msg = send_to_printer(
            job_id, dest_path, printer_key_for(colour),
            copies=max(1, n), colour_mode=colour_mode_for(colour), staff_id=None,
        )
        if ok:
            logger.info("store_puller: auto-printed %s (%s)", job_id, msg)
        else:
            logger.warning("store_puller: auto-print failed for %s: %s", job_id, msg)
        return bool(ok)
    except Exception as exc:
        logger.warning(
            "store_puller: auto-print error for %s: %s (file in %s for manual print)",
            job_id, exc, dest_path,
        )
        return False


# -- orchestration -------------------------------------------------------------

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
    successful download (used for auto-print). It must not raise; if it does,
    the job still counts as pulled.
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
        dest = os.path.join(dest_dir, safe_filename(job_id, row.get("filename")))
        try:
            n = downloader(row["file_url"], dest)
        except Exception as exc:
            logger.error("store_puller: download failed for %s: %s", job_id, exc)
            continue
        record_pulled(conn, job_id, dest)
        pulled.append(job_id)
        logger.info("store_puller: pulled %s -> %s (%s bytes)", job_id, dest, n)
        if on_pulled:
            try:
                on_pulled(row, dest)
            except Exception as exc:
                logger.warning("store_puller: on_pulled hook failed for %s: %s", job_id, exc)
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
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
            auto_print(row.get("job_id"), dest, row.get("colour"), row.get("copies"))

    logger.info(
        "store_puller: store=%s dest=%s poll=%ss mode=%s autoprint=%s",
        store_id, dest_dir, POLL_SECONDS, "once" if once else "loop", autoprint,
    )
    while True:
        pull_once(client, store_id, dest_dir, conn, on_pulled=on_pulled)
        if once:
            return 0
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
