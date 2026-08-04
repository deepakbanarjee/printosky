"""
install/bootstrap_db.py — create a fresh local jobs.db schema for a new store PC.

Run via:
    python install/bootstrap_db.py
or
    python install/bootstrap_db.py "C:\\Printosky\\Data\\jobs.db"   # explicit path

Strategy: call each module's IF-NOT-EXISTS table-creation function in
dependency order. Idempotent — safe to re-run against an existing DB.

If a module is missing a public init function we just import it (cheap
side-effect of running its top-level CREATE-TABLE block on first use is
not relied upon; we explicitly call init functions where they exist).
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

# Make the repo root importable when this script lives in install/
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _resolve_db_path() -> str:
    """Pick the DB path: CLI arg → store_config → legacy default."""
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return os.path.abspath(sys.argv[1])
    try:
        from store_config import get_store_config  # type: ignore
        return get_store_config().db_path
    except Exception:
        if sys.platform == "win32":
            return r"C:\Printosky\Data\jobs.db"
        return str(Path.home() / "Printosky" / "Data" / "jobs.db")


def _ensure_parent(db_path: str) -> None:
    parent = os.path.dirname(db_path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
        print(f"  created folder: {parent}")


def _run_init(name: str, fn, *args) -> None:
    try:
        fn(*args)
        print(f"  OK  {name}")
    except Exception as e:
        print(f"  ERR {name}: {e}")


def bootstrap(db_path: str) -> int:
    print(f"Bootstrapping local SQLite at: {db_path}")
    _ensure_parent(db_path)

    # 1. Core jobs table + daily_summary view.
    #    Inlined (mirrors watcher_.setup_database) so we don't depend on the
    #    watchdog package being installed yet — bootstrap_db.py must work
    #    BEFORE `pip install -r requirements.txt` completes.
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT UNIQUE NOT NULL,
                received_at     TEXT NOT NULL,
                filename        TEXT NOT NULL,
                file_extension  TEXT,
                file_size_kb    REAL,
                file_hash       TEXT,
                source          TEXT DEFAULT 'Hot Folder',
                sender          TEXT,
                status          TEXT DEFAULT 'Received',
                customer_name   TEXT,
                service_type    TEXT,
                pages_expected  INTEGER,
                pages_printed   INTEGER,
                amount_quoted   REAL,
                amount_collected REAL,
                payment_mode    TEXT,
                page_count      INTEGER DEFAULT 0,
                filepath        TEXT,
                staff_notes     TEXT,
                completed_at    TEXT,
                synced_to_sheets INTEGER DEFAULT 0
            );
            CREATE VIEW IF NOT EXISTS daily_summary AS
            SELECT
                DATE(received_at) as date,
                COUNT(*) as total_jobs,
                COUNT(CASE WHEN status = 'Completed' THEN 1 END) as completed,
                COUNT(CASE WHEN status = 'Received' THEN 1 END) as pending,
                COUNT(CASE WHEN status = 'Printed' THEN 1 END) as printed_not_collected,
                SUM(CASE WHEN amount_collected IS NOT NULL THEN amount_collected ELSE 0 END) as revenue,
                COUNT(CASE WHEN payment_mode = 'Cash' THEN 1 END) as cash_count,
                COUNT(CASE WHEN payment_mode = 'UPI' THEN 1 END) as upi_count
            FROM jobs
            GROUP BY DATE(received_at);
        """)
        conn.commit()
        conn.close()
        print("  OK  jobs table + daily_summary view (inlined)")
    except Exception as e:
        print(f"  ERR jobs / daily_summary: {e}")

    # 2. Print items + staff + sessions (via print_server init paths).
    #    Explicit CREATE statements mirror print_server.py for safety, so
    #    we never depend on import-time side effects.
    try:
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS staff (
                id              TEXT PRIMARY KEY,
                name            TEXT NOT NULL,
                pin_hash        TEXT NOT NULL,
                pin_salt        TEXT,
                active          INTEGER DEFAULT 1,
                created_at      TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS staff_sessions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id        TEXT NOT NULL,
                pc_id           TEXT,
                login_at        TEXT NOT NULL,
                logout_at       TEXT,
                idle_logout     INTEGER DEFAULT 0,
                FOREIGN KEY (staff_id) REFERENCES staff(id)
            );
            CREATE TABLE IF NOT EXISTS print_items (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id          TEXT NOT NULL,
                item_number     INTEGER NOT NULL,
                page_list       TEXT DEFAULT 'all',
                colour          TEXT DEFAULT 'bw',
                paper_type      TEXT,
                sides           TEXT DEFAULT 'ss',
                layout          TEXT DEFAULT '1-up',
                copies          INTEGER DEFAULT 1,
                paper_gsm       INTEGER DEFAULT 70,
                printer         TEXT,
                status          TEXT DEFAULT 'Pending',
                printed_at      TEXT,
                printed_by      TEXT,
                UNIQUE(job_id, item_number)
            );
        """)
        conn.commit()
        conn.close()
        print("  OK  print_server tables (staff, staff_sessions, print_items)")
    except Exception as e:
        print(f"  ERR print_server tables: {e}")

    # 3. Epson per-job log (weblog scrape + source='spec' rows + delta rows)
    try:
        import epson_jobs_fetcher
        conn = sqlite3.connect(db_path)
        _run_init("epson_jobs_fetcher.init_epson_jobs_table",
                  epson_jobs_fetcher.init_epson_jobs_table, conn)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ERR epson_jobs init: {e}")

    # 4. Konica per-job log
    try:
        import konica_jobs_fetcher
        conn = sqlite3.connect(db_path)
        _run_init("konica_jobs_fetcher._init_table",
                  konica_jobs_fetcher._init_table, conn)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ERR konica_jobs init: {e}")

    # 5. Printer counters (SNMP) + supplies + supply change events
    try:
        import printer_poller
        conn = sqlite3.connect(db_path)
        _run_init("printer_poller.init_printer_tables",
                  printer_poller.init_printer_tables, conn)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ERR printer_poller init: {e}")

    # 6. Work sessions (per-job time tracking)
    try:
        import work_session_tracker
        conn = sqlite3.connect(db_path)
        _run_init("work_session_tracker.setup_work_sessions_db",
                  work_session_tracker.setup_work_sessions_db, conn)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"  ERR work_session_tracker init: {e}")

    # Verify what landed
    print()
    print("--- tables after bootstrap ---")
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') ORDER BY name"
    ).fetchall()
    conn.close()
    for (name,) in rows:
        print(f"  {name}")
    print()
    print(f"Bootstrap complete. {len(rows)} tables/views in {db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(bootstrap(_resolve_db_path()))
