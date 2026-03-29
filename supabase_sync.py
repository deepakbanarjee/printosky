"""
PRINTOSKY SUPABASE SYNC
========================
Pushes job data and printer counters to Supabase every SYNC_INTERVAL seconds.
The admin page at printosky.com/admin reads from Supabase.

Setup:
1. Create free project at supabase.com
2. Go to Settings → API → copy Project URL and anon key
3. Add SUPABASE_URL and SUPABASE_KEY to .env (see .env.example)
4. Run the SQL in SCHEMA.sql to create tables (once only)

Runs as a background thread started by watcher.py.
"""

import os
import time
import sqlite3
import logging
import threading
import requests
from datetime import datetime, date
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("supabase_sync")

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY         = os.environ.get("SUPABASE_KEY", "")          # anon key (project id)
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")  # service_role key (bypasses RLS)
STORE_ID     = "OSP"       # Oxygen Students Paradise — change per store
SYNC_INTERVAL = 300        # seconds (5 minutes)

# ── Supabase REST API headers ─────────────────────────────────────────────────
def _headers():
    # Use service_role key if available — bypasses RLS for server-side writes
    auth_key = SUPABASE_SERVICE_KEY or SUPABASE_KEY
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {auth_key}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=merge-duplicates",   # upsert
    }

def _url(table):
    return f"{SUPABASE_URL}/rest/v1/{table}"

# ── Upsert helpers ────────────────────────────────────────────────────────────
def upsert(table, rows, on_conflict=None):
    """Upsert a list of dicts to a Supabase table."""
    if not rows or not SUPABASE_URL or not SUPABASE_KEY:
        return False
    try:
        url = _url(table)
        if on_conflict:
            url += f"?on_conflict={on_conflict}"
        r = requests.post(
            url,
            json=rows,
            headers=_headers(),
            timeout=10,
        )
        if r.status_code in (200, 201):
            return True
        logger.warning(f"Supabase upsert {table}: {r.status_code} {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Supabase upsert {table}: {e}")
        return False

# ── Data collectors ───────────────────────────────────────────────────────────
def collect_jobs(db_path):
    """Pull all jobs from local SQLite."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT job_id, received_at, filename, file_extension, file_size_kb,
                   source, sender, status, customer_name, service_type,
                   amount_quoted, amount_collected, payment_mode, completed_at,
                   filepath, printer, page_count, copies, colour, size, printed_by
            FROM jobs ORDER BY received_at DESC LIMIT 500
        """)
        rows = []
        for row in c.fetchall():
            d = dict(row)
            d["store_id"] = STORE_ID
            rows.append(d)
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"collect_jobs: {e}")
        return []

def collect_printer_counters(db_path):
    """Pull latest printer counter for each printer."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        rows = []
        for printer in ("konica", "epson"):
            c.execute("""
                SELECT polled_at, printer, method,
                       total_pages, print_bw, copy_bw, print_colour, copy_colour
                FROM printer_counters
                WHERE printer=?
                ORDER BY polled_at DESC LIMIT 1
            """, (printer,))
            row = c.fetchone()
            if row:
                d = dict(row)
                d["store_id"] = STORE_ID
                rows.append(d)
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"collect_printer_counters: {e}")
        return []

def collect_printer_supplies(db_path):
    """Pull latest supply reading for each printer+supply combination."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        rows = []
        for printer in ("konica", "epson"):
            c.execute("""
                SELECT ps.polled_at, ps.printer, ps.supply_index,
                       ps.description, ps.max_capacity, ps.current_level, ps.pct
                FROM printer_supplies ps
                INNER JOIN (
                    SELECT supply_index, MAX(polled_at) AS latest
                    FROM printer_supplies WHERE printer=?
                    GROUP BY supply_index
                ) latest ON ps.supply_index=latest.supply_index AND ps.polled_at=latest.latest
                WHERE ps.printer=?
            """, (printer, printer))
            for row in c.fetchall():
                d = dict(row)
                d["store_id"] = STORE_ID
                rows.append(d)
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"collect_printer_supplies: {e}")
        return []

def collect_supply_changes(db_path):
    """Pull supply change events not yet synced (last 200)."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT id, changed_at, printer, supply_index, description,
                   level_before, level_after, pct_before, pct_after
            FROM supply_changes
            ORDER BY changed_at DESC LIMIT 200
        """)
        rows = []
        for row in c.fetchall():
            d = dict(row)
            d["store_id"] = STORE_ID
            rows.append(d)
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"collect_supply_changes: {e}")
        return []


def collect_konica_jobs(db_path):
    """Pull konica job log rows (last 2000), including attributed_to."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT job_number, job_type, user_name, file_name, result,
                   num_pages, pages_printed, mono_pages, color_pages,
                   copies, job_date, print_end_date, paper_size, paper_type,
                   attributed_to
            FROM konica_jobs
            ORDER BY job_date DESC LIMIT 2000
        """)
        rows = []
        for row in c.fetchall():
            d = dict(row)
            d["store_id"] = STORE_ID
            rows.append(d)
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"collect_konica_jobs: {e}")
        return []


def collect_epson_jobs(db_path):
    """Pull epson job log rows (last 2000)."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT source, job_number, job_type, user_name, file_name, result,
                   pages_printed, mono_pages, color_pages, copies, paper_size,
                   job_date, print_end_date,
                   snmp_total_before, snmp_total_after, delta_pages,
                   attributed_job_id, imported_at
            FROM epson_jobs
            ORDER BY job_date DESC LIMIT 2000
        """)
        rows = []
        for row in c.fetchall():
            d = dict(row)
            d["store_id"] = STORE_ID
            rows.append(d)
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"collect_epson_jobs: {e}")
        return []


def collect_staff_sessions(db_path):
    """Pull recent staff sessions for Supabase sync."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("""
            SELECT id, staff_id, pc_id, login_at, logout_at, idle_logout
            FROM staff_sessions
            ORDER BY login_at DESC LIMIT 500
        """)
        rows = []
        for row in c.fetchall():
            d = dict(row)
            d["store_id"] = STORE_ID
            rows.append(d)
        conn.close()
        return rows
    except Exception as e:
        logger.warning(f"collect_staff_sessions: {e}")
        return []


def collect_daily_summary(db_path):
    """Push today's summary stats."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        today = date.today().isoformat()
        c.execute("""
            SELECT
                COUNT(*) total_jobs,
                COUNT(CASE WHEN status='Completed' THEN 1 END) completed,
                COUNT(CASE WHEN status IN ('Received','In Progress','Printed') THEN 1 END) pending,
                COALESCE(SUM(amount_collected), 0) revenue,
                COALESCE(SUM(CASE WHEN payment_mode='Cash' THEN amount_collected END), 0) cash,
                COALESCE(SUM(CASE WHEN payment_mode='UPI'  THEN amount_collected END), 0) upi
            FROM jobs WHERE DATE(received_at)=?
        """, (today,))
        row = dict(c.fetchone() or {})
        conn.close()
        row["store_id"] = STORE_ID
        row["date"]     = today
        row["synced_at"] = datetime.now().isoformat(sep=" ", timespec="seconds")
        return [row]
    except Exception as e:
        logger.warning(f"collect_daily_summary: {e}")
        return []

# ── Main sync cycle ───────────────────────────────────────────────────────────
def sync_once(db_path):
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.debug("Supabase not configured — skipping sync")
        return

    # Attribute konica jobs to staff before syncing
    try:
        from print_server import attribute_konica_jobs, KONICA_USER_PC_MAP
        attribute_konica_jobs(db_path)
    except Exception as e:
        logger.debug(f"attribute_konica_jobs skipped: {e}")

    jobs          = collect_jobs(db_path)
    printers      = collect_printer_counters(db_path)
    summary       = collect_daily_summary(db_path)
    supplies      = collect_printer_supplies(db_path)
    sup_changes   = collect_supply_changes(db_path)
    konica_jobs   = collect_konica_jobs(db_path)
    epson_jobs    = collect_epson_jobs(db_path)
    staff_sess    = collect_staff_sessions(db_path)

    ok_jobs       = upsert("jobs",             jobs,        on_conflict="job_id")                       if jobs        else True
    ok_printers   = upsert("printer_counters", printers,    on_conflict="store_id,printer,polled_at")   if printers    else True
    ok_summary    = upsert("daily_summary",    summary)                                                  if summary     else True
    ok_supplies   = upsert("printer_supplies", supplies)                                                 if supplies    else True
    ok_changes    = upsert("supply_changes",   sup_changes, on_conflict="store_id,id")                  if sup_changes else True
    ok_konica     = upsert("konica_jobs",      konica_jobs, on_conflict="store_id,job_number")          if konica_jobs else True
    ok_epson      = upsert("epson_jobs",       epson_jobs,  on_conflict="store_id,id")                  if epson_jobs  else True
    ok_sessions   = upsert("staff_sessions",   staff_sess,  on_conflict="id")                           if staff_sess  else True

    if ok_jobs and ok_printers and ok_summary and ok_supplies and ok_changes and ok_konica and ok_epson and ok_sessions:
        logger.info(f"Supabase sync OK — {len(jobs)} jobs, {len(printers)} printers, "
                    f"{len(supplies)} supplies, {len(sup_changes)} supply_changes, "
                    f"{len(konica_jobs)} konica_jobs, {len(epson_jobs)} epson_jobs, "
                    f"{len(staff_sess)} staff_sessions")
    else:
        logger.warning("Supabase sync had errors — check warnings above")

def start_sync(db_path, interval=SYNC_INTERVAL):
    """Start Supabase sync in a background daemon thread."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.info("Supabase not configured (SUPABASE_URL/KEY empty) — admin sync disabled")
        logger.info("To enable: set SUPABASE_URL and SUPABASE_KEY in supabase_sync.py")
        return None

    def loop():
        logger.info(f"Supabase sync started — pushing every {interval}s to {SUPABASE_URL}")
        while True:
            try:
                sync_once(db_path)
            except Exception as e:
                logger.error(f"Supabase sync error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True, name="SupabaseSync")
    t.start()
    logger.info("Supabase sync thread launched")
    return t
