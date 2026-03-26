"""
PRINTOSKY WHATSAPP NOTIFIER
============================
Sends WhatsApp messages via AiSensy Campaign API.
Number: 9446903907 (WhatsApp Business API — WABA via AiSensy)

AiSensy Campaign API:
  POST https://backend.aisensy.com/campaign/t1/api/v2
  Requires a campaign named AISENSY_CAMPAIGN in the AiSensy dashboard
  with a single {{1}} body parameter.
"""

import requests
import logging

logger = logging.getLogger("whatsapp_notify")

# ── AiSensy config ─────────────────────────────────────────────────────────────
AISENSY_API_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjY5YjY0YzFjYmNjYTFkMGRiYWZlYTBhNyIsIm5hbWUiOiJwcmludG9za3kuY29tICIsImFwcE5hbWUiOiJBaVNlbnN5IiwiY2xpZW50SWQiOiI2OWI2NGMxY2JjY2ExZDBkYmFmZWEwYTIiLCJhY3RpdmVQbGFuIjoiRlJFRV9GT1JFVkVSIiwiaWF0IjoxNzczNTU0NzE2fQ.zmX52sQvwxPiwnXA3G9i5qKdrUg0Yhg6BkuZxr-ePJA"
AISENSY_API_URL  = "https://backend.aisensy.com/campaign/t1/api/v2"
AISENSY_CAMPAIGN = "chatbot_reply"   # Campaign name in AiSensy dashboard (body = {{1}})
STORE_PHONE      = "919446903907"    # Printosky AiSensy number (with country code)


def _send(phone: str, message: str) -> bool:
    """Send a WhatsApp message via AiSensy Campaign API."""
    if not phone:
        return False
    # Normalise: strip + and @c.us, ensure 91 prefix for Indian numbers
    digits = phone.replace("@c.us", "").replace("+", "").strip()
    if len(digits) == 10:
        digits = "91" + digits

    payload = {
        "apiKey":         AISENSY_API_KEY,
        "campaignName":   AISENSY_CAMPAIGN,
        "destination":    digits,
        "userName":       "Customer",
        "templateParams": [message],
    }
    try:
        r = requests.post(AISENSY_API_URL, json=payload, timeout=15)
        if r.status_code == 200:
            logger.info(f"AiSensy sent to {digits}")
            return True
        logger.warning(f"AiSensy send failed: {r.status_code} {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"AiSensy notify error: {e}")
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
