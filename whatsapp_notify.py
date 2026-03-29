"""
PRINTOSKY WHATSAPP NOTIFIER
============================
Sends WhatsApp messages via Meta WhatsApp Cloud API.
Number: 9446903907 (registered as WABA number in Meta Business Manager)

Meta Cloud API:
  POST https://graph.facebook.com/v18.0/{META_PHONE_NUMBER_ID}/messages
  Authorization: Bearer {META_SYSTEM_USER_TOKEN}
"""

import os
import json
import logging
import urllib.request

logger = logging.getLogger("whatsapp_notify")

# ── Meta Cloud API config ──────────────────────────────────────────────────────
META_PHONE_ID = os.environ.get("META_PHONE_NUMBER_ID", "")
META_TOKEN    = os.environ.get("META_SYSTEM_USER_TOKEN", "")
GRAPH_URL     = "https://graph.facebook.com/v18.0"
STORE_PHONE   = "919446903907"    # Printosky WABA number (with country code)


def _send_meta(phone: str, message: str) -> bool:
    """Send a text message via Meta WhatsApp Cloud API."""
    if not phone or not META_PHONE_ID or not META_TOKEN:
        if not META_PHONE_ID or not META_TOKEN:
            logger.warning("Meta send skipped: META_PHONE_NUMBER_ID or META_SYSTEM_USER_TOKEN not set")
        return False

    # Normalise: strip + and @c.us, ensure 91 prefix for Indian numbers
    digits = phone.replace("@c.us", "").replace("+", "").strip()
    if len(digits) == 10:
        digits = "91" + digits

    url     = f"{GRAPH_URL}/{META_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to":   digits,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type":  "application/json",
                "Authorization": f"Bearer {META_TOKEN}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status == 200:
                logger.info(f"Meta sent to {digits}")
                return True
            body = r.read().decode()
            logger.warning(f"Meta send failed: {r.status} {body[:200]}")
            return False
    except Exception as e:
        logger.warning(f"Meta notify error: {e}")
        return False


def _send(phone: str, message: str) -> bool:
    """Send a WhatsApp message (routes to Meta Cloud API)."""
    return _send_meta(phone, message)


def send_file_received(job_id: str, filename: str, sender: str):
    """Message 1 - instant receipt when file arrives."""
    if not sender:
        return
    msg = (
        "✅ *File received!*\n\n"
        f"📋 Job ID: `{job_id}`\n"
        f"📄 File: {filename}\n\n"
        "_We're reviewing your file and will send pricing shortly..._\n"
        "— Printosky / Oxygen Globally 🖨️"
    )
    _send(sender, msg)


def send_payment_link(sender: str, job_id: str, amount: float,
                      pay_url: str, description: str = "") -> bool:
    """Send a Razorpay payment link to the customer."""
    desc_line = f"📝 {description}\n" if description else ""
    msg = (
        "💰 *Payment Details*\n\n"
        f"📋 Job: `{job_id}`\n"
        f"{desc_line}"
        f"💵 Amount: *₹{amount:.2f}*\n\n"
        f"🔗 Pay securely here:\n{pay_url}\n\n"
        f"_Reply PAID or call {STORE_PHONE} after payment._\n\n"
        "— Printosky 🖨️"
    )
    return _send(sender, msg)


def send_payment_confirmed(sender: str, job_id: str, amount: float) -> bool:
    """Confirm payment received."""
    msg = (
        "✅ *Payment confirmed!*\n\n"
        f"📋 Job: `{job_id}`\n"
        f"💵 ₹{amount:.2f} received\n\n"
        "Your job is now in the print queue.\n"
        "We'll notify you when it's ready for pickup 🙏\n\n"
        "— Printosky 🖨️"
    )
    return _send(sender, msg)


def send_job_ready(sender: str, job_id: str) -> bool:
    """Notify customer that job is ready to collect."""
    msg = (
        "🎉 *Your print job is ready!*\n\n"
        f"📋 Job: `{job_id}`\n\n"
        "Please collect at your convenience.\n"
        f"📞 {STORE_PHONE}\n\n"
        "— Printosky / Oxygen Globally, Thriprayar 🖨️"
    )
    return _send(sender, msg)


def send_staff_alert(message: str) -> bool:
    """Send an alert to the store staff number."""
    return _send(STORE_PHONE, f"⚠️ *Staff Alert*\n\n{message}")


def send_timeout_alert(job_id: str, step: str) -> bool:
    """Alert staff that a bot session timed out."""
    msg = (
        "⏰ *Bot timeout*\n\n"
        f"Job: `{job_id}`\n"
        f"Stopped at: {step}\n\n"
        "Customer may need a manual quote.\n"
        f"Type: `quote {job_id} AMOUNT`"
    )
    return _send(STORE_PHONE, msg)
