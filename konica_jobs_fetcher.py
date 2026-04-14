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
import csv as csv_mod
import time
import logging
import threading
import xml.etree.ElementTree as ET
import requests
from datetime import datetime

import konica_csv_importer as importer

logger = logging.getLogger("konica_fetcher")

# ── Config ────────────────────────────────────────────────────────────────────
KONICA_IP           = "192.168.55.110"
KONICA_USER         = "Admin"
KONICA_PASS         = ""           # blank by default; set if changed

# Set this once you've found the correct URL via browser DevTools (see module docstring).
# Leave as None to enable auto-discovery from _CANDIDATE_URLS.
KONICA_JOB_EXPORT_URL = None   # e.g. "http://192.168.55.110/wcd/joblist_export.csv"

KONICA_LOGIN_URL         = f"http://{KONICA_IP}/wcd/index.html"
KONICA_JOB_HISTORY_XML   = f"http://{KONICA_IP}/wcd/job_history.xml"   # confirmed endpoint
HTTP_TIMEOUT             = 30       # seconds — job log can be large
FETCH_INTERVAL      = 1800     # seconds (30 minutes)

# Cache file — persists discovered URL across restarts
_URL_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "konica_export_url.txt")

def _load_cached_url():
    try:
        with open(_URL_CACHE_FILE) as f:
            return f.read().strip() or None
    except FileNotFoundError:
        return None

def _save_cached_url(url):
    try:
        with open(_URL_CACHE_FILE, "w") as f:
            f.write(url)
    except Exception:
        pass

# Load cached URL on module import (overridden by hardcoded value above if set)
KONICA_JOB_EXPORT_URL = KONICA_JOB_EXPORT_URL or _load_cached_url()

# Candidate URLs/POSTs to try automatically
# Prefix "POST:<url>|<form_key>=<val>&..." for POST-based exports
_CANDIDATE_URLS = [
    f"http://{KONICA_IP}/wcd/joblist_export.csv",
    f"http://{KONICA_IP}/wcd/job_log_export.csv",
    f"http://{KONICA_IP}/wcd/AccountTrack/Export.csv",
    f"http://{KONICA_IP}/wcd/jobhistory.csv",
    f"POST:http://{KONICA_IP}/wcd/index.html|func=PSL_JL_EXPORT",
    f"POST:http://{KONICA_IP}/wcd/index.html|func=PSL_JL_CSV",
    f"POST:http://{KONICA_IP}/wcd/index.html|func=PSL_JH_EXPORT",
    f"POST:http://{KONICA_IP}/wcd/index.html|func=PSL_JH_CSV",
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
    Try to GET (or POST) a URL and return the response text if it looks like a CSV.
    Handles "POST:<url>|<form_data>" prefix for POST-based Konica exports.
    Uses utf-8-sig decoding to strip the BOM that Bizhub sometimes prepends.
    Returns None if the URL doesn't return CSV content.
    """
    try:
        if url.startswith("POST:"):
            _, rest = url.split("POST:", 1)
            endpoint, form_str = rest.split("|", 1)
            form_data = dict(kv.split("=", 1) for kv in form_str.split("&"))
            r = session.post(endpoint, data=form_data, timeout=HTTP_TIMEOUT)
        else:
            r = session.get(url, timeout=HTTP_TIMEOUT)

        if r.status_code != 200:
            return None

        # Decode with utf-8-sig to strip BOM; fall back to utf-16 if garbage
        try:
            content = r.content.decode("utf-8-sig", errors="replace").strip()
        except Exception:
            content = r.text.strip()

        # Must look like a CSV — "Job Number" header or commas + digits
        if ("Job Number" in content[:500]
                or (content.count(",") > 10 and any(c.isdigit() for c in content[:50]))):
            return content
        return None
    except Exception as e:
        logger.debug(f"  {url}: {e}")
        return None


def _try_download_xml(session, url):
    """
    Fetch Konica job_history.xml and convert it to the CSV format that
    konica_csv_importer expects.  Returns a CSV text string, or None on failure.

    Konica XML job history uses elements like <Job> (or <Record>) inside a
    <JobHistory> (or root) wrapper.  Child tag names vary by firmware — this
    function maps the common variants to the importer's CSV column names.
    """
    CSV_HEADERS = [
        "Job Number", "Job Type", "User Name", "File Name", "Result",
        "Number of Pages", "Number of Pages Printed",
        "Number of Monochrome Pages Printed", "Number of Color Pages Printed",
        "Number of Copies Printed", "Job Reception Date", "RIP Start Date",
        "RIP End Date", "Print Start Date", "Print End Date",
        "Paper Size", "Paper Type",
    ]
    # Normalised child tag → CSV column name
    TAG_MAP = {
        "jobnumber":                         "Job Number",
        "jobno":                             "Job Number",
        "number":                            "Job Number",
        "jobtype":                           "Job Type",
        "type":                              "Job Type",
        "username":                          "User Name",
        "userid":                            "User Name",
        "user":                              "User Name",
        "filename":                          "File Name",
        "documentname":                      "File Name",
        "docname":                           "File Name",
        "result":                            "Result",
        "status":                            "Result",
        "numberofpages":                     "Number of Pages",
        "numpages":                          "Number of Pages",
        "pages":                             "Number of Pages",
        "numberpagesprinted":                "Number of Pages Printed",
        "pagespainted":                      "Number of Pages Printed",
        "printedpages":                      "Number of Pages Printed",
        "numberofmonochromaticpagesprinted": "Number of Monochrome Pages Printed",
        "monopages":                         "Number of Monochrome Pages Printed",
        "bwpages":                           "Number of Monochrome Pages Printed",
        "numberofcolorpagesprinted":         "Number of Color Pages Printed",
        "colorpages":                        "Number of Color Pages Printed",
        "colourpages":                       "Number of Color Pages Printed",
        "numberofcopies":                    "Number of Copies Printed",
        "copies":                            "Number of Copies Printed",
        "jobreceptiondate":                  "Job Reception Date",
        "receptiondate":                     "Job Reception Date",
        "startdate":                         "Job Reception Date",
        "ripstartdate":                      "RIP Start Date",
        "ripenddate":                        "RIP End Date",
        "printstartdate":                    "Print Start Date",
        "printenddate":                      "Print End Date",
        "papersize":                         "Paper Size",
        "paper":                             "Paper Size",
        "papertype":                         "Paper Type",
    }

    try:
        r = session.get(url, timeout=HTTP_TIMEOUT)
        if r.status_code != 200:
            return None
        content = r.text.strip()
        if not content.startswith("<"):
            return None

        root = ET.fromstring(content)

        # Find job record elements — try common wrapper tag names
        job_elems = (
            list(root.iter("Job")) or
            list(root.iter("Record")) or
            list(root.iter("JobRecord")) or
            list(root.iter("JobInfo")) or
            list(root.iter("JobLog"))
        )
        if not job_elems:
            logger.debug("Konica job_history.xml: no job elements found")
            return None

        rows = []
        for job in job_elems:
            row = {h: "" for h in CSV_HEADERS}
            for child in job:
                key = child.tag.lower().replace("_", "").replace("-", "").replace(" ", "")
                mapped = TAG_MAP.get(key)
                if mapped and child.text:
                    row[mapped] = child.text.strip()
            if row.get("Job Number"):
                rows.append(row)

        if not rows:
            logger.debug("Konica job_history.xml: parsed 0 rows with a Job Number")
            return None

        out = io.StringIO()
        writer = csv_mod.DictWriter(out, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
        logger.info(f"Konica job_history.xml: parsed {len(rows)} jobs → CSV")
        return out.getvalue()

    except ET.ParseError as e:
        logger.debug(f"Konica job_history.xml parse error: {e}")
        return None
    except Exception as e:
        logger.debug(f"Konica job_history.xml: {e}")
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
            _save_cached_url(url)
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
        # CSV discovery failed — try confirmed XML endpoint
        logger.info(f"Trying XML job history endpoint: {KONICA_JOB_HISTORY_XML}")
        csv_text = _try_download_xml(session, KONICA_JOB_HISTORY_XML)
        if not csv_text:
            return None

    # Feed the in-memory CSV string to the importer (no temp file needed)
    try:
        csv_file = io.StringIO(csv_text)
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
