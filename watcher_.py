"""
PRINTOSKY HOT FOLDER WATCHER
=============================
Runs silently on the store desktop (Windows 11).
Monitors C:/Printosky/Jobs/Incoming/ for any new file.
Logs every file to:
  - A local SQLite database (jobs.db)
  - Google Sheets (live, remote access for owner/investors)

No staff action needed. Automatic from the moment a file lands.

HOW TO RUN (store PC):
  python watcher.py

HOW TO RUN AS BACKGROUND SERVICE (auto-starts with Windows):
  See INSTALL.md
"""

import os
import sys
import time
import hashlib
import sqlite3
import logging
import json
import platform
from datetime import datetime
from pathlib import Path

# ── watchdog ──────────────────────────────────────────────────────────────────
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Google Sheets (optional — works without it if no credentials) ─────────────
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSHEETS_AVAILABLE = True
except ImportError:
    GSHEETS_AVAILABLE = False

# ── Phase 3: Printer Poller (optional — works without it) ────────────────────
try:
    from printer_poller import start_poller as _start_printer_poller
    PRINTER_POLLER_AVAILABLE = True
except ImportError:
    PRINTER_POLLER_AVAILABLE = False

# ── Supabase Sync (optional — enables admin page on printosky.com) ────────────
try:
    from supabase_sync import start_sync as _start_supabase_sync
    SUPABASE_SYNC_AVAILABLE = True
except ImportError:
    SUPABASE_SYNC_AVAILABLE = False

# ── WhatsApp Notifications (optional — sends job tokens + ready alerts) ───────
try:
    from whatsapp_notify import send_job_token, send_ready_alert, send_file_received
    WHATSAPP_NOTIFY_AVAILABLE = True
except ImportError:
    WHATSAPP_NOTIFY_AVAILABLE = False
    def send_job_token(*a, **kw): pass
    def send_ready_alert(*a, **kw): pass
    def send_file_received(*a, **kw): pass

# ── WhatsApp Bot + Rate Card + Razorpay (optional) ───────────────────────────
try:
    from whatsapp_bot import handle_message as _bot_handle, setup_bot_db
    from webhook_receiver import start_webhook_server
    from session_timeout import start_timeout_monitor
    from b2b_manager import (setup_b2b_db, is_b2b, get_b2b_client,
                              register_b2b_client, set_credit_limit,
                              record_payment, list_b2b_clients,
                              print_b2b_jobs, generate_invoice_pdf,
                              mark_jobs_invoiced)
    from b2b_bot import handle_b2b_message
    BOT_AVAILABLE = True
except ImportError as _bot_err:
    BOT_AVAILABLE = False
    def setup_bot_db(*a): pass
    def start_webhook_server(*a): pass
    def start_timeout_monitor(*a): pass
    def setup_b2b_db(*a): pass
    def is_b2b(*a): return False
    def get_b2b_client(*a): return None
    def register_b2b_client(*a, **kw): return "Bot not available"
    def set_credit_limit(*a): return "Bot not available"
    def record_payment(*a): return "Bot not available"
    def list_b2b_clients(*a): return "Bot not available"
    def print_b2b_jobs(*a): return "Bot not available"
    def generate_invoice_pdf(*a): raise RuntimeError("Bot not available")
    def mark_jobs_invoiced(*a): pass
    def handle_b2b_message(*a): return []

# ══════════════════════════════════════════════════════════════════════════════
# CONFIG — edit these for your store
# ══════════════════════════════════════════════════════════════════════════════

# Folder to watch — change this to match the actual path on the store PC
if platform.system() == "Windows":
    WATCH_FOLDER = r"C:\\Printosky\Jobs\Incoming"
    ARCHIVE_FOLDER = r"C:\\Printosky\Jobs\Archive"
    DB_PATH = r"C:\\Printosky\Data\jobs.db"
    LOG_PATH = r"C:\\Printosky\Data\watcher.log"
else:
    # Development / Linux paths
    WATCH_FOLDER = str(Path.home() / "Printosky" / "Jobs" / "Incoming")
    ARCHIVE_FOLDER = str(Path.home() / "Printosky" / "Jobs" / "Archive")
    DB_PATH = str(Path.home() / "Printosky" / "Data" / "jobs.db")
    LOG_PATH = str(Path.home() / "Printosky" / "Data" / "watcher.log")

# Google Sheets config — fill in after setup (see INSTALL.md)
GSHEETS_CREDENTIALS_FILE = "credentials.json"   # service account JSON
GSHEETS_SPREADSHEET_NAME = "Printosky Job Tracker"
GSHEETS_WORKSHEET_NAME = "Job Log"

# ── Phase 3: Printer IPs (confirmed 2026-03-12 via arp -a) ───────────────────
KONICA_IP  = "192.168.55.110"   # Konica Bizhub Pro 1100 (MAC: 00-50-aa-2c-78-4c)
EPSON_IP   = "192.168.55.202"   # Epson WF-C21000       (MAC: e0-bb-9e-d6-52-2e)
# Access EWS at: http://192.168.55.110  and  http://192.168.55.202
# Phase 3 will poll these for page counts to cross-check jobs received vs printed

# File types to track (ignore temp files, system files)
TRACKED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp",
    ".txt", ".csv", ".odt", ".odf",
}

# Temp/system files to ignore
IGNORE_PATTERNS = {
    "~$",           # Office temp files
    ".tmp",         # Temp files
    "thumbs.db",    # Windows thumbnail cache
    ".ds_store",    # Mac metadata
}

# ══════════════════════════════════════════════════════════════════════════════
# SETUP
# ══════════════════════════════════════════════════════════════════════════════

def setup_folders():
    """Create required folders if they don't exist."""
    for folder in [WATCH_FOLDER, ARCHIVE_FOLDER, os.path.dirname(DB_PATH)]:
        Path(folder).mkdir(parents=True, exist_ok=True)

def setup_logging():
    """Configure logging to file + console."""
    Path(os.path.dirname(LOG_PATH)).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ]
    )

def setup_database():
    """Create SQLite database and tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Jobs table — one row per file received
    cursor.execute("""
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
        )
    """)

    # Daily summary view
    cursor.execute("""
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
        GROUP BY DATE(received_at)
        ORDER BY date DESC
    """)

    conn.commit()
    conn.close()
    logging.info("Database ready: %s", DB_PATH)

# ══════════════════════════════════════════════════════════════════════════════
# JOB ID GENERATOR
# ══════════════════════════════════════════════════════════════════════════════

def generate_job_id():
    """
    Generates a job ID like: OSP-20260311-0042
    Date-based, sequential within each day.
    """
    today = datetime.now().strftime("%Y%m%d")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM jobs WHERE job_id LIKE ?",
        (f"OSP-{today}-%",)
    )
    count = cursor.fetchone()[0] + 1
    conn.close()
    return f"OSP-{today}-{count:04d}"

# ══════════════════════════════════════════════════════════════════════════════
# FILE HASH (detect duplicates)
# ══════════════════════════════════════════════════════════════════════════════

def file_hash(filepath):
    """MD5 hash of file — detects if same file is dropped twice."""
    try:
        h = hashlib.md5()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════════════════
# GOOGLE SHEETS SYNC
# ══════════════════════════════════════════════════════════════════════════════

_sheets_client = None

def get_sheets_client():
    """Get or create Google Sheets client."""
    global _sheets_client
    if _sheets_client is not None:
        return _sheets_client
    if not GSHEETS_AVAILABLE:
        return None
    if not os.path.exists(GSHEETS_CREDENTIALS_FILE):
        return None
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(
            GSHEETS_CREDENTIALS_FILE, scopes=scopes
        )
        _sheets_client = gspread.authorize(creds)
        logging.info("Google Sheets connected")
        return _sheets_client
    except Exception as e:
        logging.warning("Google Sheets not available: %s", e)
        return None

def sync_job_to_sheets(job: dict):
    """Append one job row to Google Sheets."""
    client = get_sheets_client()
    if client is None:
        return False
    try:
        sheet = client.open(GSHEETS_SPREADSHEET_NAME).worksheet(GSHEETS_WORKSHEET_NAME)
        row = [
            job.get("job_id", ""),
            job.get("received_at", ""),
            job.get("filename", ""),
            job.get("file_extension", ""),
            job.get("file_size_kb", ""),
            job.get("source", "Hot Folder"),
            job.get("sender", ""),
            job.get("status", "Received"),
            "",  # customer name (staff fills)
            "",  # service type (staff fills)
            "",  # pages expected (staff fills)
            "",  # amount quoted (staff fills)
            "",  # amount collected (staff fills)
            "",  # payment mode (staff fills)
            "",  # notes
        ]
        sheet.append_row(row, value_input_option="USER_ENTERED")
        return True
    except Exception as e:
        logging.warning("Sheets sync failed: %s", e)
        return False

def ensure_sheets_headers():
    """Make sure the Google Sheet has headers."""
    client = get_sheets_client()
    if client is None:
        return
    try:
        sheet = client.open(GSHEETS_SPREADSHEET_NAME).worksheet(GSHEETS_WORKSHEET_NAME)
        if sheet.row_count < 1 or sheet.cell(1, 1).value != "Job ID":
            headers = [
                "Job ID", "Received At", "Filename", "Type", "Size (KB)",
                "Source", "Sender", "Status",
                "Customer Name", "Service", "Pages",
                "Quoted (₹)", "Collected (₹)", "Payment Mode", "Notes"
            ]
            sheet.insert_row(headers, 1)
            # Format header row
            sheet.format("A1:O1", {
                "backgroundColor": {"red": 0.106, "green": 0.247, "blue": 0.545},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
            })
    except Exception as e:
        logging.warning("Could not set headers: %s", e)

# ══════════════════════════════════════════════════════════════════════════════
# CORE: LOG A NEW FILE
# ══════════════════════════════════════════════════════════════════════════════

def log_new_file(filepath: str, source: str = "Hot Folder", sender: str = ""):
    """
    Called whenever a new file is detected.
    Creates a job record in SQLite and syncs to Google Sheets.
    """
    filepath = Path(filepath)

    # Ignore temp/system files
    filename_lower = filepath.name.lower()
    if any(filename_lower.startswith(p) or filename_lower.endswith(p)
           for p in IGNORE_PATTERNS):
        return

    # Only track known file types
    ext = filepath.suffix.lower()
    if ext not in TRACKED_EXTENSIONS:
        logging.debug("Skipping non-tracked file type: %s", filepath.name)
        return

    # Wait briefly for file to finish writing
    time.sleep(0.5)
    if not filepath.exists():
        return

    # Get file info
    try:
        size_kb = round(filepath.stat().st_size / 1024, 1)
    except Exception:
        size_kb = 0

    fhash = file_hash(str(filepath))

    # Check for duplicate (same hash already in DB today)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    if fhash:
        cursor.execute(
            "SELECT job_id FROM jobs WHERE file_hash = ? AND DATE(received_at) = DATE('now')",
            (fhash,)
        )
        existing = cursor.fetchone()
        if existing:
            logging.info("Duplicate file skipped (already logged as %s): %s",
                         existing[0], filepath.name)
            conn.close()
            return

    job_id = generate_job_id()
    received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    job = {
        "job_id": job_id,
        "received_at": received_at,
        "filename": filepath.name,
        "file_extension": ext.lstrip(".").upper(),
        "file_size_kb": size_kb,
        "file_hash": fhash,
        "source": source,
        "sender": sender,
        "status": "Received",
        "filepath": str(filepath),
    }

    # Insert into database
    cursor.execute("""
        INSERT INTO jobs (
            job_id, received_at, filename, file_extension,
            file_size_kb, file_hash, source, sender, status, filepath
        ) VALUES (
            :job_id, :received_at, :filename, :file_extension,
            :file_size_kb, :file_hash, :source, :sender, :status, :filepath
        )
    """, job)
    conn.commit()
    conn.close()

    logging.info("NEW JOB [%s] %s | %.1f KB | Source: %s | Sender: %s",
                 job_id, filepath.name, size_kb, source, sender or "walk-in")
    # Message 1: instant receipt confirmation, then start bot questions
    if sender:
        send_file_received(job_id, filepath.name, sender)
        import threading as _threading
        def _start_bot(jid=job_id, fp=filepath, s=sender):
            import time, sqlite3 as _sq3, logging as _log
            time.sleep(2)
            try:
                # Get page count from PDF or image (quick, no colour scan yet)
                pc = 0
                ext_lower = fp.suffix.lower()
                if ext_lower == ".pdf":
                    try:
                        import pikepdf
                        with pikepdf.open(str(fp)) as _pdf:
                            pc = len(_pdf.pages)
                        _log.info(f"PDF page count ({jid}): {pc} pages via pikepdf")
                    except Exception as _e:
                        _log.warning(f"pikepdf failed ({jid}): {_e} — trying fallback")
                        try:
                            from rate_card import get_pdf_page_count
                            pc = get_pdf_page_count(str(fp))
                            if pc > 0:
                                _log.info(f"PDF page count ({jid}): {pc} pages via fallback")
                            else:
                                _log.warning(f"PDF page count ({jid}): all methods returned 0 — check if pikepdf/pypdf installed")
                        except Exception as _e2:
                            _log.warning(f"PDF page count fallback also failed ({jid}): {_e2}")
                            pc = 0
                elif ext_lower in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"):
                    pc = 1  # single image = 1 page
                elif ext_lower in (".doc", ".docx", ".ppt", ".pptx"):
                    # Convert to PDF via Word/PowerPoint COM, then count pages with pikepdf
                    # Requires pywin32 (pip install pywin32) and MS Office on store PC
                    import tempfile, os as _os
                    tmp_pdf = _os.path.join(tempfile.gettempdir(), jid + "_pagecount.pdf")
                    try:
                        import win32com.client as _wc
                        if ext_lower in (".doc", ".docx"):
                            _app = _wc.Dispatch("Word.Application")
                            _app.Visible = False
                            _app.DisplayAlerts = False
                            _doc = _app.Documents.Open(str(fp), ReadOnly=True)
                            _doc.ExportAsFixedFormat(tmp_pdf, 17)  # 17 = wdExportFormatPDF
                            _doc.Close(False)
                            _app.Quit()
                        else:  # ppt/pptx
                            _app = _wc.Dispatch("PowerPoint.Application")
                            _prs = _app.Presentations.Open(str(fp), ReadOnly=True, WithWindow=False)
                            _prs.SaveAs(tmp_pdf, 32)  # 32 = ppSaveAsPDF
                            _prs.Close()
                            _app.Quit()
                        import pikepdf as _pk
                        with _pk.open(tmp_pdf) as _pdf:
                            pc = len(_pdf.pages)
                        _log.info(f"Word/PPT page count for {jid}: {pc} pages")
                    except Exception as _e:
                        _log.warning(f"Word/PPT page count failed ({jid}): {_e}")
                        pc = 0
                    finally:
                        try:
                            if _os.path.exists(tmp_pdf):
                                _os.remove(tmp_pdf)
                        except Exception:
                            pass
                if pc > 0:
                    c2 = _sq3.connect(DB_PATH)
                    c2.execute("UPDATE jobs SET page_count=? WHERE job_id=?", (pc, jid))
                    c2.commit(); c2.close()
                # Check if B2B client
                from b2b_manager import is_b2b, get_b2b_client
                from b2b_bot import handle_b2b_message
                from whatsapp_notify import _send
                if is_b2b(DB_PATH, s):
                    # B2B flow — greet by company name, skip retail questions
                    client = get_b2b_client(DB_PATH, s)
                    replies = handle_b2b_message(s, "", jid, client, DB_PATH)
                    for r in replies:
                        if isinstance(r, str):
                            _send(s, r)
                else:
                    # Retail flow — init bot session + ask step 1
                    c3 = _sq3.connect(DB_PATH)
                    c3.execute(
                        "INSERT INTO bot_sessions(phone,job_id,step,page_count,updated_at) "
                        "VALUES(?,?,'size',?,datetime('now')) "
                        "ON CONFLICT(phone) DO UPDATE SET job_id=excluded.job_id,"
                        "step='size',page_count=excluded.page_count,updated_at=excluded.updated_at",
                        (s, jid, pc)
                    )
                    c3.commit(); c3.close()
                    from whatsapp_bot import msg_step1_size
                    _send(s, msg_step1_size())
            except Exception as e:
                _log.warning(f"start_bot error: {e}")
        _threading.Thread(target=_start_bot, daemon=True).start()

    # Sync to Google Sheets (non-blocking)
    synced = sync_job_to_sheets(job)
    if synced:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE jobs SET synced_to_sheets = 1 WHERE job_id = ?", (job_id,))
        conn.commit()
        conn.close()

    # Print to console so staff can see a job was registered
    print(f"\n{'='*55}")
    print(f"  NEW JOB REGISTERED: {job_id}")
    print(f"  File   : {filepath.name}")
    print(f"  Size   : {size_kb} KB")
    print(f"  Time   : {received_at}")
    print(f"  Source : {source}")
    if sender:
        print(f"  From   : {sender}")
    print(f"{'='*55}\n")

# ══════════════════════════════════════════════════════════════════════════════
# FOLDER WATCHER
# ══════════════════════════════════════════════════════════════════════════════

class JobFolderHandler(FileSystemEventHandler):
    """Watchdog event handler for the hot folder."""

    def on_created(self, event):
        if event.is_directory:
            return
        # Ignore .sender sidecar files
        if event.src_path.endswith(".sender"):
            return
        logging.info("File detected: %s", event.src_path)
        # Read sender phone from sidecar file if present
        sender = ""
        sender_file = event.src_path + ".sender"
        try:
            import time as _t
            _t.sleep(0.3)  # give Node time to write sidecar
            with open(sender_file, "r") as f:
                sender = f.read().strip()
            import os as _os
            _os.remove(sender_file)  # clean up sidecar
        except Exception:
            pass
        log_new_file(event.src_path, source="Hot Folder", sender=sender)

    def on_moved(self, event):
        """Catches files moved/renamed into the folder."""
        if event.is_directory:
            return
        if event.dest_path.endswith(".sender"):
            return
        logging.info("File moved in: %s", event.dest_path)
        sender = ""
        sender_file = event.dest_path + ".sender"
        try:
            import time as _t
            _t.sleep(0.3)
            with open(sender_file, "r") as f:
                sender = f.read().strip()
            import os as _os
            _os.remove(sender_file)
        except Exception:
            pass
        log_new_file(event.dest_path, source="Hot Folder", sender=sender)

# ══════════════════════════════════════════════════════════════════════════════
# DAILY REPORT (printed to console at end of day)
# ══════════════════════════════════════════════════════════════════════════════

def print_daily_report():
    """Print today's job summary."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")

    cursor.execute("""
        SELECT
            COUNT(*) as total,
            COUNT(CASE WHEN status = 'Completed' THEN 1 END) as completed,
            COUNT(CASE WHEN status = 'Received' THEN 1 END) as pending,
            COALESCE(SUM(amount_collected), 0) as revenue
        FROM jobs
        WHERE DATE(received_at) = ?
    """, (today,))
    row = cursor.fetchone()
    conn.close()

    if row:
        total, completed, pending, revenue = row
        print(f"\n{'='*55}")
        print(f"  PRINTOSKY DAILY REPORT — {today}")
        print(f"{'='*55}")
        print(f"  Total jobs received : {total}")
        print(f"  Completed           : {completed}")
        print(f"  PENDING (not done)  : {pending}  ← action needed")
        print(f"  Revenue logged      : ₹{revenue:.2f}")
        print(f"{'='*55}\n")

def print_pending_jobs():
    """Show all jobs that are Received but not Completed."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT job_id, received_at, filename, source, sender
        FROM jobs
        WHERE status IN ('Received', 'In Progress', 'Printed')
        ORDER BY received_at ASC
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        print("\n✅ No pending jobs.\n")
        return

    print(f"\n{'='*55}")
    print(f"  PENDING JOBS ({len(rows)} total)")
    print(f"{'='*55}")
    for r in rows:
        job_id, received_at, filename, source, sender = r
        age_str = ""
        try:
            received = datetime.strptime(received_at, "%Y-%m-%d %H:%M:%S")
            minutes = int((datetime.now() - received).total_seconds() / 60)
            if minutes < 60:
                age_str = f"{minutes}m ago"
            else:
                age_str = f"{minutes // 60}h {minutes % 60}m ago"
        except Exception:
            pass
        print(f"  [{job_id}] {filename[:35]:<35} {age_str}")
        if sender:
            print(f"           From: {sender}")
    print(f"{'='*55}\n")

# ══════════════════════════════════════════════════════════════════════════════
# STAFF COMMANDS (type in terminal while watcher is running)
# ══════════════════════════════════════════════════════════════════════════════

def handle_command(cmd: str):
    """Handle simple terminal commands from staff."""
    parts = cmd.strip().split()
    if not parts:
        return

    if parts[0] == "pending":
        print_pending_jobs()

    elif parts[0] == "report":
        print_daily_report()

    elif parts[0] == "done" and len(parts) >= 2:
        job_id = parts[1].upper()
        amount = float(parts[2]) if len(parts) >= 3 else None
        mode = parts[3].upper() if len(parts) >= 4 else "Cash"
        conn = sqlite3.connect(DB_PATH)
        if amount:
            conn.execute("""
                UPDATE jobs SET status='Completed', completed_at=?,
                amount_collected=?, payment_mode=?
                WHERE job_id=?
            """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), amount, mode, job_id))
        else:
            conn.execute("""
                UPDATE jobs SET status='Completed', completed_at=?
                WHERE job_id=?
            """, (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), job_id))
        conn.commit()
        conn.close()
        print(f"\n✅ Job {job_id} marked as COMPLETED")
        if amount:
            print(f"   Payment: ₹{amount:.2f} via {mode}\n")
        # Send ready alert to customer via WhatsApp
        send_ready_alert(job_id, DB_PATH)

    elif parts[0] == "paid" and len(parts) >= 2:
        job_id = parts[1].upper()
        amount = float(parts[2]) if len(parts) >= 3 else None
        mode   = parts[3].capitalize() if len(parts) >= 4 else "Cash"
        conn   = sqlite3.connect(DB_PATH)
        row    = conn.execute("SELECT sender, filename FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if amount:
            conn.execute(
                "UPDATE jobs SET status='Paid', amount_quoted=?, payment_mode=? WHERE job_id=?",
                (amount, mode, job_id)
            )
        else:
            conn.execute("UPDATE jobs SET status='Paid' WHERE job_id=?", (job_id,))
        conn.commit()
        conn.close()
        print(f"\n\U0001f49a Job {job_id} — PAYMENT CONFIRMED, proceed to print")
        if amount:
            print(f"   Payment: \u20b9{amount:.2f} via {mode}")
        print(f"   When done printing, type: done {job_id}\n")
        if row and row[0]:
            from whatsapp_notify import send_payment_confirmed
            send_payment_confirmed(job_id, row[0], amount or 0, mode)

    elif parts[0] == "quote" and len(parts) >= 3:
        # quote OSP-xxx 350  — staff sets binding quote, generates payment link
        job_id = parts[1].upper()
        total  = float(parts[2])
        conn   = sqlite3.connect(DB_PATH)
        row    = conn.execute(
            "SELECT sender, filename FROM jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        conn.close()
        if not row:
            print(f"\n❌ Job {job_id} not found\n")
        else:
            sender, filename = row
            print(f"\n📋 Generating payment link for {job_id} — ₹{total:.2f}")
            try:
                from razorpay_integration import create_payment_link
                from whatsapp_notify import _send
                pay = create_payment_link(
                    job_id=job_id, amount=total,
                    description=f"Print job {job_id} — Printosky",
                    customer_phone=sender,
                )
                if "error" in pay:
                    print(f"   ❌ Razorpay error: {pay['error']}\n")
                else:
                    print(f"   ✅ Payment link: {pay['url']}")
                    if sender:
                        from rate_card import FINISHING_RATES
                        conn2 = sqlite3.connect(DB_PATH)
                        fin   = conn2.execute(
                            "SELECT finishing FROM bot_sessions WHERE job_id=?", (job_id,)
                        ).fetchone()
                        conn2.close()
                        fin_label = FINISHING_RATES.get(fin[0] if fin else "none", {}).get("label", "Binding")
                        msg = (
                            f"💳 *Payment link for {job_id}*\n\n"
                            f"Total: ₹{total:.2f} (includes {fin_label})\n\n"
                            f"👉 {pay['url']}\n\n"
                            f"_Printing starts once payment is confirmed!_ 🖨️"
                        )
                        from whatsapp_notify import _send
                        _send(sender, msg)
                        print(f"   💬 Payment link sent to customer\n")
            except Exception as e:
                print(f"   ❌ Error: {e}\n")

    # ── B2B: register client ─────────────────────────────────────────────────
    elif parts[0] == "b2b" and len(parts) >= 2:
        sub = parts[1] if len(parts) > 1 else ""

        if sub == "add" and len(parts) >= 4:
            # b2b add <phone> "<company>" "<contact>" <discount%>
            # Parse quoted args properly
            import shlex
            try:
                args = shlex.split(" ".join(parts[2:]))
                phone_b2b = args[0]
                company   = args[1] if len(args) > 1 else "Unknown"
                contact   = args[2] if len(args) > 2 else ""
                disc      = float(args[3]) if len(args) > 3 else 0.0
                msg = register_b2b_client(DB_PATH, phone_b2b, company, contact, disc)
                print(f"\n{msg}\n")
            except Exception as e:
                print(f"\n❌ Error: {e}")
                print('Usage: b2b add <phone> "Company Name" "Contact" <discount%>\n')

        elif sub == "list":
            print(f"\n{list_b2b_clients(DB_PATH)}\n")

        elif sub == "jobs" and len(parts) >= 3:
            print(f"\n{print_b2b_jobs(DB_PATH, parts[2])}\n")

        elif sub == "credit" and len(parts) >= 4:
            msg = set_credit_limit(DB_PATH, parts[2], float(parts[3]))
            print(f"\n{msg}\n")

        elif sub == "paid" and len(parts) >= 5:
            # b2b paid <phone> <amount> <mode> [reference]
            ref = parts[5] if len(parts) > 5 else ""
            msg = record_payment(DB_PATH, parts[2], float(parts[3]), parts[4], ref)
            print(f"\n{msg}\n")

        else:
            print("""
  B2B Commands:
  b2b add <phone> "Company" "Contact" <disc%>  → register client
  b2b list                                      → all B2B clients
  b2b jobs <phone>                              → jobs for client
  b2b credit <phone> <amount>                   → set credit limit
  b2b paid <phone> <amount> <NEFT|IMPS|CASH>   → record payment
""")

    # ── Invoice: generate + send PDF via WhatsApp ─────────────────────────────
    elif parts[0] == "invoice" and len(parts) >= 2:
        phone_inv = parts[1]
        preview   = len(parts) >= 3 and parts[2] == "preview"
        client_inv = get_b2b_client(DB_PATH, phone_inv)
        if not client_inv:
            print(f"\n❌ No B2B client found for {phone_inv}\n")
        else:
            print(f"\n📄 Generating invoice for {client_inv['company_name']}...")

            try:
                pdf_path, grand_total, job_count, inv_num = generate_invoice_pdf(DB_PATH, phone_inv)
                print(f"   ✅ Invoice: {pdf_path}")
                print(f"   Jobs: {job_count}  |  Total: ₹{grand_total:.2f}  |  Ref: {inv_num}")
                if not preview:
                    # Send via WhatsApp
                    try:
                        import requests as _req
                        with open(pdf_path, "rb") as pf:
                            _resp = _req.post(
                                "http://localhost:3004/send-document",
                                files={"file": (f"{inv_num}.pdf", pf, "application/pdf")},
                                data={"phone": phone_inv, "caption": f"📄 Invoice {inv_num} — \u20b9{grand_total:.2f}\nThank you for your business! 🙏"},
                                timeout=15,
                            )
                        if _resp.status_code == 200:
                            print(f"   💬 Invoice sent to {client_inv['company_name']} via WhatsApp")
                            mark_jobs_invoiced(DB_PATH, phone_inv, inv_num)
                            print(f"   ✅ Jobs marked as invoiced\n")

                        else:
                            print(f"   ⚠️ WhatsApp send failed ({_resp.status_code}) — PDF saved at {pdf_path}\n")

                    except Exception as e:
                        print(f"   ⚠️ WhatsApp send error: {e}")
                        print(f"   PDF saved at: {pdf_path}\n")

                else:
                    print(f"   (Preview only — not sent)\n")

            except ValueError as e:
                print(f"\n❌ {e}\n")
            except Exception as e:
                print(f"\n❌ Invoice generation error: {e}\n")

    elif parts[0] == "help":
        print("""
PRINTOSKY WATCHER — Commands
─────────────────────────────
pending          → Show all pending (unfinished) jobs
report           → Today's summary
b2b add/list/jobs/credit/paid → Manage B2B clients
invoice <phone>             → Generate + send monthly invoice PDF
quote JOB# AMOUNT           → Send Razorpay link (binding/timeout)
                              e.g: quote OSP-20260313-0001 350
paid JOB# AMT MODE          → Manual cash/UPI payment
                              e.g: paid OSP-20260313-0001 150 UPI
done JOB#                   → Mark job complete
                              e.g: done OSP-20260313-0001
help             → Show this help
        """)
    else:
        print("Unknown command. Type 'help' for options.")

# ══════════════════════════════════════════════════════════════════════════════
# BOT RELAY SERVER (port 3003)
# Receives customer text replies from Node/WhatsApp and routes through bot
# ══════════════════════════════════════════════════════════════════════════════

def start_bot_relay_server(db_path):
    """HTTP server on port 3003 — Node posts customer text replies here."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import json as _json

    class BotRelayHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # silence default HTTP logs

        def do_POST(self):
            if self.path != "/bot":
                self.send_response(404); self.end_headers(); return
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                data = _json.loads(body)
                phone = data.get("phone", "").strip()
                text  = data.get("text", "").strip()

                if not phone or not text:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(_json.dumps({"replies": []}).encode())
                    return

                # Route through bot — look up session for job_id + page_count
                from whatsapp_bot import handle_message, get_session
                from b2b_manager import is_b2b, get_b2b_client
                from b2b_bot import handle_b2b_message

                if is_b2b(db_path, phone):
                    client = get_b2b_client(db_path, phone)
                    replies = handle_b2b_message(phone, text, None, client, db_path)
                else:
                    session = get_session(db_path, phone)
                    job_id_s = session.get("job_id") or ""
                    page_count_s = session.get("page_count") or 0
                    replies = handle_message(phone, text, job_id_s, page_count_s, db_path)

                # Return replies as JSON — Node's sendBotReply will send them via WhatsApp
                # (do NOT also call _send here — that would cause duplicate messages)
                reply_list = [r for r in (replies or []) if isinstance(r, str)]

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(_json.dumps({"replies": reply_list}).encode())

            except Exception as e:
                logging.warning(f"Bot relay error: {e}")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(_json.dumps({"replies": [], "error": str(e)}).encode())

    def _run():
        server = HTTPServer(("127.0.0.1", 3003), BotRelayHandler)
        logging.info("Bot relay server started — listening on :3003/bot")
        server.serve_forever()

    t = threading.Thread(target=_run, daemon=True, name="BotRelay")
    t.start()
    return t

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    setup_folders()
    setup_logging()
    setup_database()
    setup_bot_db(DB_PATH)
    setup_b2b_db(DB_PATH)
    # Reset active session timestamps so old sessions don't immediately time out on restart
    try:
        import sqlite3 as _sq3
        c = _sq3.connect(DB_PATH)
        c.execute("UPDATE bot_sessions SET updated_at=datetime('now') WHERE step NOT IN ('done','timed_out')")
        c.commit(); c.close()
    except Exception:
        pass
    start_timeout_monitor(DB_PATH)
    start_webhook_server(DB_PATH)
    start_bot_relay_server(DB_PATH)

    # Start print server (port 3005)
    try:
        from print_server import start_print_server as _start_print_server
        import threading as _ps_thread
        _ps_thread.Thread(target=_start_print_server, daemon=True, name="PrintServer").start()
        logging.info("Print server started on port 3005")
    except Exception as _ps_err:
        logging.warning("Print server not started: %s", _ps_err)

    ensure_sheets_headers()

    logging.info("="*55)
    logging.info("PRINTOSKY JOB WATCHER STARTED")
    logging.info("Watching: %s", WATCH_FOLDER)
    logging.info("Database: %s", DB_PATH)
    logging.info("="*55)

    print(f"""
╔══════════════════════════════════════════════════╗
║         PRINTOSKY JOB WATCHER — RUNNING          ║
║                                                  ║
║  Watching: {WATCH_FOLDER[:38]:<38}  ║
║                                                  ║
║  Every file dropped here is automatically        ║
║  logged with a job ID and timestamp.             ║
║                                                  ║
║  Type 'help' for commands. Ctrl+C to stop.       ║
╚══════════════════════════════════════════════════╝
""")

    # Print pending jobs from previous session
    print_pending_jobs()

    # Start folder watcher
    event_handler = JobFolderHandler()
    observer = Observer()
    observer.schedule(event_handler, WATCH_FOLDER, recursive=False)
    observer.start()

    # Phase 3: start printer counter poller in background
    if PRINTER_POLLER_AVAILABLE:
        _start_printer_poller(DB_PATH)
        logging.info("Printer poller started (Konica + Epson)")
    else:
        logging.info("Printer poller not loaded (printer_poller.py missing)")

    # Supabase sync — admin page on printosky.com
    if SUPABASE_SYNC_AVAILABLE:
        _start_supabase_sync(DB_PATH)
    else:
        logging.info("Supabase sync not loaded (supabase_sync.py missing)")

    # Load live rate card from Supabase (falls back to hardcoded if unreachable)
    try:
        from rate_card import load_rates_from_supabase as _load_rates
        from supabase_sync import SUPABASE_URL as _SB_URL, SUPABASE_KEY as _SB_KEY
        ok = _load_rates(_SB_URL, _SB_KEY)
        logging.info("Rate card: %s", "loaded from Supabase" if ok else "using hardcoded defaults")
    except Exception as _rc_err:
        logging.warning("Rate card load skipped: %s", _rc_err)

    try:
        while True:
            cmd = input("> ").strip()
            if cmd:
                handle_command(cmd)
    except KeyboardInterrupt:
        logging.info("Watcher stopped by user")
        print("\nWatcher stopped.")
    finally:
        observer.stop()
        observer.join()

if __name__ == "__main__":
    main()
