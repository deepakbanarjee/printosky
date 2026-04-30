"""
review_manager.py — Post-collection review request and discount code system.

Flow:
  1. Staff marks job Collected → handle_complete_job() calls schedule_review()
  2. 30 min timer fires → send_review_request() sends WhatsApp to customer
  3. Customer replies 1-5 → whatsapp_bot.handle_message() calls record_rating()
  4. Rating 4-5 → generate_discount_code() → send code + Google Maps link
  5. Rating 1-3 → log only (staff can follow up manually)

Usage:
    from review_manager import schedule_review, record_rating, setup_review_db
"""

import logging
import random
import sqlite3
import string
import threading
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

REVIEW_DELAY_SEC = 30 * 60      # 30 minutes
DISCOUNT_PCT     = 10            # percent off for 4-5 star reviews
GOOGLE_MAPS_URL  = "https://g.page/r/Printosky"   # update with real short link

REFERRAL_INVITE_DELAY_SEC = 5 * 60   # 5 minutes after a 4-5 star rating
REFERRAL_BUSINESS_NUMBER  = "919495706405"
REFERRAL_PAYOUT_INR       = 20

# Scheduled review timers (job_id → Timer), kept to allow cancellation
_pending_timers: dict[str, threading.Timer] = {}
_pending_referral_timers: dict[str, threading.Timer] = {}


# ── DB setup ──────────────────────────────────────────────────────────────────

def setup_review_db(conn: sqlite3.Connection) -> None:
    """Create job_reviews and discount_codes tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_reviews (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      TEXT    NOT NULL,
            phone       TEXT,
            rating      INTEGER,
            feedback    TEXT,
            review_sent INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reviews_job ON job_reviews (job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_reviews_phone ON job_reviews (phone)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS discount_codes (
            code        TEXT PRIMARY KEY,
            phone       TEXT    NOT NULL,
            pct_off     INTEGER DEFAULT 10,
            source      TEXT    DEFAULT 'review',
            used        INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_discounts_phone ON discount_codes (phone)"
    )
    conn.commit()


# ── Review scheduling ─────────────────────────────────────────────────────────

def schedule_review(
    db_path: str,
    job_id: str,
    phone: str,
    send_fn,
    delay_sec: int = REVIEW_DELAY_SEC,
) -> None:
    """
    Schedule a review request WhatsApp message 30 minutes after job collection.

    send_fn: callable(phone, message) → bool  (wraps _send_whatsapp)
    Skips scheduling if phone is blank (walk-in with no number).
    Skips if already scheduled for this job (idempotent).
    """
    if not phone:
        logger.info("Review skipped for %s — no phone number", job_id)
        return

    if job_id in _pending_timers:
        logger.info("Review already scheduled for %s", job_id)
        return

    # Insert pending review row
    conn = sqlite3.connect(db_path)
    try:
        setup_review_db(conn)
        conn.execute(
            "INSERT OR IGNORE INTO job_reviews (job_id, phone) VALUES (?, ?)",
            (job_id, phone),
        )
        conn.commit()
    finally:
        conn.close()

    def _fire():
        _pending_timers.pop(job_id, None)
        send_review_request(db_path, job_id, phone, send_fn)

    t = threading.Timer(delay_sec, _fire)
    t.daemon = True
    t.start()
    _pending_timers[job_id] = t
    logger.info("Review request scheduled for %s in %ds", job_id, delay_sec)


def cancel_review(job_id: str) -> None:
    """Cancel a pending review request (e.g. if job is re-opened)."""
    t = _pending_timers.pop(job_id, None)
    if t:
        t.cancel()
        logger.info("Review request cancelled for %s", job_id)


# ── Send review request ───────────────────────────────────────────────────────

def send_review_request(
    db_path: str,
    job_id: str,
    phone: str,
    send_fn,
) -> bool:
    """
    Send the review request WhatsApp. Marks review_sent=1 in DB.
    Returns True if WhatsApp was sent successfully.
    """
    msg = (
        f"Hi! Thank you for visiting Printosky 🙏\n\n"
        f"How was your experience today?\n"
        f"Reply with a number:\n"
        f"1 — Poor\n"
        f"2 — Below average\n"
        f"3 — Average\n"
        f"4 — Good\n"
        f"5 — Excellent ⭐\n\n"
        f"Your feedback helps us serve you better!"
    )
    sent = send_fn(phone, msg)

    conn = sqlite3.connect(db_path)
    try:
        setup_review_db(conn)
        conn.execute(
            "UPDATE job_reviews SET review_sent=1 WHERE job_id=?", (job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Review request sent to %s for job %s: %s", phone, job_id, "ok" if sent else "failed")
    return sent


# ── Record rating ─────────────────────────────────────────────────────────────

def record_rating(
    db_path: str,
    phone: str,
    rating: int,
    send_fn,
) -> dict:
    """
    Record a customer rating (1-5) for their most recent pending review.
    Called from whatsapp_bot when customer replies to the review request.

    Returns {"ok": True, "rating": N, "discount_code": "..." or None}
    """
    if rating not in range(1, 6):
        return {"ok": False, "error": "Rating must be 1-5"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        setup_review_db(conn)
        # Find most recent unanswered review for this phone
        row = conn.execute("""
            SELECT id, job_id FROM job_reviews
            WHERE phone=? AND rating IS NULL AND review_sent=1
            ORDER BY id DESC LIMIT 1
        """, (phone,)).fetchone()

        if not row:
            return {"ok": False, "error": "No pending review found for this number"}

        review_id = row["id"]
        job_id    = row["job_id"]

        conn.execute(
            "UPDATE job_reviews SET rating=? WHERE id=?",
            (rating, review_id),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Review recorded: job=%s phone=%s rating=%d", job_id, phone, rating)

    discount_code = None

    if rating >= 4:
        # Generate unique discount code
        discount_code = _generate_code(db_path, phone)
        # Send thank-you + Google Maps link + discount
        msg = (
            f"Thank you for your ⭐⭐⭐⭐{'⭐' if rating == 5 else ''} rating!\n\n"
            f"We'd love a Google review too — it means a lot to a small business:\n"
            f"{GOOGLE_MAPS_URL}\n\n"
            f"As a thank-you, here's 10% off your next order:\n"
            f"*{discount_code}*\n\n"
            f"See you again at Printosky! 🙏"
        )
        send_fn(phone, msg)
        # Schedule referral invite a few minutes later — they're a happy customer
        schedule_referral_invite(phone, send_fn)
    else:
        # Low rating — acknowledge, no discount
        msg = (
            f"Thank you for your honest feedback. We're sorry your experience wasn't great.\n"
            f"We'll work on improving. Hope to serve you better next time!"
        )
        send_fn(phone, msg)

    return {"ok": True, "rating": rating, "job_id": job_id, "discount_code": discount_code}


# ── Discount code helpers ─────────────────────────────────────────────────────

def _generate_code(db_path: str, phone: str) -> str:
    """Generate a unique 8-char alphanumeric discount code and store it."""
    conn = sqlite3.connect(db_path)
    try:
        setup_review_db(conn)
        for _ in range(10):  # retry up to 10 times on collision
            code = "THANK-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
            exists = conn.execute(
                "SELECT 1 FROM discount_codes WHERE code=?", (code,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO discount_codes (code, phone, pct_off, source) VALUES (?,?,?,?)",
                    (code, phone, DISCOUNT_PCT, "review"),
                )
                conn.commit()
                logger.info("Discount code %s generated for %s", code, phone)
                return code
    finally:
        conn.close()
    raise RuntimeError("Could not generate unique discount code after 10 attempts")


def get_unused_discount(db_path: str, phone: str) -> Optional[str]:
    """
    Return the most recent unused discount code for a phone number, or None.
    Called by whatsapp_bot to auto-apply discount on next order.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        setup_review_db(conn)
        row = conn.execute(
            "SELECT code, pct_off FROM discount_codes WHERE phone=? AND used=0 ORDER BY created_at DESC LIMIT 1",
            (phone,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def redeem_discount(db_path: str, code: str) -> bool:
    """Mark a discount code as used. Returns True if code was valid and unused."""
    conn = sqlite3.connect(db_path)
    try:
        setup_review_db(conn)
        cur = conn.execute(
            "UPDATE discount_codes SET used=1 WHERE code=? AND used=0", (code,)
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


# ── Referral invitations (sent to 4-5 star customers) ────────────────────────

def _generate_referral_code(phone: str) -> str:
    """Build a memorable code: REF + last 4 of phone + 2 random uppercase letters."""
    digits = "".join(c for c in phone if c.isdigit())
    tail = digits[-4:].zfill(4) if digits else "0000"
    suffix = "".join(random.choices(string.ascii_uppercase, k=2))
    return f"REF{tail}{suffix}"


def schedule_referral_invite(
    phone: str,
    send_fn,
    delay_sec: int = REFERRAL_INVITE_DELAY_SEC,
) -> None:
    """
    Schedule a referral-invite WhatsApp to a happy customer.
    Idempotent per phone — won't queue twice for the same number.
    """
    if not phone:
        return
    if phone in _pending_referral_timers:
        logger.info("Referral invite already scheduled for %s", phone)
        return

    def _fire():
        _pending_referral_timers.pop(phone, None)
        send_referral_invite(phone, send_fn)

    t = threading.Timer(delay_sec, _fire)
    t.daemon = True
    t.start()
    _pending_referral_timers[phone] = t
    logger.info("Referral invite scheduled for %s in %ds", phone, delay_sec)


def _normalize_phone(p: str) -> str:
    """Match api/index.py's normalization: digits-only, 91-prefixed for 10-digit Indian."""
    if not p:
        return ""
    s = str(p).replace("@c.us", "").replace("@lid", "").replace("@s.whatsapp.net", "").strip()
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def send_referral_invite(phone: str, send_fn) -> bool:
    """
    Generate (or look up) a unique referrer code for this customer,
    create the Supabase `referrers` row, then send the invite WhatsApp.
    Idempotent — re-sending uses the same code.
    Stores label in canonical 91XXXXXXXXXX form so Vercel-side lookups match.
    """
    raw_phone = phone
    phone = _normalize_phone(phone)
    if not phone:
        return False

    code: Optional[str] = None
    try:
        from db_cloud import _client
        sb = _client()
        existing = sb.table("referrers").select("code").eq("label", phone).execute()
        if existing.data:
            code = existing.data[0]["code"]
            logger.info("Referrer code %s already exists for %s — re-using", code, phone)
        else:
            for _ in range(10):
                candidate = _generate_referral_code(phone)
                hit = sb.table("referrers").select("code").eq("code", candidate).execute()
                if not hit.data:
                    sb.table("referrers").insert({
                        "code": candidate,
                        "label": phone,
                        "platform": "whatsapp_invited",
                    }).execute()
                    code = candidate
                    logger.info("Created referrer row %s for %s", code, phone)
                    break
            if not code:
                logger.error("Could not generate unique referral code for %s after 10 attempts", phone)
                return False
    except Exception as e:
        logger.error("send_referral_invite Supabase error for %s: %s", phone, e)
        return False

    share_link = f"https://wa.me/{REFERRAL_BUSINESS_NUMBER}?text=ref_{code}"
    msg = (
        f"One more thing — earn Printosky store credit for sharing! 🎟️\n\n"
        f"For every friend who places an order using your link, you earn "
        f"*₹{REFERRAL_PAYOUT_INR} store credit* — apply it on your next order.\n\n"
        f"Your unique link:\n{share_link}\n\n"
        f"Share with classmates, hostel mates, anyone who needs printing or projects.\n"
        f"Reply *MY CREDITS* anytime to check your balance."
    )
    # Send to the original phone format (whatever WhatsApp/store layer expects);
    # Supabase rows are keyed by the normalized form.
    sent = send_fn(raw_phone, msg)
    logger.info("Referral invite sent to %s (code %s): %s", phone, code, "ok" if sent else "failed")
    return bool(sent)
