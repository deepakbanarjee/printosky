"""
print_server.py — Printosky Print Server
Runs on store PC at port 3005.
Receives print commands from admin dashboard → prints via SumatraPDF → updates job status.

Endpoints:
  POST /print          — { job_id, filepath, printer, copies, colour_mode, staff_id }
  POST /staff-login    — { pin, pc_id } → { ok, staff_id, name, session_id }
  POST /staff-logout   — { session_id, idle }
  GET  /active-staff   — ?pc_id=PC1 → { staff_id, name, session_id }
  GET  /status         — health check
  GET  /printers       — list configured printers
  GET  /health         — full system health
  POST /create-job     — { customer_name, phone, source, colour, sides, copies, pages, paper_size, finishing, amount_collected|amount_partial, payment_mode, override_reason }
  POST /upload-file    — { filename, file_data (base64) } → saves to hot folder
"""

import hashlib
import hmac
import json
import logging
import math
import os
import socket
import sqlite3
import subprocess
import sys
import threading
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from dotenv import load_dotenv

load_dotenv()

# Rate card engine (same directory)
sys.path.insert(0, os.path.dirname(__file__))
try:
    import rate_card as _rc
except ImportError:
    _rc = None
    logging.warning("rate_card.py not found — /quote will return 0")

# Job tracker — status machine + audit log
try:
    from job_tracker import log_event as _jt_log, transition as _jt_transition, get_events as _jt_events, setup_job_events_db as _jt_setup
    JOB_TRACKER_AVAILABLE = True
except ImportError:
    JOB_TRACKER_AVAILABLE = False
    def _jt_log(*a, **kw): return 0
    def _jt_transition(*a, **kw): return {"ok": False, "error": "job_tracker not available"}
    def _jt_events(*a, **kw): return []
    def _jt_setup(*a): pass

# Colour detector — PyMuPDF-based colour page detection
try:
    from colour_detector import (
        build_colour_map as _cd_build,
        save_colour_map as _cd_save,
        confirm_colour_map as _cd_confirm,
    )
    COLOUR_DETECTOR_AVAILABLE = True
except ImportError:
    COLOUR_DETECTOR_AVAILABLE = False
    def _cd_build(*a, **kw): return {"error": "colour_detector not available"}
    def _cd_save(*a, **kw): pass
    def _cd_confirm(*a, **kw): pass

# Review manager — post-collection review requests + discount codes
try:
    from review_manager import (
        schedule_review as _rv_schedule,
        setup_review_db as _rv_setup,
        record_rating as _rv_record,
    )
    REVIEW_MANAGER_AVAILABLE = True
except ImportError:
    REVIEW_MANAGER_AVAILABLE = False
    def _rv_schedule(*a, **kw): pass
    def _rv_setup(*a): pass
    def _rv_record(*a, **kw): return {"ok": False, "error": "review_manager not available"}

# Work session tracker — DTP / editing timer
try:
    from work_session_tracker import (
        start_session as _ws_start,
        pause_session as _ws_pause,
        resume_session as _ws_resume,
        end_session as _ws_end,
        get_sessions as _ws_get,
        get_open_session as _ws_open,
        setup_work_sessions_db as _ws_setup,
    )
    WORK_SESSION_AVAILABLE = True
except ImportError:
    WORK_SESSION_AVAILABLE = False
    def _ws_start(*a, **kw): return {"ok": False, "error": "work_session_tracker not available"}
    def _ws_pause(*a, **kw): return {"ok": False, "error": "work_session_tracker not available"}
    def _ws_resume(*a, **kw): return {"ok": False, "error": "work_session_tracker not available"}
    def _ws_end(*a, **kw): return {"ok": False, "error": "work_session_tracker not available"}
    def _ws_get(*a, **kw): return []
    def _ws_open(*a, **kw): return None
    def _ws_setup(*a): pass

# ── Konica Windows username → PC identifier mapping ───────────────────────────
# PC1 = Priya/Deepak/Anu  |  PC2 = Revana  |  PC3 = rarely used (Nirmal)
KONICA_USER_PC_MAP = {
    # Current Windows usernames (as they appear in Konica job log)
    "ABC":        "PC1",   # Priya / Deepak / Anu
    "OXYGEN":     "PC2",   # Revana
    "NIRMAL":     "PC3",   # rarely used
    # Future — after Windows computer names are renamed
    "OXYGEN PC1": "PC1",
    "OXYGEN PC2": "PC2",
    "OXYGEN PC3": "PC3",
}

# ── Staff session helpers ──────────────────────────────────────────────────────
_active_sessions = {}   # pc_id → {staff_id, name, session_id}  (in-memory cache)

# ── Supabase JWT cache (for returning to admin.html on staff login) ────────────
_supabase_jwt_cache = {"token": None, "expires_at": 0}

def _get_supabase_jwt() -> str:
    """Return a cached Supabase JWT, refreshing if expired or missing."""
    import time, json as _json
    now = time.time()
    if _supabase_jwt_cache["token"] and now < _supabase_jwt_cache["expires_at"] - 60:
        return _supabase_jwt_cache["token"]
    url  = os.environ.get("SUPABASE_URL", "")
    email = os.environ.get("SUPABASE_AUTH_EMAIL", "")
    pwd   = os.environ.get("SUPABASE_AUTH_PASSWORD", "")
    if not (url and email and pwd):
        return ""
    try:
        import urllib.request as _ur, urllib.error
        body = _json.dumps({"email": email, "password": pwd}).encode()
        req  = _ur.Request(f"{url}/auth/v1/token?grant_type=password", data=body,
                           headers={"apikey": os.environ.get("SUPABASE_KEY", ""),
                                    "Content-Type": "application/json"})
        with _ur.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
        token = data.get("access_token", "")
        expires_in = data.get("expires_in", 3600)
        _supabase_jwt_cache["token"] = token
        _supabase_jwt_cache["expires_at"] = now + expires_in
        return token
    except Exception as e:
        logging.warning(f"Supabase JWT refresh failed: {e}")
        return ""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# Allowed directories for legacy /print filepath parameter
_PRINT_ALLOWED_DIRS = [
    Path(r"C:\Printosky\Jobs\Incoming"),
    Path(r"C:\Printosky\Jobs\Archive"),
]

def _is_allowed_filepath(filepath: str) -> bool:
    """Return True only if filepath resolves inside an allowed print directory."""
    try:
        resolved = Path(filepath).resolve()
        return any(
            resolved.parent == d.resolve()
            for d in _PRINT_ALLOWED_DIRS
        )
    except Exception:
        return False


def init_staff_tables(db_path: str):
    """Ensure staff, staff_sessions, and work_sessions tables exist (idempotent)."""
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staff (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            pin_hash TEXT NOT NULL, active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS staff_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            staff_id TEXT NOT NULL, pc_id TEXT,
            login_at TEXT NOT NULL, logout_at TEXT,
            idle_logout INTEGER DEFAULT 0,
            FOREIGN KEY (staff_id) REFERENCES staff(id)
        )
    """)
    conn.commit()
    _ws_setup(conn)
    _rv_setup(conn)
    conn.close()


def staff_login(db_path: str, pin: str, pc_id: str):
    """Validate PIN, close prior session on this PC, open new session. Returns dict."""
    pin_hash = _sha256(pin)
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id, name FROM staff WHERE pin_hash=? AND active=1", (pin_hash,)
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": "Invalid PIN"}

    staff_id, name = row
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Close any open session for this pc_id
    if pc_id:
        conn.execute(
            "UPDATE staff_sessions SET logout_at=? WHERE pc_id=? AND logout_at IS NULL",
            (now, pc_id)
        )

    cur = conn.execute(
        "INSERT INTO staff_sessions (staff_id, pc_id, login_at) VALUES (?,?,?)",
        (staff_id, pc_id, now)
    )
    session_id = cur.lastrowid
    conn.commit()
    conn.close()

    _active_sessions[pc_id] = {"staff_id": staff_id, "name": name, "session_id": session_id}
    logging.info("Staff login: %s (%s) on %s — session #%d", name, staff_id, pc_id, session_id)
    return {"ok": True, "staff_id": staff_id, "name": name, "session_id": session_id}


def staff_logout(db_path: str, session_id: int, idle: bool = False):
    """Close a staff session."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE staff_sessions SET logout_at=?, idle_logout=? WHERE id=? AND logout_at IS NULL",
        (now, 1 if idle else 0, session_id)
    )
    conn.commit()
    # Clear from in-memory cache
    for pc_id, info in list(_active_sessions.items()):
        if info.get("session_id") == session_id:
            del _active_sessions[pc_id]
            break
    conn.close()
    logging.info("Staff logout: session #%d (idle=%s)", session_id, idle)
    return {"ok": True}


def get_active_staff(db_path: str, pc_id: str):
    """Return active staff for a given pc_id (checks DB for open session)."""
    if pc_id in _active_sessions:
        return _active_sessions[pc_id]
    conn = sqlite3.connect(db_path)
    row = conn.execute("""
        SELECT ss.id, ss.staff_id, s.name
        FROM staff_sessions ss JOIN staff s ON ss.staff_id = s.id
        WHERE ss.pc_id=? AND ss.logout_at IS NULL
        ORDER BY ss.login_at DESC LIMIT 1
    """, (pc_id,)).fetchone()
    conn.close()
    if row:
        info = {"session_id": row[0], "staff_id": row[1], "name": row[2]}
        _active_sessions[pc_id] = info
        return info
    return {"staff_id": None}


def attribute_konica_jobs(db_path: str):
    """Attribute unattributed konica_jobs to staff via active session at print time."""
    conn = sqlite3.connect(db_path)
    unattr = conn.execute(
        "SELECT job_number, user_name, job_date FROM konica_jobs WHERE attributed_to IS NULL"
    ).fetchall()
    updated = 0
    for job_number, user_name, job_date in unattr:
        pc_id = KONICA_USER_PC_MAP.get(user_name)
        if not pc_id or not job_date:
            continue
        row = conn.execute("""
            SELECT staff_id FROM staff_sessions
            WHERE pc_id=? AND login_at <= ?
              AND (logout_at IS NULL OR logout_at >= ?)
            ORDER BY login_at DESC LIMIT 1
        """, (pc_id, job_date, job_date)).fetchone()
        if row:
            conn.execute(
                "UPDATE konica_jobs SET attributed_to=? WHERE job_number=?",
                (row[0], job_number)
            )
            updated += 1
    if updated:
        conn.commit()
        logging.info("Attributed %d konica_jobs to staff", updated)
    conn.close()

# ── Internet / network health check ──────────────────────────────────────────
def check_internet(host="8.8.8.8", port=53, timeout=3) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False

def check_printer_reachable(ip: str, timeout=2) -> bool:
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((ip, 9100))
        s.close()
        return True
    except Exception:
        return False

PRINTER_IPS = {
    "konica": "192.168.55.110",
    "epson":  "192.168.55.201",
}

def get_system_health() -> dict:
    internet  = check_internet()
    konica_ok = check_printer_reachable(PRINTER_IPS["konica"])
    epson_ok  = check_printer_reachable(PRINTER_IPS["epson"])

    if internet and (konica_ok or epson_ok):
        mode = "full"
        mode_label = "Full — payments + printing active"
    elif not internet and (konica_ok or epson_ok):
        mode = "offline_print"
        mode_label = "Offline — walk-in printing only, no online payments"
    elif internet and not konica_ok and not epson_ok:
        mode = "online_no_printers"
        mode_label = "Printers unreachable — check network/power"
    else:
        mode = "manual"
        mode_label = "Manual mode — no internet, no printers"

    return {
        "internet":     internet,
        "konica":       konica_ok,
        "epson":        epson_ok,
        "mode":         mode,
        "mode_label":   mode_label,
        "active_staff": list(_active_sessions.keys()),
        "staff_count":  len(_active_sessions),
        "time":         datetime.now().strftime("%H:%M:%S"),
    }

# ── Config ────────────────────────────────────────────────────────────────────

PORT = 3005

# Printer names must match exactly what Windows sees in "Devices and Printers"
PRINTERS = {
    "konica": "KONICA MINOLTA 1100 PS",
    "epson":  "WF-C21000 Series(Network)",
}

# SumatraPDF path — portable version in project folder or installed
SUMATRA_PATHS = [
    r"C:\printosky_watcher\SumatraPDF.exe",
    r"C:\Users\ABC\AppData\Local\SumatraPDF\SumatraPDF.exe",
    r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
    r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
]

# Shared secret — must match STORE_TOKEN in .env and storeToken in browser localStorage
STORE_TOKEN = os.environ.get("STORE_TOKEN", "")

# SQLite DB path
if sys.platform == "win32":
    DB_PATH = r"C:\Printosky\Data\jobs.db"
else:
    DB_PATH = str(Path.home() / "Printosky" / "Data" / "jobs.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PRINT] %(message)s",
    datefmt="%H:%M:%S",
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def find_sumatra():
    for p in SUMATRA_PATHS:
        if os.path.exists(p):
            return p
    return None


def update_job_status(job_id: str, status: str, printer: str, staff_id: str = None):
    """Update job status, printer, printed_by, and notes in SQLite, then push to Supabase immediately."""
    try:
        conn = sqlite3.connect(DB_PATH)
        old_row = conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        old_status = old_row[0] if old_row else None
        note = f"Printed on {printer} at {datetime.now().strftime('%H:%M')}"
        if staff_id:
            note += f" by {staff_id}"
        conn.execute(
            "UPDATE jobs SET status=?, printer=?, notes=COALESCE(notes||' | ','') || ?"
            + (", printed_by=?" if staff_id else "")
            + " WHERE job_id=?",
            (status, printer, note, staff_id, job_id) if staff_id else (status, printer, note, job_id)
        )
        conn.commit()
        conn.close()
        logging.info("Job %s status → %s (printer: %s, staff: %s)", job_id, status, printer, staff_id or "—")
    except Exception as e:
        logging.error("DB update failed for %s: %s", job_id, e)
        return
    # Log in audit trail
    _jt_log(DB_PATH, job_id, "print_sent",
            from_status=old_status, to_status=status,
            staff_id=staff_id, notes=f"printer={printer}")
    # Immediately push status to Supabase so admin panel reflects change without waiting for sync cycle
    threading.Thread(target=_push_job_status_supabase, args=(job_id, status, printer), daemon=True).start()


def _push_job_status_supabase(job_id: str, status: str, printer: str):
    """PATCH job status to Supabase using service key (bypasses RLS)."""
    import json as _json
    sb_url = os.environ.get("SUPABASE_URL", "")
    sb_key = os.environ.get("SUPABASE_SERVICE_KEY", "") or os.environ.get("SUPABASE_KEY", "")
    if not sb_url or not sb_key:
        return
    try:
        payload = _json.dumps({"status": status, "printer": printer}).encode()
        req = urllib.request.Request(
            f"{sb_url}/rest/v1/jobs?job_id=eq.{job_id}",
            data=payload,
            method="PATCH",
            headers={
                "apikey": sb_key,
                "Authorization": f"Bearer {sb_key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        logging.info("Supabase status push OK: %s → %s", job_id, status)
    except Exception as e:
        logging.warning("Supabase status push failed for %s: %s", job_id, e)


def send_to_printer(job_id: str, filepath: str, printer_key: str, copies: int = 1, colour_mode: str = "auto", staff_id: str = None):
    """
    Execute print command via SumatraPDF (silent, no UI).
    Returns (success: bool, message: str)
    """
    printer_name = PRINTERS.get(printer_key)
    if not printer_name:
        return False, f"Unknown printer key: {printer_key}"

    if not os.path.exists(filepath):
        # Check archive folder — file may have been moved after receipt
        archive_path = os.path.join(r"C:\Printosky\Jobs\Archive", os.path.basename(filepath))
        if os.path.exists(archive_path):
            filepath = archive_path
            logging.info("File found in archive: %s", archive_path)
        else:
            return False, f"File not found: {filepath} (also checked Archive)"

    sumatra = find_sumatra()
    if not sumatra:
        # Fallback: use Windows print verb (less control but always available)
        logging.warning("SumatraPDF not found — using Windows shell print")
        return windows_shell_print(filepath, printer_name, copies)

    # Build SumatraPDF command
    # -print-to <printer>  : print to named printer silently
    # -print-settings      : copies, colour settings
    # -exit-when-done      : close after printing
    settings = f"{copies}x"
    if colour_mode == "bw":
        settings += ",monochrome"
    elif colour_mode == "colour":
        settings += ",color"
    # "auto" = let printer decide

    file_dir  = os.path.dirname(os.path.abspath(filepath))
    file_name = os.path.basename(filepath)
    cmd = [
        sumatra,
        "-print-to", printer_name,
        "-print-settings", settings,
        "-exit-when-done",
        "-silent",
        file_name,
    ]

    logging.info("Print command: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd, timeout=60, capture_output=True, text=True, cwd=file_dir)
        if result.returncode == 0:
            update_job_status(job_id, "Printed", printer_name, staff_id)
            return True, f"Sent to {printer_name} ({copies} cop{'y' if copies==1 else 'ies'})"
        else:
            err = result.stderr or result.stdout or "Unknown error"
            logging.error("SumatraPDF error: %s", err)
            return False, f"Print failed: {err}"
    except subprocess.TimeoutExpired:
        return False, "Print command timed out after 60s"
    except Exception as e:
        return False, str(e)


def windows_shell_print(filepath: str, printer_name: str, copies: int):
    """Fallback: use Windows shell to print (no copies control)."""
    try:
        # Set default printer temporarily and print
        subprocess.run(
            ["rundll32", "printui.dll,PrintUIEntry", "/y", "/n", printer_name],
            check=True, timeout=10
        )
        os.startfile(filepath, "print")
        return True, f"Sent via Windows shell to {printer_name}"
    except Exception as e:
        return False, f"Shell print failed: {e}"


# ── New Sprint 1 helpers ───────────────────────────────────────────────────────

def _db():
    """Return a sqlite3 connection to the jobs DB with row_factory."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _send_whatsapp(phone: str, message: str) -> bool:
    """Send WhatsApp message via Meta Cloud API. Returns True on success."""
    if not phone:
        return False
    from whatsapp_notify import _send
    return _send(phone, message)


def _job_quote(print_items: list, finishing: str, is_student: bool,
               urgent: bool, paper_size: str = "A4") -> dict:
    """Calculate quote using rate_card. Returns {total, print_cost, finishing_cost, breakdown}."""
    if _rc is None:
        return {"total": 0, "print_cost": 0, "finishing_cost": 0, "breakdown": []}
    return _rc.calculate_quote(
        print_items=print_items,
        finishing=finishing or "none",
        is_student=bool(is_student),
        urgent=bool(urgent),
        paper_size=paper_size or "A4",
    )


def _build_page_range_arg(page_list: str, total_pages: int) -> str:
    """
    Convert page_list string to SumatraPDF -print-settings range format.
    page_list: 'all' | '1-10' | '1,5,12' | '1-5,10-15'
    Returns empty string for 'all', else the range string.
    """
    if not page_list or page_list.strip().lower() in ("all", ""):
        return ""
    return page_list.strip()


def update_job_quote(job_id: str, amount: float):
    """Update amount_quoted on the jobs table."""
    try:
        conn = _db()
        conn.execute("UPDATE jobs SET amount_quoted=? WHERE job_id=?", (amount, job_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error("update_job_quote failed: %s", e)


# ── A1: Update job specs ───────────────────────────────────────────────────────

def handle_update_job(body: dict) -> dict:
    """
    PUT /update-job
    Save print_items + finishing + flags to DB. Recalculate and store quote.
    """
    job_id    = body.get("job_id", "")
    staff_id  = body.get("staff_id", "")
    finishing = body.get("finishing", "none")
    is_student= bool(body.get("is_student", False))
    urgent    = bool(body.get("urgent", False))
    paper_size= body.get("paper_size", "A4")
    items_raw = body.get("print_items", [])

    if not job_id:
        return {"ok": False, "error": "job_id required"}

    conn = _db()

    # Upsert print_items — delete old, insert new
    conn.execute("DELETE FROM print_items WHERE job_id=?", (job_id,))

    rc_items = []
    for item in items_raw:
        item_number = int(item.get("item_number", 1))
        page_list   = item.get("page_list", "all")
        paper_type  = item.get("paper_type", "A4_BW")
        colour      = item.get("colour", "bw")
        sides       = item.get("sides", "ss")
        layout      = item.get("layout", "1-up")
        copies      = int(item.get("copies", 1))
        paper_gsm   = int(item.get("paper_gsm", 70))
        printer     = item.get("printer") or ("epson" if colour == "col" else "konica")

        conn.execute("""
            INSERT INTO print_items
              (job_id, item_number, page_list, paper_type, colour, sides, layout,
               copies, paper_gsm, printer, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,'Pending')
        """, (job_id, item_number, page_list, paper_type, colour, sides, layout,
              copies, paper_gsm, printer))

        # For quote calculation, approximate pages from page_list
        # If page_list = 'all', fetch page_count from jobs table
        rc_items.append({
            "pages": item.get("pages", 1),
            "paper_type": paper_type,
            "sides": sides,
            "layout": layout,
            "copies": copies,
        })

    # Update main job flags
    conn.execute("""
        UPDATE jobs SET finishing=?, is_student=?, urgent=?, paper_size=?,
               notes=COALESCE(notes||' | ','') || ?
        WHERE job_id=?
    """, (finishing, int(is_student), int(urgent), paper_size,
          f"Specs updated at {_now()} by {staff_id}", job_id))

    # Calculate quote
    quote = _job_quote(rc_items, finishing, is_student, urgent, paper_size)
    conn.execute("UPDATE jobs SET amount_quoted=? WHERE job_id=?",
                 (quote["total"], job_id))

    conn.commit()
    conn.close()

    logging.info("Job %s specs updated by %s — quote Rs.%.0f", job_id, staff_id, quote["total"])
    return {
        "ok": True,
        "quote": quote,
        "printer_assigned": {
            f"item_{i+1}": (it.get("colour","bw") == "col" and "epson" or "konica")
            for i, it in enumerate(items_raw)
        },
    }


# ── A3: Mark ready ────────────────────────────────────────────────────────────

def handle_mark_ready(body: dict) -> dict:
    """
    POST /mark-ready
    Change status to 'Ready', send WhatsApp to customer.
    """
    job_id   = body.get("job_id", "")
    staff_id = body.get("staff_id", "")
    if not job_id:
        return {"ok": False, "error": "job_id required"}

    conn = _db()
    row = conn.execute(
        "SELECT sender, filename, customer_name FROM jobs WHERE job_id=?", (job_id,)
    ).fetchone()

    if not row:
        conn.close()
        return {"ok": False, "error": "Job not found"}

    phone    = row["sender"]
    filename = row["filename"] or job_id
    name     = row["customer_name"] or "Customer"

    # Update status
    conn.execute("""
        UPDATE jobs SET status='Ready',
               notes=COALESCE(notes||' | ','') || ?
        WHERE job_id=?
    """, (f"Ready notified at {_now()} by {staff_id}", job_id))
    conn.commit()
    conn.close()

    # Send WhatsApp
    whatsapp_sent = False
    if phone:
        msg = (f"Hi! Your print job is ready for collection at Printosky, Thriprayar.\n"
               f"Job: {job_id}\n"
               f"Please collect at your earliest convenience.")
        whatsapp_sent = _send_whatsapp(phone, msg)

    logging.info("Job %s marked Ready (WhatsApp: %s)", job_id, "sent" if whatsapp_sent else "skipped")
    _jt_log(DB_PATH, job_id, "job_ready",
            from_status=None, to_status="Ready",
            staff_id=staff_id,
            notes=f"whatsapp={'sent' if whatsapp_sent else 'skipped'}")
    return {"ok": True, "whatsapp_sent": whatsapp_sent, "phone": phone or "walk-in"}


# ── A4: Complete job ──────────────────────────────────────────────────────────

def handle_complete_job(body: dict) -> dict:
    """
    POST /complete-job
    Record payment collected at counter. Mark job as Completed.
    """
    job_id      = body.get("job_id", "")
    amount      = body.get("amount_collected", 0)
    mode        = body.get("payment_mode", "Cash")
    staff_id    = body.get("staff_id", "")

    if not job_id:
        return {"ok": False, "error": "job_id required"}
    if mode not in ("Cash", "UPI", "Online"):
        mode = "Cash"

    try:
        amount = float(amount)
    except (TypeError, ValueError):
        return {"ok": False, "error": "amount_collected must be a number"}

    conn = _db()
    row = conn.execute("SELECT sender FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    phone = row["sender"] if row else None
    conn.execute("""
        UPDATE jobs SET
            status='Completed',
            amount_collected=?,
            payment_mode=?,
            completed_at=?,
            notes=COALESCE(notes||' | ','') || ?
        WHERE job_id=?
    """, (amount, mode, _now(),
          f"Completed at {_now()} by {staff_id} — Rs.{amount:.0f} {mode}",
          job_id))
    conn.commit()
    conn.close()

    logging.info("Job %s COMPLETED — Rs.%.0f %s by %s", job_id, amount, mode, staff_id)
    _jt_log(DB_PATH, job_id, "job_collected",
            from_status="Ready", to_status="Completed",
            staff_id=staff_id,
            notes=f"Rs.{amount:.0f} {mode}")

    # Schedule review request 30 min after collection
    if phone:
        _rv_schedule(DB_PATH, job_id, phone, _send_whatsapp)

    return {"ok": True, "job_id": job_id, "amount": amount, "mode": mode}


# ── A5: New photocopy job ─────────────────────────────────────────────────────

def handle_new_photocopy(body: dict) -> dict:
    """
    POST /new-photocopy
    Create an immediate Completed job entry for a photocopy (no file).
    """
    staff_id      = body.get("staff_id", "")
    customer_name = body.get("customer_name", "")
    phone         = body.get("phone", "")
    pages         = int(body.get("pages", 1))
    colour        = body.get("colour", "bw")
    copies        = int(body.get("copies", 1))
    amount        = float(body.get("amount_collected", 0))
    mode          = body.get("payment_mode", "Cash")

    if mode not in ("Cash", "UPI", "Online"):
        mode = "Cash"

    # Generate next job_id
    today = datetime.now().strftime("%Y%m%d")
    conn  = _db()
    row   = conn.execute(
        "SELECT job_id FROM jobs WHERE job_id LIKE ? ORDER BY job_id DESC LIMIT 1",
        (f"OSP-{today}-%",)
    ).fetchone()
    if row:
        last_seq = int(row["job_id"].split("-")[-1])
        seq = last_seq + 1
    else:
        seq = 1
    job_id = f"OSP-{today}-{seq:04d}"
    now    = _now()

    conn.execute("""
        INSERT INTO jobs
          (job_id, received_at, filename, source, sender, customer_name,
           service_type, page_count, colour, copies,
           status, amount_collected, payment_mode, completed_at,
           printed_by, notes, amount_quoted)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (job_id, now, "Photocopy Job", "Photocopy", phone or None, customer_name or None,
          "Photocopy", pages, colour, copies,
          "Completed", amount, mode, now,
          staff_id,
          f"Photocopy job created at {now} by {staff_id}",
          amount))
    conn.commit()
    conn.close()

    logging.info("Photocopy job %s created — Rs.%.0f %s by %s", job_id, amount, mode, staff_id)
    return {"ok": True, "job_id": job_id}


# ── A6b: Upload file (base64 JSON) ────────────────────────────────────────────

def handle_upload_file(body: dict) -> dict:
    """
    POST /upload-file
    Accepts { filename, file_data (base64) }.
    Saves to hot folder. Returns { ok, filename, filepath }.
    """
    import base64 as _b64
    filename  = (body.get("filename") or "upload.bin").strip()
    file_data = body.get("file_data", "")
    if not filename or not file_data:
        return {"ok": False, "error": "filename and file_data required"}

    safe_name = Path(filename).name          # strip any path components
    if not safe_name:
        return {"ok": False, "error": "Invalid filename"}

    dest_dir = Path(r"C:\Printosky\Jobs\Incoming")
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest = dest_dir / safe_name
    if dest.exists():
        stem, ext, i = dest.stem, dest.suffix, 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{i}{ext}"
            i += 1

    try:
        raw = _b64.b64decode(file_data)
    except Exception as exc:
        return {"ok": False, "error": f"base64 decode failed: {exc}"}

    dest.write_bytes(raw)
    logging.info("Uploaded file: %s (%d bytes)", dest.name, len(raw))
    return {"ok": True, "filename": dest.name, "filepath": str(dest)}


# ── A6c: Create job — manual / walk-in entry ──────────────────────────────────

def handle_create_job(body: dict) -> dict:
    """
    POST /create-job
    Create a job from admin panel (walk-in, WhatsApp fallback, etc.).
    Payment (full or partial) OR override_reason required to set status=Queued.
    Without either, status=Draft (pending payment collection).
    """
    customer_name    = body.get("customer_name", "")
    phone            = body.get("phone", "")
    source           = body.get("source", "Walk-in")
    notes            = body.get("notes", "")
    filename         = body.get("filename", "")
    filepath_stored  = body.get("filepath", "")
    service_type     = body.get("service_type", "")
    colour           = body.get("colour", "bw")
    sides            = body.get("sides", "ss")
    copies           = max(1, int(body.get("copies") or 1))
    paper_size       = (body.get("paper_size") or "A4").upper()
    finishing        = body.get("finishing", "none")
    pages            = max(1, int(body.get("pages") or 1))
    is_student       = bool(body.get("is_student", False))
    urgent           = bool(body.get("urgent", False))
    amount_quoted    = float(body.get("amount_quoted") or 0)
    amount_collected = float(body.get("amount_collected") or 0)
    amount_partial   = float(body.get("amount_partial") or 0)
    payment_mode     = body.get("payment_mode", "Cash")
    override_reason  = (body.get("override_reason") or "").strip()
    staff_id         = body.get("staff_id", "")

    if payment_mode not in ("Cash", "UPI", "Online"):
        payment_mode = "Cash"

    paid = amount_collected > 0 or amount_partial > 0
    if not paid and not override_reason:
        return {"ok": False, "error": "Payment or override reason required"}

    # Generate job_id
    today_str = datetime.now().strftime("%Y%m%d")
    conn = _db()
    row = conn.execute(
        "SELECT job_id FROM jobs WHERE job_id LIKE ? ORDER BY job_id DESC LIMIT 1",
        (f"OSP-{today_str}-%",)
    ).fetchone()
    seq = (int(row["job_id"].split("-")[-1]) + 1) if row else 1
    job_id  = f"OSP-{today_str}-{seq:04d}"
    now_str = _now()

    status    = "Queued" if (paid or override_reason) else "Draft"
    queued_at = now_str  if status == "Queued" else None

    # Auto-calculate quote if not provided
    if amount_quoted == 0 and _rc is not None:
        paper_type_rc = f"{paper_size}_BW" if colour == "bw" else f"{paper_size}_col"
        rc_items = [{"pages": pages, "paper_type": paper_type_rc,
                     "sides": sides, "layout": "1-up", "copies": copies}]
        try:
            result = _rc.calculate_quote(rc_items, finishing, urgent, is_student, paper_size)
            amount_quoted = result["total"]
        except Exception as exc:
            logging.warning("Quote calc failed for %s: %s", job_id, exc)

    final_amount = amount_collected if amount_collected > 0 else (amount_partial if amount_partial > 0 else None)
    final_mode   = payment_mode if final_amount else None

    ext = Path(filename).suffix.lstrip(".") if filename else ""

    conn.execute("""
        INSERT INTO jobs
          (job_id, received_at, filename, file_extension, source, sender,
           customer_name, service_type, colour, sides, copies, finishing,
           paper_size, page_count, amount_quoted, amount_collected, amount_partial,
           payment_mode, override_reason, status, queued_at, filepath, notes, staff_notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        job_id, now_str,
        filename or "Manual Entry", ext,
        source,
        phone or None,
        customer_name or None,
        service_type or None,
        colour, sides, copies, finishing,
        paper_size, pages,
        amount_quoted, final_amount,
        amount_partial if amount_partial > 0 else None,
        final_mode, override_reason or None,
        status, queued_at,
        filepath_stored or None,
        notes or None,
        f"Manual entry at {now_str} by {staff_id}",
    ))
    conn.commit()

    # Insert a print_items row so the print panel loads specs immediately
    if pages > 0:
        paper_type = f"{paper_size}_BW" if colour == "bw" else f"{paper_size}_col"
        printer    = "epson" if colour == "col" else "konica"
        try:
            conn.execute("""
                INSERT INTO print_items
                  (job_id, item_number, page_list, paper_type, colour, sides,
                   layout, copies, paper_gsm, printer, status)
                VALUES (?,1,'all',?,?,?,?,?,70,?,'Pending')
            """, (job_id, paper_type, colour, sides, "1-up", copies, printer))
            conn.commit()
        except Exception as exc:
            logging.warning("print_items insert skipped for %s: %s", job_id, exc)

    conn.close()
    logging.info("Manual job %s — %s — Rs.%.0f — status=%s — by %s",
                 job_id, source, amount_quoted, status, staff_id)

    # Audit event
    action = "job_created_queued" if status == "Queued" else "job_created_draft"
    _jt_log(DB_PATH, job_id, action,
            from_status=None, to_status=status,
            staff_id=staff_id,
            notes=f"source={source} amount={amount_quoted} payment={payment_mode or 'none'}")

    return {"ok": True, "job_id": job_id, "status": status, "amount_quoted": amount_quoted}


# ── A6: Send to vendor ────────────────────────────────────────────────────────

def handle_vendor_send(body: dict) -> dict:
    """
    POST /vendor-send
    Create a vendor step entry and update job status to 'At Vendor'.
    """
    job_id       = body.get("job_id", "")
    step_number  = int(body.get("step_number", 1))
    vendor_type  = body.get("vendor_type", "")
    vendor_name  = body.get("vendor_name", "")
    expected_ret = body.get("expected_return_date", "")
    cost         = float(body.get("cost_to_vendor", 0))
    staff_id     = body.get("staff_id", "")

    if not job_id or not vendor_type:
        return {"ok": False, "error": "job_id and vendor_type required"}

    now  = _now()
    conn = _db()

    conn.execute("""
        INSERT INTO job_vendor_steps
          (job_id, step_number, vendor_type, vendor_name, sent_date,
           expected_return_date, cost_to_vendor, status)
        VALUES (?,?,?,?,?,?,?,'At Vendor')
    """, (job_id, step_number, vendor_type, vendor_name, now, expected_ret, cost))

    conn.execute("""
        UPDATE jobs SET status='At Vendor',
               vendor_name=?,
               vendor_sent_date=?,
               vendor_return_date=?,
               notes=COALESCE(notes||' | ','') || ?
        WHERE job_id=?
    """, (vendor_name, now, expected_ret,
          f"Step {step_number} sent to {vendor_name} ({vendor_type}) at {now} by {staff_id}",
          job_id))
    conn.commit()
    conn.close()

    logging.info("Job %s → vendor step %d (%s: %s)", job_id, step_number, vendor_type, vendor_name)
    return {"ok": True, "job_id": job_id, "step": step_number}


# ── A7: Vendor returned ───────────────────────────────────────────────────────

def handle_vendor_return(body: dict) -> dict:
    """
    POST /vendor-return
    Mark a vendor step as returned. If all steps done, set job to 'Printed' (ready to complete).
    """
    job_id      = body.get("job_id", "")
    step_number = int(body.get("step_number", 1))
    staff_id    = body.get("staff_id", "")

    if not job_id:
        return {"ok": False, "error": "job_id required"}

    now  = _now()
    conn = _db()

    conn.execute("""
        UPDATE job_vendor_steps SET status='Returned', actual_return_date=?
        WHERE job_id=? AND step_number=?
    """, (now, job_id, step_number))

    # Check if all steps are returned
    pending = conn.execute(
        "SELECT COUNT(*) FROM job_vendor_steps WHERE job_id=? AND status='At Vendor'",
        (job_id,)
    ).fetchone()[0]

    all_done = (pending == 0)

    if all_done:
        conn.execute("""
            UPDATE jobs SET status='Printed',
                   notes=COALESCE(notes||' | ','') || ?
            WHERE job_id=?
        """, (f"All vendor steps returned at {now} — ready for collection", job_id))

        # Notify customer
        row = conn.execute("SELECT sender, customer_name FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        phone = row["sender"] if row else None
        whatsapp_sent = False
        if phone:
            msg = (f"Great news! Your print job is back from finishing and ready for collection.\n"
                   f"Job: {job_id}\nCome collect at Printosky, Thriprayar.")
            whatsapp_sent = _send_whatsapp(phone, msg)
    else:
        conn.execute("""
            UPDATE jobs SET notes=COALESCE(notes||' | ','') || ?
            WHERE job_id=?
        """, (f"Step {step_number} returned at {now} by {staff_id} — {pending} step(s) remaining", job_id))
        whatsapp_sent = False

    conn.commit()
    conn.close()

    logging.info("Job %s vendor step %d returned — all_done=%s", job_id, step_number, all_done)
    return {"ok": True, "all_done": all_done, "whatsapp_sent": whatsapp_sent if all_done else None}


# ── A8: Quote calculator ──────────────────────────────────────────────────────

def handle_quote(qs: dict) -> dict:
    """
    GET /quote?pages=34&paper_type=A4_BW&sides=ds&layout=1-up&copies=1&finishing=spiral
             &is_student=false&urgent=false
    Returns price breakdown without writing to DB.
    """
    if _rc is None:
        return {"total": 0, "error": "rate_card not loaded"}

    pages      = int(qs.get("pages", [1])[0])
    sides      = qs.get("sides", ["ss"])[0]
    layout     = qs.get("layout", ["1-up"])[0]
    copies     = int(qs.get("copies", [1])[0])
    finishing  = qs.get("finishing", ["none"])[0]
    is_student = qs.get("is_student", ["false"])[0].lower() in ("true", "1")
    urgent     = qs.get("urgent",     ["false"])[0].lower() in ("true", "1")
    paper_size = qs.get("paper_size", ["A4"])[0].upper()

    # paper_type: accept explicit param OR derive from colour + paper_size shorthand
    colour_raw = qs.get("colour", [""])[0].lower()
    if "paper_type" in qs:
        paper_type = qs["paper_type"][0]
    elif colour_raw in ("col", "colour", "color"):
        paper_type = f"{paper_size}_col"
    else:
        paper_type = f"{paper_size}_BW"

    items = [{"pages": pages, "paper_type": paper_type, "sides": sides,
              "layout": layout, "copies": copies}]
    result = _rc.calculate_quote(items, finishing, urgent, is_student, paper_size)
    sheets = _rc.calc_sheets(pages, sides, layout)

    return {
        "sheets":         sheets,
        "print_cost":     result["print_cost"],
        "finishing_cost": result["finishing_cost"],
        "total":          result["total"],
        "breakdown":      result["breakdown"],
    }


# ── A10: Colour detection ─────────────────────────────────────────────────────

def handle_detect_colour(body: dict) -> dict:
    """
    POST /detect-colour
    Run colour page detection on the PDF for a job and save result to DB.
    Returns the colour_map dict.
    """
    job_id   = body.get("job_id", "")
    staff_id = body.get("staff_id", "")
    if not job_id:
        return {"ok": False, "error": "job_id required"}

    conn = _db()
    row = conn.execute(
        "SELECT filepath, filename FROM jobs WHERE job_id=?", (job_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"ok": False, "error": "Job not found"}

    filepath = row["filepath"] or ""
    if not filepath or not os.path.exists(filepath):
        # Try archive
        archive = os.path.join(r"C:\Printosky\Jobs\Archive", row["filename"] or "")
        if os.path.exists(archive):
            filepath = archive
        else:
            return {"ok": False, "error": "PDF file not found on disk"}

    if not filepath.lower().endswith(".pdf"):
        return {"ok": False, "error": "Colour detection only supported for PDF files"}

    cmap = _cd_build(filepath)
    _cd_save(DB_PATH, job_id, cmap)
    _jt_log(DB_PATH, job_id, "colour_detected",
            staff_id=staff_id,
            notes=f"colour={len(cmap.get('colour',[]))} bw={len(cmap.get('bw',[]))} mixed={cmap.get('is_mixed',False)}")
    logging.info("Colour detection [%s]: %s", job_id, cmap)
    return {"ok": True, "job_id": job_id, "colour_map": cmap}


def handle_confirm_colour(body: dict) -> dict:
    """
    POST /confirm-colour
    Staff confirms (or overrides) the colour page map for a job.
    colour_pages: optional list[int] of 1-indexed page numbers staff marks as colour.
    If omitted, confirms auto-detected result as-is.
    """
    job_id       = body.get("job_id", "")
    staff_id     = body.get("staff_id", "")
    colour_pages = body.get("colour_pages")   # None = confirm as-is; list = override
    if not job_id:
        return {"ok": False, "error": "job_id required"}

    if colour_pages is not None:
        try:
            colour_pages = [int(p) for p in colour_pages]
        except (TypeError, ValueError):
            return {"ok": False, "error": "colour_pages must be a list of integers"}

    _cd_confirm(DB_PATH, job_id, colour_pages)
    _jt_log(DB_PATH, job_id, "colour_confirmed",
            staff_id=staff_id,
            notes=f"override={colour_pages is not None} pages={colour_pages}")
    return {"ok": True, "job_id": job_id}


# ── A12: Review rating ────────────────────────────────────────────────────────

def handle_review_rating(body: dict) -> dict:
    """
    POST /review-rating
    Record a customer's 1-5 star rating after collection.
    Called by the WhatsApp bot when customer replies to the review request.
    { phone, rating }
    """
    phone  = (body.get("phone") or "").strip()
    rating = body.get("rating")
    if not phone:
        return {"ok": False, "error": "phone required"}
    try:
        rating = int(rating)
    except (TypeError, ValueError):
        return {"ok": False, "error": "rating must be an integer 1-5"}
    if rating not in range(1, 6):
        return {"ok": False, "error": "rating must be 1-5"}
    return _rv_record(DB_PATH, phone, rating, _send_whatsapp)


# ── A11: Work session timer ───────────────────────────────────────────────────

def handle_session_start(body: dict) -> dict:
    """
    POST /session-start
    { job_id, staff_id }
    Open a new work session for DTP / editing work.
    """
    import re as _re
    job_id   = (body.get("job_id") or "").strip()
    staff_id = (body.get("staff_id") or "").strip()
    if not job_id or not staff_id:
        return {"ok": False, "error": "job_id and staff_id required"}
    if not _re.match(r'^OSP-\d{8}-\d{4}$', job_id):
        return {"ok": False, "error": "invalid job_id format"}
    # Verify job exists and staff is real
    conn = _db()
    job_row = conn.execute("SELECT job_id FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    staff_row = conn.execute("SELECT id FROM staff WHERE id=? AND active=1", (staff_id,)).fetchone()
    conn.close()
    if not job_row:
        return {"ok": False, "error": f"Job {job_id} not found"}
    if not staff_row:
        return {"ok": False, "error": "Staff not found or inactive"}
    result = _ws_start(DB_PATH, job_id, staff_id)
    if result.get("ok"):
        _jt_log(DB_PATH, job_id, "work_session_started",
                staff_id=staff_id,
                notes=f"session_id={result['session_id']}")
    return result


def _get_session_staff(session_id: int) -> str | None:
    """Return the staff_id that owns a work session, or None if not found."""
    conn = _db()
    try:
        row = conn.execute(
            "SELECT staff_id FROM work_sessions WHERE id=?", (session_id,)
        ).fetchone()
        return row["staff_id"] if row else None
    finally:
        conn.close()


def handle_session_pause(body: dict) -> dict:
    """
    POST /session-pause
    { session_id, staff_id }
    Pause an open work session. Only the owning staff member may pause it.
    """
    session_id = body.get("session_id")
    staff_id   = (body.get("staff_id") or "").strip()
    if session_id is None:
        return {"ok": False, "error": "session_id required"}
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "session_id must be an integer"}
    if not staff_id:
        return {"ok": False, "error": "staff_id required"}
    owner = _get_session_staff(session_id)
    if owner is None:
        return {"ok": False, "error": f"Session {session_id} not found"}
    if owner != staff_id:
        return {"ok": False, "error": "Not authorized to modify this session"}
    return _ws_pause(DB_PATH, session_id)


def handle_session_resume(body: dict) -> dict:
    """
    POST /session-resume
    { session_id, staff_id }
    Resume a paused work session. Only the owning staff member may resume it.
    """
    session_id = body.get("session_id")
    staff_id   = (body.get("staff_id") or "").strip()
    if session_id is None:
        return {"ok": False, "error": "session_id required"}
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "session_id must be an integer"}
    if not staff_id:
        return {"ok": False, "error": "staff_id required"}
    owner = _get_session_staff(session_id)
    if owner is None:
        return {"ok": False, "error": f"Session {session_id} not found"}
    if owner != staff_id:
        return {"ok": False, "error": "Not authorized to modify this session"}
    return _ws_resume(DB_PATH, session_id)


def handle_session_end(body: dict) -> dict:
    """
    POST /session-end
    { session_id, staff_id, notes?, dtp_pages?, graph_count? }
    End a work session and calculate billing. Only the owning staff member may end it.
    """
    session_id = body.get("session_id")
    staff_id   = (body.get("staff_id") or "").strip()
    if session_id is None:
        return {"ok": False, "error": "session_id required"}
    try:
        session_id = int(session_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "session_id must be an integer"}
    if not staff_id:
        return {"ok": False, "error": "staff_id required"}
    owner = _get_session_staff(session_id)
    if owner is None:
        return {"ok": False, "error": f"Session {session_id} not found"}
    if owner != staff_id:
        return {"ok": False, "error": "Not authorized to end this session"}

    notes       = body.get("notes", "")
    dtp_pages   = max(0, int(body.get("dtp_pages") or 0))
    graph_count = max(0, int(body.get("graph_count") or 0))

    result = _ws_end(DB_PATH, session_id, notes=notes,
                     dtp_pages=dtp_pages, graph_count=graph_count)
    if result.get("ok"):
        _jt_log(DB_PATH, result["job_id"], "work_session_ended",
                staff_id=result.get("staff_id"),
                notes=(f"billing={result['billing_minutes']}min "
                       f"dtp_pages={dtp_pages} graphs={graph_count}"))
    return result


# ── A9: Print receipt (thermal printer stub) ──────────────────────────────────

RECEIPT_PRINTER = None  # Set to {"vendor": 0xXXXX, "product": 0xXXXX} when hardware arrives

def handle_print_receipt(body: dict) -> dict:
    """
    POST /print-receipt
    Fire thermal receipt printer. Currently a stub — returns not-configured if no hardware.
    """
    if RECEIPT_PRINTER is None:
        return {"ok": False, "error": "Receipt printer not configured"}

    job_id = body.get("job_id", "")
    if not job_id:
        return {"ok": False, "error": "job_id required"}

    try:
        conn = _db()
        row = conn.execute(
            "SELECT * FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.close()
        if not row:
            return {"ok": False, "error": "Job not found"}

        # When hardware arrives: format receipt and send via python-escpos
        # from escpos.printer import Usb
        # p = Usb(RECEIPT_PRINTER["vendor"], RECEIPT_PRINTER["product"])
        # p.text(f"PRINTOSKY\n{job_id}\n...")
        # p.cut()

        logging.info("Receipt printer: job %s (hardware not yet connected)", job_id)
        return {"ok": True, "note": "Hardware not connected yet"}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── GET /vendors ──────────────────────────────────────────────────────────────

def handle_get_vendors(qs: dict) -> dict:
    """GET /vendors?finishing_type=Project+Binding"""
    finishing_type = qs.get("finishing_type", [None])[0]
    conn = _db()
    if finishing_type:
        rows = conn.execute(
            "SELECT * FROM vendors WHERE active=1 AND (finishing_types LIKE ? OR is_default_for=?)",
            (f"%{finishing_type}%", finishing_type)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM vendors WHERE active=1").fetchall()
    conn.close()
    return {"ok": True, "vendors": [dict(r) for r in rows]}


# ── A2: Print a specific print_item (reads all settings from DB) ──────────────

def handle_print_item(job_id: str, item_number: int, staff_id: str = None) -> dict:
    """
    POST /print  { job_id, item_number, staff_id }
    Reads print_items row from DB → builds exact SumatraPDF command → fires print.
    Updates print_items.status + checks if all items done → updates jobs.status.
    """
    conn = _db()

    # Load the print item
    item = conn.execute(
        "SELECT * FROM print_items WHERE job_id=? AND item_number=?",
        (job_id, item_number)
    ).fetchone()

    # Load the job (for filepath + fallback defaults)
    job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not job:
        conn.close()
        return {"ok": False, "error": f"Job {job_id} not found"}

    # Auto-create print_items row from job defaults if missing
    # (happens when staff clicks Print without saving specs first)
    if not item:
        logging.warning("print_items row missing for %s item %d — auto-creating from job defaults",
                        job_id, item_number)
        colour_raw = job["colour"] if "colour" in job.keys() else "bw"
        colour = "col" if colour_raw in ("col", "colour", "color") else "bw"
        printer_default = "epson" if colour == "col" else "konica"
        conn.execute("""
            INSERT OR IGNORE INTO print_items
              (job_id, item_number, page_list, colour, sides, layout,
               copies, paper_gsm, printer, status)
            VALUES (?,?,   'all',   ?,     'ss', '1-up',
                   1,    70,      ?,     'Pending')
        """, (job_id, item_number, colour, printer_default))
        conn.commit()
        item = conn.execute(
            "SELECT * FROM print_items WHERE job_id=? AND item_number=?",
            (job_id, item_number)
        ).fetchone()
        if not item:
            conn.close()
            return {"ok": False, "error": f"Could not auto-create print item for {job_id}"}

    conn.close()

    # Resolve file path
    filepath = job["filepath"] if "filepath" in job.keys() else ""
    if not filepath:
        filepath = job["file_path"] if "file_path" in job.keys() else ""
    if not filepath and job["filename"]:
        # Try archive folder
        filepath = os.path.join(r"C:\Printosky\Jobs\Archive", job["filename"])

    if not filepath or not os.path.exists(filepath):
        # Check archive
        if job["filename"]:
            arc = os.path.join(r"C:\Printosky\Jobs\Archive", job["filename"])
            if os.path.exists(arc):
                filepath = arc
        if not filepath or not os.path.exists(filepath):
            return {"ok": False, "error": f"File not found for job {job_id}"}

    # Resolve SumatraPDF
    sumatra = find_sumatra()
    if not sumatra:
        return {"ok": False, "error": "SumatraPDF not found on this PC"}

    # Read item settings from DB (trust DB, NOT frontend)
    printer_key = item["printer"] or ("epson" if item["colour"] == "col" else "konica")
    printer_name = PRINTERS.get(printer_key)
    if not printer_name:
        return {"ok": False, "error": f"Unknown printer: {printer_key}"}

    copies    = int(item["copies"] or 1)
    colour    = item["colour"] or "bw"      # 'bw' | 'col'
    sides     = item["sides"] or "ss"       # 'ss' | 'ds'
    layout    = item["layout"] or "1-up"
    page_list = item["page_list"] or "all"

    # Build -print-settings string
    settings_parts = [f"{copies}x"]
    if colour == "bw":
        settings_parts.append("monochrome")
    else:
        settings_parts.append("color")

    if sides == "ds":
        settings_parts.append("duplexlong")   # long-edge duplex

    # Layout / n-up
    if layout == "2-up":
        settings_parts.append("nup2")
    elif layout == "4-up":
        settings_parts.append("nup4")

    # Page range
    page_range = _build_page_range_arg(page_list, job["page_count"] or 0)
    if page_range:
        settings_parts.append(f"pagerange:{page_range}")

    settings_str = ",".join(settings_parts)

    file_dir2  = os.path.dirname(os.path.abspath(filepath))
    file_name2 = os.path.basename(filepath)
    cmd = [
        sumatra,
        "-print-to", printer_name,
        "-print-settings", settings_str,
        "-exit-when-done",
        "-silent",
        file_name2,
    ]

    logging.info("Print item %d of job %s: printer=%s settings=%s pages=%s",
                 item_number, job_id, printer_key, settings_str, page_list)

    try:
        result = subprocess.run(cmd, timeout=90, capture_output=True, text=True, cwd=file_dir2)
        if result.returncode != 0:
            err = result.stderr or result.stdout or "Unknown SumatraPDF error"
            logging.error("SumatraPDF error for item %d: %s", item_number, err)
            return {"ok": False, "error": f"Print failed: {err}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Print command timed out after 90s"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Mark this item as Printed
    now = _now()
    conn2 = _db()
    conn2.execute("""
        UPDATE print_items SET status='Printed', printed_at=?, printed_by=?
        WHERE job_id=? AND item_number=?
    """, (now, staff_id, job_id, item_number))

    # Check if ALL items for this job are Printed
    pending_count = conn2.execute(
        "SELECT COUNT(*) FROM print_items WHERE job_id=? AND status!='Printed'",
        (job_id,)
    ).fetchone()[0]

    all_items_printed = (pending_count == 0)

    if all_items_printed:
        conn2.execute("""
            UPDATE jobs SET status='Printed', printed_by=?,
                   notes=COALESCE(notes||' | ','') || ?
            WHERE job_id=?
        """, (staff_id,
              f"All items printed at {now}" + (f" by {staff_id}" if staff_id else ""),
              job_id))
        logging.info("Job %s — ALL items printed. Status -> Printed", job_id)

    conn2.commit()
    conn2.close()

    if all_items_printed:
        threading.Thread(target=_push_job_status_supabase, args=(job_id, "Printed", printer_name), daemon=True).start()

    return {
        "ok": True,
        "job_id": job_id,
        "item_number": item_number,
        "printer": printer_name,
        "settings": settings_str,
        "all_items_printed": all_items_printed,
    }


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class PrintHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        logging.info(fmt % args)

    def _json(self, code: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Store-Token")
        self.end_headers()

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length)) if length else {}
        except Exception:
            return {}

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/status":
            sumatra = find_sumatra()
            self._json(200, {
                "ok": True,
                "sumatra": sumatra or "not found (will use shell fallback)",
                "printers": PRINTERS,
                "db": os.path.exists(DB_PATH),
            })
        elif path == "/printers":
            self._json(200, {"printers": PRINTERS})
        elif path == "/health":
            self._json(200, get_system_health())
        elif path == "/active-staff":
            pc_id = qs.get("pc_id", [None])[0]
            if not pc_id:
                self._json(400, {"error": "pc_id required"})
                return
            self._json(200, get_active_staff(DB_PATH, pc_id))
        elif path == "/job-items":
            job_id = qs.get("job_id", [None])[0]
            if not job_id:
                self._json(400, {"error": "job_id required"}); return
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM print_items WHERE job_id=? ORDER BY item_number",
                (job_id,)
            ).fetchall()
            conn.close()
            self._json(200, {"items": [dict(r) for r in rows]})
        elif path == "/quote":
            self._json(200, handle_quote(qs))
        elif path == "/vendors":
            self._json(200, handle_get_vendors(qs))
        elif path == "/file":
            job_id = qs.get("job_id", [None])[0]
            if not job_id:
                self._json(400, {"error": "job_id required"})
                return
            conn = _db()
            job  = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            conn.close()
            if not job:
                self._json(404, {"error": "Job not found"})
                return
            # Resolve filepath
            fp = ""
            try:    fp = job["filepath"] or ""
            except: pass
            if not fp:
                try:    fp = job["file_path"] or ""
                except: pass
            if not fp and job["filename"]:
                fp = os.path.join(r"C:\Printosky\Jobs\Archive", job["filename"])
            if not fp or not os.path.exists(fp):
                if job["filename"]:
                    arc = os.path.join(r"C:\Printosky\Jobs\Archive", job["filename"])
                    if os.path.exists(arc):
                        fp = arc
            if not fp or not os.path.exists(fp):
                self._json(404, {"error": "File not found on disk"})
                return
            # Serve the file
            ext = os.path.splitext(fp)[1].lower()
            mime_map = {
                ".pdf": "application/pdf",
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp",
                ".doc": "application/msword",
                ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                ".xls": "application/vnd.ms-excel",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            }
            mime = mime_map.get(ext, "application/octet-stream")
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'inline; filename="{os.path.basename(fp)}"')
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        elif path == "/events":
            # Require store token — audit trail contains staff IDs and action history
            token = self.headers.get("X-Store-Token", "")
            if not STORE_TOKEN or not hmac.compare_digest(token.encode(), STORE_TOKEN.encode()):
                self._json(403, {"error": "Forbidden"})
                return
            job_id = qs.get("job_id", [None])[0]
            if not job_id:
                self._json(400, {"error": "job_id required"})
                return
            import re as _re
            if not _re.match(r'^OSP-\d{8}-\d{4}$', job_id):
                self._json(400, {"error": "invalid job_id format"})
                return
            self._json(200, {"events": _jt_events(DB_PATH, job_id)})
        elif path == "/work-sessions":
            token = self.headers.get("X-Store-Token", "")
            if not STORE_TOKEN or not hmac.compare_digest(token.encode(), STORE_TOKEN.encode()):
                self._json(403, {"error": "Forbidden"})
                return
            job_id = qs.get("job_id", [None])[0]
            if not job_id:
                self._json(400, {"error": "job_id required"})
                return
            import re as _re
            if not _re.match(r'^OSP-\d{8}-\d{4}$', job_id):
                self._json(400, {"error": "invalid job_id format"})
                return
            open_session = _ws_open(DB_PATH, job_id)
            all_sessions = _ws_get(DB_PATH, job_id)
            self._json(200, {"sessions": all_sessions, "open_session": open_session})
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self):
        path = urlparse(self.path).path

        # Verify shared secret on all mutation endpoints.
        # /staff-login and /staff-logout are exempt (needed before token is available).
        if path not in ("/staff-login", "/staff-logout"):
            token = self.headers.get("X-Store-Token", "")
            if not STORE_TOKEN or not hmac.compare_digest(
                token.encode(), STORE_TOKEN.encode()
            ):
                self._json(403, {"error": "Forbidden"})
                return

        if path == "/staff-login":
            body = self._read_body()
            pin   = body.get("pin", "")
            pc_id = body.get("pc_id", "")
            if not pin:
                self._json(400, {"error": "pin required"})
                return
            result = staff_login(DB_PATH, pin, pc_id)
            if result.get("ok"):
                result["supabase_jwt"] = _get_supabase_jwt()
            self._json(200 if result["ok"] else 401, result)
            return

        if path == "/staff-logout":
            body = self._read_body()
            session_id = body.get("session_id")
            idle = bool(body.get("idle", False))
            if not session_id:
                self._json(400, {"error": "session_id required"})
                return
            self._json(200, staff_logout(DB_PATH, session_id, idle))
            return


        # ── Sprint 1 endpoints ────────────────────────────────────────────────
        if path == "/update-job":
            body = self._read_body()
            self._json(200, handle_update_job(body))
            return

        if path == "/mark-ready":
            body = self._read_body()
            self._json(200, handle_mark_ready(body))
            return

        if path == "/complete-job":
            body = self._read_body()
            result = handle_complete_job(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        if path == "/new-photocopy":
            body = self._read_body()
            self._json(200, handle_new_photocopy(body))
            return

        if path == "/upload-file":
            body = self._read_body()
            result = handle_upload_file(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        if path == "/create-job":
            body = self._read_body()
            result = handle_create_job(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        if path == "/vendor-send":
            body = self._read_body()
            self._json(200, handle_vendor_send(body))
            return

        if path == "/vendor-return":
            body = self._read_body()
            self._json(200, handle_vendor_return(body))
            return

        if path == "/print-receipt":
            body = self._read_body()
            self._json(200, handle_print_receipt(body))
            return

        if path == "/detect-colour":
            body = self._read_body()
            result = handle_detect_colour(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        if path == "/confirm-colour":
            body = self._read_body()
            result = handle_confirm_colour(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        # ── Sprint 12B: Review rating ─────────────────────────────────────────
        if path == "/review-rating":
            body = self._read_body()
            result = handle_review_rating(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        # ── Sprint 12: Work session timer ─────────────────────────────────────
        if path == "/session-start":
            body = self._read_body()
            result = handle_session_start(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        if path == "/session-pause":
            body = self._read_body()
            result = handle_session_pause(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        if path == "/session-resume":
            body = self._read_body()
            result = handle_session_resume(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        if path == "/session-end":
            body = self._read_body()
            result = handle_session_end(body)
            self._json(200 if result.get("ok") else 400, result)
            return

        # GET /work-sessions is handled in do_GET

        # ── /print — supports both old-style (filepath in body) and
        #            new-style (item_number in body — reads specs from DB) ────
        if path != "/print":
            self._json(404, {"error": "Not found"})
            return

        body = self._read_body()
        if not body:
            self._json(400, {"error": "Bad request: empty body"})
            return

        job_id   = body.get("job_id", "")
        staff_id = body.get("staff_id") or None

        if not job_id:
            self._json(400, {"error": "job_id is required"})
            return

        # New-style: item_number provided — read everything from print_items DB
        item_number = body.get("item_number")
        if item_number is not None:
            result = handle_print_item(job_id, int(item_number), staff_id)
            self._json(200 if result.get("ok") else 400, result)
            return

        # Old-style (legacy / fallback): filepath + settings in body
        filepath    = body.get("filepath", "")
        printer     = body.get("printer", "konica")
        copies      = int(body.get("copies", 1))
        colour_mode = body.get("colour_mode", "auto")

        if not filepath:
            self._json(400, {"error": "filepath or item_number is required"})
            return

        if not _is_allowed_filepath(filepath):
            logging.warning("Blocked /print with disallowed filepath: %s", filepath)
            self._json(400, {"error": "Invalid filepath"})
            return

        logging.info("Print request (legacy): job=%s printer=%s copies=%d staff=%s file=%s",
                     job_id, printer, copies, staff_id or "—", filepath)

        def do_print():
            ok, msg = send_to_printer(job_id, filepath, printer, copies, colour_mode, staff_id)
            if ok:
                logging.info("Print OK -- %s", msg)
            else:
                logging.error("Print FAILED -- %s", msg)

        threading.Thread(target=do_print, daemon=True).start()

        self._json(200, {
            "ok": True,
            "job_id": job_id,
            "printer": PRINTERS.get(printer, printer),
            "message": "Print job queued",
        })


# ── Main ──────────────────────────────────────────────────────────────────────

def start_print_server():
    init_staff_tables(DB_PATH)
    server = HTTPServer(("0.0.0.0", PORT), PrintHandler)
    logging.info("🖨️  Print server running on port %d", PORT)
    logging.info("   Konica : %s", PRINTERS["konica"])
    logging.info("   Epson  : %s", PRINTERS["epson"])
    sumatra = find_sumatra()
    if sumatra:
        logging.info("   SumatraPDF: %s", sumatra)
    else:
        logging.warning("   SumatraPDF NOT FOUND — will use Windows shell fallback")
        logging.warning("   Download: https://www.sumatrapdfreader.org/download-free-pdf-viewer")
    server.serve_forever()


if __name__ == "__main__":
    start_print_server()
