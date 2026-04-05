"""
PRINTOSKY CLOUD DB ADAPTER
===========================
Supabase backend for whatsapp_bot.py and webhook_receiver.py.
Activated when SUPABASE_URL env var is set (Vercel deployment).
The store PC continues to use the SQLite path (no SUPABASE_URL).

All functions mirror the SQLite function signatures exactly so
whatsapp_bot.py can swap backends transparently.
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger("db_cloud")

# ── Supabase client (lazy singleton) ─────────────────────────────────────────

_sb = None


def _client():
    global _sb
    if _sb is None:
        from supabase import create_client
        url = os.environ["SUPABASE_URL"]
        key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
        _sb = create_client(url, key)
    return _sb


# ── bot_sessions ──────────────────────────────────────────────────────────────

def get_session(db_path: str, phone: str) -> dict:
    """Fetch bot session from Supabase (db_path is ignored in cloud mode)."""
    try:
        result = _client().table("bot_sessions").select("*").eq("phone", phone).execute()
        return result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"get_session error for {phone}: {e}")
        return {}


def save_session(db_path: str, phone: str, **kwargs) -> None:
    """Upsert bot session into Supabase, updating only the provided fields."""
    kwargs["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        # UPDATE existing row; if no rows matched, INSERT a new one.
        result = _client().table("bot_sessions").update(kwargs).eq("phone", phone).execute()
        if not result.data:
            kwargs["phone"] = phone
            _client().table("bot_sessions").insert(kwargs).execute()
    except Exception as e:
        logger.error(f"save_session error for {phone}: {e}")


def clear_session(db_path: str, phone: str) -> None:
    """Delete bot session from Supabase."""
    try:
        _client().table("bot_sessions").delete().eq("phone", phone).execute()
    except Exception as e:
        logger.error(f"clear_session error for {phone}: {e}")


# ── customer_profiles ─────────────────────────────────────────────────────────

def save_customer_profile(phone: str, settings: dict, db_path: str) -> None:
    """Upsert customer's last-used print settings."""
    row = {
        "phone":          phone,
        "last_size":      settings["size"],
        "last_colour":    settings["colour"],
        "last_layout":    settings["layout"],
        "last_copies":    settings["copies"],
        "last_finishing": settings["finishing"],
        "last_delivery":  int(settings["delivery"]),
        "updated_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        _client().table("customer_profiles").upsert(row, on_conflict="phone").execute()
    except Exception as e:
        logger.error(f"save_customer_profile error for {phone}: {e}")


# ── jobs ──────────────────────────────────────────────────────────────────────

def get_job(job_id: str) -> dict:
    """Fetch a single job row."""
    try:
        result = _client().table("jobs").select("*").eq("job_id", job_id).execute()
        return result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"get_job error for {job_id}: {e}")
        return {}


def get_job_filepath(job_id: str) -> str | None:
    """Return file_url (cloud) or filepath (store PC) for a job."""
    job = get_job(job_id)
    return job.get("file_url") or job.get("filepath")


def get_job_sender(job_id: str) -> str | None:
    """Return the customer phone for a job."""
    return get_job(job_id).get("sender")


def update_job_settings(job_id: str, amount_quoted: float, copies: int,
                        finishing: str, size: str, colour: str, layout: str) -> None:
    """Persist quoted price and print settings onto a job row."""
    try:
        _client().table("jobs").update({
            "amount_quoted": amount_quoted,
            "copies":        copies,
            "finishing":     finishing,
            "size":          size,
            "colour":        colour,
            "layout":        layout,
        }).eq("job_id", job_id).execute()
    except Exception as e:
        logger.error(f"update_job_settings error for {job_id}: {e}")


def update_job_delivery(job_id: str, delivery: int) -> None:
    """Set delivery flag on a single job."""
    try:
        _client().table("jobs").update({"delivery": delivery}).eq("job_id", job_id).execute()
    except Exception as e:
        logger.error(f"update_job_delivery error for {job_id}: {e}")


def update_job_paid(job_id: str, amount: float, method: str, pay_id: str) -> None:
    """Mark a job as Paid and record payment details."""
    try:
        _client().table("jobs").update({
            "status":              "Paid",
            "amount_collected":    amount,
            "payment_mode":        method,
            "razorpay_payment_id": pay_id,
        }).eq("job_id", job_id).execute()
    except Exception as e:
        logger.error(f"update_job_paid error for {job_id}: {e}")


def update_jobs_payment_link(job_ids: list, link_id: str, link_sent_at: str) -> None:
    """Set the Razorpay link ID on multiple jobs."""
    try:
        for jid in job_ids:
            _client().table("jobs").update({
                "razorpay_link_id": link_id,
                "link_sent_at":     link_sent_at,
            }).eq("job_id", jid).execute()
    except Exception as e:
        logger.error(f"update_jobs_payment_link error: {e}")


def insert_job_from_webhook(job_id: str, sender: str, filename: str,
                            file_url: str) -> None:
    """Insert a new Pending job row when a file arrives via WhatsApp webhook."""
    try:
        _client().table("jobs").upsert({
            "job_id":      job_id,
            "sender":      sender,
            "filename":    filename,
            "file_url":    file_url,
            "status":      "Pending",
            "received_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, on_conflict="job_id").execute()
    except Exception as e:
        logger.error(f"insert_job_from_webhook error for {job_id}: {e}")


# ── job_batches ───────────────────────────────────────────────────────────────

def get_batch(batch_id: str) -> dict:
    """Fetch a job batch row."""
    try:
        result = _client().table("job_batches").select("*").eq("batch_id", batch_id).execute()
        return result.data[0] if result.data else {}
    except Exception as e:
        logger.error(f"get_batch error for {batch_id}: {e}")
        return {}


def update_batch_payment(batch_id: str, total_amount: float,
                         link_id: str, link_sent_at: str) -> None:
    """Record payment link details on a batch."""
    try:
        _client().table("job_batches").update({
            "total_amount":     total_amount,
            "razorpay_link_id": link_id,
            "link_sent_at":     link_sent_at,
            "status":           "awaiting_payment",
        }).eq("batch_id", batch_id).execute()
    except Exception as e:
        logger.error(f"update_batch_payment error for {batch_id}: {e}")


def update_batch_paid(batch_id: str) -> None:
    """Mark a batch as paid."""
    try:
        _client().table("job_batches").update(
            {"status": "paid"}
        ).eq("batch_id", batch_id).execute()
    except Exception as e:
        logger.error(f"update_batch_paid error for {batch_id}: {e}")


# ── job_reviews ───────────────────────────────────────────────────────────────

def get_pending_review(phone: str) -> dict | None:
    """Return a pending (unsent rating) review row for a customer, or None."""
    try:
        result = (
            _client().table("job_reviews")
            .select("id")
            .eq("phone", phone)
            .is_("rating", "null")
            .eq("review_sent", True)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception as e:
        logger.error(f"get_pending_review error for {phone}: {e}")
        return None


# ── Supabase Storage (replaces C:\Printosky\Jobs\Incoming\) ──────────────────

INCOMING_BUCKET = "incoming-files"


def upload_file(filename: str, content: bytes, mime_type: str) -> str:
    """
    Upload a customer file to Supabase Storage.
    Returns the public URL (store PC polls Supabase and downloads from here).
    """
    try:
        _client().storage.from_(INCOMING_BUCKET).upload(
            path=filename,
            file=content,
            file_options={"content-type": mime_type, "upsert": "true"},
        )
        return _client().storage.from_(INCOMING_BUCKET).get_public_url(filename)
    except Exception as e:
        logger.error(f"upload_file error for {filename}: {e}")
        return ""
