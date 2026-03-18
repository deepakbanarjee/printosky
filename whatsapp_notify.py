"""
PRINTOSKY WHATSAPP NOTIFIER
============================
Sends WhatsApp messages via the local whatsapp-web.js capture server.
All messages go through port 3001 (send server in index.js).
"""

import requests
import logging

logger = logging.getLogger("whatsapp_notify")

SEND_URL     = "http://localhost:3001/send"
STORE_PHONE  = "8943232033"   # Test number — swap to 9495706405 for production


def _send(phone: str, message: str) -> bool:
    """Send a WhatsApp message via the local capture server."""
    if not phone:
        return False
    digits = phone.replace("@c.us", "").replace("+", "").strip()
    try:
        r = requests.post(SEND_URL, json={"phone": digits, "message": message}, timeout=10)
        if r.status_code == 200:
            logger.info(f"WhatsApp sent to {digits}")
            return True
        logger.warning(f"WhatsApp send failed: {r.status_code} {r.text[:100]}")
        return False
    except Exception as e:
        logger.warning(f"WhatsApp notify error: {e}")
        return False


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
