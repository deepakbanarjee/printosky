"""
EPSON JOB LOG FETCHER
======================
Fetches per-job print details from the Epson WF-C21000 (192.168.55.202).

Two-tier approach:
  Tier 1 — Web log CSV: logs in via form POST, extracts SETUPTOKEN from
            INFO_JOBHISTORY/TOP, then POSTs to OUTPUT.CSV. Rows stored
            with source='weblog'. Auth: Oxygen / Oxygen@1234 (set 2026-04-29).

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

import csv
import io
import re
import time
import sqlite3
import logging
import threading
import requests
import urllib3
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger("epson_fetcher")

# ── Config ─────────────────────────────────────────────────────────────────────
EPSON_IP           = "192.168.55.202"
EPSON_BASE         = f"https://{EPSON_IP}"
EPSON_USER         = "Oxygen"
EPSON_PASS         = "Oxygen@1234"
HTTP_TIMEOUT       = 15        # seconds
FETCH_INTERVAL     = 300       # seconds (5 minutes — matches SNMP poll)
_WEBLOG_FAIL_LIMIT = 3         # give up Tier 1 after this many consecutive failures

LOGIN_URL   = f"{EPSON_BASE}/PRESENTATION/ADVANCED/PASSWORD/SET"
HISTORY_URL = f"{EPSON_BASE}/PRESENTATION/ADVANCED/INFO_JOBHISTORY/TOP"
EXPORT_URL  = f"{EPSON_BASE}/PRESENTATION/ADVANCED/INFO_JOBHISTORY/OUTPUT.CSV"

# Session-level state
_weblog_fail_count = 0
_weblog_available  = True   # set to False after _WEBLOG_FAIL_LIMIT failures


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


# ── Tier 1: Web log CSV (form login + SETUPTOKEN) ─────────────────────────────

def _make_session() -> requests.Session:
    session = requests.Session()
    session.verify = False
    return session


def _login(session: requests.Session) -> bool:
    """POST credentials to ADVANCED/PASSWORD/SET to acquire a session cookie."""
    try:
        r = session.post(
            LOGIN_URL,
            data={
                "SEL_SESSIONTYPE": "ADMIN",
                "INPUTT_USERNAME":  EPSON_USER,
                "INPUTT_PASSWORD":  EPSON_PASS,
                "access":           "https",
            },
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )
        # Successful login sets EPSON_COOKIE_SESSION; check for it
        ok = r.status_code == 200 and "EPSON_COOKIE_SESSION" in session.cookies
        if not ok:
            logger.debug(f"Epson login failed — no session cookie. len={len(r.text)}")
        return ok
    except Exception as e:
        logger.debug(f"Epson login error: {e}")
        return False


def _get_setup_token(session: requests.Session) -> str | None:
    """GET job history page and extract the per-session SETUPTOKEN."""
    try:
        r = session.get(HISTORY_URL, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        m = re.search(r'name="INPUTT_SETUPTOKEN"\s+value="([^"]+)"', r.text)
        return m.group(1) if m else None
    except Exception as e:
        logger.debug(f"Epson token fetch error: {e}")
        return None


def _download_csv(session: requests.Session, token: str) -> str | None:
    """POST to the CSV export endpoint with the session token."""
    try:
        r = session.post(
            EXPORT_URL,
            data={"INPUTT_SETUPTOKEN": token, "INPUTD_HISTORYTYPE": "ALL"},
            timeout=HTTP_TIMEOUT,
        )
        r.raise_for_status()
        return r.text
    except Exception as e:
        logger.debug(f"Epson CSV download error: {e}")
        return None


def _probe_job_log(session: requests.Session):
    """
    Login, get token, download CSV.
    Returns (EXPORT_URL, csv_text, 'csv') or (None, None, None) on failure.
    """
    if not _login(session):
        logger.debug("Epson Tier 1: login failed")
        return None, None, None
    token = _get_setup_token(session)
    if not token:
        logger.debug("Epson Tier 1: SETUPTOKEN not found")
        return None, None, None
    csv_text = _download_csv(session, token)
    if csv_text:
        logger.info("Epson web log CSV downloaded successfully")
        return EXPORT_URL, csv_text, "csv"
    return None, None, None


def _parse_joblog_csv(csv_text: str) -> list[dict]:
    """
    Parse the Epson job history CSV.
    CSV columns: Receipt No., Date/Time, Completed, Type, Result, Pages,
                 From, To, Total Receipt Fax Data, File Name,
                 Reference Receipt No., Receipt Number for Sending, Storage
    Returns Print-type jobs only.
    """
    lines = csv_text.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if "Receipt No." in line),
        None,
    )
    if header_idx is None:
        logger.warning("Epson CSV: header row not found")
        return []

    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    rows = []
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    for row in reader:
        row = {k.strip().strip('"'): v.strip().strip('"') for k, v in row.items() if k}
        if not row.get("Receipt No."):
            continue
        if row.get("Type", "").strip() != "Print":
            continue
        # Map to epson_jobs schema
        pages_str = row.get("Pages", "")  # format "2/2"
        try:
            pages_printed = int(pages_str.split("/")[0])
        except (ValueError, IndexError):
            pages_printed = None
        rows.append({
            "source":        "weblog",
            "job_number":    row.get("Receipt No."),
            "job_type":      row.get("Type"),
            "file_name":     row.get("File Name"),
            "result":        row.get("Result"),
            "pages_printed": pages_printed,
            "job_date":      row.get("Date/Time"),
            "print_end_date": row.get("Completed"),
            "imported_at":   now,
        })
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
                   OR LOWER(printer) LIKE '%192.168.55.202%')
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
                if fmt == "csv":
                    rows = _parse_joblog_csv(text)
                    if rows:
                        _import_weblog_rows(conn, rows)
                    else:
                        logger.info("Epson web log: CSV downloaded but no Print jobs found")
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
