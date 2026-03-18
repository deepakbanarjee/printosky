"""
PRINTOSKY WHATSAPP BOT
========================
Manages multi-step conversation flow for print orders.

Flow:
  File received → Step 1 (size) → Step 2 (colour) → Step 3 (layout)
  → Step 3b (multiup options if needed) → Step 4 (copies)
  → Step 5 (finishing) → Step 6 (delivery)
  → Calculate → Generate Razorpay link → Send to customer

State is stored in SQLite (bot_sessions table) so it survives restarts.

Staff hybrid commands (when bot can't quote):
  quote OSP-xxx 250   → set binding quote, generate payment link, send to customer
"""

import sqlite3
import logging
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
            phone       TEXT PRIMARY KEY,
            job_id      TEXT,
            step        TEXT,
            size        TEXT,
            colour      TEXT,
            layout      TEXT,
            multiup_per TEXT,
            multiup_sided TEXT,
            copies      INTEGER,
            finishing   TEXT,
            delivery    INTEGER DEFAULT 0,
            page_count  INTEGER DEFAULT 0,
            updated_at  TEXT
        )
    """)
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

    text = text.strip()
    session = get_session(db_path, phone)

    # ── New job: file just received ───────────────────────────────────────────
    if job_id and not session:
        save_session(db_path, phone, job_id=job_id, step="size", page_count=page_count)
        return [msg_step1_size()]

    if not session:
        # No active session and no new job — ignore
        return []

    step    = session.get("step")
    s_jobid = session.get("job_id")

    # ── Step 1: Size ──────────────────────────────────────────────────────────
    if step == "size":
        size = SIZE_MAP.get(text)
        if not size:
            return ["_Please reply with 1, 2, or 3_ 👆"]
        if size == "other":
            clear_session(db_path, phone)
            return [msg_other_size(s_jobid)]
        save_session(db_path, phone, size=size, step="colour")
        return [msg_step2_colour()]

    # ── Step 2: Colour ────────────────────────────────────────────────────────
    elif step == "colour":
        colour = COLOUR_MAP.get(text)
        if not colour:
            return ["_Please reply with 1, 2, or 3_ 👆"]
        save_session(db_path, phone, colour=colour, step="layout")
        return [msg_step3_layout()]

    # ── Step 3: Layout ────────────────────────────────────────────────────────
    elif step == "layout":
        layout = LAYOUT_MAP.get(text)
        if not layout:
            return ["_Please reply with 1, 2, or 3_ 👆"]
        if layout == "multiup":
            save_session(db_path, phone, layout="multiup", step="multiup_per")
            return [msg_step3b_multiup()]
        save_session(db_path, phone, layout=layout, step="copies")
        return [msg_step4_copies()]

    # ── Step 3b: Multi-up per sheet ───────────────────────────────────────────
    elif step == "multiup_per":
        mup = MULTIUP_MAP.get(text)
        if not mup:
            return ["_Please reply with 1, 2, 3, or 4_ 👆"]
        save_session(db_path, phone, multiup_per=mup, step="multiup_sided")
        return [msg_step3c_multiup_sided()]

    # ── Step 3c: Multi-up sided ───────────────────────────────────────────────
    elif step == "multiup_sided":
        sided = SIDED_MAP.get(text)
        if not sided:
            return ["_Please reply with 1 or 2_ 👆"]
        save_session(db_path, phone, multiup_sided=sided, step="copies")
        return [msg_step4_copies()]

    # ── Step 4: Copies ────────────────────────────────────────────────────────
    elif step == "copies":
        try:
            copies = int(text)
            if copies < 1 or copies > 999:
                raise ValueError
        except ValueError:
            return ["_Please reply with a valid number (e.g. 1, 2, 5)_ 👆"]
        save_session(db_path, phone, copies=copies, step="finishing")
        return [msg_step5_finishing()]

    # ── Step 5: Finishing ─────────────────────────────────────────────────────
    elif step == "finishing":
        finishing = FINISHING_MAP.get(text)
        if not finishing:
            return ["_Please reply with a number from 1 to 9_ 👆"]
        save_session(db_path, phone, finishing=finishing, step="delivery")
        return [msg_step6_delivery()]

    # ── Step 6: Delivery → Calculate → Payment link ───────────────────────────
    elif step == "delivery":
        if text not in ("1", "2"):
            return ["_Please reply with 1 or 2_ 👆"]
        delivery = text == "2"
        save_session(db_path, phone, delivery=int(delivery), step="done")

        # Reload full session
        session = get_session(db_path, phone)
        page_count = session.get("page_count") or 0

        # If we don't have page count (non-PDF), ask staff to quote
        if not page_count:
            clear_session(db_path, phone)
            fin_label = FINISHING_RATES.get(session["finishing"], {}).get("label", "")
            return [
                f"📋 *{s_jobid}* — file received but page count could not be read automatically.\n"
                f"Staff will review and send a quote shortly.\n\n"
                f"Finishing requested: {fin_label}\n"
                f"Delivery: {'Yes (+₹30)' if delivery else 'Pickup (free)'}"
            ]

        # Determine layout key for rate card
        layout = session.get("layout") or "single"
        if layout == "multiup":
            layout = session.get("multiup_per") or "2up"
        sided = session.get("multiup_sided") or "single"

        colour_mode = session.get("colour", "bw")

        # ── Mixed colour: scan PDF now (only when customer selected mixed) ────
        if colour_mode == "mixed":
            try:
                from pdf_scanner import scan_pdf, calculate_mixed_cost
                job_filepath = None
                conn_f = sqlite3.connect(db_path)
                row_f  = conn_f.execute("SELECT filepath FROM jobs WHERE job_id=?", (s_jobid,)).fetchone()
                conn_f.close()
                if row_f: job_filepath = row_f[0]

                if job_filepath:
                    scan = scan_pdf(job_filepath, timeout_seconds=30)
                    if scan.get("error") and "timed out" in scan["error"].lower():
                        # Scan timed out — hand to staff
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
                        scan=scan, size=session.get("size","A4"),
                        layout=layout, sided=sided,
                        copies=session.get("copies",1),
                    )
                    fin      = FINISHING_RATES.get(session.get("finishing","none"), FINISHING_RATES["none"])
                    fin_cost = fin["price"] * session.get("copies",1) if not fin["staff_quote"] else 0
                    del_cost = 30 if delivery else 0
                    total    = mixed["total_print_cost"] + fin_cost + del_cost
                    lines    = mixed["breakdown_lines"][:]
                    if fin_cost: lines.append(f"{fin['label']} × {session.get('copies',1)} = ₹{fin_cost:.2f}")
                    if del_cost: lines.append(f"Delivery = ₹{del_cost:.2f}")
                    lines.append(f"*Total: ₹{total:.2f}*" if not fin["staff_quote"] else f"*Print total: ₹{total:.2f} + binding*")
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
            # ── Normal B&W or Colour ──────────────────────────────────────────
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

        # If binding needs staff quote — send to staff
        if result["staff_quote_needed"]:
            # Notify staff (sent via whatsapp_notify to store number)
            staff_msg = msg_staff_quote_needed(
                s_jobid, result["finishing_label"], result["print_cost"]
            )
            # Return [customer_msg, ("STAFF", staff_msg)] — caller handles routing
            customer_msg = (
                f"📋 *Job received: {s_jobid}*\n\n"
                f"Print cost: ₹{result['print_cost']:.2f}\n"
                f"Binding: {result['finishing_label']} — our staff will confirm the cost shortly.\n\n"
                f"We'll send your payment link once the quote is ready! 🙏"
            )
            return [customer_msg, ("STAFF_QUOTE", staff_msg, s_jobid, result["print_cost"])]

        # Full auto — generate payment link
        total = result["total"]
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

    return []
