"""
PRINTOSKY BOT SESSION TIMEOUT MONITOR
=======================================
Runs in background. Checks for WhatsApp bot sessions where the
customer has gone silent (no reply in TIMEOUT_MINUTES).

When a session times out:
  1. Logs a console alert for staff
  2. Sends staff a WhatsApp alert with job details
  3. Marks the session as timed out (removes it so customer can restart)
  4. Customer gets a gentle follow-up message

Staff can then:
  - Call the customer
  - Manually quote via: quote OSP-xxx 150
  - Or wait for customer to message again (bot restarts automatically)
"""

import sys
import time
import sqlite3
import logging
import threading
from datetime import datetime, timedelta

# Ensure stdout can handle Unicode/emoji on Windows
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

logger = logging.getLogger("session_timeout")

TIMEOUT_MINUTES  = 15    # flag session if no reply for this long
CHECK_INTERVAL_S = 60    # check every 60 seconds
STORE_PHONE      = "919446903907"   # Printosky brand number (AiSensy)

def _get_timed_out_sessions(db_path: str, timeout_minutes: int) -> list:
    """Return bot sessions that haven't been updated in timeout_minutes."""
    try:
        cutoff = (datetime.now() - timedelta(minutes=timeout_minutes)).strftime("%Y-%m-%d %H:%M:%S")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT b.phone, b.job_id, b.step, b.updated_at,
                   j.filename, j.filepath
            FROM bot_sessions b
            LEFT JOIN jobs j ON j.job_id = b.job_id
            WHERE b.step != 'done'
              AND b.step != 'timed_out'
              AND b.updated_at < ?
        """, (cutoff,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"timeout check error: {e}")
        return []

def _mark_timed_out(db_path: str, phone: str):
    """Mark session as timed out so it doesn't fire again."""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE bot_sessions SET step='timed_out', updated_at=datetime('now') WHERE phone=?",
            (phone,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"mark timed_out error: {e}")

def _handle_timeout(session: dict, db_path: str):
    """Handle a single timed-out session."""
    phone    = session["phone"]
    job_id   = session["job_id"] or "Unknown"
    step     = session["step"]
    filename = session["filename"] or "Unknown file"

    # Map step to human readable
    step_labels = {
        "size":          "Paper size",
        "colour":        "Colour/B&W",
        "layout":        "Single/Double/Multi-up",
        "multiup_per":   "Multi-up pages per sheet",
        "multiup_sided": "Multi-up sided",
        "copies":        "Number of copies",
        "finishing":     "Finishing option",
        "delivery":      "Delivery/Pickup",
    }
    stopped_at = step_labels.get(step, step)
    phone_display = phone.replace("@c.us", "").replace("91", "+91 ", 1)

    logger.warning(f"Session timeout: {job_id} from {phone_display} — stopped at '{stopped_at}'")

    # Console alert for staff
    try:
        print(f"\n{'─'*52}")
        print(f"  [TIMEOUT] BOT TIMEOUT - CUSTOMER WENT SILENT")
        print(f"  Job    : {job_id}")
        print(f"  File   : {filename}")
        print(f"  Phone  : {phone_display}")
        print(f"  Stopped: {stopped_at}")
        print(f"  Action : Call customer or type: quote {job_id} <amount>")
        print(f"{'─'*52}\n")
    except Exception:
        logger.warning(f"[TIMEOUT] {job_id} from {phone_display} stopped at {stopped_at}")

    # Send follow-up to customer
    try:
        from whatsapp_notify import _send
        customer_msg = (
            f"👋 *Hi! Just checking in on your print order.*\n\n"
            f"📋 Job ID: `{job_id}`\n"
            f"📄 File: {filename}\n\n"
            f"We noticed you didn't complete your order details.\n"
            f"Reply here to continue, or call us at *9446903907*.\n\n"
            f"_— Printosky / Oxygen Globally_ 🖨️"
        )
        _send(phone, customer_msg)
        logger.info(f"Follow-up sent to {phone_display}")
    except Exception as e:
        logger.warning(f"Follow-up send error: {e}")

    # Alert staff on WhatsApp
    try:
        from whatsapp_notify import _send
        staff_msg = (
            f"⏰ *Bot timeout — customer went silent*\n\n"
            f"📋 Job: {job_id}\n"
            f"📄 File: {filename}\n"
            f"📞 Customer: {phone_display}\n"
            f"🔴 Stopped at: {stopped_at}\n\n"
            f"Options:\n"
            f"• Call customer\n"
            f"• Type: `quote {job_id} <amount>` to send payment link manually"
        )
        _send(STORE_PHONE + "@c.us", staff_msg)
    except Exception as e:
        logger.debug(f"Staff alert error: {e}")

    # Mark as timed out so it doesn't re-trigger
    _mark_timed_out(db_path, phone)

def start_timeout_monitor(db_path: str,
                          timeout_minutes: int = TIMEOUT_MINUTES,
                          check_interval: int = CHECK_INTERVAL_S):
    """Start the timeout monitor in a background thread."""
    def loop():
        logger.info(f"Session timeout monitor started — {timeout_minutes}min timeout, checking every {check_interval}s")
        while True:
            try:
                sessions = _get_timed_out_sessions(db_path, timeout_minutes)
                for s in sessions:
                    _handle_timeout(s, db_path)
            except Exception as e:
                logger.warning(f"Timeout monitor error: {e}")
            time.sleep(check_interval)

    t = threading.Thread(target=loop, daemon=True, name="SessionTimeoutMonitor")
    t.start()
    return t
