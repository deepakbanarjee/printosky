"""
KONICA JOB LOG CSV IMPORTER
============================
Imports job history CSV exported from Konica Bizhub admin panel
into the local SQLite jobs DB (konica_jobs table).

CSV format (exported from Bizhub C1100 / Pro 1100):
  Job Number, Job Type, User Name, File Name, Result,
  Number of Pages, Number of Pages Printed,
  Number of Monochrome Pages Printed, Number of Color Pages Printed,
  Number of Copies Printed, Job Reception Date, RIP Start Date, RIP End Date,
  Print Start Date, Print End Date, Paper Size, Paper Type

Date format in CSV: "16/Mar/2026 9:46:14 AM"

Usage:
  python konica_csv_importer.py                    # import all CSVs from data/imports/
  python konica_csv_importer.py path/to/file.csv   # import a specific file

Auto-watch: dropping a CSV into data/imports/ and running this script
  will import it and move it to data/imports/done/
"""

import os
import sys
import csv
import sqlite3
import shutil
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [konica_importer] %(message)s"
)
logger = logging.getLogger("konica_importer")

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(os.path.expanduser("~"), "Printosky", "Data", "jobs.db")
IMPORT_DIR  = os.path.join(BASE_DIR, "data", "imports")
DONE_DIR    = os.path.join(IMPORT_DIR, "done")


# ── DB setup ──────────────────────────────────────────────────────────────────

def init_konica_jobs_table(conn):
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
            job_date       TEXT,       -- ISO datetime (job reception)
            print_end_date TEXT,       -- ISO datetime (print finished)
            paper_size     TEXT,
            paper_type     TEXT,
            imported_at    TEXT
        )
    """)
    conn.commit()


# ── Date parser ───────────────────────────────────────────────────────────────

def parse_konica_date(s):
    """
    Parse Konica date string like "16/Mar/2026 9:46:14 AM" → ISO datetime string.
    Returns None if blank or unparseable.
    """
    if not s or not s.strip():
        return None
    s = s.strip()
    for fmt in ("%d/%b/%Y %I:%M:%S %p", "%d/%b/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).isoformat(sep=" ", timespec="seconds")
        except ValueError:
            continue
    logger.debug(f"Could not parse date: {s!r}")
    return None


def safe_int(s):
    """Convert string to int, return None if blank/invalid."""
    if not s or not s.strip():
        return None
    try:
        return int(s.strip())
    except ValueError:
        return None


# ── CSV importer ─────────────────────────────────────────────────────────────

def import_csv(csv_path, db_path=DB_PATH):
    """
    Read a Konica job log CSV and upsert rows into konica_jobs table.
    Returns (inserted, skipped, errors) counts.
    """
    logger.info(f"Importing {csv_path}")

    conn = sqlite3.connect(db_path)
    init_konica_jobs_table(conn)

    inserted = skipped = errors = 0
    now_iso  = datetime.now().isoformat(sep=" ", timespec="seconds")

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)

    logger.info(f"  {len(rows)} rows in CSV")

    for row in rows:
        try:
            job_num = safe_int(row.get("Job Number", ""))
            if job_num is None:
                skipped += 1
                continue

            job_date      = parse_konica_date(row.get("Job Reception Date", ""))
            print_end     = parse_konica_date(row.get("Print End Date", ""))
            job_type      = (row.get("Job Type", "") or "").strip()
            user_name     = (row.get("User Name", "") or "").strip() or None
            file_name     = (row.get("File Name", "") or "").strip() or None
            result        = (row.get("Result", "") or "").strip()
            num_pages     = safe_int(row.get("Number of Pages", ""))
            pages_printed = safe_int(row.get("Number of Pages Printed", ""))
            mono_pages    = safe_int(row.get("Number of Monochrome Pages Printed", ""))
            color_pages   = safe_int(row.get("Number of Color Pages Printed", ""))
            copies        = safe_int(row.get("Number of Copies Printed", ""))
            paper_size    = (row.get("Paper Size", "") or "").strip() or None
            paper_type    = (row.get("Paper Type", "") or "").strip() or None

            conn.execute("""
                INSERT OR IGNORE INTO konica_jobs
                    (job_number, job_type, user_name, file_name, result,
                     num_pages, pages_printed, mono_pages, color_pages,
                     copies, job_date, print_end_date, paper_size, paper_type,
                     imported_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (job_num, job_type, user_name, file_name, result,
                  num_pages, pages_printed, mono_pages, color_pages,
                  copies, job_date, print_end, paper_size, paper_type,
                  now_iso))

            if conn.execute("SELECT changes()").fetchone()[0]:
                inserted += 1
            else:
                skipped += 1   # already exists (UNIQUE job_number)

        except Exception as e:
            logger.warning(f"  Row error (job {row.get('Job Number','?')}): {e}")
            errors += 1

    conn.commit()
    conn.close()
    logger.info(f"  Done — inserted={inserted}, skipped(dup)={skipped}, errors={errors}")
    return inserted, skipped, errors


# ── Watch-folder mode ─────────────────────────────────────────────────────────

def import_from_folder(import_dir=IMPORT_DIR, done_dir=DONE_DIR, db_path=DB_PATH):
    """
    Import all CSV files found in import_dir.
    Successfully imported files are moved to done_dir.
    """
    os.makedirs(import_dir, exist_ok=True)
    os.makedirs(done_dir,   exist_ok=True)

    csvs = [f for f in os.listdir(import_dir)
            if f.lower().endswith(".csv") and os.path.isfile(os.path.join(import_dir, f))]

    if not csvs:
        logger.info(f"No CSV files found in {import_dir}")
        return

    total_in = total_sk = total_err = 0
    for fname in csvs:
        fpath = os.path.join(import_dir, fname)
        try:
            ins, sk, err = import_csv(fpath, db_path)
            total_in += ins; total_sk += sk; total_err += err
            shutil.move(fpath, os.path.join(done_dir, fname))
            logger.info(f"  Moved {fname} → done/")
        except Exception as e:
            logger.error(f"Failed to import {fname}: {e}")

    logger.info(f"Folder import complete — total inserted={total_in}, "
                f"skipped={total_sk}, errors={total_err}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Specific file(s) passed as arguments
        for path in sys.argv[1:]:
            if not os.path.isfile(path):
                print(f"File not found: {path}")
                continue
            ins, sk, err = import_csv(path)
            print(f"Imported {path}: +{ins} new rows, {sk} duplicates, {err} errors")
    else:
        # Watch-folder mode
        print(f"Looking for CSVs in: {IMPORT_DIR}")
        print(f"DB: {DB_PATH}")
        import_from_folder()
