"""
PRINTOSKY WHATSAPP BOT
========================
Manages multi-step conversation flow for print orders.

Flow (single file / batch of 1):
  File received → 60s wait → start_batch_conversation()
  → Step 1 (size) → Step 2 (colour) → Step 3 (layout)
  → Step 3b (multiup options if needed) → Step 4 (copies)
  → Step 5 (finishing) → Step 6 (delivery)
  → Calculate → Generate Razorpay link → Send to customer

Flow (multi-file batch):
  Files received → 60s wait → start_batch_conversation()
  → For each file: batch_confirm (saved?) or size→colour→layout→copies→finishing
  → Delivery asked ONCE → Combined Razorpay link → All jobs marked Paid on payment

State is stored in SQLite (bot_sessions table) so it survives restarts.

Staff hybrid commands (when bot can't quote):
  quote OSP-xxx 250   → set binding quote, generate payment link, send to customer
"""

import sqlite3
import logging
import json
import os
from datetime import datetime

logger = logging.getLogger("whatsapp_bot")

DB_PATH = os.environ.get("PRINTOSKY_DB", r"C:\Printosky\Data\jobs.db")

# ── Session state keys ────────────────────────────────────────────────────────
STEPS = ["size", "colour", "layout", "multiup_per", "multiup_sided", "copies", "finishing", "delivery"]

# ── DB setup ──────────────────────────────────────────────────────────────────
def setup_bot_db(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_sessions (
            phone           TEXT PRIMARY KEY,
            job_id          TEXT,
            step            TEXT,
            size            TEXT,
            colour          TEXT,
            layout          TEXT,
            multiup_per     TEXT,
            multiup_sided   TEXT,
            copies          INTEGER,
            finishing       TEXT,
            delivery        INTEGER DEFAULT 0,
            page_count      INTEGER DEFAULT 0,
            updated_at      TEXT,
            batch_id        TEXT,
            current_job_index INTEGER DEFAULT 0,
            jobs_json       TEXT,
            saved_json      TEXT,
            job_settings_json TEXT DEFAULT '{}'
        )
    """)
    # Add new columns to existing tables (safe — skips if already present)
    for col, typedef in [
        ("batch_id",            "TEXT"),
        ("current_job_index",   "INTEGER DEFAULT 0"),
        ("jobs_json",           "TEXT"),
        ("saved_json",          "TEXT"),
        ("job_settings_json",   "TEXT DEFAULT '{}'"),
    ]:
        try:
            conn.execute(f"ALTER TABLE bot_sessions ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    conn.commit()
    conn.close()


def get_session(db_path: str, phone: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM bot_sessions WHERE phone=?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else {}


def save_session(db_path: str, phone: str, **kwargs):
    conn = sqlite3.connect(db_path)
    kwargs["phone"] = phone
    kwargs["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(f":{k}" for k in kwargs.keys())
    conn.execute(f"""
        INSERT INTO bot_sessions ({cols}) VALUES ({placeholders})
        ON CONFLICT(phone) DO UPDATE SET
        {', '.join(f"{k}=excluded.{k}" for k in kwargs.keys() if k != 'phone')}
    """, kwargs)
    conn.commit()
    conn.close()


def clear_session(db_path: str, phone: str):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM bot_sessions WHERE phone=?", (phone,))
    conn.commit()
    conn.close()


# ── Message builders ──────────────────────────────────────────────────────────
def msg_step1_size():
    return (
        "📄 *What paper size do you need?*\n\n"
        "1️⃣  A4 (standard)\n"
        "2️⃣  A3 (large)\n"
        "3️⃣  Other (we'll quote manually)\n\n"
        "_Reply with 1, 2, or 3_"
    )

def msg_step2_colour():
    return (
        "🎨 *Colour or Black & White?*\n\n"
        "1️⃣  Black & White (B&W)\n"
        "2️⃣  Colour\n"
        "3️⃣  Mixed (some pages colour, some B&W)\n\n"
        "_Reply with 1, 2, or 3_"
    )

def msg_step3_layout():
    return (
        "📋 *How would you like the pages printed?*\n\n"
        "1️⃣  Single side (one page per sheet side)\n"
        "2️⃣  Double side (front & back)\n"
        "3️⃣  Multiple-up (2/4/6/9 pages per sheet)\n\n"
        "_Reply with 1, 2, or 3_"
    )

def msg_step3b_multiup():
    return (
        "📐 *How many pages per sheet?*\n\n"
        "1️⃣  2-up (2 pages per sheet)\n"
        "2️⃣  4-up (4 pages per sheet)\n"
        "3️⃣  6-up (6 pages per sheet)\n"
        "4️⃣  9-up (9 pages per sheet)\n\n"
        "_Reply with 1, 2, 3, or 4_"
    )

def msg_step3c_multiup_sided():
    return (
        "📄 *Should the sheet be printed on:*\n\n"
        "1️⃣  Single side only\n"
        "2️⃣  Both sides (double sided)\n\n"
        "_Reply with 1 or 2_"
    )

def msg_step4_copies():
    return (
        "🔢 *How many copies do you need?*\n\n"
        "_Reply with a number (e.g. 1, 2, 5...)_"
    )

def msg_step5_finishing():
    return (
        "📎 *Finishing options:*\n\n"
        "1️⃣  None / Corner staple _(free)_\n"
        "2️⃣  Side staple _(₹5)_\n"
        "3️⃣  Spiral binding _(from ₹30)_\n"
        "4️⃣  Wiro binding _(from ₹50)_\n"
        "5️⃣  Soft binding _(₹80+)_\n"
        "6️⃣  Project binding _(₹200+)_\n"
        "7️⃣  Hard binding _(₹150+)_\n"
        "8️⃣  Record binding _(₹150)_\n"
        "9️⃣  Thesis binding _(₹500)_\n\n"
        "_Reply with a number (1–9)_"
    )

def msg_step6_delivery():
    return (
        "🚚 *Delivery or pickup?*\n\n"
        "1️⃣  Collect at store _(free)_\n"
        "2️⃣  Delivery to my address _(+₹30)_\n\n"
        "_Reply with 1 or 2_"
    )

def msg_staff_quote_needed(job_id: str, finishing_label: str, print_cost: float):
    return (
        f"📋 *New quote needed — {job_id}*\n\n"
        f"Print cost: ₹{print_cost:.2f}\n"
        f"Finishing: {finishing_label} (staff to quote)\n\n"
        f"Type: `quote {job_id} <total_amount>`\n"
        f"e.g. `quote {job_id} 350`"
    )

def msg_payment_link(job_id: str, breakdown: str, pay_url: str, expires_min: int = 60):
    return (
        f"✅ *Order Summary*\n\n"
        f"📋 Job ID: `{job_id}`\n\n"
        f"{breakdown}\n\n"
        f"💳 *Pay now to start printing:*\n"
        f"👉 {pay_url}\n\n"
        f"_Link expires in {expires_min} minutes._\n"
        f"Printing begins the moment payment is received! 🖨️"
    )

def msg_other_size(job_id: str):
    return (
        f"📋 Job ID: `{job_id}`\n\n"
        f"Your file has been received. Since you need a non-standard size, "
        f"our staff will review and send you a quote shortly.\n\n"
        f"📞 Questions? Call 80896 99436"
    )

# ── Batch message builders ────────────────────────────────────────────────────
def msg_batch_confirm(job_index: int, total_jobs: int, filename: str, page_count: int, saved: dict) -> str:
    file_label = f"File {job_index + 1}" + (f" of {total_jobs}" if total_jobs > 1 else "")
    size_label     = "A4" if saved["size"] == "A4" else "A3"
    colour_label   = "B&W" if saved["colour"] == "bw" else "Colour"
    layout_label   = {"single": "Single side", "double": "Double side"}.get(saved["layout"], saved["layout"])
    finishing_label = {"none": "No binding", "staple": "Staple"}.get(saved["finishing"], saved["finishing"].title())
    delivery_label = "Delivery" if saved["delivery"] else "Pickup"
    copies = saved["copies"]
    return (
        f"📄 *{file_label}: {filename}* ({page_count} pages)\n\n"
        f"Last time you printed:\n"
        f"  {size_label} · {colour_label} · {layout_label} · "
        f"{copies} cop{'y' if copies == 1 else 'ies'} · {finishing_label} · {delivery_label}\n\n"
        f"Reply *1* to use same settings\n"
        f"Reply *2* for different settings"
    )

def msg_batch_file_header(job_index: int, total_jobs: int, filename: str, page_count: int) -> str:
    """Shown before asking step 1 when no saved settings or customer chose new settings."""
    if total_jobs == 1:
        return ""  # single file, no header needed
    return f"📄 *File {job_index + 1} of {total_jobs}: {filename}* ({page_count} pages)\n"

def msg_batch_summary(jobs_with_settings: list, delivery: bool, total: float, pay_url: str) -> str:
    lines = ["*Order Summary* 🧾\n"]
    for i, j in enumerate(jobs_with_settings):
        lines.append(f"{i+1}. {j['filename']} — {j['breakdown_short']} — ₹{j['amount']:.2f}")
    if delivery:
        lines.append("Delivery — ₹30.00")
    lines.append(f"\n*Total: ₹{total:.2f}*")
    lines.append(f"\nPay here to confirm all {len(jobs_with_settings)} job(s) 👇")
    lines.append(pay_url)
    return "\n".join(lines)


# ── Input parsers ─────────────────────────────────────────────────────────────
SIZE_MAP = {"1": "A4", "2": "A3", "3": "other"}
COLOUR_MAP = {"1": "bw", "2": "col", "3": "mixed"}
LAYOUT_MAP = {"1": "single", "2": "double", "3": "multiup"}
MULTIUP_MAP = {"1": "2up", "2": "4up", "3": "6up", "4": "9up"}
SIDED_MAP = {"1": "single", "2": "double"}
FINISHING_MAP = {
    "1": "none", "2": "staple", "3": "spiral", "4": "wiro",
    "5": "soft", "6": "project", "7": "hard", "8": "record", "9": "thesis"
}


# ── Customer profile ──────────────────────────────────────────────────────────
def save_customer_profile(phone: str, settings: dict, db_path: str):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO customer_profiles(phone, last_size, last_colour, last_layout,
            last_copies, last_finishing, last_delivery, last_multiup_sided, updated_at)
        VALUES(?,?,?,?,?,?,?,?,datetime('now'))
        ON CONFLICT(phone) DO UPDATE SET
            last_size=excluded.last_size, last_colour=excluded.last_colour,
            last_layout=excluded.last_layout, last_copies=excluded.last_copies,
            last_finishing=excluded.last_finishing, last_delivery=excluded.last_delivery,
            last_multiup_sided=excluded.last_multiup_sided,
            updated_at=excluded.updated_at
    """, (
        phone, settings["size"], settings["colour"], settings["layout"],
        settings["copies"], settings["finishing"], int(settings["delivery"]),
        settings.get("sided", "single"),
    ))
    conn.commit()
    conn.close()


# ── Batch helpers ─────────────────────────────────────────────────────────────
def _layout_short(layout: str) -> str:
    """Human label for layout in batch summary breakdown_short."""
    return {"single": "Single", "double": "Double"}.get(layout, layout)


def _ensure_job_columns(conn):
    """Add size/colour/layout/delivery columns to jobs if missing."""
    for col, typedef in [("size", "TEXT"), ("colour", "TEXT"),
                         ("layout", "TEXT"), ("delivery", "INTEGER")]:
        try:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {typedef}")
        except Exception:
            pass


def _build_job_settings(session: dict, job: dict) -> dict:
    """Build a settings dict for one job using current session fields."""
    layout = session.get("layout") or "single"
    if layout == "multiup":
        layout = session.get("multiup_per") or "2up"
    sided = session.get("multiup_sided") or "single"
    return {
        "size":       session.get("size") or "A4",
        "colour":     session.get("colour") or "bw",
        "layout":     layout,
        "sided":      sided,
        "copies":     session.get("copies") or 1,
        "finishing":  session.get("finishing") or "none",
        "page_count": job["page_count"],
        "filename":   job["filename"],
        "job_id":     job["job_id"],
    }


def _build_job_settings_from_saved(saved: dict, job: dict) -> dict:
    """Build a settings dict for one job using saved profile settings."""
    layout = saved["layout"]
    sided  = saved.get("multiup_sided") or "single"
    return {
        "size":       saved["size"],
        "colour":     saved["colour"],
        "layout":     layout,
        "sided":      sided,
        "copies":     saved["copies"],
        "finishing":  saved["finishing"],
        "page_count": job["page_count"],
        "filename":   job["filename"],
        "job_id":     job["job_id"],
    }


def _update_job_quote_db(job_id: str, amount_quoted: float, copies: int,
                         finishing: str, size: str, colour: str, layout: str,
                         db_path: str) -> None:
    """Persist quoted price and settings onto a jobs row (SQLite path)."""
    conn = sqlite3.connect(db_path)
    _ensure_job_columns(conn)
    conn.execute(
        "UPDATE jobs SET amount_quoted=?, copies=?, finishing=?, size=?, colour=?, layout=? WHERE job_id=?",
        (amount_quoted, copies, finishing, size, colour, layout, job_id)
    )
    conn.commit()
    conn.close()


def _update_jobs_delivery_db(job_ids: list, delivery: int, db_path: str) -> None:
    """Set delivery flag on a list of jobs (SQLite path)."""
    conn = sqlite3.connect(db_path)
    for jid in job_ids:
        conn.execute("UPDATE jobs SET delivery=? WHERE job_id=?", (delivery, jid))
    conn.commit()
    conn.close()


def _update_batch_payment_db(batch_id: str, total: float, link_id: str,
                              link_sent_at: str, job_ids: list, db_path: str) -> None:
    """Record Razorpay link on batch + all its jobs (SQLite path)."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE job_batches SET total_amount=?, razorpay_link_id=?, link_sent_at=?, "
        "status='awaiting_payment' WHERE batch_id=?",
        (total, link_id, link_sent_at, batch_id)
    )
    for jid in job_ids:
        conn.execute(
            "UPDATE jobs SET razorpay_link_id=?, link_sent_at=? WHERE job_id=?",
            (link_id, link_sent_at, jid)
        )
    conn.commit()
    conn.close()


def _get_pending_review_db(phone: str, db_path: str):
    """Return a pending review row for the customer, or None (SQLite path)."""
    conn = sqlite3.connect(db_path)
    try:
        from review_manager import setup_review_db
        setup_review_db(conn)
        return conn.execute(
            "SELECT id FROM job_reviews WHERE phone=? AND rating IS NULL AND review_sent=1 LIMIT 1",
            (phone,)
        ).fetchone()
    finally:
        conn.close()


def _get_job_filepath_db(job_id: str, db_path: str) -> str | None:
    """Return the file path for a job (SQLite path)."""
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT filepath FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def _persist_job_settings(settings: dict, db_path: str, job_settings_json_current: str) -> str:
    """
    Calculate cost for one job's settings (no delivery), save to jobs table,
    and return updated job_settings_json string with this job added.
    """
    from rate_card import calculate_print_cost

    result = calculate_print_cost(
        page_count=settings["page_count"],
        size=settings["size"],
        colour=settings["colour"],
        layout=settings["layout"],
        sided=settings.get("sided", "single"),
        copies=settings["copies"],
        finishing=settings["finishing"],
        delivery=False,
    )

    settings["amount"]             = result["total"]
    settings["staff_quote_needed"] = result["staff_quote_needed"]
    settings["finishing_label"]    = result["finishing_label"]
    settings["breakdown_short"]    = (
        f"{'B&W' if settings['colour'] == 'bw' else 'Colour'} "
        f"{settings['size']} "
        f"{_layout_short(settings['layout'])} "
        f"× {settings['copies']}"
    )

    _update_job_quote_db(
        settings["job_id"], result["total"], settings["copies"],
        settings["finishing"], settings["size"], settings["colour"],
        settings["layout"], db_path
    )

    # Merge into job_settings_json
    existing = json.loads(job_settings_json_current or "{}")
    existing[settings["job_id"]] = settings
    return json.dumps(existing)


def _advance_to_next_job(phone: str, db_path: str) -> list:
    """
    Increment current_job_index. If more jobs remain, send next file's prompt.
    If all jobs done, ask delivery (once for the whole batch).
    """
    session  = get_session(db_path, phone)
    jobs     = json.loads(session.get("jobs_json") or "[]")
    next_idx = (session.get("current_job_index") or 0) + 1
    saved    = json.loads(session.get("saved_json") or "null")

    # Clear per-file fields and advance index
    save_session(db_path, phone,
                 current_job_index=next_idx,
                 size=None, colour=None, layout=None,
                 multiup_per=None, multiup_sided=None,
                 copies=None, finishing=None)

    if next_idx < len(jobs):
        job  = jobs[next_idx]
        msgs = []
        if saved:
            save_session(db_path, phone, step="batch_confirm")
            msgs.append(msg_batch_confirm(next_idx, len(jobs), job["filename"], job["page_count"], saved))
        else:
            save_session(db_path, phone, step="size")
            header = msg_batch_file_header(next_idx, len(jobs), job["filename"], job["page_count"])
            if header:
                msgs.append(header)
            msgs.append(msg_step1_size())
        return msgs
    else:
        # All files configured — ask delivery once
        save_session(db_path, phone, step="delivery")
        return [msg_step6_delivery()]


def _send_batch_summary(phone: str, delivery: bool, db_path: str) -> list:
    """
    Calculate total across all jobs, create one Razorpay link, send combined summary.
    """
    from rate_card import calculate_print_cost, FINISHING_RATES, DELIVERY_CHARGE
    from razorpay_integration import create_payment_link

    session      = get_session(db_path, phone)
    batch_id     = session.get("batch_id")
    jobs_list    = json.loads(session.get("jobs_json") or "[]")
    job_settings = json.loads(session.get("job_settings_json") or "{}")

    jobs_with_settings = []
    total_print        = 0.0
    any_staff_quote    = False

    for job in jobs_list:
        jid = job["job_id"]
        s   = job_settings.get(jid)
        if not s:
            logger.warning(f"No settings found for job {jid} in batch {batch_id}")
            continue

        # Recalculate cleanly (settings already persisted, this is just for the summary total)
        result = calculate_print_cost(
            page_count=s["page_count"],
            size=s["size"],
            colour=s["colour"],
            layout=s["layout"],
            sided=s.get("sided", "single"),
            copies=s["copies"],
            finishing=s["finishing"],
            delivery=False,
        )
        s["amount"]             = result["total"]
        s["staff_quote_needed"] = result["staff_quote_needed"]
        s["finishing_label"]    = result["finishing_label"]
        if not s.get("breakdown_short"):
            s["breakdown_short"] = (
                f"{'B&W' if s['colour'] == 'bw' else 'Colour'} "
                f"{s['size']} {_layout_short(s['layout'])} × {s['copies']}"
            )
        total_print += result["total"]
        if result["staff_quote_needed"]:
            any_staff_quote = True
        jobs_with_settings.append(s)

    del_cost = DELIVERY_CHARGE if delivery else 0
    total    = total_print + del_cost

    # Persist delivery on each job
    _update_jobs_delivery_db([j["job_id"] for j in jobs_list], int(delivery), db_path)

    # If any job needs staff quote — route to staff
    if any_staff_quote:
        clear_session(db_path, phone)
        from whatsapp_notify import send_staff_alert
        items = [
            f"  {s['filename']} ({s['finishing_label']})"
            for s in jobs_with_settings if s["staff_quote_needed"]
        ]
        send_staff_alert(
            f"Batch {batch_id} needs manual quote.\n"
            f"Phone: {phone}\n"
            f"Print total (excl. binding): ₹{total_print:.2f}\n"
            f"Items needing quote:\n" + "\n".join(items) +
            f"\n\nType: `quote {batch_id} AMOUNT`"
        )
        return [
            f"📋 *Your order has been noted!*\n\n"
            f"Some items require binding quotes. "
            f"Our staff will review and send your payment link shortly.\n\n"
            f"📞 Call 80896 99436 if urgent"
        ]

    # Create one Razorpay link for the whole batch
    description = f"Printosky — {len(jobs_with_settings)} print job(s) — {batch_id}"
    pay = create_payment_link(
        job_id=batch_id,
        amount=total,
        description=description,
        customer_phone=phone,
    )

    clear_session(db_path, phone)

    if "error" in pay:
        logger.error(f"Payment link failed for batch {batch_id}: {pay['error']}")
        return [
            f"📋 *Your order is confirmed!*\n\n"
            f"Our staff will send your payment link shortly. 🙏"
        ]

    pay_url  = pay["url"]
    link_id  = pay.get("link_id")
    now_str  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    _update_batch_payment_db(
        batch_id, total, link_id, now_str,
        [j["job_id"] for j in jobs_list], db_path
    )

    return [msg_batch_summary(jobs_with_settings, delivery, total, pay_url)]


# ── Batch conversation starter (called by watcher._fire_batch_conversation) ───
def start_batch_conversation(phone: str, batch_id: str, jobs: list,
                             saved: dict | None, db_path: str) -> list:
    """
    Initialise bot session for a batch and return first messages to send.

    jobs:  [{"job_id": "OSP-001", "filename": "essay.pdf", "page_count": 12}, ...]
    saved: {size, colour, layout, copies, finishing, delivery} or None
    """
    from rate_card import FINISHING_RATES

    # If saved finishing requires a staff quote, treat as no saved settings
    if saved:
        fin_info = FINISHING_RATES.get(saved.get("finishing", "none"), {})
        if fin_info.get("staff_quote"):
            saved = None

    msgs = []

    if len(jobs) > 1:
        msgs.append(
            f"You've sent {len(jobs)} files. "
            f"I'll set them up one by one, then send one payment link. 📋"
        )

    first_job = jobs[0]
    jobs_json = json.dumps(jobs)
    saved_json = json.dumps(saved) if saved else None

    if saved:
        save_session(db_path, phone,
                     job_id=first_job["job_id"],
                     batch_id=batch_id,
                     step="batch_confirm",
                     current_job_index=0,
                     jobs_json=jobs_json,
                     saved_json=saved_json,
                     job_settings_json="{}")
        msgs.append(msg_batch_confirm(0, len(jobs), first_job["filename"], first_job["page_count"], saved))
    else:
        save_session(db_path, phone,
                     job_id=first_job["job_id"],
                     batch_id=batch_id,
                     step="size",
                     current_job_index=0,
                     jobs_json=jobs_json,
                     saved_json=None,
                     job_settings_json="{}")
        header = msg_batch_file_header(0, len(jobs), first_job["filename"], first_job["page_count"])
        if header:
            msgs.append(header)
        msgs.append(msg_step1_size())

    return msgs


# ── Main bot handler ──────────────────────────────────────────────────────────
def handle_message(phone: str, text: str, job_id: str, page_count: int,
                   db_path: str, store_phone: str = None) -> list:
    """
    Process an incoming WhatsApp message in the bot conversation.
    Returns list of reply strings to send.
    job_id: set on first message (file received), None for subsequent replies
    page_count: PDF page count if known, else 0
    Returns: list of message strings to send back
    """
    from rate_card import calculate_print_cost, FINISHING_RATES
    from razorpay_integration import create_payment_link
    from whatsapp_notify import send_payment_confirmed

    text    = text.strip()
    session = get_session(db_path, phone)

    # ── Staff hold: customer requests human agent ─────────────────────────────
    if text.lower() in ("agent", "help", "staff"):
        prev_step = (session or {}).get("step", "")
        save_session(db_path, phone, step="staff_hold", prev_step=prev_step)
        return [
            "Our staff will contact you shortly. Please wait.",
            ("STAFF_QUOTE", f"[HOLD] Customer {phone} requested a staff agent. "
             f"Reply via WhatsApp Business Suite. To resume bot: POST /staff/resume "
             f"{{\"phone\":\"{phone}\"}}", phone, 0)
        ]

    # ── Staff hold: ignore all messages until staff resumes ───────────────────
    if session and session.get("step") == "staff_hold":
        return []

    # ── Review reply (1-5) — checked before session flow ─────────────────────
    # If no active conversation and message is a digit 1-5, treat as review rating.
    if not session and text in ("1", "2", "3", "4", "5"):
        try:
            from review_manager import record_rating
            pending = _get_pending_review_db(phone, db_path)
            if pending:
                from whatsapp_notify import send_whatsapp_message as _swm
                def _send(p, m):
                    try:
                        return _swm(p, m)
                    except Exception:
                        return False
                result = record_rating(db_path, phone, int(text), _send)
                if result.get("ok"):
                    logger.info("Review recorded via bot: phone=%s rating=%s", phone, text)
                    return []   # review_manager already sent the reply
        except ImportError:
            pass

    # ── New job (legacy direct path — batch flow uses start_batch_conversation) ─
    if job_id and not session:
        save_session(db_path, phone, job_id=job_id, step="size", page_count=page_count)
        return [msg_step1_size()]

    if not session:
        return []

    step    = session.get("step")
    s_jobid = session.get("job_id")
    batch_id = session.get("batch_id")

    # ── Batch confirm: use saved settings or pick new? ────────────────────────
    if step == "batch_confirm":
        jobs  = json.loads(session.get("jobs_json") or "[]")
        idx   = session.get("current_job_index") or 0
        saved = json.loads(session.get("saved_json") or "null")

        if text == "1":
            # Apply saved settings for this job
            if saved and idx < len(jobs):
                job      = jobs[idx]
                settings = _build_job_settings_from_saved(saved, job)
                new_json = _persist_job_settings(
                    settings, db_path, session.get("job_settings_json") or "{}"
                )
                save_session(db_path, phone, job_settings_json=new_json)
                return _advance_to_next_job(phone, db_path)
            # Fallback: session corrupted
            return ["_Something went wrong. Please send your file again._"]

        elif text == "2":
            # Customer wants fresh settings for this file
            save_session(db_path, phone, step="size")
            msgs   = []
            header = msg_batch_file_header(idx, len(jobs),
                                           jobs[idx]["filename"], jobs[idx]["page_count"])
            if header:
                msgs.append(header)
            msgs.append(msg_step1_size())
            return msgs

        else:
            # Invalid — re-send prompt
            if saved and idx < len(jobs):
                return [msg_batch_confirm(idx, len(jobs),
                                          jobs[idx]["filename"], jobs[idx]["page_count"], saved)]
            return ["_Please reply with 1 or 2_ 👆\n\nType *AGENT* to speak to our staff."]

    # ── Step 1: Size ──────────────────────────────────────────────────────────
    if step == "size":
        size = SIZE_MAP.get(text)
        if not size:
            return ["_Please reply with 1, 2, or 3_ 👆\n\nType *AGENT* to speak to our staff."]
        if size == "other":
            clear_session(db_path, phone)
            return [msg_other_size(s_jobid)]
        save_session(db_path, phone, size=size, step="colour")
        return [msg_step2_colour()]

    # ── Step 2: Colour ────────────────────────────────────────────────────────
    elif step == "colour":
        colour = COLOUR_MAP.get(text)
        if not colour:
            return ["_Please reply with 1, 2, or 3_ 👆\n\nType *AGENT* to speak to our staff."]
        save_session(db_path, phone, colour=colour, step="layout")
        return [msg_step3_layout()]

    # ── Step 3: Layout ────────────────────────────────────────────────────────
    elif step == "layout":
        layout = LAYOUT_MAP.get(text)
        if not layout:
            return ["_Please reply with 1, 2, or 3_ 👆\n\nType *AGENT* to speak to our staff."]
        if layout == "multiup":
            save_session(db_path, phone, layout="multiup", step="multiup_per")
            return [msg_step3b_multiup()]
        save_session(db_path, phone, layout=layout, step="copies")
        return [msg_step4_copies()]

    # ── Step 3b: Multi-up per sheet ───────────────────────────────────────────
    elif step == "multiup_per":
        mup = MULTIUP_MAP.get(text)
        if not mup:
            return ["_Please reply with 1, 2, 3, or 4_ 👆\n\nType *AGENT* to speak to our staff."]
        save_session(db_path, phone, multiup_per=mup, step="multiup_sided")
        return [msg_step3c_multiup_sided()]

    # ── Step 3c: Multi-up sided ───────────────────────────────────────────────
    elif step == "multiup_sided":
        sided = SIDED_MAP.get(text)
        if not sided:
            return ["_Please reply with 1 or 2_ 👆\n\nType *AGENT* to speak to our staff."]
        save_session(db_path, phone, multiup_sided=sided, step="copies")
        return [msg_step4_copies()]

    # ── Step 4: Copies ────────────────────────────────────────────────────────
    elif step == "copies":
        try:
            copies = int(text)
            if copies < 1 or copies > 999:
                raise ValueError
        except ValueError:
            return ["_Please reply with a valid number (e.g. 1, 2, 5)_ 👆\n\nType *AGENT* to speak to our staff."]
        save_session(db_path, phone, copies=copies, step="finishing")
        return [msg_step5_finishing()]

    # ── Step 5: Finishing ─────────────────────────────────────────────────────
    elif step == "finishing":
        finishing = FINISHING_MAP.get(text)
        if not finishing:
            return ["_Please reply with a number from 1 to 9_ 👆\n\nType *AGENT* to speak to our staff."]
        save_session(db_path, phone, finishing=finishing)

        if batch_id:
            # Batch mode: save this job's settings and advance to next job (or delivery)
            session  = get_session(db_path, phone)   # reload with finishing saved
            jobs     = json.loads(session.get("jobs_json") or "[]")
            idx      = session.get("current_job_index") or 0
            if idx < len(jobs):
                job      = jobs[idx]
                settings = _build_job_settings(session, job)
                new_json = _persist_job_settings(
                    settings, db_path, session.get("job_settings_json") or "{}"
                )
                save_session(db_path, phone, job_settings_json=new_json)
            return _advance_to_next_job(phone, db_path)
        else:
            save_session(db_path, phone, step="delivery")
            return [msg_step6_delivery()]

    # ── Step 6: Delivery → Calculate/Summary ─────────────────────────────────
    elif step == "delivery":
        if text not in ("1", "2"):
            return ["_Please reply with 1 or 2_ 👆\n\nType *AGENT* to speak to our staff."]
        delivery = text == "2"
        save_session(db_path, phone, delivery=int(delivery), step="done")

        # ── Batch mode: create combined payment link ───────────────────────
        if batch_id:
            return _send_batch_summary(phone, delivery, db_path)

        # ── Single job (legacy / non-batch) flow ──────────────────────────
        session    = get_session(db_path, phone)
        page_count = session.get("page_count") or 0

        if not page_count:
            clear_session(db_path, phone)
            fin_label = FINISHING_RATES.get(session["finishing"], {}).get("label", "")
            return [
                f"📋 *{s_jobid}* — file received but page count could not be read automatically.\n"
                f"Staff will review and send a quote shortly.\n\n"
                f"Finishing requested: {fin_label}\n"
                f"Delivery: {'Yes (+₹30)' if delivery else 'Pickup (free)'}"
            ]

        layout = session.get("layout") or "single"
        if layout == "multiup":
            layout = session.get("multiup_per") or "2up"
        sided       = session.get("multiup_sided") or "single"
        colour_mode = session.get("colour", "bw")

        # ── Mixed colour: scan PDF ─────────────────────────────────────────
        if colour_mode == "mixed":
            try:
                from pdf_scanner import scan_pdf, calculate_mixed_cost
                job_filepath = _get_job_filepath_db(s_jobid, db_path)

                if job_filepath:
                    scan = scan_pdf(job_filepath, timeout_seconds=30)
                    if scan.get("error") and "timed out" in scan["error"].lower():
                        clear_session(db_path, phone)
                        return [
                            f"📋 *{s_jobid}* — your order is noted!\n\n"
                            f"Your file has mixed colour pages. Our staff will review and send you a quote shortly.\n\n"
                            f"📞 80896 99436",
                            ("STAFF_MIXED_TIMEOUT", s_jobid, session)
                        ]
                    if not scan.get("total_pages"):
                        raise ValueError("no pages detected")

                    from rate_card import FINISHING_RATES
                    mixed    = calculate_mixed_cost(
                        scan=scan, size=session.get("size", "A4"),
                        layout=layout, sided=sided,
                        copies=session.get("copies", 1),
                    )
                    fin      = FINISHING_RATES.get(session.get("finishing", "none"), FINISHING_RATES["none"])
                    fin_cost = fin["price"] * session.get("copies", 1) if not fin["staff_quote"] else 0
                    del_cost = 30 if delivery else 0
                    total    = mixed["total_print_cost"] + fin_cost + del_cost
                    lines    = mixed["breakdown_lines"][:]
                    if fin_cost:
                        lines.append(f"{fin['label']} × {session.get('copies',1)} = ₹{fin_cost:.2f}")
                    if del_cost:
                        lines.append(f"Delivery = ₹{del_cost:.2f}")
                    lines.append(
                        f"*Total: ₹{total:.2f}*"
                        if not fin["staff_quote"]
                        else f"*Print total: ₹{total:.2f} + binding*"
                    )
                    result = {
                        "total": total, "print_cost": mixed["total_print_cost"],
                        "finishing_cost": fin_cost, "delivery_cost": del_cost,
                        "staff_quote_needed": fin["staff_quote"],
                        "finishing_label": fin["label"],
                        "breakdown": "\n".join(lines),
                    }
                else:
                    raise ValueError("filepath not found")
            except Exception as e:
                logger.warning(f"Mixed scan error for {s_jobid}: {e}")
                clear_session(db_path, phone)
                return [
                    f"📋 *{s_jobid}* — your order is noted!\n\n"
                    f"Our staff will review your file and send a quote shortly.\n\n"
                    f"📞 80896 99436",
                    ("STAFF_MIXED_TIMEOUT", s_jobid, session)
                ]
        else:
            result = calculate_print_cost(
                page_count=page_count,
                size=session.get("size", "A4"),
                colour=colour_mode,
                layout=layout,
                sided=sided,
                copies=session.get("copies", 1),
                finishing=session.get("finishing", "none"),
                delivery=delivery,
            )

        clear_session(db_path, phone)

        if result["staff_quote_needed"]:
            staff_msg    = msg_staff_quote_needed(s_jobid, result["finishing_label"], result["print_cost"])
            customer_msg = (
                f"📋 *Job received: {s_jobid}*\n\n"
                f"Print cost: ₹{result['print_cost']:.2f}\n"
                f"Binding: {result['finishing_label']} — our staff will confirm the cost shortly.\n\n"
                f"We'll send your payment link once the quote is ready! 🙏"
            )
            return [customer_msg, ("STAFF_QUOTE", staff_msg, s_jobid, result["print_cost"])]

        total       = result["total"]
        description = f"Print job {s_jobid} — Printosky / Oxygen Globally"
        pay = create_payment_link(
            job_id=s_jobid,
            amount=total,
            description=description,
            customer_phone=phone,
        )
        if "error" in pay:
            logger.error(f"Payment link failed for {s_jobid}: {pay['error']}")
            return [
                f"📋 *{s_jobid}* — your order is confirmed!\n\n"
                f"{result['breakdown']}\n\n"
                f"Our staff will send your payment link shortly. 🙏"
            ]

        return [msg_payment_link(s_jobid, result["breakdown"], pay["url"])]

    # ── Fallthrough: unrecognised input for current step ─────────────────────
    return [
        "Sorry, I didn't understand that. Please reply with one of the options above.\n\n"
        "Type *AGENT* to speak to our staff. 🙏"
    ]


# ── Cloud mode: swap SQLite DB functions to Supabase backend ──────────────────
# Activated when SUPABASE_URL is set (Vercel deployment).
# Store PC has no SUPABASE_URL → continues to use the SQLite path above.
if os.environ.get("SUPABASE_URL"):
    import db_cloud as _dbc

    get_session           = _dbc.get_session           # noqa: F811
    save_session          = _dbc.save_session           # noqa: F811
    clear_session         = _dbc.clear_session          # noqa: F811
    save_customer_profile = _dbc.save_customer_profile  # noqa: F811

    def _update_job_quote_db(job_id, amount_quoted, copies,          # noqa: F811
                              finishing, size, colour, layout, db_path):
        _dbc.update_job_settings(job_id, amount_quoted, copies, finishing, size, colour, layout)

    def _update_jobs_delivery_db(job_ids: list, delivery: int, db_path: str):  # noqa: F811
        for jid in job_ids:
            _dbc.update_job_delivery(jid, delivery)

    def _update_batch_payment_db(batch_id, total, link_id,           # noqa: F811
                                  link_sent_at, job_ids, db_path):
        _dbc.update_batch_payment(batch_id, total, link_id, link_sent_at)
        _dbc.update_jobs_payment_link(job_ids, link_id, link_sent_at)

    def _get_pending_review_db(phone: str, db_path: str):            # noqa: F811
        return _dbc.get_pending_review(phone)

    def _get_job_filepath_db(job_id: str, db_path: str) -> str | None:  # noqa: F811
        return _dbc.get_job_filepath(job_id)
