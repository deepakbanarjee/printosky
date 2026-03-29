"""
EPSON JOB LOG FETCHER
======================
Fetches per-job print details from the Epson WF-C21000 (192.168.55.201).

Two-tier approach:
  Tier 1 — Web log probe: attempts to pull a job history table from the
            EpsonNet Config web admin (HTTP Basic auth). If found, rows are
            parsed and stored with source='weblog'.

  Tier 2 — SNMP delta attribution: compares consecutive SNMP page-counter
            readings in printer_counters and attributes the page delta to
            jobs that were printed on the Epson in that window (matched via
            jobs.printer + jobs.printed_at). Stored with source='delta'.

Tier 2 always runs. Tier 1 is attempted on each cycle until it fails
_WEBLOG_FAIL_LIMIT consecutive times, after which it is skipped for the
session to avoid repeated timeouts.

Runs as a background daemon thread started by watcher.py.
Poll interval: 300 seconds (5 min) to match the SNMP poller cycle.
"""

import os
import re
import time
import sqlite3
import logging
import threading
import requests
from datetime import datetime

logger = logging.getLogger("epson_fetcher")

# ── Config ─────────────────────────────────────────────────────────────────────
EPSON_IP            = "192.168.55.201"
EPSON_ADMIN_USER    = "admin"   # EpsonNet Config default; check printer label
EPSON_ADMIN_PASS    = "admin"   # common default — update if changed
HTTP_TIMEOUT        = 10        # seconds
FETCH_INTERVAL      = 300       # seconds (5 minutes — matches SNMP poll)
_WEBLOG_FAIL_LIMIT  = 3         # give up Tier 1 after this many consecutive failures

# EpsonNet Config web admin — known job log URL candidates
_JOBLOG_CANDIDATES = [
    f"http://{EPSON_IP}/PRESENTATION/ADVANCED/JOBLOG/TOP.HTML",
    f"http://{EPSON_IP}/PRESENTATION/ADVANCED/JOBLOG/",
    f"http://{EPSON_IP}/job_history",
    f"http://{EPSON_IP}/joblog.htm",
    f"http://{EPSON_IP}/job_log",
]

# Session-level state
_weblog_fail_count  = 0
_weblog_available   = True   # set to False after _WEBLOG_FAIL_LIMIT failures


# ── DB init ────────────────────────────────────────────────────────────────────

def init_epson_jobs_table(conn):
    """Create epson_jobs + index if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS epson_jobs (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            source             TEXT    NOT NULL DEFAULT 'delta',
            job_number         TEXT,
            job_type           TEXT,
            user_name          TEXT,
            file_name          TEXT,
            result             TEXT,
            pages_printed      INTEGER,
            mono_pages         INTEGER,
            color_pages        INTEGER,
            copies             INTEGER,
            paper_size         TEXT,
            job_date           TEXT,
            print_end_date     TEXT,
            snmp_total_before  INTEGER,
            snmp_total_after   INTEGER,
            delta_pages        INTEGER,
            attributed_job_id  TEXT,
            imported_at        TEXT
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uix_epson_jobs_weblog
            ON epson_jobs (job_number) WHERE source = 'weblog'
    """)
    conn.commit()
    logger.info("epson_jobs table ready")


# ── Tier 1: Web log probe ──────────────────────────────────────────────────────

def _make_session():
    """HTTP session with Basic auth for EpsonNet Config web admin."""
    session = requests.Session()
    session.auth = (EPSON_ADMIN_USER, EPSON_ADMIN_PASS)
    return session


def _probe_job_log(session):
    """
    Try candidate URLs and return (url, response_text, fmt) where fmt is
    'html' or 'csv', or (None, None, None) if nothing found.
    """
    for url in _JOBLOG_CANDIDATES:
        try:
            r = session.get(url, timeout=HTTP_TIMEOUT)
            if r.status_code != 200:
                continue
            ct = r.headers.get("Content-Type", "")
            text = r.content.decode("utf-8", errors="replace")

            if "text/csv" in ct or url.endswith(".csv"):
                if text.count(",") > 5:
                    logger.info(f"Epson web log found (CSV): {url}")
                    return url, text, "csv"

            # HTML table heuristic: page with a <table> and job-related headers
            if "<table" in text.lower():
                keywords = ("job", "print", "date", "page", "file", "result")
                hits = sum(1 for kw in keywords if kw in text.lower())
                if hits >= 3:
                    logger.info(f"Epson web log found (HTML): {url}")
                    return url, text, "html"

            logger.debug(f"Epson {url}: {r.status_code} — no job log content")
        except Exception as e:
            logger.debug(f"Epson {url}: {e}")

    return None, None, None


def _parse_joblog_html(html):
    """
    Parse an HTML table from the Epson web log into a list of dicts.
    Maps column headers to epson_jobs fields by keyword matching.
    Returns list of dicts (may be empty).
    """
    rows = []
    try:
        # Extract all <tr> rows
        tr_blocks = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.IGNORECASE | re.DOTALL)
        if not tr_blocks:
            return rows

        # First row = headers
        headers_raw = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr_blocks[0], re.IGNORECASE | re.DOTALL)
        headers = [re.sub(r"<[^>]+>", "", h).strip().lower() for h in headers_raw]

        def _col(row_cells, *keywords):
            for i, h in enumerate(headers):
                if any(kw in h for kw in keywords):
                    if i < len(row_cells):
                        return row_cells[i].strip()
            return None

        for tr in tr_blocks[1:]:
            cells_raw = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", tr, re.IGNORECASE | re.DOTALL)
            cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells_raw]
            if not cells or all(c == "" for c in cells):
                continue

            row = {
                "source":      "weblog",
                "job_number":  _col(cells, "job", "number", "#"),
                "job_type":    _col(cells, "type", "kind"),
                "user_name":   _col(cells, "user", "host", "sender"),
                "file_name":   _col(cells, "file", "name", "document"),
                "result":      _col(cells, "result", "status", "error"),
                "pages_printed": _col(cells, "page", "count", "printed"),
                "job_date":    _col(cells, "date", "time", "start"),
                "print_end_date": _col(cells, "end", "finish", "complete"),
                "paper_size":  _col(cells, "paper", "media", "size"),
                "imported_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            }
            # Normalise pages to int
            try:
                row["pages_printed"] = int(row["pages_printed"]) if row["pages_printed"] else None
            except (ValueError, TypeError):
                row["pages_printed"] = None

            rows.append(row)
    except Exception as e:
        logger.warning(f"Epson HTML parse error: {e}")
    return rows


def _import_weblog_rows(conn, rows):
    """Insert web log rows; skip duplicates by job_number."""
    inserted = 0
    for r in rows:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO epson_jobs
                    (source, job_number, job_type, user_name, file_name, result,
                     pages_printed, job_date, print_end_date, paper_size, imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                r.get("source", "weblog"),
                r.get("job_number"),
                r.get("job_type"),
                r.get("user_name"),
                r.get("file_name"),
                r.get("result"),
                r.get("pages_printed"),
                r.get("job_date"),
                r.get("print_end_date"),
                r.get("paper_size"),
                r.get("imported_at"),
            ))
            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
        except Exception as e:
            logger.debug(f"Epson weblog insert: {e}")
    conn.commit()
    if inserted:
        logger.info(f"Epson web log: +{inserted} new job rows")
    return inserted


# ── Tier 2: SNMP delta attribution ────────────────────────────────────────────

def _delta_attribution(conn):
    """
    Compare consecutive printer_counters readings for 'epson'.
    For each interval where total_pages increased, find Printosky jobs
    that were printed on the Epson during that window and attribute the
    page delta proportionally. Insert epson_jobs rows with source='delta'.

    Skips intervals that have already been processed (checks for existing
    epson_jobs rows with snmp_total_before matching the interval start).
    """
    now = datetime.now().isoformat(sep=" ", timespec="seconds")

    # Get up to 100 recent SNMP readings (covers ~8 hours at 5-min intervals)
    readings = conn.execute("""
        SELECT polled_at, total_pages, print_colour
        FROM printer_counters
        WHERE printer = 'epson' AND total_pages IS NOT NULL
        ORDER BY polled_at DESC
        LIMIT 100
    """).fetchall()

    if len(readings) < 2:
        logger.debug("Epson delta: not enough SNMP readings yet")
        return 0

    inserted_total = 0

    # Walk pairs oldest→newest (reverse the DESC list)
    readings = list(reversed(readings))
    for i in range(len(readings) - 1):
        ts_before, total_before, col_before = readings[i]
        ts_after,  total_after,  col_after  = readings[i + 1]

        delta = total_after - total_before
        if delta <= 0:
            continue

        # Skip if already attributed
        already = conn.execute("""
            SELECT 1 FROM epson_jobs
            WHERE source = 'delta' AND snmp_total_before = ? AND snmp_total_after = ?
            LIMIT 1
        """, (total_before, total_after)).fetchone()
        if already:
            continue

        # Derive colour delta if available
        colour_delta = None
        if col_before is not None and col_after is not None:
            colour_delta = col_after - col_before
            if colour_delta < 0:
                colour_delta = None

        # Find Printosky jobs printed on Epson in this window
        jobs = conn.execute("""
            SELECT job_id, page_count, copies
            FROM jobs
            WHERE printed_at BETWEEN ? AND ?
              AND (LOWER(printer) LIKE '%epson%' OR LOWER(printer) LIKE '%wf%'
                   OR LOWER(printer) LIKE '%192.168.55.201%')
        """, (ts_before, ts_after)).fetchall()

        if jobs:
            # Distribute delta proportionally by expected page count
            total_expected = sum(
                max((j[1] or 1) * (j[2] or 1), 1) for j in jobs
            )
            for job_id, page_count, copies in jobs:
                weight = max((page_count or 1) * (copies or 1), 1)
                attributed = round(delta * weight / total_expected)
                col_attr = round(colour_delta * weight / total_expected) if colour_delta is not None else None
                mono_attr = (attributed - col_attr) if col_attr is not None else None

                conn.execute("""
                    INSERT INTO epson_jobs
                        (source, job_date, print_end_date,
                         pages_printed, mono_pages, color_pages,
                         snmp_total_before, snmp_total_after, delta_pages,
                         attributed_job_id, imported_at)
                    VALUES ('delta', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    ts_before, ts_after,
                    attributed, mono_attr, col_attr,
                    total_before, total_after, delta,
                    job_id, now,
                ))
                inserted_total += 1
        else:
            # No matching job — insert unattributed delta row
            mono_delta = (delta - colour_delta) if colour_delta is not None else None
            conn.execute("""
                INSERT INTO epson_jobs
                    (source, job_date, print_end_date,
                     pages_printed, mono_pages, color_pages,
                     snmp_total_before, snmp_total_after, delta_pages,
                     imported_at)
                VALUES ('delta', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ts_before, ts_after,
                delta, mono_delta, colour_delta,
                total_before, total_after, delta,
                now,
            ))
            inserted_total += 1

    if inserted_total:
        conn.commit()
        logger.info(f"Epson delta attribution: +{inserted_total} rows")
    return inserted_total


# ── Orchestrator ───────────────────────────────────────────────────────────────

def fetch_and_import(db_path):
    """
    One fetch cycle:
      1. Tier 1 — try Epson web log (unless disabled after repeated failures)
      2. Tier 2 — SNMP delta attribution (always)
    """
    global _weblog_fail_count, _weblog_available

    conn = sqlite3.connect(db_path)
    init_epson_jobs_table(conn)

    # Tier 1
    if _weblog_available:
        try:
            session = _make_session()
            url, text, fmt = _probe_job_log(session)
            if text:
                if fmt == "html":
                    rows = _parse_joblog_html(text)
                    if rows:
                        _import_weblog_rows(conn, rows)
                    else:
                        logger.info("Epson web log: HTML found but no parseable rows")
                elif fmt == "csv":
                    # CSV parsing not yet implemented — treat as unstructured text
                    logger.info(f"Epson web log: CSV found at {url} — manual parsing needed")
                _weblog_fail_count = 0
            else:
                _weblog_fail_count += 1
                logger.debug(f"Epson web log probe: no data ({_weblog_fail_count}/{_WEBLOG_FAIL_LIMIT})")
                if _weblog_fail_count >= _WEBLOG_FAIL_LIMIT:
                    _weblog_available = False
                    logger.info(
                        f"Epson web log unavailable after {_WEBLOG_FAIL_LIMIT} attempts — "
                        "falling back to delta-only mode for this session"
                    )
        except Exception as e:
            logger.warning(f"Epson Tier 1 error: {e}")
            _weblog_fail_count += 1

    # Tier 2
    try:
        _delta_attribution(conn)
    except Exception as e:
        logger.error(f"Epson delta attribution error: {e}")

    conn.close()


# ── Background thread ──────────────────────────────────────────────────────────

def start_fetcher(db_path, interval=FETCH_INTERVAL):
    """
    Start the Epson job fetcher as a daemon background thread.
    Called from watcher.py after startup.
    """
    def loop():
        logger.info(f"Epson job fetcher started — polling every {interval}s")
        logger.info(f"  Epson IP: {EPSON_IP}")
        logger.info(f"  Tier 1 (web log): {'enabled' if _weblog_available else 'disabled'}")
        while True:
            try:
                fetch_and_import(db_path)
            except Exception as e:
                logger.error(f"Epson fetcher error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True, name="EpsonJobFetcher")
    t.start()
    logger.info("Epson job fetcher thread launched")
    return t


# ── Standalone test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")

    db = r"C:\Printosky\Data\jobs.db"
    if len(sys.argv) > 1:
        db = sys.argv[1]

    print(f"DB: {db}")
    print(f"Epson IP: {EPSON_IP}\n")

    fetch_and_import(db)

    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT source, COUNT(*), MIN(job_date), MAX(job_date) FROM epson_jobs GROUP BY source"
    ).fetchall()
    print("\nepson_jobs summary:")
    for r in rows:
        print(f"  source={r[0]}  count={r[1]}  from={r[2]}  to={r[3]}")
    conn.close()
