"""
PRINTOSKY B2B BOT FLOW
========================
Handles WhatsApp conversation for registered B2B clients.
Called instead of the retail bot when sender is in b2b_clients table.

B2B flow is intentionally minimal:
  - Greet by name + company
  - Ask for copies + finishing only (specs agreed in advance)
  - Staff confirms and processes
  - No Razorpay auto-link (monthly invoice)
"""

import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger("b2b_bot")

# ── Greeting ──────────────────────────────────────────────────────────────────
def msg_b2b_welcome(client: dict, filename: str) -> str:
    contact = client.get("contact_name") or "there"
    company = client["company_name"]
    disc    = client.get("discount_pct", 0)
    disc_note = f"\n_({disc}% client discount applied automatically)_" if disc else ""
    return (
        f"👋 *Hi {contact}!*\n\n"
        f"File received from *{company}* 🖨️\n"
        f"📄 {filename}\n"
        f"{disc_note}\n\n"
        f"Please let us know:\n"
        f"1️⃣  How many copies?\n"
        f"2️⃣  Any finishing? (spiral, wiro, staple, none)\n\n"
        f"_Or just type the number of copies if no special finishing needed._"
    )

def msg_b2b_confirmed(job_id: str, company: str, copies: int,
                      finishing: str, amount: float, disc_pct: float) -> str:
    disc_note = f"\n_Discount ({disc_pct}%) applied: -₹{amount * disc_pct/100/(1-disc_pct/100):.2f}_" if disc_pct else ""
    return (
        f"✅ *Order confirmed!*\n\n"
        f"📋 Job ID: `{job_id}`\n"
        f"🏢 {company}\n"
        f"📋 {copies} cop{'y' if copies==1 else 'ies'} | {finishing}{disc_note}\n"
        f"💰 Amount: ₹{amount:.2f}\n\n"
        f"_Will be added to your monthly invoice._\n"
        f"We'll notify you when ready for pickup 🙏"
    )

def msg_b2b_ready(job_id: str, company: str) -> str:
    return (
        f"🎉 *Your job is ready!*\n\n"
        f"📋 Job ID: `{job_id}`\n"
        f"🏢 {company}\n\n"
        f"Please collect at your convenience.\n"
        f"— Printosky / Oxygen Globally, Thriprayar 🖨️"
    )

# ── Session state ─────────────────────────────────────────────────────────────
def get_b2b_session(db_path: str, phone: str) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM bot_sessions WHERE phone=?", (phone,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}

def save_b2b_session(db_path: str, phone: str, **kwargs):
    conn = sqlite3.connect(db_path)
    kwargs["phone"] = phone
    kwargs["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cols  = ", ".join(kwargs.keys())
    ph    = ", ".join(f":{k}" for k in kwargs.keys())
    conn.execute(f"""
        INSERT INTO bot_sessions ({cols}) VALUES ({ph})
        ON CONFLICT(phone) DO UPDATE SET
        {', '.join(f"{k}=excluded.{k}" for k in kwargs.keys() if k != 'phone')}
    """, kwargs)
    conn.commit()
    conn.close()

def clear_b2b_session(db_path: str, phone: str):
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM bot_sessions WHERE phone=?", (phone,))
    conn.commit()
    conn.close()

# ── Main handler ──────────────────────────────────────────────────────────────
def handle_b2b_message(phone: str, text: str, job_id: str,
                        client: dict, db_path: str) -> list:
    """
    Handle an incoming WhatsApp message from a B2B client.
    job_id: set when a new file arrives, None for text replies.
    Returns list of reply strings.
    """
    from rate_card import FINISHING_RATES, RATES
    import math

    text    = (text or "").strip().lower()
    session = get_b2b_session(db_path, phone)
    company = client["company_name"]
    disc    = client.get("discount_pct", 0)

    # ── New file received ─────────────────────────────────────────────────────
    if job_id:
        conn = sqlite3.connect(db_path)
        row  = conn.execute("SELECT filename FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        conn.close()
        filename = row[0] if row else "your file"
        save_b2b_session(db_path, phone, job_id=job_id, step="b2b_copies")
        return [msg_b2b_welcome(client, filename)]

    if not session:
        return []

    step    = session.get("step", "")
    s_jobid = session.get("job_id", "")

    # ── Step: copies (+ optional finishing in same message) ───────────────────
    if step == "b2b_copies":
        # Parse: "50" or "50 spiral" or "100 wiro" or "2 none"
        parts    = text.split()
        copies   = None
        finishing = "none"

        for part in parts:
            if part.isdigit():
                copies = int(part)
            elif part in ("spiral", "wiro", "staple", "side staple", "none", "no"):
                finishing = "none" if part == "no" else part.replace(" ", "_")

        if not copies or copies < 1:
            return ["_Please reply with the number of copies (e.g. 50, or 50 spiral)_ 👆"]

        # Map finishing
        fin_key_map = {
            "spiral": "spiral", "wiro": "wiro",
            "staple": "staple", "side_staple": "staple", "none": "none",
        }
        fin_key  = fin_key_map.get(finishing, "none")
        fin_info = FINISHING_RATES.get(fin_key, FINISHING_RATES["none"])

        # Get page count from job
        conn = sqlite3.connect(db_path)
        row  = conn.execute(
            "SELECT page_count, filename FROM jobs WHERE job_id=?", (s_jobid,)
        ).fetchone()
        conn.close()
        pages    = row[0] if row else 0
        filename = row[1] if row else ""

        # If binding needs staff quote, flag to staff
        if fin_info["staff_quote"] and pages:
            clear_b2b_session(db_path, phone)
            return [
                f"✅ Got it! *{copies} copies* with {fin_info['label']}.\n\n"
                f"Our staff will confirm the binding cost shortly. 🙏",
                ("B2B_STAFF_QUOTE", s_jobid, copies, fin_key, company)
            ]

        # Calculate amount
        if pages:
            import math
            sheets   = math.ceil(pages / 1)   # single side default for B2B
            rate     = RATES["A4"]["bw"]["single"]   # default A4 B&W; staff can override
            base_amt = round(sheets * copies * rate, 2)
            fin_amt  = fin_info["price"] * copies
            subtotal = base_amt + fin_amt
            amount   = round(subtotal * (1 - disc / 100), 2)
        else:
            # No page count — staff will confirm amount
            amount = 0

        clear_b2b_session(db_path, phone)

        # Update job record
        conn = sqlite3.connect(db_path)
        conn.execute("""
            UPDATE jobs SET copies=?, finishing=?, amount_collected=?, status='Received'
            WHERE job_id=?
        """, (copies, fin_key, amount if amount else None, s_jobid))
        conn.commit()
        conn.close()

        if amount:
            return [msg_b2b_confirmed(s_jobid, company, copies, fin_info["label"], amount, disc)]
        else:
            # No page count yet — staff confirms
            return [
                f"✅ *{copies} copies* with {fin_info['label']} — noted!\n\n"
                f"Our staff will confirm the amount and we'll update your invoice. 🙏",
                ("B2B_STAFF_CONFIRM", s_jobid, copies, fin_key, company)
            ]

    return []
