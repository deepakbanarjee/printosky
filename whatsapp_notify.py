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
import requests as _requests

logger = logging.getLogger("whatsapp_notify")

# ── Meta Cloud API config ──────────────────────────────────────────────────────
META_PHONE_ID = os.environ.get("META_PHONE_NUMBER_ID", "")
META_TOKEN    = os.environ.get("META_SYSTEM_USER_TOKEN", "")
GRAPH_URL     = "https://graph.facebook.com/v21.0"
STORE_PHONE   = os.environ.get("STORE_WHATSAPP_PHONE", "919495706405")  # Oxygen WABA number (with country code)


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
                try:
                    from db_cloud import log_message
                    log_message(digits, "outbound", message[:500], message_type="text")
                except Exception:
                    pass
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


def _normalise_phone(phone: str) -> str:
    digits = phone.replace("@c.us", "").replace("+", "").strip()
    if len(digits) == 10:
        digits = "91" + digits
    return digits


def _post_interactive(phone: str, interactive: dict, log_text: str) -> bool:
    """POST a type=interactive message (buttons or list) via Meta Cloud API."""
    if not phone or not META_PHONE_ID or not META_TOKEN:
        if not META_PHONE_ID or not META_TOKEN:
            logger.warning("Meta interactive send skipped: token/phone-id not set")
        return False
    digits  = _normalise_phone(phone)
    url     = f"{GRAPH_URL}/{META_PHONE_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to":   digits,
        "type": "interactive",
        "interactive": interactive,
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {META_TOKEN}"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            if r.status == 200:
                try:
                    from db_cloud import log_message
                    log_message(digits, "outbound", log_text[:500], message_type="text")
                except Exception:
                    pass
                return True
            body = r.read().decode()
            logger.warning(f"Meta interactive send failed: {r.status} {body[:300]}")
            return False
    except Exception as e:
        # urllib raises HTTPError for 4xx — capture Meta's error body for debugging.
        try:
            err_body = e.read().decode()[:300]  # type: ignore[attr-defined]
        except Exception:
            err_body = ""
        logger.warning(f"Meta interactive error: {e} {err_body}")
        return False


def send_buttons(phone: str, body: str, buttons: list,
                 header: str | None = None, footer: str | None = None) -> bool:
    """Send up to 3 reply buttons.

    buttons: list of (id, title) tuples. Title is truncated to 20 chars (Meta limit).
    """
    action_buttons = []
    for bid, title in buttons[:3]:
        action_buttons.append({
            "type": "reply",
            "reply": {"id": str(bid)[:256], "title": str(title)[:20]},
        })
    interactive: dict = {
        "type":   "button",
        "body":   {"text": body[:1024]},
        "action": {"buttons": action_buttons},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}
    log_text = body + "\n[" + " | ".join(t for _, t in buttons[:3]) + "]"
    return _post_interactive(phone, interactive, log_text)


def send_list(phone: str, body: str, button_text: str, rows: list,
              header: str | None = None, section_title: str = "Options") -> bool:
    """Send a list (tap-to-open menu) with up to 10 rows.

    rows: list of dicts {"id", "title", "description"(optional)}.
    """
    list_rows = []
    for r in rows[:10]:
        row = {"id": str(r["id"])[:200], "title": str(r["title"])[:24]}
        if r.get("description"):
            row["description"] = str(r["description"])[:72]
        list_rows.append(row)
    interactive: dict = {
        "type":   "list",
        "body":   {"text": body[:1024]},
        "action": {"button": button_text[:20],
                   "sections": [{"title": section_title[:24], "rows": list_rows}]},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    log_text = body + "\n[" + " | ".join(r["title"] for r in rows[:10]) + "]"
    return _post_interactive(phone, interactive, log_text)


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


def send_file_received_with_quote_start(job_id: str, filename: str, sender: str) -> bool:
    """Single combined message: receipt + first quote question.

    Sends ONE Meta API call instead of two, staying safely within
    Vercel's 10-second function timeout on Hobby plan.
    """
    if not sender:
        return False
    msg = (
        "✅ *File received!*\n\n"
        f"📋 Job ID: `{job_id}`\n"
        f"📄 File: {filename}\n\n"
        "📄 *What paper size do you need?*\n\n"
        "1️⃣  A4 (standard)\n"
        "2️⃣  A3 (large)\n"
        "3️⃣  Other (we'll quote manually)\n\n"
        "_Reply with 1, 2, or 3_"
    )
    return _send(sender, msg)


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
    """Notify customer that job is ready to collect.

    DEPRECATED: single-store-baked. Use ``send_pickup_ready`` for new flows
    that go through the routing engine.
    """
    msg = (
        "🎉 *Your print job is ready!*\n\n"
        f"📋 Job: `{job_id}`\n\n"
        "Please collect at your convenience.\n"
        f"📞 {STORE_PHONE}\n\n"
        "— Printosky / Oxygen Globally, Thriprayar 🖨️"
    )
    return _send(sender, msg)


def send_pickup_ready(sender: str, pickup_code: str,
                      store_label: str | None,
                      store_address: str,
                      deep_link: str | None = None) -> bool:
    """Notify the customer that their job is ready for pickup.

    This is the *only* customer-facing message in which the fulfilling
    store's name appears. Multi-store-aware: the platform stays the
    Printosky brand throughout the rest of the flow.
    """
    label_line = f"{store_label}\n" if store_label else ""
    link_line = f"\n🔗 Track: {deep_link}" if deep_link else ""
    msg = (
        "🎉 *Your job is ready for pickup!*\n\n"
        f"🎫 Code: *{pickup_code}*\n\n"
        f"📍 *Pickup at:*\n{label_line}{store_address}\n\n"
        "Please show this code at the counter."
        f"{link_line}\n\n"
        "— Printosky 🖨️"
    )
    return _send(sender, msg)


def send_pickup_completed(sender: str, pickup_code: str,
                          rating_url: str | None = None) -> bool:
    """Confirm pickup + (optional) ask for a rating."""
    rating_line = (
        f"\n\nHow was your experience? ⭐ {rating_url}"
        if rating_url else ""
    )
    msg = (
        "✅ *Picked up — thank you!*\n\n"
        f"🎫 {pickup_code}\n\n"
        "We hope your prints turned out great."
        f"{rating_line}\n\n"
        "— Printosky 🖨️"
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


# ── File sending via Meta media upload API ────────────────────────────────────

def _mime_to_wa_type(mime_type: str) -> str:
    """Map MIME type to WhatsApp Cloud API message type string."""
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    return "document"


def _meta_upload_media(data: bytes, mime_type: str, filename: str) -> dict:
    """Upload raw bytes to Meta and return the response dict (contains 'id').

    Uses multipart/form-data — the only format Meta accepts for media upload.
    Raises requests.HTTPError on a non-2xx response.
    """
    url = f"{GRAPH_URL}/{META_PHONE_ID}/media"
    resp = _requests.post(
        url,
        headers={"Authorization": f"Bearer {META_TOKEN}"},
        files={"file": (filename, data, mime_type)},
        data={"messaging_product": "whatsapp"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _send_meta_media(phone: str, media_id: str, msg_type: str,
                     caption: str, filename: str) -> bool:
    """Send a WhatsApp message that references a pre-uploaded media_id."""
    url = f"{GRAPH_URL}/{META_PHONE_ID}/messages"
    media_obj: dict = {"id": media_id}
    if caption:
        media_obj["caption"] = caption
    if msg_type == "document" and filename:
        media_obj["filename"] = filename
    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": msg_type,
        msg_type: media_obj,
    }
    resp = _requests.post(
        url,
        headers={
            "Authorization": f"Bearer {META_TOKEN}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=15,
    )
    return resp.ok


def send_file(phone: str, data: bytes, mime_type: str,
              filename: str, caption: str = "") -> bool:
    """Upload a file to Meta then send it as a WhatsApp message.

    Returns True on success, False on any error (never raises).
    """
    try:
        upload_resp = _meta_upload_media(data, mime_type, filename)
        media_id = upload_resp["id"]
        msg_type = _mime_to_wa_type(mime_type)
        return _send_meta_media(phone, media_id, msg_type, caption, filename)
    except Exception as exc:
        logger.error("send_file failed for %s: %s", phone, exc)
        return False
