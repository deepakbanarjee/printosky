"""
KONICA JOB LOG FETCHER — CLRC SOAP API
========================================
Pulls job history directly from the Konica bizhub PRO 1100 via its built-in
Tomcat/Axis2 SOAP service on port 30081 (no Job Centro GUI needed).

Endpoint:  http://192.168.55.110:30081/clrc/services/CLRC
Operation: GetHistoryList (startIndex / endIndex, most-recent-first)
Auth:      None (printer auth mode is OFF)

Runs as a background thread started by watcher.py (via start_fetcher()).
Fetches every FETCH_INTERVAL seconds (default: 30 min).
New jobs are inserted; duplicates silently ignored (UNIQUE on job_number).
"""

import html
import io
import logging
import os
import re
import sqlite3
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger("konica_fetcher")

# ── Config ────────────────────────────────────────────────────────────────────
KONICA_IP      = "192.168.55.110"
SOAP_PORT      = 30081
SOAP_ENDPOINT  = f"http://{KONICA_IP}:{SOAP_PORT}/clrc/services/CLRC"
FETCH_INTERVAL = 1800          # seconds (30 min)
BATCH_SIZE     = 200           # jobs per SOAP call
HTTP_TIMEOUT   = 30

# ── SOAP helper ───────────────────────────────────────────────────────────────

_ENVELOPE = """\
<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:web="http://web.clrc.bskk.konicaminolta.jp">
  <soapenv:Header/>
  <soapenv:Body>
    <web:GetHistoryList>
      <web:opeUserXML></web:opeUserXML>
      <web:startIndex>{start}</web:startIndex>
      <web:endIndex>{end}</web:endIndex>
    </web:GetHistoryList>
  </soapenv:Body>
</soapenv:Envelope>"""


def _soap_get_history(start: int, end: int) -> list[dict]:
    """Call GetHistoryList and return a list of job dicts."""
    body = _ENVELOPE.format(start=start, end=end)
    r = requests.post(
        SOAP_ENDPOINT,
        data=body.encode("utf-8"),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": "urn:GetHistoryList",
        },
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()

    # The inner XML is HTML-entity-escaped inside <ns:return>
    m = re.search(r"<ns:return>(.*?)</ns:return>", r.text, re.DOTALL)
    if not m:
        raise ValueError("No <ns:return> in SOAP response")

    inner = html.unescape(m.group(1))
    root = ET.fromstring(inner)

    result_el = root.find("Result")
    result_code = result_el.get("code", "") if result_el is not None else ""
    if not result_code.startswith("2"):
        raise ValueError(f"Printer returned error code: {result_code}")

    jobs = []
    for h in root.iter("History"):
        jobs.append({
            "job_number":    h.get("jobnumber", ""),
            "job_type":      h.get("jobtype", ""),
            "user":          h.get("user", ""),
            "name":          h.get("name", ""),
            "result":        h.get("result", ""),
            "pages":         h.get("pages", "0"),
            "print_pages":   h.get("printpages", "0"),
            "mono_pages":    h.get("pagesmono", "0"),
            "color_pages":   h.get("pagescolor", "0"),
            "copies":        h.get("copies", "1"),
            "print_time":    h.get("printtime", ""),
            "media_size":    h.get("mediasize", ""),
            "media_type":    h.get("mediatype", ""),
            "register_time": h.get("jobregisttime", ""),
            "rip_start":     h.get("ripstarttime", ""),
            "rip_end":       h.get("ripendtime", ""),
            "print_start":   h.get("printstarttime", ""),
        })
    return jobs


def _get_job_count() -> int:
    """Return total number of history jobs stored on the printer."""
    body = """\
<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:web="http://web.clrc.bskk.konicaminolta.jp">
  <soapenv:Header/>
  <soapenv:Body><web:GetJobHistoryNum/></soapenv:Body>
</soapenv:Envelope>"""
    r = requests.post(
        SOAP_ENDPOINT,
        data=body.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": "urn:GetJobHistoryNum"},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    m = re.search(r"JobHistoryNum value=.(\d+)", html.unescape(r.text))
    return int(m.group(1)) if m else 0


# ── SQLite helpers ────────────────────────────────────────────────────────────

def _init_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS konica_jobs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            job_number     INTEGER UNIQUE,
            job_type       TEXT,
            user_name      TEXT,
            file_name      TEXT,
            result         TEXT,
            num_pages      INTEGER,
            pages_printed  INTEGER,
            mono_pages     INTEGER,
            color_pages    INTEGER,
            copies         INTEGER,
            job_date       TEXT,
            print_end_date TEXT,
            paper_size     TEXT,
            paper_type     TEXT,
            attributed_to  TEXT,
            imported_at    TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def _insert_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    inserted = skipped = 0
    for j in jobs:
        try:
            conn.execute("""
                INSERT INTO konica_jobs
                  (job_number, job_type, user_name, file_name, result,
                   num_pages, pages_printed, mono_pages, color_pages, copies,
                   job_date, print_end_date, paper_size, paper_type)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                j["job_number"], j["job_type"], j["user"], j["name"], j["result"],
                int(j["pages"] or 0), int(j["print_pages"] or 0),
                int(j["mono_pages"] or 0), int(j["color_pages"] or 0),
                int(j["copies"] or 1),
                j["register_time"], j["print_time"],
                j["media_size"], j["media_type"],
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()
    return inserted, skipped


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_and_import(db_path: str) -> tuple[int, int, int] | None:
    """
    Fetch recent job history from the printer and import new rows.
    Returns (inserted, skipped, errors) or None on failure.

    Strategy: fetch the most-recent BATCH_SIZE jobs (index 1..BATCH_SIZE).
    Once all are duplicates (skipped == batch size), stop — no older new jobs.
    """
    try:
        conn = sqlite3.connect(db_path)
        _init_table(conn)

        total_inserted = total_skipped = total_errors = 0
        start = 1

        while True:
            end = start + BATCH_SIZE - 1
            logger.info(f"Konica SOAP fetch: jobs {start}–{end}")
            try:
                jobs = _soap_get_history(start, end)
            except Exception as e:
                logger.error(f"SOAP fetch error at {start}-{end}: {e}")
                total_errors += 1
                break

            if not jobs:
                break

            ins, sk = _insert_jobs(conn, jobs)
            total_inserted += ins
            total_skipped  += sk

            # Stop when entire batch was already in DB (caught up)
            if sk == len(jobs) and ins == 0:
                logger.info("Konica fetch: all duplicates — caught up.")
                break

            if len(jobs) < BATCH_SIZE:
                break  # last page

            start = end + 1

        conn.close()
        logger.info(
            f"Konica SOAP import done: +{total_inserted} new, "
            f"{total_skipped} duplicates, {total_errors} errors"
        )
        return total_inserted, total_skipped, total_errors

    except Exception as e:
        logger.error(f"Konica fetch_and_import failed: {e}")
        return None


# ── Background thread ─────────────────────────────────────────────────────────

def start_fetcher(db_path: str, interval: int = FETCH_INTERVAL) -> threading.Thread:
    def loop():
        logger.info(f"Konica SOAP fetcher started — polling every {interval}s")
        while True:
            try:
                fetch_and_import(db_path)
            except Exception as e:
                logger.error(f"Konica fetcher loop error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True, name="KonicaJobFetcher")
    t.start()
    return t


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    db = r"C:\Printosky\Data\jobs.db"
    if len(sys.argv) > 1:
        db = sys.argv[1]

    print(f"Printer:  {SOAP_ENDPOINT}")
    print(f"DB:       {db}")

    count = _get_job_count()
    print(f"Total jobs on printer: {count}")

    result = fetch_and_import(db)
    if result:
        ins, sk, err = result
        print(f"\nResult: +{ins} new, {sk} duplicates, {err} errors")
    else:
        print("\nFetch failed — check logs above")
