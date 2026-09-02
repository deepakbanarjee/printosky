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

import json
import os
import time
import sqlite3
import logging
import threading
import requests
from datetime import datetime, date
from dotenv import load_dotenv

from store_config import get_store_config
from ops_watchdog import report as _report_health

load_dotenv()

logger = logging.getLogger("supabase_sync")

# ── Config ────────────────────────────────────────────────────────────────────
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY         = os.environ.get("SUPABASE_KEY", "")          # anon key (project id)
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")  # service_role key (bypasses RLS)
STORE_ID     = get_store_config().store_id  # from store_config.json; falls back to "OSP"
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
def _has_service_columns(cursor) -> bool:
    """Does this store PC's jobs table carry the v38 service columns yet?"""
    have = {row[1] for row in cursor.execute("PRAGMA table_info(jobs)")}
    return "service_kind" in have and "service_meta" in have


#: The inter-store finishing columns (B-8) plus the drop-off marker. These were
#: written at the counter from 2026-09-01 and pushed nowhere: collect_jobs never
#: selected them, so a job sent to Nattika for binding was invisible in the
#: cloud, and the over-48h digest line had nothing to find even once it was
#: wired up. Synced from SCHEMA_v40.
_FINISHING_COLUMNS = ("finishing_store_id", "finishing_status", "finishing_sent_at",
                      "print_amount", "finishing_amount", "finishing_internal_amount",
                      "item_received_at", "dropoff_reminded_at")


def _present_columns(cursor, wanted) -> tuple:
    """Those of `wanted` this store PC actually has.

    Selected defensively because a PC that has not restarted since the migration
    does not have them, and a SELECT naming a missing column takes the whole
    sync down.
    """
    have = {row[1] for row in cursor.execute("PRAGMA table_info(jobs)")}
    return tuple(c for c in wanted if c in have)


def _as_json_object(raw, job_id=None):
    """Parse a stored service_meta string into an object for the jsonb column.

    A value that will not parse is dropped to NULL rather than pushed as a
    string, and says so — a silently mangled meta is a job nobody can explain.
    """
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.error("collect_jobs: service_meta for %s is not JSON (%s): %r",
                     job_id or "?", exc, raw)
        return None


def collect_jobs(db_path):
    """Pull all jobs from local SQLite."""
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # service_kind / service_meta joined this list on 2026-08-31, once
        # SCHEMA_v38 was applied to Supabase. Until then they were deliberately
        # absent: a store PC must never push a column the cloud has not got.
        # Selected defensively because a store PC that has not restarted since
        # B-2 does not have them locally either.
        service_cols = _has_service_columns(c)
        extra = ", service_kind, service_meta" if service_cols else ""
        finishing = _present_columns(c, _FINISHING_COLUMNS)
        if finishing:
            extra += ", " + ", ".join(finishing)
        c.execute(f"""
            SELECT job_id, received_at, filename, file_extension, file_size_kb,
                   source, sender, status, customer_name, service_type,
                   amount_quoted, amount_collected, payment_mode, completed_at,
                   filepath, printer, page_count, copies, colour, size, printed_by,
                   pickup_code, pickup_ready_at, delivered_at{extra}
            FROM jobs ORDER BY received_at DESC LIMIT 500
        """)
        rows = []
        for row in c.fetchall():
            d = dict(row)
            d["store_id"] = STORE_ID
            # jobs.service_meta is jsonb in Supabase and TEXT here. Pushing the
            # string would store a jsonb *string* — valid, and unreadable by
            # every query that expects an object.
            if "service_meta" in d:
                d["service_meta"] = _as_json_object(d["service_meta"], d.get("job_id"))
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
    """
    Pull epson job rows for Supabase upload.

    Sources:
      'weblog' — scraped from the Epson Web Config job log CSV (printer's own
                 view of what happened). Unique by integer job_number.
      'spec'   — written by print_server.py on successful Epson dispatch
                 (what we *told* the printer to print, including mono/colour
                 split, copies, paper size). Unique by Printosky-style
                 job_number 'OSP-YYYYMMDD-NNNN-itemN-YYYYMMDDHHMMSS'.

    'delta' rows are deliberately excluded — they have NULL job_number and
    would bypass the (store_id, job_number) UNIQUE conflict key, recreating
    the duplication bug fixed in SCHEMA_v19.
    """
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
            WHERE source IN ('weblog', 'spec')
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
                -- payment_mode is stored inconsistently (cash/Cash/CASH), so
                -- match case-insensitively or cash/upi read 0 while revenue > 0.
                COALESCE(SUM(CASE WHEN UPPER(payment_mode)='CASH' THEN amount_collected END), 0) cash,
                COALESCE(SUM(CASE WHEN UPPER(payment_mode)='UPI'  THEN amount_collected END), 0) upi
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

# ── Sync health / heartbeat ───────────────────────────────────────────────────
# A dead or never-started sync used to be invisible: it logged at INFO/DEBUG and
# returned. These globals + get_sync_status() make staleness queryable so a
# health check can alert, instead of the pipeline silently going dark for months.
_last_successful_sync = None   # datetime of the last fully-successful cycle
_last_sync_error = None        # str describing the most recent failure, or None


def _record_sync_success() -> None:
    global _last_successful_sync, _last_sync_error
    _last_successful_sync = datetime.now()
    _last_sync_error = None
    _report_health("sync.supabase", True, "sync cycle completed")


def _record_sync_failure(msg: str) -> None:
    global _last_sync_error
    _last_sync_error = msg
    # Everything the consoles show comes through this sync. When it stops, the
    # admin does not go blank — it goes STALE, which looks identical to a quiet
    # day. So a failed sync is an alert, not just a log line.
    _report_health("sync.supabase", False,
                   f"{msg} — the admin console is now showing stale data")


def get_sync_status() -> dict:
    """Snapshot of sync health for health checks / monitoring.

    healthy == configured AND a fully-successful sync within the last 2 intervals.
    A long-dead or never-started sync reads healthy=False instead of being
    silently invisible.
    """
    configured = bool(SUPABASE_URL and SUPABASE_KEY)
    last = _last_successful_sync
    age = (datetime.now() - last).total_seconds() if last else None
    healthy = bool(configured and age is not None and age < SYNC_INTERVAL * 2)
    return {
        "configured":   configured,
        "last_success": last.isoformat() if last else None,
        "age_seconds":  age,
        "healthy":      healthy,
        "last_error":   _last_sync_error,
    }


# ── Main sync cycle ───────────────────────────────────────────────────────────
def _report_presence():
    """Register this box in `store_devices` with the commit it is running.

    A store PC runs whatever it last pulled, so without this the only way to
    answer "is that box on the latest code?" is to stand in front of it. Runs
    on every sync cycle, from every box (not just the lease holder) — the
    point is a roll-call of machines, so a box that never wins a lease still
    has to appear.

    Best-effort: a failed presence write must not fail the data sync that
    follows it. device_lease.heartbeat logs its own warning, and a Supabase
    that is unreachable altogether is already alerted by the sync's own
    watchdog reporting.
    """
    try:
        from app_version import get_version
        from device_lease import heartbeat
        heartbeat(app_version=get_version())
    except Exception as exc:
        logger.warning("presence/version report failed (sync continues): %s", exc)


def sync_once(db_path):
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.debug("Supabase not configured — skipping sync")
        return

    _report_presence()

    # Konica attribution retired 2026-05-12 (0/4507 attribution rate).
    # See retired/2026-05-12-graveyard/konica_attribution.py for the dropped
    # call sequence and the four root causes documented there.

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
    ok_epson      = upsert("epson_jobs",       epson_jobs,  on_conflict="store_id,job_number")          if epson_jobs  else True
    ok_sessions   = upsert("staff_sessions",   staff_sess,  on_conflict="id")                           if staff_sess  else True

    if ok_jobs and ok_printers and ok_summary and ok_supplies and ok_changes and ok_konica and ok_epson and ok_sessions:
        _record_sync_success()
        logger.info(f"Supabase sync OK — {len(jobs)} jobs, {len(printers)} printers, "
                    f"{len(supplies)} supplies, {len(sup_changes)} supply_changes, "
                    f"{len(konica_jobs)} konica_jobs, {len(epson_jobs)} epson_jobs, "
                    f"{len(staff_sess)} staff_sessions")
    else:
        _record_sync_failure("one or more table upserts failed")
        logger.error("Supabase sync had errors — admin data is now STALE until the "
                     "next clean cycle; see warnings above")

def start_sync(db_path, interval=SYNC_INTERVAL):
    """Start Supabase sync in a background daemon thread."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Supabase NOT configured (SUPABASE_URL/KEY empty) — admin sync is "
                     "DISABLED. The admin dashboard will show stale data until "
                     "SUPABASE_URL and SUPABASE_KEY are set in the environment.")
        _report_health("sync.supabase", False,
                       "SUPABASE_URL/KEY not set on this PC — nothing this store does "
                       "will reach the admin console")
        return None

    def loop():
        logger.info(f"Supabase sync started — pushing every {interval}s to {SUPABASE_URL}")
        while True:
            try:
                sync_once(db_path)
            except Exception as e:
                _record_sync_failure(str(e))
                logger.error(f"Supabase sync error: {e}")
            time.sleep(interval)

    t = threading.Thread(target=loop, daemon=True, name="SupabaseSync")
    t.start()
    logger.info("Supabase sync thread launched")
    return t
