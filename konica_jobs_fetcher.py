"""
KONICA JOB LOG AUTO-FETCHER
============================
Periodically downloads the job history CSV from the Konica Bizhub web admin
and feeds it to konica_csv_importer.py.

HOW TO FIND THE CORRECT DOWNLOAD URL:
  1. Open Chrome/Edge, go to http://192.168.55.110
  2. Log in (username: Admin, password: blank or whatever is set)
  3. Navigate to the job log / job history page
  4. Press F12 → Network tab → clear log
  5. Click the CSV download / export button
  6. In the Network tab, find the request that downloads the CSV
  7. Right-click → Copy → Copy URL
  8. Paste that URL into KONICA_JOB_EXPORT_URL below

Common Konica Bizhub URL patterns (try these if the above is unclear):
  http://192.168.55.110/wcd/joblist_export.csv
  http://192.168.55.110/wcd/job_history.csv
  http://192.168.55.110/wcd/index.html?func=PSL_JL_EXPORT  (POST)

Runs as a background thread started by watcher.py (via start_fetcher()).
Fetches every FETCH_INTERVAL seconds (default: 30 minutes).
On first successful import, subsequent runs only import new job numbers (deduped by job_number UNIQUE constraint).
"""

import os
import io
import time
import logging
import threading
import requests
from datetime import datetime

import konica_csv_importer as importer

logger = logging.getLogger("konica_fetcher")

# ── Config ────────────────────────────────────────────────────────────────────
KONICA_IP           = "192.168.55.110"
KONICA_USER         = "Admin"
KONICA_PASS         = ""           # blank by default; set if changed

# Set this once you've found the correct URL via browser DevTools (see above).
# Leave as None to disable auto-fetch (manual CSV import still works).
KONICA_JOB_EXPORT_URL = None   # e.g. "http://192.168.55.110/wcd/joblist_export.csv"

KONICA_LOGIN_URL    = f"http://{KONICA_IP}/wcd/index.html"
HTTP_TIMEOUT        = 30       # seconds — job log can be large
FETCH_INTERVAL      = 1800     # seconds (30 minutes)

# Fallback URLs to try automatically if KONICA_JOB_EXPORT_URL is None
_CANDIDATE_URLS = [
    f"http://{KONICA_IP}/wcd/joblist_export.csv",
    f"http://{KONICA_IP}/wcd/job_log_export.csv",
    f"http://{KONICA_IP}/wcd/AccountTrack/Export.csv",
    f"http://{KONICA_IP}/wcd/jobhistory.csv",
]

# ── HTTP session helper ───────────────────────────────────────────────────────

def _make_session():
    """Create an authenticated requests session to the Konica web admin."""
    session = requests.Session()
    try:
        login_data = {
            "func":           "PSL_LP_SLOGIN",
            "S_LoginName":    KONICA_USER,
            "S_Password":     KONICA_PASS,
            "S_Permissions":  "",
        }
        session.post(KONICA_LOGIN_URL, data=login_data, timeout=10)
        logger.debug("Konica web login attempted")
    except Exception as e:
        logger.debug(f"Konica web login: {e}")
    return session


def _try_download(session, url):
    """
    Try to GET a URL and return the response text if it looks like a CSV.
    Returns None if the URL doesn't return CSV content.
    """
    try:
        r = session.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None
        content = r.text.strip()
        # Must start with "Job Number" header or at least have commas and digits
        if "Job Number" in content[:200] or (content.count(",") > 10 and content[0].isdigit()):
            return content
        return None
    except Exception as e:
        logger.debug(f"  {url}: {e}")
        return None


def discover_export_url():
    """
    Try candidate URLs and return the first one that returns valid CSV data.
    Saves the discovered URL back to module-level config.
    """
    global KONICA_JOB_EXPORT_URL
    logger.info("Discovering Konica job export URL…")
    session = _make_session()
    for url in _CANDIDATE_URLS:
        logger.info(f"  Trying {url}")
        csv_text = _try_download(session, url)
        if csv_text:
            logger.info(f"  Found export URL: {url}")
            KONICA_JOB_EXPORT_URL = url
            return url, csv_text
    logger.warning(
        "Could not auto-discover Konica job export URL.\n"
        "  → Open http://192.168.55.110 in browser, go to job log, click CSV export,\n"
        "    inspect the download URL in DevTools (F12 → Network),\n"
        "    then set KONICA_JOB_EXPORT_URL in konica_jobs_fetcher.py"
    )
    return None, None


# ── Main fetch function ───────────────────────────────────────────────────────

def fetch_and_import(db_path):
    """
    Download the job log CSV from Konica and import new rows.
    Returns (inserted, skipped, errors) or None on failure.
    """
    global KONICA_JOB_EXPORT_URL

    session = _make_session()
    csv_text = None

    if KONICA_JOB_EXPORT_URL:
        csv_text = _try_download(session, KONICA_JOB_EXPORT_URL)
        if not csv_text:
            logger.warning(f"Konica export URL returned no CSV: {KONICA_JOB_EXPORT_URL}")
            KONICA_JOB_EXPORT_URL = None   # reset and re-discover next time

    if not csv_text:
        url, csv_text = discover_export_url()
        if not csv_text:
            return None

    # Feed the in-memory CSV string to the importer (no temp file needed)
    try:
        csv_file = io.StringIO(csv_text)
        import csv as csv_mod
        reader = csv_mod.DictReader(csv_file)
        rows = list(reader)

        # Use the importer's internal helpers
        importer.init_konica_jobs_table(
            __import__("sqlite3").connect(db_path)
        )

        # Write to a temp file so the importer can read it normally
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                        delete=False, encoding="utf-8") as tf:
            tmp_path = tf.name
            tf.write(csv_text)

        result = importer.import_csv(tmp_path, db_path)
        os.unlink(tmp_path)

        ins, sk, err = result
        logger.info(f"Konica auto-fetch complete: +{ins} new jobs, {sk} duplicates, {err} errors")
        return result

    except Exception as e:
        logger.error(f"Konica auto-fetch import error: {e}")
        return None


# ── Background thread ─────────────────────────────────────────────────────────

def start_fetcher(db_path, interval=FETCH_INTERVAL):
    """
    Start the Konica job log fetcher as a daemon background thread.
    Called from watcher.py after first CSV has been imported.

    The thread:
      - Runs an immediate fetch on startup
      - Then fetches every `interval` seconds
      - Skips gracefully if Konica is unreachable
      - New jobs are inserted; duplicates are silently ignored
    """
    def loop():
        logger.info(f"Konica job fetcher started — polling every {interval}s")
        while True:
            try:
                fetch_and_import(db_path)
            except Exception as e:
                logger.error(f"Konica fetcher error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True, name="KonicaJobFetcher")
    t.start()
    logger.info("Konica job fetcher thread launched")
    return t


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")

    db = os.path.join(os.path.expanduser("~"), "Printosky", "Data", "jobs.db")
    if len(sys.argv) > 1:
        db = sys.argv[1]

    print(f"DB: {db}")
    print(f"Konica IP: {KONICA_IP}")
    print()

    result = fetch_and_import(db)
    if result:
        ins, sk, err = result
        print(f"\nResult: +{ins} new jobs, {sk} duplicates, {err} errors")
    else:
        print("\nFetch failed — check logs above and set KONICA_JOB_EXPORT_URL manually")
