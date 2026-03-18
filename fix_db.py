"""
PRINTOSKY DB MIGRATION SCRIPT
==============================
Run this once to apply schema updates.
Safe to run multiple times (uses IF NOT EXISTS / IF NOT EXISTS column checks).

Usage:
    python fix_db.py
"""

import sqlite3
import os
import sys

DB_PATH = r"C:\Printosky\Data\jobs.db"


def run_migrations(db_path: str):
    conn = sqlite3.connect(db_path)
    cur  = conn.cursor()

    print(f"Connected to: {db_path}")

    # ── Init base tables via watcher.py (creates jobs, batches, profiles, staff) ─
    print("\n[Init] Initialising base tables via watcher.py")
    try:
        import sys
        sys.path.insert(0, os.path.dirname(__file__))
        import watcher as _w
        _w.setup_database()
        print("  . Base tables initialised")
    except Exception as e:
        print(f"  ! Could not auto-init via watcher.py: {e}")
        print("  . Continuing — tables may already exist")

    # ── Helper: add column only if it doesn't exist ──────────────────────────
    def add_column(table, col, definition):
        cur.execute(f"PRAGMA table_info({table})")
        existing = [row[1] for row in cur.fetchall()]
        if col not in existing:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            print(f"  + Added column: {table}.{col}")
        else:
            print(f"  . Exists:       {table}.{col}")

    # ─────────────────────────────────────────────────────────────────────────
    # MIGRATION 1 — jobs table new columns (Sprint 0)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Migration 1] jobs table — new spec columns")
    job_cols = [
        ("pages_from",  "INTEGER"),
        ("pages_to",    "INTEGER"),
        ("layout",      "TEXT DEFAULT '1-up'"),
        ("paper_size",  "TEXT DEFAULT 'A4'"),
        ("paper_type",  "TEXT DEFAULT 'A4_BW'"),
        ("paper_gsm",   "INTEGER DEFAULT 70"),
        ("is_student",  "INTEGER DEFAULT 0"),
        ("is_mixed",    "INTEGER DEFAULT 0"),
        ("urgent",      "INTEGER DEFAULT 0"),
        ("sides",       "TEXT DEFAULT 'ss'"),
        ("amount_quoted","REAL DEFAULT 0"),
    ]
    for col, defn in job_cols:
        add_column("jobs", col, defn)

    # ─────────────────────────────────────────────────────────────────────────
    # MIGRATION 2 — print_items table (Sprint 1 — mixed print jobs)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Migration 2] print_items table")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS print_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id       TEXT NOT NULL,
            item_number  INTEGER NOT NULL DEFAULT 1,
            page_list    TEXT DEFAULT 'all',
            paper_type   TEXT DEFAULT 'A4_BW',
            colour       TEXT DEFAULT 'bw',
            sides        TEXT DEFAULT 'ss',
            layout       TEXT DEFAULT '1-up',
            copies       INTEGER DEFAULT 1,
            paper_gsm    INTEGER DEFAULT 70,
            printer      TEXT DEFAULT 'konica',
            status       TEXT DEFAULT 'Pending',
            printed_at   TEXT,
            printed_by   TEXT
        )
    """)
    print("  . print_items table ready")

    # ─────────────────────────────────────────────────────────────────────────
    # MIGRATION 3 — vendors table (Sprint 3 — outsourced finishing)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Migration 3] vendors table")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            phone           TEXT,
            finishing_types TEXT,
            is_default_for  TEXT,
            notes           TEXT,
            active          INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    print("  . vendors table ready")

    # ─────────────────────────────────────────────────────────────────────────
    # MIGRATION 4 — job_vendor_steps table (Sprint 3)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Migration 4] job_vendor_steps table")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS job_vendor_steps (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id               TEXT NOT NULL,
            step_number          INTEGER NOT NULL DEFAULT 1,
            vendor_type          TEXT NOT NULL,
            vendor_id            INTEGER,
            vendor_name          TEXT,
            sent_date            TEXT,
            expected_return_date TEXT,
            actual_return_date   TEXT,
            cost_to_vendor       REAL DEFAULT 0,
            status               TEXT DEFAULT 'Pending',
            notes                TEXT
        )
    """)
    print("  . job_vendor_steps table ready")

    # ─────────────────────────────────────────────────────────────────────────
    # MIGRATION 5 — existing tables compatibility fixes
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Migration 5] Existing table compatibility")

    # printed_by on jobs (was added by schema_v3 — may already exist)
    add_column("jobs", "printed_by", "TEXT")

    # notes column on jobs (catch-all log)
    add_column("jobs", "notes", "TEXT")

    # vendor tracking on jobs (for single-step outsource)
    vendor_job_cols = [
        ("vendor_name",        "TEXT"),
        ("vendor_sent_date",   "TEXT"),
        ("vendor_return_date", "TEXT"),
        ("vendor_cost",        "REAL"),
    ]
    for col, defn in vendor_job_cols:
        add_column("jobs", col, defn)

    # ─────────────────────────────────────────────────────────────────────────
    # MIGRATION 6 — Seed default vendors
    # ─────────────────────────────────────────────────────────────────────────
    print("\n[Migration 6] Seed default vendors (skip if already seeded)")
    cur.execute("SELECT COUNT(*) FROM vendors")
    if cur.fetchone()[0] == 0:
        default_vendors = [
            ("Vendor 1 - Binding",  None, '["Project Binding","Record Binding","Soft Binding"]', "Project Binding"),
            ("Vendor 2 - Lam",      None, '["Roll Lamination","Cover Lamination"]',              "Roll Lamination"),
            ("Vendor 3 - Cover",    None, '["Cover Printing"]',                                  "Cover Printing"),
        ]
        cur.executemany(
            "INSERT INTO vendors (name, phone, finishing_types, is_default_for) VALUES (?,?,?,?)",
            default_vendors
        )
        print("  + Seeded 3 placeholder vendors (update names/phones in DB or admin panel)")
    else:
        print("  . Vendors already seeded — skipped")

    conn.commit()
    conn.close()
    print("\nAll migrations complete.")


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found at {DB_PATH}")
        sys.exit(1)
    run_migrations(DB_PATH)
