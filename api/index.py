"""
PRINTOSKY — Vercel Python Serverless Webhook
=============================================
Handles:
  GET  /whatsapp-webhook  → Meta webhook verification challenge
  POST /whatsapp-webhook  → Incoming WhatsApp messages (text + media)
  POST /webhook/razorpay  → Razorpay payment confirmations
  GET  /                  → Health check

Differences from webhook_receiver.py (store PC):
  - No daemon threads — synchronous processing (serverless constraint)
  - SQLite → Supabase via db_cloud.py
  - File writes → Supabase Storage via db_cloud.upload_file()
  - Port 3003 bot relay → direct whatsapp_bot.handle_message() call
"""

import sys
import os

# Ensure repo root is on the path so sibling modules (whatsapp_bot, db_cloud, etc.) import.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Load .env for local dev; Vercel injects env vars natively in production.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))
except ImportError:
    pass

import hmac
import hashlib
import json
import logging
import re
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("api.webhook")

# ── Config (from env vars set in Vercel dashboard) ───────────────────────────
META_APP_SECRET           = os.environ.get("META_APP_SECRET", "")
META_WEBHOOK_VERIFY_TOKEN = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "PrintoskyMeta2026")
META_SYSTEM_USER_TOKEN    = os.environ.get("META_SYSTEM_USER_TOKEN", "")
GRAPH_API_BASE            = "https://graph.facebook.com/v21.0"

FILE_MIME_TYPES = {
    "application/pdf":                                                          ".pdf",
    "application/msword":                                                       ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint":                                            ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.ms-excel":                                                 ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":       ".xlsx",
    "image/jpeg": ".jpg",
    "image/png":  ".png",
    "image/gif":  ".gif",
    "image/webp": ".webp",
}


# ── Signature helpers ─────────────────────────────────────────────────────────

def _verify_meta_sig(body: bytes, sig_header: str) -> bool:
    if not META_APP_SECRET:
        logger.error("META_APP_SECRET not configured — rejecting all webhooks")
        return False
    if not sig_header.startswith("sha256="):
        return False
    expected = hmac.new(META_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header[7:])


# ── Meta media download ───────────────────────────────────────────────────────

def _download_meta_media(media_id: str) -> bytes | None:
    """Two-step Meta Graph API media fetch (URL lookup then binary download)."""
    try:
        req = urllib.request.Request(
            f"{GRAPH_API_BASE}/{media_id}",
            headers={"Authorization": f"Bearer {META_SYSTEM_USER_TOKEN}"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            info = json.loads(r.read())
        url = info.get("url", "")
        if not url:
            logger.error(f"No download URL for media_id {media_id}")
            return None
        req2 = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {META_SYSTEM_USER_TOKEN}"},
        )
        with urllib.request.urlopen(req2, timeout=55) as r2:
            return r2.read()
    except Exception as e:
        logger.error(f"Media download error for {media_id}: {e}")
        return None


# ── Message processors ────────────────────────────────────────────────────────

# ── Referral tracking ────────────────────────────────────────────────────────

def _normalize_phone(p: str) -> str:
    """Canonicalize a WhatsApp phone to digits-only Indian format (91XXXXXXXXXX).

    Strips '@c.us', '@lid', '@s.whatsapp.net' suffixes, removes non-digits,
    auto-prepends '91' for bare 10-digit Indian numbers. Returns '' if empty.
    """
    if not p:
        return ""
    s = str(p).replace("@c.us", "").replace("@lid", "").replace("@s.whatsapp.net", "").strip()
    digits = "".join(c for c in s if c.isdigit())
    if len(digits) == 10:  # bare Indian mobile
        digits = "91" + digits
    return digits


def _capture_referral_code(phone: str, text: str) -> None:
    """If text starts with ref_CODE, store the code in bot_sessions (first time only)."""
    from db_cloud import _client
    m = re.match(r'^ref_(\w{1,30})', text.strip(), re.IGNORECASE)
    if not m:
        return
    code = m.group(1).upper()
    phone = _normalize_phone(phone)
    if not phone:
        return
    try:
        existing = _client().table("bot_sessions").select("referral_code").eq("phone", phone).execute()
        if existing.data and existing.data[0].get("referral_code"):
            return  # already tagged — don't overwrite
        _client().table("bot_sessions").upsert({"phone": phone, "referral_code": code}).execute()
        logger.info(f"Referral code {code!r} captured for {phone}")
    except Exception as e:
        logger.error(f"_capture_referral_code error for {phone}: {e}")


def _credit_referrer(phone: str, order_id: str) -> None:
    """Insert a referral_credits row if this customer arrived via a ref link."""
    from db_cloud import _client
    phone = _normalize_phone(phone)
    if not phone or not order_id:
        return
    try:
        row = _client().table("bot_sessions").select("referral_code").eq("phone", phone).execute()
        if not row.data:
            return
        code = (row.data[0].get("referral_code") or "").strip()
        if not code:
            return
        # Idempotency: skip if a credit already exists for this (code, order_id)
        dup = _client().table("referral_credits").select("id").eq("referrer_code", code).eq("order_id", order_id).execute()
        if dup.data:
            logger.info(f"Referral credit already exists for {code} / {order_id} — skipping")
            return
        _client().table("referral_credits").insert({
            "referrer_code": code,
            "customer_phone": phone,
            "order_id": order_id,
            "amount_inr": 20,
        }).execute()
        logger.info(f"Referral credit Rs.20 logged -> {code!r} for order {order_id}")
    except Exception as e:
        logger.error(f"_credit_referrer error for {phone} / {order_id}: {e}")


def _send_credits_balance(phone: str) -> None:
    """Reply to a 'MY CREDITS' message with the customer's referral store-credit balance."""
    from db_cloud import _client
    from whatsapp_notify import _send
    raw_phone = phone
    phone = _normalize_phone(phone)
    if not phone:
        return
    try:
        ref = _client().table("referrers").select("code").eq("label", phone).execute()
        if not ref.data:
            _send(raw_phone,
                  "You don't have a Printosky referral code yet.\n\n"
                  "Tip: rate your next order 4 or 5 stars - we'll send you a personal "
                  "share link so you can start earning store credit.")
            return
        code = ref.data[0]["code"]
        # Sum unredeemed credits only.
        credits = (_client().table("referral_credits")
                            .select("amount_inr")
                            .eq("referrer_code", code)
                            .is_("redeemed_at", "null")
                            .execute())
        balance = sum(int(row.get("amount_inr") or 0) for row in (credits.data or []))
        share_link = f"https://wa.me/919495706405?text=ref_{code}"
        if balance == 0:
            _send(raw_phone,
                  f"Your share code: *{code}*\n\n"
                  f"Rs.0 store credit so far - you'll earn Rs.20 each time a friend orders using your link:\n"
                  f"{share_link}")
        else:
            _send(raw_phone,
                  f"*Printosky Store Credit*\n\n"
                  f"Balance: *Rs.{balance}*\n"
                  f"Code: {code}\n\n"
                  f"Mention this on your next order - staff will apply it at checkout.\n\n"
                  f"Keep sharing: {share_link}")
        logger.info(f"Credits balance Rs.{balance} sent to {phone} (code {code})")
    except Exception as e:
        logger.error(f"_send_credits_balance error for {phone}: {e}")
        try:
            _send(raw_phone, "Sorry, couldn't fetch your balance right now. Please try again later.")
        except Exception:
            pass


def _handle_text(sender: str, text: str) -> None:
    """Route a customer text through the bot state machine and send replies."""
    from whatsapp_bot import handle_message
    from whatsapp_notify import _send, send_staff_alert

    # Capture referral code; treat ref_CODE message as a plain greeting
    _capture_referral_code(sender, text)

    # Intercept MY CREDITS query - return balance, don't pass to bot
    norm = re.sub(r"\s+", " ", text.strip()).upper()
    if norm in ("MY CREDITS", "MYCREDITS", "MY CREDIT", "BALANCE", "CREDITS"):
        _send_credits_balance(sender)
        return

    bot_text = "hi" if re.match(r'^ref_\w', text.strip(), re.IGNORECASE) else text

    replies = handle_message(
        phone=sender,
        text=bot_text,
        job_id=None,
        page_count=0,
        db_path="supabase",   # db_path is ignored in cloud mode
    )
    for reply in replies:
        if isinstance(reply, str):
            _send(sender, reply)
            try:
                from db_cloud import log_message
                log_message(sender, "outbound", reply, message_type="text")
            except Exception:
                pass
        elif isinstance(reply, tuple) and reply:
            tag = reply[0]
            if tag in ("STAFF_QUOTE", "STAFF_MIXED_TIMEOUT"):
                msg = reply[1] if len(reply) > 1 else str(reply)
                send_staff_alert(msg)


def _handle_media(sender: str, msg_type: str, media_id: str,
                  mime_type: str, orig_filename: str) -> str | None:
    """Download a WhatsApp attachment, upload to Supabase Storage, create job row."""
    from db_cloud import upload_file, insert_job_from_webhook, clear_session, save_session
    from whatsapp_notify import send_file_received_with_quote_start

    ext = FILE_MIME_TYPES.get(mime_type, "")
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")

    if orig_filename and "." in orig_filename:
        base_name = re.sub(r"[^\w.\- ]", "_", os.path.basename(orig_filename)).strip()
    else:
        base_name = f"{sender}_{ts}{ext or '.bin'}"

    dest_name = f"{sender}_{ts}_{base_name}"   # unique storage key
    job_id    = f"OSP-{datetime.now().strftime('%Y%m%d')}-{sender[-4:]}-{ts[-4:]}-{os.urandom(3).hex()}"

    # ── Step 1: ONE Meta API call — receipt + size question combined ─────────
    # This stays within Vercel's 10s Hobby timeout. Splitting into two calls
    # caused the second to be killed by the timeout.
    insert_job_from_webhook(job_id, sender, base_name, "")   # file_url filled after upload
    sent = send_file_received_with_quote_start(job_id, base_name, sender)
    logger.info(f"Job created, combined receipt+question sent ({sent}): {job_id} for {sender}")

    # ── Step 2: save bot session so handle_message can process the reply ────
    try:
        clear_session("supabase", sender)
        save_session("supabase", sender,
                     job_id=job_id,
                     batch_id=job_id,
                     step="size",
                     current_job_index=0,
                     jobs_json=json.dumps([{"job_id": job_id,
                                            "filename": base_name,
                                            "page_count": 0}]),
                     saved_json=None,
                     job_settings_json="{}")
        logger.info(f"Session saved step=size for {sender}")
    except Exception as e:
        logger.error(f"Session save error for {sender}: {e}")

    # ── Step 3: download + upload to Supabase Storage (slow — runs last) ────
    content = _download_meta_media(media_id)
    if content is None:
        logger.error(f"Failed to download {media_id} from {sender}")
        return None
    content  = _compress_lossless(content, mime_type or "")
    file_url = upload_file(dest_name, content, mime_type or "application/octet-stream")
    insert_job_from_webhook(job_id, sender, base_name, file_url)
    logger.info(f"Uploaded {dest_name} ({len(content)} bytes) → {file_url}")
    return dest_name  # storage path — callers store this in media_url column


def _process_meta_webhook(data: dict) -> None:
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value    = change.get("value", {})
            contacts = value.get("contacts", [])
            pushname = (contacts[0].get("profile", {}).get("name")
                        if contacts else None)
            for msg in value.get("messages", []):
                sender   = msg.get("from", "")
                msg_type = msg.get("type", "")
                logger.info(f"Meta message from {sender}: type={msg_type}")

                if msg_type == "text":
                    text = (msg.get("text") or {}).get("body", "").strip()
                    if text:
                        _handle_text(sender, text)
                        try:
                            from db_cloud import log_message, upsert_contact
                            log_message(sender, "inbound", text, message_type="text")
                            upsert_contact(sender, name=pushname)
                        except Exception:
                            pass

                elif msg_type in ("document", "image", "video", "audio"):
                    blk      = msg.get(msg_type, {})
                    media_id = blk.get("id", "")
                    mime     = blk.get("mime_type", "")
                    fname    = blk.get("filename", "")
                    if media_id:
                        storage_path = _handle_media(sender, msg_type, media_id, mime, fname)
                        try:
                            from db_cloud import log_message, upsert_contact
                            log_message(sender, "inbound",
                                        fname or f"[{msg_type}]",
                                        message_type=msg_type, filename=fname,
                                        media_url=storage_path)
                            upsert_contact(sender, name=pushname)
                        except Exception:
                            pass


def _process_razorpay_payment(data: dict) -> None:
    from razorpay_integration import parse_payment_webhook
    from whatsapp_notify import send_payment_confirmed
    from db_cloud import (get_batch, get_job, update_job_paid,
                          update_batch_paid, update_jobs_payment_link)

    payment = parse_payment_webhook(data)
    if not payment:
        logger.debug(f"Razorpay event ignored: {data.get('event')}")
        return

    ref_id = payment["job_id"]
    amount = payment["amount"]
    method = payment["method"]
    pay_id = payment["payment_id"]
    logger.info(f"Payment confirmed: {ref_id} ₹{amount} via {method}")

    # Batch payment?
    batch = get_batch(ref_id)
    if batch:
        job_ids = [j for j in (batch.get("job_ids") or "").split(",") if j.strip()]
        for jid in job_ids:
            update_job_paid(jid, amount, method, pay_id)
        update_batch_paid(ref_id)
        phone = batch.get("phone", "")
        if phone:
            send_payment_confirmed(phone, ref_id, amount)
            _credit_referrer(phone, ref_id)
        logger.info(f"Batch {ref_id}: {len(job_ids)} jobs marked Paid")
        return

    # Single job
    update_job_paid(ref_id, amount, method, pay_id)
    job = get_job(ref_id)
    if job.get("sender"):
        send_payment_confirmed(job["sender"], ref_id, amount)
        _credit_referrer(job["sender"], ref_id)
    logger.info(f"Job {ref_id} marked Paid")


# ── Staff PIN endpoints ───────────────────────────────────────────────────────

ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")


def _sha256(value: str) -> str:
    """SHA-256 — used for admin password comparison only. Do NOT use for PIN hashing."""
    return hashlib.sha256(value.encode()).hexdigest()


def _compress_lossless(data: bytes, mime: str) -> bytes:
    """Lossless PNG-only optimisation. All other types pass through unchanged.

    Never re-encodes JPEG (already lossy). If the compressed PNG is larger than
    the original, the original bytes are returned unchanged.
    """
    if mime != "image/png":
        return data
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        compressed = buf.getvalue()
        return compressed if len(compressed) < len(data) else data
    except Exception:
        return data  # on any error, use original unchanged


def _auth_admin_pw(pw: str) -> bool:
    """Return True if pw matches the ADMIN_PASSWORD_HASH env var via HMAC."""
    return bool(ADMIN_PASSWORD_HASH) and hmac.compare_digest(
        _sha256(pw), ADMIN_PASSWORD_HASH
    )


def _fmt_phone(phone: str) -> str:
    """Format a phone number for display. 919495706405 → +91 94957 06405."""
    if len(phone) == 12 and phone.startswith("91"):
        return f"+91 {phone[2:7]} {phone[7:]}"
    return ("+" + phone) if not phone.startswith("+") else phone


# ── PBKDF2 PIN hashing ────────────────────────────────────────────────────────
import secrets as _sec

_PBKDF2_ITER = 260_000

def _hash_pin(pin: str) -> tuple[str, str]:
    """Return (hash_hex, salt_hex) using PBKDF2-HMAC-SHA256."""
    salt = _sec.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), _PBKDF2_ITER).hex()
    return h, salt

def _verify_pin(pin: str, stored_hash: str, stored_salt: str | None) -> bool:
    """Constant-time PIN verify. Handles legacy SHA-256 (salt=None) and PBKDF2."""
    if stored_salt is None:
        return hmac.compare_digest(stored_hash, hashlib.sha256(pin.encode()).hexdigest())
    expected = hashlib.pbkdf2_hmac("sha256", pin.encode(), stored_salt.encode(), _PBKDF2_ITER).hex()
    return hmac.compare_digest(stored_hash, expected)


def _send_cors_headers(h) -> None:
    """Attach CORS headers. Endpoints are individually auth-gated so * is safe."""
    h.send_header("Access-Control-Allow-Origin",  "*")
    h.send_header("Access-Control-Allow-Methods", "POST, GET, PATCH, OPTIONS")
    h.send_header("Access-Control-Allow-Headers", "Content-Type, X-Hub-Signature-256, X-Razorpay-Signature, X-Staff-Pin, X-Student-Phone")
    h.send_header("Access-Control-Max-Age",       "86400")


def _json_response(h, status: int, data: dict) -> None:
    body = json.dumps(data).encode()
    h.send_response(status)
    h.send_header("Content-Type", "application/json")
    _send_cors_headers(h)
    h.end_headers()
    h.wfile.write(body)


def _handle_staff_set_pin(h, body: bytes) -> None:
    """POST /staff/set-pin — staff changes own PIN using current PIN as auth."""
    try:
        payload = json.loads(body)
        staff_id  = payload.get("staff_id", "").strip().lower()
        current   = payload.get("current_pin", "").strip()
        new_pin   = payload.get("new_pin", "").strip()
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return

    if not staff_id or not current or not new_pin:
        _json_response(h, 400, {"error": "staff_id, current_pin, new_pin required"})
        return
    if not new_pin.isdigit() or len(new_pin) != 4:
        _json_response(h, 400, {"error": "new_pin must be 4 digits"})
        return

    from db_cloud import _client
    try:
        result = _client().table("staff").select("pin_hash,pin_salt,active").eq("id", staff_id).execute()
        if not result.data:
            _json_response(h, 404, {"error": "Staff not found"})
            return
        row = result.data[0]
        if not row.get("active"):
            _json_response(h, 403, {"error": "Account inactive"})
            return
        if not _verify_pin(current, row["pin_hash"], row.get("pin_salt")):
            _json_response(h, 403, {"error": "Current PIN incorrect"})
            return
        new_hash, new_salt = _hash_pin(new_pin)
        _client().table("staff").update({"pin_hash": new_hash, "pin_salt": new_salt}).eq("id", staff_id).execute()
        _json_response(h, 200, {"ok": True, "message": "PIN updated"})
        logger.info(f"Staff {staff_id} changed own PIN")
    except Exception as e:
        logger.error(f"set-pin error: {e}")
        _json_response(h, 500, {"error": "Server error"})


def _handle_staff_resume(h, body: bytes) -> None:
    """POST /staff/resume — resume bot for a customer held by staff."""
    try:
        payload = json.loads(body)
        phone   = payload.get("phone", "").strip()
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return

    if not phone:
        _json_response(h, 400, {"error": "phone required"})
        return

    from db_cloud import _client, save_session
    try:
        result = _client().table("bot_sessions").select("prev_step").eq("phone", phone).execute()
        if not result.data:
            _json_response(h, 404, {"error": "No session found for this phone"})
            return
        prev_step = result.data[0].get("prev_step") or "size"
        save_session("supabase", phone, step=prev_step)
        _json_response(h, 200, {"ok": True, "message": f"Bot resumed for {phone} at step={prev_step}"})
        logger.info(f"Staff resumed bot for {phone} at step={prev_step}")
    except Exception as e:
        logger.error(f"staff-resume error: {e}")
        _json_response(h, 500, {"error": "Server error"})


def _handle_admin_reset_pin(h, body: bytes) -> None:
    """POST /admin/reset-pin — admin resets any staff PIN using admin password."""
    try:
        payload = json.loads(body)
        admin_pw  = payload.get("admin_password", "").strip()
        staff_id  = payload.get("staff_id", "").strip().lower()
        new_pin   = payload.get("new_pin", "").strip()
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return

    if not ADMIN_PASSWORD_HASH:
        _json_response(h, 503, {"error": "Admin auth not configured"})
        return
    if not hmac.compare_digest(_sha256(admin_pw), ADMIN_PASSWORD_HASH):
        _json_response(h, 403, {"error": "Invalid admin password"})
        return
    if not staff_id or not new_pin:
        _json_response(h, 400, {"error": "staff_id, new_pin required"})
        return
    if not new_pin.isdigit() or len(new_pin) != 4:
        _json_response(h, 400, {"error": "new_pin must be 4 digits"})
        return

    from db_cloud import _client
    try:
        new_hash, new_salt = _hash_pin(new_pin)
        result = _client().table("staff").update({"pin_hash": new_hash, "pin_salt": new_salt}).eq("id", staff_id).execute()
        if not result.data:
            _json_response(h, 404, {"error": "Staff not found"})
            return
        _json_response(h, 200, {"ok": True, "message": f"PIN reset for {staff_id}"})
        logger.info(f"Admin reset PIN for {staff_id}")
    except Exception as e:
        logger.error(f"admin reset-pin error: {e}")
        _json_response(h, 500, {"error": "Server error"})


def _handle_admin_send(h, body: bytes) -> None:
    """POST /admin/send — staff manually sends a WhatsApp message to a customer."""
    try:
        payload  = json.loads(body)
        admin_pw = payload.get("admin_password", "").strip()
        phone    = payload.get("phone", "").strip()
        message  = payload.get("message", "").strip()
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return

    if not ADMIN_PASSWORD_HASH:
        _json_response(h, 503, {"error": "Admin auth not configured"})
        return
    if not hmac.compare_digest(_sha256(admin_pw), ADMIN_PASSWORD_HASH):
        _json_response(h, 403, {"error": "Invalid admin password"})
        return
    if not phone or not message:
        _json_response(h, 400, {"error": "phone and message required"})
        return

    from whatsapp_notify import _send
    try:
        ok = _send(phone, message)
        if ok:
            try:
                from db_cloud import log_message
                log_message(phone, "outbound", message, message_type="text")
            except Exception:
                pass
            _json_response(h, 200, {"ok": True})
            logger.info(f"Admin manually sent message to {phone}")
        else:
            _json_response(h, 502, {"error": "WhatsApp send failed"})
    except Exception as e:
        logger.error(f"admin-send error: {e}")
        _json_response(h, 500, {"error": "Server error"})


# ── Academic project order endpoints ─────────────────────────────────────────

def _acad_auth_staff(h) -> bool:
    """Return True if X-Staff-Pin matches any active staff member in Supabase."""
    pin = h.headers.get("X-Staff-Pin", "").strip()
    if not pin:
        return False
    try:
        from db_cloud import _client
        result = (
            _client()
            .table("staff")
            .select("id,pin_hash,pin_salt")
            .eq("active", True)
            .execute()
        )
        return any(
            _verify_pin(pin, r["pin_hash"], r.get("pin_salt"))
            for r in (result.data or [])
        )
    except Exception as e:
        logger.error(f"_acad_auth_staff Supabase error: {e}")
        return False


def _acad_auth_student(h, pid: str) -> bool:
    """Return True if X-Student-Phone matches the order's whatsapp_phone."""
    phone = h.headers.get("X-Student-Phone", "").strip()
    if not phone:
        return False
    try:
        from db_cloud_academic import get_order
        order = get_order(pid)
        return bool(order and order.get("whatsapp_phone") == phone)
    except Exception:
        return False


# ── Referral store-credit redemption (staff endpoints) ──────────────────────

def _handle_referrals_balance(h) -> None:
    """GET /referrals/balance?phone=91XXXXXXXXXX — staff auth.
    Returns the customer's referral code and unredeemed store-credit balance.
    """
    if not _acad_auth_staff(h):
        _json_response(h, 401, {"error": "staff PIN required"})
        return
    qs = parse_qs(urlparse(h.path).query)
    phone = _normalize_phone(qs.get("phone", [""])[0] or "")
    if not phone:
        _json_response(h, 400, {"error": "phone parameter required"})
        return
    try:
        from db_cloud import _client
        sb = _client()
        ref = sb.table("referrers").select("code").eq("label", phone).execute()
        if not ref.data:
            _json_response(h, 200, {"phone": phone, "code": None, "balance": 0, "credits": []})
            return
        code = ref.data[0]["code"]
        rows = (sb.table("referral_credits")
                  .select("id,amount_inr,customer_phone,order_id,created_at")
                  .eq("referrer_code", code)
                  .is_("redeemed_at", "null")
                  .order("created_at")
                  .execute())
        credits = rows.data or []
        balance = sum(int(c.get("amount_inr") or 0) for c in credits)
        _json_response(h, 200, {
            "phone": phone, "code": code, "balance": balance, "credits": credits
        })
    except Exception as e:
        logger.error(f"_handle_referrals_balance error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_referrals_redeem(h, body: bytes) -> None:
    """POST /referrals/redeem — staff auth.
    Body: { phone, order_id, amount_inr, staff_id }
    Marks oldest unredeemed credits up to amount_inr as redeemed.

    Idempotent: if a redemption against (referrer_code, order_id) already exists,
    returns success without further changes.

    Race-safe: each row update filters on redeemed_at IS NULL — if another worker
    grabbed the same row first, the update returns 0 rows; we skip and try the next.
    """
    if not _acad_auth_staff(h):
        _json_response(h, 401, {"error": "staff PIN required"})
        return
    try:
        data = json.loads(body or b"{}")
    except Exception:
        _json_response(h, 400, {"error": "invalid JSON"})
        return
    phone    = _normalize_phone(data.get("phone") or "")
    order_id = (data.get("order_id") or "").strip()
    staff_id = (data.get("staff_id") or "").strip() or "unknown"
    try:
        amount = int(data.get("amount_inr") or 0)
    except Exception:
        amount = 0
    if not phone or not order_id or amount <= 0:
        _json_response(h, 400, {"error": "phone, order_id, amount_inr (>0) required"})
        return
    try:
        from db_cloud import _client
        sb = _client()
        ref = sb.table("referrers").select("code").eq("label", phone).execute()
        if not ref.data:
            _json_response(h, 404, {"error": "no referrer for this phone"})
            return
        code = ref.data[0]["code"]

        # Idempotency: same (code, redeemed_order_id) already booked? Return success.
        prior = (sb.table("referral_credits")
                   .select("id,amount_inr")
                   .eq("referrer_code", code)
                   .eq("redeemed_order_id", order_id)
                   .execute())
        if prior.data:
            already = sum(int(c.get("amount_inr") or 0) for c in prior.data)
            logger.info(f"Idempotent redeem: order {order_id} already had Rs.{already} from {code}")
            return _json_response(h, 200, {
                "ok": True, "redeemed": already,
                "applied_credit_ids": [c["id"] for c in prior.data],
                "idempotent": True,
            })

        rows = (sb.table("referral_credits")
                  .select("id,amount_inr")
                  .eq("referrer_code", code)
                  .is_("redeemed_at", "null")
                  .order("created_at")
                  .execute())
        available = rows.data or []
        total_available = sum(int(c.get("amount_inr") or 0) for c in available)
        if total_available < amount:
            _json_response(h, 400, {
                "error": "insufficient balance",
                "balance": total_available, "requested": amount
            })
            return

        applied: list[int] = []
        remaining = amount
        actually_redeemed = 0
        now_iso = datetime.utcnow().isoformat() + "Z"
        for row in available:
            if remaining <= 0:
                break
            credit_amt = int(row.get("amount_inr") or 0)
            if credit_amt <= remaining:
                # Atomic: only update if still unredeemed (race guard)
                upd = (sb.table("referral_credits").update({
                    "redeemed_at": now_iso,
                    "redeemed_order_id": order_id,
                    "redeemed_by": staff_id,
                }).eq("id", row["id"]).is_("redeemed_at", "null").execute())
                if upd.data:
                    applied.append(row["id"])
                    remaining -= credit_amt
                    actually_redeemed += credit_amt
                # else: another worker won the race; loop continues
            else:
                # Partial: shrink original row to `remaining` and mark redeemed,
                # insert leftover as new unredeemed row. Race-guard the shrink.
                upd = (sb.table("referral_credits").update({
                    "amount_inr": remaining,
                    "redeemed_at": now_iso,
                    "redeemed_order_id": order_id,
                    "redeemed_by": staff_id,
                }).eq("id", row["id"]).is_("redeemed_at", "null").execute())
                if upd.data:
                    sb.table("referral_credits").insert({
                        "referrer_code":  code,
                        "customer_phone": "split",
                        "order_id":       row.get("order_id") or order_id,
                        "amount_inr":     credit_amt - remaining,
                    }).execute()
                    applied.append(row["id"])
                    actually_redeemed += remaining
                    remaining = 0
        if actually_redeemed < amount:
            # Race lost on too many rows. Report what we got.
            logger.warning(f"Redeem partial: requested Rs.{amount}, got Rs.{actually_redeemed} from {code}")
            return _json_response(h, 409, {
                "error": "race lost — try again",
                "redeemed": actually_redeemed, "requested": amount,
                "applied_credit_ids": applied,
            })
        logger.info(f"Redeemed Rs.{actually_redeemed} from {code} for order {order_id} (staff {staff_id})")
        _json_response(h, 200, {
            "ok": True,
            "redeemed": actually_redeemed,
            "applied_credit_ids": applied,
            "remaining_balance": total_available - actually_redeemed,
        })
    except Exception as e:
        logger.error(f"_handle_referrals_redeem error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_referrals_leaderboard(h) -> None:
    """GET /referrals/leaderboard — staff auth.
    Returns all referrers with aggregated stats:
      [{code, label, platform, orders, earned_inr, redeemed_inr, balance_inr, created_at}]
    Sorted by balance_inr DESC.
    """
    if not _acad_auth_staff(h):
        _json_response(h, 401, {"error": "staff PIN required"})
        return
    try:
        from db_cloud import _client
        sb = _client()
        refs    = sb.table("referrers").select("code,label,platform,created_at").execute()
        credits = sb.table("referral_credits").select("referrer_code,amount_inr,redeemed_at").execute()

        # Aggregate in Python — small enough for now (P3 will need a Postgres view)
        agg: dict[str, dict] = {}
        for c in (credits.data or []):
            code = c.get("referrer_code")
            if not code:
                continue
            row = agg.setdefault(code, {"orders": 0, "earned": 0, "redeemed": 0})
            amt = int(c.get("amount_inr") or 0)
            row["orders"]  += 1
            row["earned"]  += amt
            if c.get("redeemed_at"):
                row["redeemed"] += amt

        out = []
        for r in (refs.data or []):
            code = r["code"]
            a = agg.get(code, {"orders": 0, "earned": 0, "redeemed": 0})
            out.append({
                "code":         code,
                "label":        r.get("label"),
                "platform":     r.get("platform"),
                "created_at":   r.get("created_at"),
                "orders":       a["orders"],
                "earned_inr":   a["earned"],
                "redeemed_inr": a["redeemed"],
                "balance_inr":  a["earned"] - a["redeemed"],
            })
        out.sort(key=lambda x: (x["balance_inr"], x["earned_inr"]), reverse=True)
        _json_response(h, 200, {"referrers": out})
    except Exception as e:
        logger.error(f"_handle_referrals_leaderboard error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_referrals_credits(h) -> None:
    """GET /referrals/credits?code=REFXXXX — staff auth.
    Returns the referrer's row plus every credit (redeemed and unredeemed),
    newest first. Used by the drill-in panel.
    """
    if not _acad_auth_staff(h):
        _json_response(h, 401, {"error": "staff PIN required"})
        return
    qs = parse_qs(urlparse(h.path).query)
    code = (qs.get("code", [""])[0] or "").strip().upper()
    if not code:
        _json_response(h, 400, {"error": "code parameter required"})
        return
    try:
        from db_cloud import _client
        sb = _client()
        ref = sb.table("referrers").select("code,label,platform,created_at").eq("code", code).execute()
        if not ref.data:
            _json_response(h, 404, {"error": "referrer not found"})
            return
        credits = (sb.table("referral_credits")
                     .select("id,customer_phone,order_id,amount_inr,created_at,"
                             "redeemed_at,redeemed_order_id,redeemed_by")
                     .eq("referrer_code", code)
                     .order("created_at", desc=True)
                     .execute())
        _json_response(h, 200, {
            "referrer": ref.data[0],
            "credits":  credits.data or [],
        })
    except Exception as e:
        logger.error(f"_handle_referrals_credits error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_orders_get(h) -> None:
    """GET /academic/orders — list all orders (staff only)."""
    qs = parse_qs(urlparse(h.path).query)
    status_filter = qs.get("status", [None])[0]
    try:
        from db_cloud_academic import list_orders
        _json_response(h, 200, {"orders": list_orders(status=status_filter)})
    except Exception as e:
        logger.error(f"acad orders list error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_order_get(h, pid: str) -> None:
    """GET /academic/orders/{id} — get single order (staff only)."""
    try:
        from db_cloud_academic import get_order
        order = get_order(pid)
        if order is None:
            _json_response(h, 404, {"error": "not found"})
        else:
            _json_response(h, 200, order)
    except Exception as e:
        logger.error(f"acad order get error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_orders_post(h, body: bytes) -> None:
    """POST /academic/orders — create new order (public, student-facing).

    Privileged fields (advance_paid, status, payment_mode) are ALWAYS
    server-set and ignored from the request body — students cannot
    self-elevate their order status.
    """
    try:
        payload = json.loads(body)
    except Exception:
        _json_response(h, 400, {"error": "invalid json"})
        return
    for f in ("customer_name", "whatsapp_phone", "course", "topic"):
        if not payload.get(f):
            _json_response(h, 400, {"error": f"missing {f}"})
            return
    try:
        from db_cloud_academic import next_project_id, create_order
        pid = next_project_id()
        order: dict = {
            "project_id":     pid,
            "customer_name":  str(payload["customer_name"])[:200],
            "whatsapp_phone": str(payload["whatsapp_phone"])[:20],
            "course":         str(payload["course"])[:100],
            "topic":          str(payload["topic"])[:500],
            "study_area":     str(payload.get("study_area", ""))[:200],
            "sample_size":    max(1, min(int(payload.get("sample_size", 100)), 10000)),
            "tables_json":    json.dumps(payload.get("tables", [])),
            # Privileged — always server-controlled, never from request body
            "advance_paid":   False,
            "status":         "order_received",
        }
        create_order(order)
        _json_response(h, 201, {"project_id": pid})
    except Exception as e:
        logger.error(f"acad order create error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_generate(h, body: bytes, pid: str, phase: str) -> None:
    """POST /academic/orders/{id}/generate/{phase1|phase2} — set generating status (worker picks it up)."""
    try:
        from db_cloud_academic import get_order, update_status
        order = get_order(pid)
        if order is None:
            _json_response(h, 404, {"error": "not found"})
            return
        if phase == "phase1":
            if order["status"] not in ("order_received", "advance_paid"):
                _json_response(h, 400, {"error": f"invalid status for phase1: {order['status']}"})
                return
            update_status(pid, "chapters_generating")
        else:
            if order["status"] not in ("details_collected", "chapters_approved"):
                _json_response(h, 400, {"error": f"invalid status for phase2: {order['status']}"})
                return
            update_status(pid, "final_generating")
        _json_response(h, 200, {"status": "generating"})
    except Exception as e:
        logger.error(f"acad generate error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_approve_chapters(h, body: bytes, pid: str) -> None:
    """POST /academic/orders/{id}/approve/chapters — approve chapters, notify student."""
    try:
        from db_cloud_academic import get_order, update_status
        from academic_whatsapp import notify_phase2_link
        order = get_order(pid)
        if order is None:
            _json_response(h, 404, {"error": "not found"})
            return
        update_status(pid, "chapters_approved")
        notify_phase2_link(order["whatsapp_phone"], order.get("customer_name", ""), pid)
        _json_response(h, 200, {"ok": True})
    except Exception as e:
        logger.error(f"acad approve chapters error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_finalize(h, body: bytes, pid: str) -> None:
    """POST /academic/orders/{id}/finalize — student submits phase 2 details."""
    if not _acad_auth_student(h, pid):
        _json_response(h, 401, {"error": "unauthorized"})
        return
    try:
        payload = json.loads(body) if body else {}
    except Exception:
        _json_response(h, 400, {"error": "invalid json"})
        return
    updatable = [
        "college", "department", "semester", "year",
        "guide_name", "guide_designation", "hod_name", "register_number",
    ]
    fields = {k: payload[k] for k in updatable if k in payload}
    try:
        from db_cloud_academic import update_fields, update_status
        if fields:
            update_fields(pid, **fields)
        update_status(pid, "details_collected")
        _json_response(h, 200, {"ok": True})
    except LookupError:
        _json_response(h, 404, {"error": "not found"})
    except Exception as e:
        logger.error(f"acad finalize error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_approve_final(h, body: bytes, pid: str) -> None:
    """POST /academic/orders/{id}/approve/final — approve final doc, request balance payment."""
    try:
        from db_cloud_academic import get_order, update_status
        from academic_whatsapp import notify_balance_due
        order = get_order(pid)
        if order is None:
            _json_response(h, 404, {"error": "not found"})
            return
        update_status(pid, "balance_due")
        notify_balance_due(
            order["whatsapp_phone"],
            order.get("customer_name", ""),
            order.get("balance_amount", 500),
            order.get("razorpay_balance_link", ""),
        )
        _json_response(h, 200, {"ok": True})
    except Exception as e:
        logger.error(f"acad approve final error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_revise(h, body: bytes, pid: str) -> None:
    """POST /academic/orders/{id}/revise — staff adds revision note."""
    try:
        payload = json.loads(body) if body else {}
    except Exception:
        _json_response(h, 400, {"error": "invalid json"})
        return
    note = payload.get("note", "").strip()
    if not note:
        _json_response(h, 400, {"error": "missing note"})
        return
    try:
        from db_cloud_academic import update_fields
        update_fields(pid, revision_note=note)
        _json_response(h, 200, {"ok": True})
    except LookupError:
        _json_response(h, 404, {"error": "not found"})
    except Exception as e:
        logger.error(f"acad revise error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_deliver(h, body: bytes, pid: str) -> None:
    """POST /academic/orders/{id}/deliver — mark delivered with Drive link, notify student."""
    try:
        payload = json.loads(body) if body else {}
    except Exception:
        _json_response(h, 400, {"error": "invalid json"})
        return
    drive_url = payload.get("drive_url", "").strip()
    if not drive_url:
        _json_response(h, 400, {"error": "missing drive_url"})
        return
    try:
        from db_cloud_academic import get_order, update_fields, update_status
        from academic_whatsapp import notify_delivered
        order = get_order(pid)
        if order is None:
            _json_response(h, 404, {"error": "not found"})
            return
        update_fields(pid, drive_url=drive_url, balance_paid=True)
        update_status(pid, "delivered")
        notify_delivered(order["whatsapp_phone"], order.get("customer_name", ""), drive_url)
        _json_response(h, 200, {"ok": True})
    except LookupError:
        _json_response(h, 404, {"error": "not found"})
    except Exception as e:
        logger.error(f"acad deliver error: {e}")
        _json_response(h, 500, {"error": "server error"})


def _handle_acad_razorpay_webhook(h, body: bytes) -> None:
    """POST /academic/razorpay-webhook — Razorpay payment confirmation for academic orders."""
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
    if not secret:
        logger.error("RAZORPAY_WEBHOOK_SECRET not configured — rejecting academic webhook")
        _json_response(h, 500, {"error": "webhook not configured"})
        return
    sig = h.headers.get("X-Razorpay-Signature", "")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        _json_response(h, 401, {"error": "invalid signature"})
        return
    try:
        payload = json.loads(body)
    except Exception:
        _json_response(h, 400, {"error": "invalid json"})
        return
    event = payload.get("event", "")
    notes = (
        payload.get("payload", {})
        .get("payment", {})
        .get("entity", {})
        .get("notes", {})
    )
    pid          = notes.get("project_id", "")
    payment_type = notes.get("payment_type", "")
    if event == "payment.captured" and pid:
        try:
            from db_cloud_academic import get_order, update_fields, update_status
            from academic_whatsapp import notify_advance_paid
            order = get_order(pid)
            if order:
                if payment_type == "advance":
                    update_fields(pid, advance_paid=True)
                    update_status(pid, "advance_paid")
                    notify_advance_paid(order["whatsapp_phone"], order.get("customer_name", ""))
                elif payment_type == "balance":
                    update_fields(pid, balance_paid=True)
                    update_status(pid, "balance_paid")
        except Exception as e:
            logger.error(f"acad razorpay webhook error: {e}")
    _json_response(h, 200, {"ok": True})


def _handle_admin_conversations(h) -> None:
    """GET /admin/conversations — inbox: one row per contact, last msg + unread count."""
    from urllib.parse import parse_qs, urlparse
    params   = parse_qs(urlparse(h.path).query)
    admin_pw = params.get("admin_password", [""])[0]
    if not _auth_admin_pw(admin_pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return

    try:
        from db_cloud import _client as _dbc
        client = _dbc()

        # Fetch recent rows across all contacts (newest first, cap 500)
        log_rows = (
            client.table("conversation_log")
            .select("phone,direction,message_type,body,filename,created_at")
            .order("created_at", desc=True)
            .limit(500)
            .execute()
            .data
        )

        # Fetch contacts for names + last_seen_at
        contacts_data = (
            client.table("whatsapp_contacts")
            .select("phone,name,last_seen_at")
            .execute()
            .data
        )
        contacts_map = {c["phone"]: c for c in contacts_data}

        # One entry per phone — first occurrence in log_rows is the newest message
        seen_phones: set = set()
        inbox = []
        for row in log_rows:
            ph = row["phone"]
            if ph in seen_phones:
                continue
            seen_phones.add(ph)

            contact   = contacts_map.get(ph, {})
            name      = contact.get("name") or _fmt_phone(ph)
            last_seen = contact.get("last_seen_at")

            # Count unread inbound messages newer than last_seen_at
            unread = sum(
                1 for r in log_rows
                if r["phone"] == ph
                and r["direction"] == "inbound"
                and (not last_seen or r["created_at"] > last_seen)
            )

            mt = row.get("message_type") or "text"
            if mt.startswith("image"):
                preview = "Image"
            elif mt.startswith("audio"):
                preview = "Voice note"
            elif mt.startswith("video"):
                preview = "Video"
            elif "pdf" in mt or mt.startswith("application"):
                preview = f"File: {row.get('filename') or 'file'}"
            else:
                preview = (row.get("body") or "")[:60]

            inbox.append({
                "phone":             ph,
                "name":              name,
                "last_message":      preview,
                "last_message_type": mt,
                "unread_count":      unread,
                "ts":                row["created_at"],
            })

        _json_response(h, 200, sorted(inbox, key=lambda x: x["ts"], reverse=True))
    except Exception as exc:
        logger.error("GET /admin/conversations error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_thread(h) -> None:
    """GET /admin/thread?phone=X — thread messages for one contact."""
    from urllib.parse import parse_qs, urlparse
    params   = parse_qs(urlparse(h.path).query)
    admin_pw = params.get("admin_password", [""])[0]
    if not _auth_admin_pw(admin_pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return

    phone = params.get("phone", [""])[0]
    if not phone:
        _json_response(h, 400, {"error": "phone required"})
        return
    limit = min(int(params.get("limit", ["100"])[0]), 200)

    try:
        from db_cloud import _client as _dbc, get_media_url
        rows = (
            _dbc().table("conversation_log")
            .select("id,direction,message_type,body,filename,media_url,created_at")
            .eq("phone", phone)
            .order("created_at", desc=False)
            .limit(limit)
            .execute()
            .data
        )
        # Resolve storage paths to public URLs
        for row in rows:
            mp = row.get("media_url")
            if mp and not mp.startswith("http"):
                row["media_url"] = get_media_url(mp)
        _json_response(h, 200, rows)
    except Exception as exc:
        logger.error("GET /admin/thread error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_contacts_seen(h, body: bytes) -> None:
    """PATCH /admin/contacts/seen — mark contact thread as read."""
    try:
        payload = json.loads(body)
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return
    if not _auth_admin_pw(payload.get("admin_password", "")):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    phone = payload.get("phone", "")
    if not phone:
        _json_response(h, 400, {"error": "phone required"})
        return
    try:
        from db_cloud import mark_contact_seen
        mark_contact_seen(phone)
        _json_response(h, 200, {"ok": True})
    except Exception as exc:
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_upload_token(h, body: bytes) -> None:
    """POST /admin/upload-token — issue a Supabase signed upload URL (5 min).

    Browser uploads the file directly to Supabase (bypasses Vercel 4.5MB limit).
    Returns {upload_url, storage_path}.
    """
    try:
        payload = json.loads(body)
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return
    if not _auth_admin_pw(payload.get("admin_password", "")):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    filename = payload.get("filename", "")
    if not filename:
        _json_response(h, 400, {"error": "filename required"})
        return

    import re as _re, time as _time
    safe_name    = _re.sub(r"[^a-zA-Z0-9._\-]", "_", filename)
    storage_path = f"outbound/{int(_time.time())}_{safe_name}"

    try:
        from db_cloud import _client as _dbc, INCOMING_BUCKET
        resp = _dbc().storage.from_(INCOMING_BUCKET).create_signed_upload_url(storage_path)
        _json_response(h, 200, {
            "upload_url":   resp["signedURL"],
            "storage_path": storage_path,
        })
    except Exception as exc:
        logger.error("upload-token error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_send_file(h, body: bytes) -> None:
    """POST /admin/send-file — server downloads from storage, sends via Meta, logs."""
    try:
        payload = json.loads(body)
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return
    if not _auth_admin_pw(payload.get("admin_password", "")):
        _json_response(h, 403, {"error": "Unauthorized"})
        return

    phone        = payload.get("phone", "")
    storage_path = payload.get("storage_path", "")
    caption      = payload.get("caption", "")
    mime_type    = payload.get("mime_type", "application/octet-stream")
    filename     = payload.get("filename", "file")

    if not phone or not storage_path:
        _json_response(h, 400, {"error": "phone and storage_path required"})
        return

    try:
        from db_cloud import _client as _dbc, INCOMING_BUCKET, log_message
        from whatsapp_notify import send_file

        # Download from Supabase Storage (server-to-server, no size limit)
        file_bytes = _dbc().storage.from_(INCOMING_BUCKET).download(storage_path)

        # Upload to Meta and send WhatsApp message
        ok = send_file(phone, file_bytes, mime_type, filename, caption)
        if not ok:
            _json_response(h, 502, {"error": "WhatsApp send failed"})
            return

        # Log outbound message with storage path as media_url
        log_message(phone, "outbound", caption or filename,
                    message_type=mime_type, filename=filename,
                    media_url=storage_path)

        _json_response(h, 200, {"ok": True})
    except Exception as exc:
        logger.error("send-file error for %s: %s", phone, exc)
        _json_response(h, 500, {"error": str(exc)})


# ── Vercel request handler ────────────────────────────────────────────────────

class handler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):  # suppress default stderr output
        logger.debug("HTTP: " + format % args)

    def do_GET(self):
        if self.path.startswith("/whatsapp-webhook"):
            params       = parse_qs(urlparse(self.path).query)
            verify_token = params.get("hub.verify_token", [""])[0]
            challenge    = params.get("hub.challenge",    [""])[0]

            if verify_token == META_WEBHOOK_VERIFY_TOKEN and challenge:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(challenge.encode())
                logger.info("Meta webhook verification challenge passed")
            else:
                self.send_response(403)
                self.end_headers()
                logger.warning("Meta webhook verify failed — token mismatch")
            return

        # ── Academic project orders ──────────────────────────────────────────
        if self.path.startswith("/academic/orders"):
            m = re.match(r"^/academic/orders/([^/?]+)$", self.path.split("?")[0])
            if m:
                _handle_acad_order_get(self, m.group(1))
            else:
                _handle_acad_orders_get(self)
            return

        # ── Referral store-credit balance lookup (staff) ─────────────────────
        if self.path.startswith("/referrals/balance"):
            _handle_referrals_balance(self)
            return

        # ── Referral leaderboard (staff) ─────────────────────────────────────
        if self.path.startswith("/referrals/leaderboard"):
            _handle_referrals_leaderboard(self)
            return

        # ── Referral drill-in credits (staff) ────────────────────────────────
        if self.path.startswith("/referrals/credits"):
            _handle_referrals_credits(self)
            return

        # ── Admin inbox (conversations) ───────────────────────────────────────
        if self.path.startswith("/admin/conversations"):
            _handle_admin_conversations(self)
            return
        if self.path.startswith("/admin/thread"):
            _handle_admin_thread(self)
            return

        # Health check
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Printosky webhook OK (cloud)")

    def do_OPTIONS(self):
        # CORS preflight — allow any origin, advertise supported methods/headers.
        self.send_response(204)
        _send_cors_headers(self)
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        # ── Meta WhatsApp Cloud API ──────────────────────────────────────────
        if self.path == "/whatsapp-webhook":
            # Must return 200 immediately or Meta retries
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

            sig = self.headers.get("X-Hub-Signature-256", "")
            if not _verify_meta_sig(body, sig):
                logger.warning("Meta signature verification failed — dropping")
                return
            try:
                _process_meta_webhook(json.loads(body))
            except Exception as e:
                logger.error(f"Meta webhook processing error: {e}")
            return

        # ── Razorpay payment ─────────────────────────────────────────────────
        if self.path == "/webhook/razorpay":
            from razorpay_integration import verify_webhook
            sig = self.headers.get("X-Razorpay-Signature", "")
            if not verify_webhook(body, sig):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Invalid signature")
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
            try:
                _process_razorpay_payment(json.loads(body))
            except Exception as e:
                logger.error(f"Razorpay webhook processing error: {e}")
            return

        # ── Staff PIN self-service ───────────────────────────────────────────
        # ── Academic project orders ──────────────────────────────────────────
        if self.path == "/academic/orders":
            _handle_acad_orders_post(self, body)
            return

        _am = re.match(r"^/academic/orders/([^/]+)/generate/(phase[12])$", self.path)
        if _am:
            _handle_acad_generate(self, body, _am.group(1), _am.group(2))
            return

        _am = re.match(r"^/academic/orders/([^/]+)/approve/chapters$", self.path)
        if _am:
            _handle_acad_approve_chapters(self, body, _am.group(1))
            return

        _am = re.match(r"^/academic/orders/([^/]+)/finalize$", self.path)
        if _am:
            _handle_acad_finalize(self, body, _am.group(1))
            return

        _am = re.match(r"^/academic/orders/([^/]+)/approve/final$", self.path)
        if _am:
            _handle_acad_approve_final(self, body, _am.group(1))
            return

        _am = re.match(r"^/academic/orders/([^/]+)/revise$", self.path)
        if _am:
            _handle_acad_revise(self, body, _am.group(1))
            return

        _am = re.match(r"^/academic/orders/([^/]+)/deliver$", self.path)
        if _am:
            _handle_acad_deliver(self, body, _am.group(1))
            return

        if self.path == "/academic/razorpay-webhook":
            _handle_acad_razorpay_webhook(self, body)
            return

        # ── Staff PIN self-service ───────────────────────────────────────────
        if self.path == "/staff/set-pin":
            _handle_staff_set_pin(self, body)
            return

        if self.path == "/admin/reset-pin":
            _handle_admin_reset_pin(self, body)
            return

        if self.path == "/staff/resume":
            _handle_staff_resume(self, body)
            return

        if self.path == "/admin/send":
            _handle_admin_send(self, body)
            return

        if self.path == "/admin/upload-token":
            _handle_admin_upload_token(self, body)
            return
        if self.path == "/admin/send-file":
            _handle_admin_send_file(self, body)
            return

        # ── Referral store-credit redemption (staff) ─────────────────────────
        if self.path == "/referrals/redeem":
            _handle_referrals_redeem(self, body)
            return

        self.send_response(404)
        self.end_headers()

    def do_PATCH(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        if self.path == "/admin/contacts/seen":
            _handle_admin_contacts_seen(self, body)
            return
        _json_response(self, 404, {"error": "Not found"})
