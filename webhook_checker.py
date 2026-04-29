"""
PRINTOSKY WEBHOOK CHECKER
==========================
Background thread that runs every 10 minutes.
Detects payment links that were sent but never confirmed by Razorpay webhook
(e.g. customer paid but webhook dropped, or Razorpay link expired).

For each stale job/batch:
  - Verifies current status against Razorpay API
  - If paid: force-marks as Paid in DB, notifies customer
  - If unpaid + expired: alerts staff to follow up

Start via:
  from webhook_checker import start_checker
  start_checker(db_path)
"""

import sqlite3
import logging
import threading
import time
import requests
from datetime import datetime, timedelta
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("webhook_checker")

LINK_GRACE_MINUTES = 60   # how long after link_sent_at before we consider it stale
CHECK_INTERVAL     = 600  # seconds between checks (10 minutes)


# ── Stale job queries ─────────────────────────────────────────────────────────
def get_stale_jobs(db_path: str) -> list:
    """Return single jobs with payment links sent >LINK_GRACE_MINUTES ago and still unpaid."""
    cutoff = (datetime.now() - timedelta(minutes=LINK_GRACE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("""
            SELECT job_id, sender, amount_quoted, razorpay_link_id, link_sent_at
            FROM jobs
            WHERE status NOT IN ('Paid', 'Completed', 'Cancelled')
              AND razorpay_link_id IS NOT NULL
              AND link_sent_at IS NOT NULL
              AND link_sent_at < ?
              AND (batch_id IS NULL OR batch_id = '')
        """, (cutoff,)).fetchall()
        conn.close()
        return [
            {"job_id": r[0], "sender": r[1], "amount_quoted": r[2],
             "razorpay_link_id": r[3], "link_sent_at": r[4], "is_batch": False}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"get_stale_jobs error: {e}")
        return []


def get_stale_batch_jobs(db_path: str) -> list:
    """Return batches with payment links sent >LINK_GRACE_MINUTES ago and still unpaid."""
    cutoff = (datetime.now() - timedelta(minutes=LINK_GRACE_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(db_path)
        rows = conn.execute("""
            SELECT batch_id, phone, total_amount, razorpay_link_id, link_sent_at
            FROM job_batches
            WHERE status = 'awaiting_payment'
              AND link_sent_at IS NOT NULL
              AND link_sent_at < ?
        """, (cutoff,)).fetchall()
        conn.close()
        return [
            {"job_id": r[0], "sender": r[1], "amount_quoted": r[2],
             "razorpay_link_id": r[3], "link_sent_at": r[4], "is_batch": True}
            for r in rows
        ]
    except Exception as e:
        logger.error(f"get_stale_batch_jobs error: {e}")
        return []


# ── Razorpay status check ─────────────────────────────────────────────────────
def _check_razorpay_link_status(link_id: str) -> str | None:
    """
    Query Razorpay API for payment link status.
    Returns 'paid' | 'created' | 'expired' | 'cancelled' | None on error.
    """
    try:
        from razorpay_integration import RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET
        r = requests.get(
            f"https://api.razorpay.com/v1/payment_links/{link_id}",
            auth=HTTPBasicAuth(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            timeout=10,
        )
        if r.status_code == 200:
            return r.json().get("status")
        logger.warning(f"Razorpay link check {link_id}: HTTP {r.status_code}")
        return None
    except Exception as e:
        logger.warning(f"Razorpay link check failed for {link_id}: {e}")
        return None


# ── Force-mark paid ───────────────────────────────────────────────────────────
def force_mark_paid(item: dict, db_path: str):
    """Force-mark a stale job or batch as Paid after confirming Razorpay shows paid."""
    from whatsapp_notify import send_payment_confirmed

    if item["is_batch"]:
        batch_id = item["job_id"]
        phone    = item["sender"]
        amount   = item["amount_quoted"] or 0
        try:
            conn    = sqlite3.connect(db_path)
            job_ids_str = conn.execute(
                "SELECT job_ids FROM job_batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            job_ids = [j for j in (job_ids_str[0] or "").split(",") if j.strip()] if job_ids_str else []
            for jid in job_ids:
                conn.execute(
                    "UPDATE jobs SET status='Paid', payment_mode='Online' WHERE job_id=?", (jid,)
                )
            conn.execute(
                "UPDATE job_batches SET status='paid' WHERE batch_id=?", (batch_id,)
            )
            conn.commit()
            conn.close()
            send_payment_confirmed(phone, batch_id, amount)
            logger.info(f"Force-marked batch {batch_id} as Paid ({len(job_ids)} jobs)")
        except Exception as e:
            logger.error(f"force_mark_paid batch {batch_id}: {e}")
    else:
        job_id = item["job_id"]
        sender = item["sender"]
        amount = item["amount_quoted"] or 0
        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE jobs SET status='Paid', payment_mode='Online' WHERE job_id=?", (job_id,)
            )
            conn.commit()
            conn.close()
            if sender:
                send_payment_confirmed(sender, job_id, amount)
            logger.info(f"Force-marked job {job_id} as Paid")
        except Exception as e:
            logger.error(f"force_mark_paid job {job_id}: {e}")


# ── Staff alert for truly unpaid stale links ──────────────────────────────────
def _alert_staff_stale(item: dict):
    """Notify staff that a payment link went stale without being paid."""
    try:
        from whatsapp_notify import send_staff_alert
        label = item["job_id"]
        sent  = item.get("link_sent_at", "?")
        amt   = item["amount_quoted"] or 0
        kind  = "Batch" if item["is_batch"] else "Job"
        send_staff_alert(
            f"⏰ *Unpaid link — {kind} {label}*\n\n"
            f"Link sent: {sent}\n"
            f"Amount: ₹{amt:.2f}\n"
            f"Customer: {item['sender']}\n\n"
            f"Follow up or resend link manually."
        )
    except Exception as e:
        logger.warning(f"Staff stale alert failed: {e}")


# ── Main check routine ────────────────────────────────────────────────────────
def run_check(db_path: str):
    """
    Check all stale jobs and batches.
    If Razorpay says paid → force-mark.
    If Razorpay says expired/created (unpaid) → alert staff.
    """
    stale = get_stale_jobs(db_path) + get_stale_batch_jobs(db_path)
    if not stale:
        logger.debug("Webhook checker: no stale items found")
        return

    logger.info(f"Webhook checker: {len(stale)} stale item(s) to check")
    for item in stale:
        link_id = item.get("razorpay_link_id")
        if not link_id:
            continue

        status = _check_razorpay_link_status(link_id)
        logger.info(f"  {item['job_id']} — Razorpay status: {status}")

        if status == "paid":
            force_mark_paid(item, db_path)
        elif status in ("expired", "cancelled"):
            # Mark in DB so it stops appearing as stale on every poll
            try:
                conn = sqlite3.connect(db_path)
                if item.get("is_batch"):
                    conn.execute(
                        "UPDATE job_batches SET status='cancelled' WHERE batch_id=?",
                        (item["job_id"],)
                    )
                else:
                    conn.execute(
                        "UPDATE jobs SET status='Cancelled' WHERE job_id=?",
                        (item["job_id"],)
                    )
                conn.commit()
                conn.close()
                logger.info(f"  Marked {item['job_id']} as cancelled (Razorpay: {status})")
            except Exception as e:
                logger.error(f"  Failed to mark {item['job_id']} cancelled: {e}")
            _alert_staff_stale(item)
        elif status == "created":
            # Link still active but unpaid — alert staff to follow up
            _alert_staff_stale(item)


# ── Background thread ─────────────────────────────────────────────────────────
def start_checker(db_path: str):
    """Start the webhook checker as a background daemon thread."""
    def _loop():
        logger.info("Webhook checker started — interval: every 10 min")
        # Wait one interval before first check so startup noise settles
        time.sleep(CHECK_INTERVAL)
        while True:
            try:
                run_check(db_path)
            except Exception as e:
                logger.error(f"Webhook checker loop error: {e}")
            time.sleep(CHECK_INTERVAL)

    t = threading.Thread(target=_loop, daemon=True, name="WebhookChecker")
    t.start()
    return t
