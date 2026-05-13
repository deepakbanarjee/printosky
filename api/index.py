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
import time
import collections
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("api.webhook")

# ── Bypass rate limiter — max 5 wrong attempts per IP per 15 min ─────────────
_bypass_attempts: dict = collections.defaultdict(list)  # ip → [timestamp, ...]
_BYPASS_MAX_ATTEMPTS = 5
_BYPASS_WINDOW_SEC   = 900  # 15 minutes

def _check_bypass_rate_limit(ip: str) -> bool:
    """Return True if the IP is allowed, False if rate-limited."""
    now = time.time()
    attempts = _bypass_attempts[ip]
    # Drop attempts older than the window
    _bypass_attempts[ip] = [t for t in attempts if now - t < _BYPASS_WINDOW_SEC]
    return len(_bypass_attempts[ip]) < _BYPASS_MAX_ATTEMPTS

def _record_bypass_failure(ip: str) -> None:
    _bypass_attempts[ip].append(time.time())

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
        # Look up campaign-specific credit amount; default 20 for regular referrers
        ref_row = _client().table("referrers").select("credit_amount").eq("code", code).execute()
        credit_amount = int((ref_row.data[0].get("credit_amount") or 20) if ref_row.data else 20)
        _client().table("referral_credits").insert({
            "referrer_code": code,
            "customer_phone": phone,
            "order_id": order_id,
            "amount_inr": credit_amount,
        }).execute()
        logger.info(f"Referral credit Rs.{credit_amount} logged -> {code!r} for order {order_id}")
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


HELP_KEYWORDS: frozenset[str] = frozenset({"help", "support", "human", "agent"})


def _is_help_keyword(text: str) -> bool:
    """True if the customer typed a bare help/support/human/agent keyword."""
    return text.strip().lower() in HELP_KEYWORDS


def _mark_session_needs_human(phone: str) -> None:
    """Flag the bot_session as needing human attention. Best-effort; never raises."""
    try:
        from datetime import timezone
        from db_cloud import _client
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        _client().table("bot_sessions").upsert({
            "phone": phone,
            "needs_human": True,
            "last_help_request_at": ts,
        }).execute()
    except Exception as exc:
        logger.warning(f"_mark_session_needs_human({phone}) failed: {exc}")


def _handle_help_request(sender: str, trigger: str) -> None:
    """Customer asked for a human. Flag the session, alert staff, ack the customer."""
    from whatsapp_notify import _send, send_staff_alert

    _mark_session_needs_human(sender)
    try:
        send_staff_alert(
            f"Customer {_fmt_phone(sender)} requested human support "
            f"(typed '{trigger}'). Open the Conversations tab → 'Needs human' filter."
        )
    except Exception as exc:
        logger.warning(f"send_staff_alert in _handle_help_request failed: {exc}")
    ack = (
        "Got it — I've alerted the team. "
        "Someone will message you shortly. You can keep typing in the meantime."
    )
    try:
        _send(sender, ack)
        from db_cloud import log_message
        log_message(sender, "outbound", ack, message_type="text")
    except Exception as exc:
        logger.warning(f"_handle_help_request ack send failed for {sender}: {exc}")


def _handle_text(sender: str, text: str) -> None:
    """Route a customer text through the bot state machine and send replies."""
    from whatsapp_bot import handle_message
    from whatsapp_notify import _send, send_staff_alert

    # Help escape hatch: short-circuit before any state-machine work.
    # Customer typed `help` / `support` / `human` / `agent` → flag session,
    # alert staff, ack the customer. TASK-009.
    if _is_help_keyword(text):
        _handle_help_request(sender, text.strip().lower())
        return

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


def _store_media_only(sender: str, media_id: str, mime_type: str,
                      orig_filename: str) -> str | None:
    """Download a WhatsApp attachment and upload to Supabase — no job, no reply."""
    from db_cloud import upload_file
    ext = FILE_MIME_TYPES.get(mime_type, "")
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    if orig_filename and "." in orig_filename:
        base_name = re.sub(r"[^\w.\- ]", "_", os.path.basename(orig_filename)).strip()
    else:
        base_name = f"{sender}_{ts}{ext or '.bin'}"
    dest_name = f"{sender}_{ts}_{base_name}"
    content = _download_meta_media(media_id)
    if content is None:
        logger.error(f"Failed to download chat media {media_id} from {sender}")
        return None
    content = _compress_lossless(content, mime_type or "")
    upload_file(dest_name, content, mime_type or "application/octet-stream")
    logger.info(f"Stored chat media {dest_name} ({len(content)} bytes)")
    return dest_name


def _notify_pickup_ready(job_id: str) -> None:
    """Block 5: customer-facing pickup-ready WhatsApp on store READY."""
    from db_cloud import _client
    from whatsapp_notify import send_pickup_ready
    client = _client()
    job_rows = (
        client.table("jobs")
        .select("job_id,sender,pickup_code,assigned_store_id,store_id")
        .eq("job_id", job_id)
        .limit(1)
        .execute()
    )
    rows = getattr(job_rows, "data", None) or []
    if not rows:
        return
    job = rows[0]
    sender = job.get("sender")
    code = job.get("pickup_code")
    if not sender or not code:
        return
    partner_id = job.get("assigned_store_id") or job.get("store_id") or "OSP"
    partner_rows = (
        client.table("partners")
        .select("display_pickup_label,pickup_address,name")
        .eq("store_id", partner_id)
        .limit(1)
        .execute()
    )
    prows = getattr(partner_rows, "data", None) or []
    partner = prows[0] if prows else {}
    deep_link = f"https://printosky.com/track.html?code={code}"
    send_pickup_ready(
        sender=sender,
        pickup_code=code,
        store_label=partner.get("display_pickup_label"),
        store_address=partner.get("pickup_address") or partner.get("name") or "",
        deep_link=deep_link,
    )


def _notify_pickup_completed(job_id: str) -> None:
    """Block 5: thank-you / rate-us message on store DELIVERED."""
    from db_cloud import _client
    from whatsapp_notify import send_pickup_completed
    client = _client()
    job_rows = (
        client.table("jobs")
        .select("sender,pickup_code")
        .eq("job_id", job_id)
        .limit(1)
        .execute()
    )
    rows = getattr(job_rows, "data", None) or []
    if not rows:
        return
    job = rows[0]
    sender = job.get("sender")
    code = job.get("pickup_code")
    if not sender or not code:
        return
    send_pickup_completed(sender=sender, pickup_code=code, rating_url=None)


def _mark_webhook_processed(event_id: str, handler: str) -> bool:
    """Record that we've handled this webhook event_id; return True if the
    caller should proceed with processing, False if it's a duplicate retry.

    Uses processed_webhooks.event_id as a Postgres PRIMARY KEY for race-safe
    dedupe (any concurrent retry from Meta/Razorpay loses on UNIQUE conflict).

    Failure modes:
      - empty event_id -> return True (we can't dedupe without a key; better
        to risk a duplicate than to silently drop a real event)
      - DB unique-violation (duplicate event) -> return False
      - any other DB error -> return True (log + let through; missing dedupe
        is recoverable, dropped payments are not)
    """
    if not event_id:
        return True
    try:
        from db_cloud import _client
        resp = _client().table("processed_webhooks").insert(
            {"event_id": event_id, "handler": handler}
        ).execute()
        return bool(getattr(resp, "data", None))
    except Exception as exc:
        msg = str(exc).lower()
        if "duplicate" in msg or "23505" in msg or "unique" in msg or "conflict" in msg:
            logger.info(f"Webhook event {event_id} ({handler}) already processed -- skipping")
            return False
        logger.warning(f"_mark_webhook_processed({event_id}, {handler}) failed: {exc}")
        return True  # fail-open: don't drop real events on a DB hiccup


def _process_meta_webhook(data: dict) -> None:
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value    = change.get("value", {})
            contacts = value.get("contacts", [])
            pushname = (contacts[0].get("profile", {}).get("name")
                        if contacts else None)
            for msg in value.get("messages", []):
                # Idempotency guard (TASK-013): Meta retries on slow handlers.
                # Each WhatsApp message has a unique wamid (msg.id). Skip
                # silently if we've already processed this exact message.
                if not _mark_webhook_processed(msg.get("id", ""), "meta"):
                    continue

                sender   = msg.get("from", "")
                msg_type = msg.get("type", "")
                logger.info(f"Meta message from {sender}: type={msg_type}")

                if msg_type == "text":
                    text = (msg.get("text") or {}).get("body", "").strip()
                    if text:
                        # Block 5: if the sender is a known store-owner
                        # dispatch number AND the text parses as a known
                        # action (ACCEPT/READY/DELIVERED/REJECT/QUERY),
                        # route to store_dispatch instead of the customer
                        # flow. Falls through to _handle_text on parse miss
                        # so a store owner can still chat with us normally.
                        handled_as_store = False
                        try:
                            from store_dispatch import (
                                parse_store_reply,
                                apply_store_reply,
                            )
                            from db_cloud import _client
                            parsed = parse_store_reply(text)
                            if parsed is not None:
                                result = apply_store_reply(_client(), sender, parsed)
                                if result.ok:
                                    handled_as_store = True
                                    logger.info(
                                        "store_dispatch handled %s reply: %s",
                                        sender, result.message,
                                    )
                                    # Customer-side fan-out for state transitions
                                    if result.new_status == "Ready":
                                        try:
                                            _notify_pickup_ready(result.job_id)
                                        except Exception as e:
                                            logger.error(
                                                "pickup-ready notify failed: %s", e
                                            )
                                    elif result.new_status == "Delivered":
                                        try:
                                            _notify_pickup_completed(result.job_id)
                                        except Exception as e:
                                            logger.error(
                                                "pickup-completed notify failed: %s", e
                                            )
                        except Exception as e:
                            logger.error("store_dispatch routing failed: %s", e)

                        if not handled_as_store:
                            _handle_text(sender, text)
                        try:
                            from db_cloud import log_message, upsert_contact
                            log_message(sender, "inbound", text, message_type="text")
                            upsert_contact(sender, name=pushname)
                        except Exception:
                            pass

                elif msg_type in ("document", "image"):
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
                elif msg_type in ("audio", "video"):
                    blk      = msg.get(msg_type, {})
                    media_id = blk.get("id", "")
                    mime     = blk.get("mime_type", "")
                    fname    = blk.get("filename", "")
                    if media_id:
                        storage_path = _store_media_only(sender, media_id, mime, fname)
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

    # Idempotency guard (TASK-013): Razorpay can fire the same payment.captured
    # event twice. data.id is the unique webhook event ID. Skip duplicates.
    if not _mark_webhook_processed(data.get("id", ""), "razorpay_print"):
        return

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


def _handle_internal_notify_owner(h, body: bytes) -> None:
    """Internal endpoint for trusted local scripts to send a WhatsApp alert.

    Auth: shared secret in JSON body (`secret` field) compared against
    UPTIME_NOTIFY_SECRET env var. Constant-time comparison to avoid
    timing leaks.

    Body: {"secret": "<hex>", "message": "<text>"}
    Returns 200 {"sent": true} on success, 401 on bad secret, 400 on
    malformed body, 500 if WhatsApp send fails.
    """
    expected = os.environ.get("UPTIME_NOTIFY_SECRET", "")
    if not expected:
        _json_response(h, 503, {"error": "alerting disabled (UPTIME_NOTIFY_SECRET unset)"})
        return
    try:
        data = json.loads(body or b"{}")
    except Exception:
        _json_response(h, 400, {"error": "invalid JSON"})
        return
    provided = str(data.get("secret", ""))
    if not hmac.compare_digest(provided, expected):
        _json_response(h, 401, {"error": "bad secret"})
        return
    message = str(data.get("message", "")).strip()
    if not message or len(message) > 4000:
        _json_response(h, 400, {"error": "message must be 1-4000 chars"})
        return
    owner_phone = os.environ.get("OWNER_PHONE", "919495706405")
    try:
        from whatsapp_notify import _send_meta
        ok = _send_meta(owner_phone, message)
    except Exception as e:
        logger.exception("notify-owner: send failed")
        _json_response(h, 500, {"sent": False, "error": str(e)[:200]})
        return
    _json_response(h, 200 if ok else 500, {"sent": bool(ok)})


def _handle_health(h) -> None:
    """Lightweight health check for external uptime monitoring.

    Returns 200 with {"ok": true, "checks": {...}} when every required env
    var is present. Returns 503 with the same shape (ok=false) otherwise.
    Does NOT make any external network calls — this is a liveness probe.
    Safe to expose publicly: only emits booleans, never secret values.
    """
    required = {
        "meta_token":   bool(os.environ.get("META_SYSTEM_USER_TOKEN")),
        "meta_phone":   bool(os.environ.get("META_PHONE_NUMBER_ID")),
        "supabase_url": bool(os.environ.get("SUPABASE_URL")),
        "supabase_key": bool(os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_KEY")),
        "razorpay_key": bool(os.environ.get("RAZORPAY_KEY_ID")),
        "razorpay_sec": bool(os.environ.get("RAZORPAY_WEBHOOK_SECRET")),
    }
    all_ok = all(required.values())
    payload = {
        "ok": all_ok,
        "service": "printosky-api",
        "checks": required,
        "ts": datetime.utcnow().isoformat() + "Z",
    }
    _json_response(h, 200 if all_ok else 503, payload)


def _send_cors_headers(h) -> None:
    """Attach CORS headers. Endpoints are individually auth-gated so * is safe."""
    h.send_header("Access-Control-Allow-Origin",  "*")
    h.send_header("Access-Control-Allow-Methods", "POST, GET, PATCH, OPTIONS")
    h.send_header("Access-Control-Allow-Headers", "Content-Type, X-Hub-Signature-256, X-Razorpay-Signature, X-Staff-Pin, X-Student-Phone, X-Admin-Password, X-Whatsapp-Phone")
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


def _handle_track(h, code: str) -> None:
    """GET /api/track/<code> — public read-only order tracker.

    Returns minimum-PII status + (only after status='Ready') the pickup
    location. Pickup codes are 30^4=810k random tokens so brute-forcing is
    impractical; non-existent codes return 404 (no information leaked about
    code-validity vs. code-existence).
    """
    try:
        from pickup_code import is_valid_pickup_code
        if not is_valid_pickup_code(code):
            _json_response(h, 400, {"error": "invalid pickup code format"})
            return

        from db_cloud import _client
        client = _client()
        job_rows = (
            client.table("jobs")
            .select("status,customer_name,assigned_store_id,store_id,"
                    "received_at,pickup_ready_at,delivered_at,pickup_code")
            .eq("pickup_code", code)
            .limit(1)
            .execute()
        )
        rows = getattr(job_rows, "data", None) or []
        if not rows:
            _json_response(h, 404, {"error": "not found"})
            return

        job = rows[0]
        status = (job.get("status") or "").strip() or "Pending"
        is_ready_or_later = status in {"Ready", "Delivered", "Completed", "Printed"}

        partner_id = job.get("assigned_store_id") or job.get("store_id") or "OSP"
        pickup_label = None
        pickup_address = None
        if is_ready_or_later:
            partner_rows = (
                client.table("partners")
                .select("display_pickup_label,pickup_address,name")
                .eq("store_id", partner_id)
                .limit(1)
                .execute()
            )
            prows = getattr(partner_rows, "data", None) or []
            if prows:
                pickup_label = prows[0].get("display_pickup_label")
                pickup_address = prows[0].get("pickup_address") or prows[0].get("name")

        full_name = (job.get("customer_name") or "").strip()
        first_name = full_name.split()[0] if full_name else None

        _json_response(h, 200, {
            "pickup_code":     code,
            "status":          status,
            "first_name":      first_name,
            "received_at":     job.get("received_at"),
            "pickup_ready_at": job.get("pickup_ready_at"),
            "delivered_at":    job.get("delivered_at"),
            "pickup_label":    pickup_label,
            "pickup_address":  pickup_address,
        })
    except Exception as e:
        logger.error(f"track lookup error for {code!r}: {e}")
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
        from db_cloud_academic import next_project_id, create_order, update_fields
        pid = next_project_id()
        customer_name  = str(payload["customer_name"])[:200]
        whatsapp_phone = str(payload["whatsapp_phone"])[:20]
        order: dict = {
            "project_id":     pid,
            "customer_name":  customer_name,
            "whatsapp_phone": whatsapp_phone,
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
    except Exception as e:
        logger.error(f"acad order create error: {e}")
        _json_response(h, 500, {"error": "server error"})
        return

    # Best-effort: create the Rs. 500 advance payment link.
    # If this fails the order still exists — staff can re-send via WhatsApp.
    advance_url = None
    try:
        from razorpay_integration import create_academic_payment_link
        link = create_academic_payment_link(
            project_id     = pid,
            payment_type   = "advance",
            amount         = 500.0,
            description    = f"Printosky academic project advance — {pid}",
            customer_phone = whatsapp_phone,
            customer_name  = customer_name,
        )
        if "url" in link and link["url"]:
            advance_url = link["url"]
            try:
                update_fields(pid, razorpay_advance_link=link["url"])
            except Exception as e:
                logger.warning(f"Could not persist razorpay_advance_link for {pid}: {e}")
        else:
            logger.error(f"Razorpay link create failed for {pid}: {link.get('error')}")
    except Exception as e:
        logger.error(f"Razorpay link create exception for {pid}: {e}")

    _json_response(h, 201, {
        "project_id":   pid,
        "payment_url":  advance_url,   # may be null if link creation failed
        "amount_inr":   500,
    })


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

    # Idempotency guard (TASK-013): Razorpay retries on slow ACKs. payload.id
    # is the webhook event ID. Skip duplicates with 200 so Razorpay stops
    # retrying.
    if not _mark_webhook_processed(payload.get("id", ""), "razorpay_acad"):
        _json_response(h, 200, {"status": "already_processed"})
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
    admin_pw = h.headers.get("X-Admin-Password", "").strip()
    if not admin_pw:
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

        # Fetch sessions flagged as needing human attention (TASK-009)
        # Tolerant of pre-v17 schema: missing column returns empty set, no crash.
        needs_human_phones: set = set()
        try:
            help_rows = (
                client.table("bot_sessions")
                .select("phone")
                .eq("needs_human", True)
                .execute()
                .data
            )
            needs_human_phones = {r["phone"] for r in (help_rows or [])}
        except Exception as exc:
            logger.debug("bot_sessions.needs_human read skipped: %s", exc)

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
                "needs_human":       ph in needs_human_phones,
            })

        _json_response(h, 200, sorted(inbox, key=lambda x: x["ts"], reverse=True))
    except Exception as exc:
        logger.error("GET /admin/conversations error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_thread(h) -> None:
    """GET /admin/thread?phone=X — thread messages for one contact."""
    from urllib.parse import parse_qs, urlparse
    params   = parse_qs(urlparse(h.path).query)
    admin_pw = h.headers.get("X-Admin-Password", "").strip() or params.get("admin_password", [""])[0]
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
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
            .data
        )
        rows.reverse()  # return oldest→newest for display
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


# ── Project Builder helpers ──────────────────────────────────────────────────

def _parse_multipart(body: bytes, content_type: str) -> dict[str, any]:
    """Parse multipart/form-data. Return dict with form fields and file data.

    Returns {'field_name': 'value', ...file field as 'filename': bytes_data}
    """
    parts = {}

    # Extract boundary from Content-Type header
    if "boundary=" not in content_type:
        return {}

    boundary_str = content_type.split("boundary=")[-1].strip().strip('"')
    boundary = b"--" + boundary_str.encode()
    final_boundary = b"--" + boundary_str.encode() + b"--"

    # Split by boundary
    segments = body.split(boundary)

    for segment in segments[1:-1]:  # Skip first (before first boundary) and last (after final)
        if not segment.strip():
            continue

        # Split headers from content
        try:
            header_end = segment.find(b'\r\n\r\n')
            if header_end == -1:
                header_end = segment.find(b'\n\n')
                if header_end == -1:
                    continue
                headers = segment[:header_end].decode('utf-8', errors='ignore')
                content = segment[header_end+2:]
            else:
                headers = segment[:header_end].decode('utf-8', errors='ignore')
                content = segment[header_end+4:]
        except Exception:
            continue

        # Remove trailing CRLLF
        if content.endswith(b'\r\n'):
            content = content[:-2]
        elif content.endswith(b'\n'):
            content = content[:-1]

        # Parse Content-Disposition to get name and filename
        name = None
        filename = None
        for line in headers.split('\n'):
            if 'name=' in line:
                match = re.search(r'name="?([^";\r\n]+)"?', line)
                if match:
                    name = match.group(1)
            if 'filename=' in line:
                match = re.search(r'filename="?([^";\r\n]+)"?', line)
                if match:
                    filename = match.group(1)

        if not name:
            continue

        if filename:
            # Binary file field
            parts[name] = {'filename': filename, 'data': content}
        else:
            # Text field
            parts[name] = content.decode('utf-8', errors='ignore')

    return parts


def _handle_admin_format_fixer(h, body: bytes) -> None:
    """POST /admin/format-fixer — admin uploads DOCX, returns fixed DOCX.

    Accepts multipart/form-data with fields:
    - admin_password: admin password for auth
    - university_id: university config (optional, defaults to 'default')
    - file: DOCX file to format

    Returns fixed DOCX as attachment, no storage.
    """
    try:
        # Parse multipart form data
        content_type = h.headers.get('Content-Type', '')
        form_data = _parse_multipart(body, content_type)

        # Verify admin auth
        admin_pw = form_data.get('admin_password', '')
        if not _auth_admin_pw(admin_pw):
            _json_response(h, 403, {'error': 'Unauthorized'})
            return

        # Extract file
        if 'file' not in form_data or not isinstance(form_data['file'], dict):
            _json_response(h, 400, {'error': 'No file uploaded'})
            return

        file_data = form_data['file']
        file_bytes = file_data.get('data', b'')
        filename = file_data.get('filename', 'document.docx')

        if not file_bytes:
            _json_response(h, 400, {'error': 'Empty file'})
            return

        # Get university_id (optional, defaults to 'default')
        university_id = form_data.get('university_id', 'default').strip()

        # Format the DOCX
        import docx_engine
        fixed_bytes = docx_engine.format_fix_docx_inplace(file_bytes, university_id)

        # Return as attachment
        h.send_response(200)
        h.send_header('Content-Type', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
        h.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        h.send_header('Content-Length', str(len(fixed_bytes)))
        h.end_headers()
        h.wfile.write(fixed_bytes)

        logger.info(f"Admin formatted DOCX: {filename} via university={university_id}")

    except Exception as exc:
        logger.error(f"admin format-fixer error: {exc}", exc_info=True)
        _json_response(h, 500, {'error': str(exc)})


def _generate_pb_order_id() -> str:
    """Generate a unique project builder order ID: PB-YYYYMMDD-xxxxxx."""
    import uuid
    return f"PB-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"


def _send_pb_whatsapp(phone: str, order_id: str, download_url: str) -> None:
    """Send the formatted document download link via WhatsApp.

    NOTE: Works within the 24-hour Meta service window (customer initiated).
    For students who have never messaged the WABA number, this will fail
    silently — the browser download link is the primary delivery mechanism.
    A pre-approved template should be submitted to Meta for all-India cold reach.
    """
    from whatsapp_notify import _send_meta
    msg = (
        f"✅ Your project report is ready!\n\n"
        f"📄 Download link:\n{download_url}\n\n"
        f"🔖 Order ID: *{order_id}*\n"
        f"Save this — you can re-download at printosky.com/pb-retrieve\n\n"
        f"Need corrections? Reply with your Order ID.\n\n"
        f"— Printosky | printosky.com"
    )
    try:
        _send_meta(phone, msg)
    except Exception as e:
        logger.warning(f"_send_pb_whatsapp failed for {order_id}: {e}")


# ── Project Builder handlers ─────────────────────────────────────────────────

def _pb_docx_response(h, docx_bytes: bytes, filename: str) -> None:
    """Send a .docx file as an HTTP response with CORS headers."""
    h.send_response(200)
    h.send_header(
        "Content-Type",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    h.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    h.send_header("Content-Length", str(len(docx_bytes)))
    h.send_header("Access-Control-Allow-Origin", "*")
    h.end_headers()
    h.wfile.write(docx_bytes)


def _handle_pb_templates_get(h) -> None:
    """GET /project-builder/templates — return list of supported universities."""
    import docx_engine
    unis = docx_engine.list_universities()
    _json_response(h, 200, {"universities": unis})


def _handle_pb_template_download(h, university_id: str) -> None:
    """GET /project-builder/templates/{id} — generate and return free .docx template."""
    import docx_engine
    try:
        docx_bytes = docx_engine.generate_free_template(university_id)
        cfg        = docx_engine.load_university_config(university_id)
    except ValueError as e:
        _json_response(h, 400, {"error": str(e)})
        return
    filename = cfg["short_name"].replace(" ", "_") + "_Project_Template.docx"
    _pb_docx_response(h, docx_bytes, filename)


def _handle_pb_analyse(h, body: bytes) -> None:
    """POST /project-builder/analyse — free chapter detection before payment."""
    import base64
    import docx_engine
    try:
        data = json.loads(body)
        content_b64 = data.get("content_b64", "")
        filename = data.get("filename", "document.docx")

        if not content_b64:
            _json_response(h, 400, {"error": "No file content provided"})
            return

        file_bytes = base64.b64decode(content_b64)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext == "pdf":
            _json_response(h, 400, {
                "error": "PDF upload is not supported. Open the PDF in Word, "
                         "save as .docx, then upload that file."
            })
            return
        if ext != "docx":
            _json_response(h, 400, {"error": "Only .docx files are supported."})
            return

        # Smart 3-pass detection: Word styles → heuristics → Claude metadata
        structure  = docx_engine.detect_structure_from_docx(file_bytes)
        text       = docx_engine.extract_text_from_docx(file_bytes)
        word_count = len(text.split())

        if "error" in structure:
            _json_response(h, 200, {
                "title":      "",
                "chapters":   [],
                "word_count": word_count,
                "structured": False,
            })
            return

        result_chapters = []
        for ch in structure.get("chapters", []):
            words = len(ch.get("content", "").split()) + sum(
                len(s.get("content", "").split()) for s in ch.get("sections", [])
            )
            result_chapters.append({
                "number":     ch.get("number", 0),
                "heading":    ch.get("heading", ""),
                "word_count": words,
            })

        _json_response(h, 200, {
            "title":      structure.get("title", ""),
            "chapters":   result_chapters,
            "word_count": word_count,
            "structured": True,
        })

    except Exception as exc:
        logger.error("pb analyse error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_pb_create_order(h, body: bytes) -> None:
    """POST /project-builder/create-order — create Razorpay order for paid tier."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    service    = data.get("service", "")
    university = data.get("university", "")
    word_count = int(data.get("word_count", 0))

    if service not in ("format_fix", "generate"):
        _json_response(h, 400, {"error": "service must be 'format_fix' or 'generate'"})
        return
    if not university:
        _json_response(h, 400, {"error": "university is required"})
        return

    # Pricing: format_fix = Rs.49, generate = Rs.99 (<50 pages est.) or Rs.149
    if service == "format_fix":
        amount_inr = 49
    else:
        est_pages  = max(1, word_count // 250)
        amount_inr = 99 if est_pages < 50 else 149

    from razorpay_integration import create_project_order
    result = create_project_order(
        amount_paise=amount_inr * 100,
        receipt=f"pb_{service[:3]}_{university[:6]}",
    )

    if "error" in result:
        _json_response(h, 500, {"error": result["error"]})
        return

    _json_response(h, 200, {
        "razorpay_order_id": result["id"],
        "amount":            result["amount"],
        "currency":          result["currency"],
        "key_id":            os.environ.get("RAZORPAY_KEY_ID", ""),
    })


def _handle_pb_format_preview(h, body: bytes) -> None:
    """POST /project-builder/format-preview — free generation, upload to Supabase, return token.

    No payment required. Generates the DOCX, uploads it under a UUID path, and
    returns the token so the client can show a preview before charging.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    service    = data.get("service", "format_fix")
    university = data.get("university", "")

    if service not in ("format_fix", "generate"):
        _json_response(h, 400, {"error": "invalid service"})
        return

    import docx_engine

    try:
        if service == "format_fix":
            content_b64  = data.get("content_b64", "")
            content_type = data.get("content_type", "text")   # "text"|"docx"|"pdf"
            content      = data.get("content", "")

            if content_b64:
                import base64 as _b64
                file_bytes = _b64.b64decode(content_b64)
                if content_type == "pdf":
                    _json_response(h, 400, {
                        "error": "PDF upload is not supported. Open the PDF in Word, "
                                 "save as .docx, then upload that file."
                    })
                    return
                if content_type == "docx":
                    # In-place reformat — images and tables preserved
                    docx_bytes = docx_engine.format_fix_docx_inplace(file_bytes, university)
                    structure  = docx_engine.detect_structure_from_docx(file_bytes)
                else:
                    text = file_bytes.decode("utf-8", errors="replace")
                    if len(text) > 200_000:
                        _json_response(h, 400, {"error": "Document too large (max ~200k characters)"})
                        return
                    docx_bytes = docx_engine.format_fix(text, university)
                    try:
                        structure = docx_engine._parse_structure_with_claude(text)
                    except Exception:
                        structure = {}
            elif content:
                text = content
                if len(text) > 200_000:
                    _json_response(h, 400, {"error": "Document too large (max ~200k characters)"})
                    return
                docx_bytes = docx_engine.format_fix(text, university)
                try:
                    structure = docx_engine._parse_structure_with_claude(text)
                except Exception:
                    structure = {}
            else:
                _json_response(h, 400, {"error": "content or content_b64 required"})
                return

        else:  # generate
            form_data = data.get("form_data", {})
            if not form_data:
                _json_response(h, 400, {"error": "form_data required for generate service"})
                return
            docx_bytes = docx_engine.generate_from_form(form_data, university)
            structure = {
                "title": form_data.get("title", ""),
                "chapters": [
                    {"number": i + 1, "heading": ch.get("title") or ch.get("heading", f"Chapter {i + 1}")}
                    for i, ch in enumerate(form_data.get("chapters", []))
                ],
            }

    except ValueError as e:
        _json_response(h, 400, {"error": str(e)})
        return
    except Exception as e:
        logger.error(f"project-builder format-preview generation error: {e}")
        _json_response(h, 500, {"error": "Document generation failed. Please try again."})
        return

    # Upload DOCX to Supabase Storage under a UUID — public bucket, unguessable path
    import uuid as _uuid
    token = str(_uuid.uuid4())
    cfg   = docx_engine.load_university_config(university)
    mime_docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    from db_cloud import upload_file
    url = upload_file(f"project-builder/previews/{token}.docx", docx_bytes, mime_docx)
    if not url:
        _json_response(h, 500, {"error": "Upload failed. Please try again."})
        return

    _json_response(h, 200, {
        "file_token": token,
        "size_bytes": len(docx_bytes),
        "university": cfg.get("short_name", university.upper()),
        "preview":    structure,
    })


def _handle_pb_process(h, body: bytes) -> None:
    """POST /project-builder/process — verify Razorpay payment, generate DOCX."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    # Admin bypass — skip Razorpay if correct admin key provided
    admin_key = data.get("admin_key", "")
    if admin_key:
        client_ip = h.headers.get("x-forwarded-for", h.client_address[0]).split(",")[0].strip()
        if not _check_bypass_rate_limit(client_ip):
            _json_response(h, 429, {"error": "Too many attempts. Try again in 15 minutes."})
            return
        admin_pass = os.environ.get("PB_BYPASS_KEY", "")
        if not admin_pass:
            _json_response(h, 500, {"error": "PB_BYPASS_KEY not set on server"})
            return
        if admin_key != admin_pass:
            _record_bypass_failure(client_ip)
            remaining = _BYPASS_MAX_ATTEMPTS - len(_bypass_attempts[client_ip])
            _json_response(h, 403, {"error": f"Wrong bypass password. {remaining} attempt(s) left."})
            return
        # Password correct — bypass payment
        file_token = data.get("file_token", "")
        if not file_token:
            _json_response(h, 400, {"error": "file_token required for admin download"})
            return
        from db_cloud import get_media_url
        path   = f"project-builder/previews/{file_token}.docx"
        dl_url = get_media_url(path)
        _json_response(h, 200, {"download_url": dl_url})
        return

    payment_id = data.get("razorpay_payment_id", "")
    order_id   = data.get("razorpay_order_id", "")
    signature  = data.get("razorpay_signature", "")

    from razorpay_integration import verify_checkout_payment
    if not verify_checkout_payment(order_id, payment_id, signature):
        _json_response(h, 403, {"error": "Payment verification failed"})
        return

    # Token mode: file was pre-generated by /format-preview — return URL + save order
    file_token = data.get("file_token", "")
    if file_token:
        from db_cloud import get_media_url, save_pb_order
        path   = f"project-builder/previews/{file_token}.docx"
        dl_url = get_media_url(path)

        # Collect optional student metadata from request
        wa_phone     = _normalize_phone(data.get("whatsapp_phone", ""))
        student_name = str(data.get("student_name", ""))[:100]
        service_type = data.get("service", "format_fix")
        university   = data.get("university", "")
        try:
            amount_inr = int(data.get("amount_inr", 49))
        except (ValueError, TypeError):
            amount_inr = 49

        pb_oid = _generate_pb_order_id()
        try:
            save_pb_order(
                order_id=pb_oid,
                tier=service_type,
                university=university,
                whatsapp_phone=wa_phone,
                student_name=student_name,
                razorpay_order_id=order_id,
                razorpay_payment_id=payment_id,
                amount_inr=amount_inr,
                storage_path=path,
                download_url=dl_url,
            )
        except Exception as _e:
            logger.warning(f"save_pb_order non-critical failure for {pb_oid}: {_e}")

        if wa_phone:
            _send_pb_whatsapp(wa_phone, pb_oid, dl_url)

        _json_response(h, 200, {"download_url": dl_url, "order_id": pb_oid})
        return

    service    = data.get("service", "")
    university = data.get("university", "")

    if service not in ("format_fix", "generate"):
        _json_response(h, 400, {"error": "invalid service"})
        return

    import docx_engine

    try:
        if service == "format_fix":
            content_b64  = data.get("content_b64", "")
            content_type = data.get("content_type", "text")  # "text"|"docx"|"pdf"
            content      = data.get("content", "")

            if content_b64:
                import base64 as _b64
                file_bytes = _b64.b64decode(content_b64)
                if content_type == "docx":
                    text = docx_engine.extract_text_from_docx(file_bytes)
                elif content_type == "pdf":
                    text = docx_engine.extract_text_from_pdf(file_bytes)
                else:
                    text = file_bytes.decode("utf-8", errors="replace")
            elif content:
                text = content
            else:
                _json_response(h, 400, {"error": "content or content_b64 required"})
                return

            if len(text) > 200_000:
                _json_response(h, 400, {"error": "Document too large (max ~200k characters)"})
                return

            docx_bytes = docx_engine.format_fix(text, university)

        else:  # generate
            form_data = data.get("form_data", {})
            if not form_data:
                _json_response(h, 400, {"error": "form_data required for generate service"})
                return
            docx_bytes = docx_engine.generate_from_form(form_data, university)

    except ValueError as e:
        _json_response(h, 400, {"error": str(e)})
        return
    except Exception as e:
        logger.error(f"project-builder process error: {e}")
        _json_response(h, 500, {"error": "Document generation failed. Please try again."})
        return

    # Upload to storage and return URL (consistent with token-mode response)
    from db_cloud import upload_pb_doc, save_pb_order
    pb_oid = _generate_pb_order_id()
    dl_url = upload_pb_doc(pb_oid, docx_bytes)
    if not dl_url:
        _json_response(h, 500, {"error": "Storage upload failed. Please try again."})
        return

    wa_phone     = _normalize_phone(data.get("whatsapp_phone", ""))
    student_name = str(data.get("student_name", ""))[:100]
    try:
        amount_inr = int(data.get("amount_inr", 99))
    except (ValueError, TypeError):
        amount_inr = 99

    try:
        save_pb_order(
            order_id=pb_oid,
            tier=service,
            university=university,
            whatsapp_phone=wa_phone,
            student_name=student_name,
            razorpay_order_id=order_id,
            razorpay_payment_id=payment_id,
            amount_inr=amount_inr,
            storage_path=f"project-builder/orders/{pb_oid}.docx",
            download_url=dl_url,
        )
    except Exception as _e:
        logger.warning(f"save_pb_order non-critical failure for {pb_oid}: {_e}")

    if wa_phone:
        _send_pb_whatsapp(wa_phone, pb_oid, dl_url)

    _json_response(h, 200, {"download_url": dl_url, "order_id": pb_oid})


# ── Project Builder — order retrieval (Phase 3) ───────────────────────────────

def _handle_pb_order_get(h, order_id: str) -> None:
    """GET /project-builder/orders/{id}?phone=91XXXXXXXXXX — retrieve an order.

    Requires X-Whatsapp-Phone header or ?phone= query param matching the stored
    phone number. Returns the download_url so the student can re-download.
    """
    from db_cloud import get_pb_order
    # Accept phone from header or query param
    phone = h.headers.get("X-Whatsapp-Phone", "")
    if not phone:
        params = parse_qs(urlparse(h.path).query)
        phone  = params.get("phone", [""])[0]

    order = get_pb_order(order_id, _normalize_phone(phone) if phone else None)
    if not order:
        _json_response(h, 404, {"error": "Order not found or phone number mismatch."})
        return
    _json_response(h, 200, {
        "order_id":     order["id"],
        "tier":         order["tier"],
        "university":   order["university"],
        "download_url": order["download_url"],
        "status":       order["status"],
        "created_at":   order["created_at"],
    })


def _handle_pb_orders_admin(h) -> None:
    """GET /project-builder/orders — list all orders (admin auth required)."""
    admin_pw = h.headers.get("X-Admin-Password", "").strip()
    if not admin_pw:
        params   = parse_qs(urlparse(h.path).query)
        admin_pw = params.get("admin_password", [""])[0]
    if not _auth_admin_pw(admin_pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    from db_cloud import list_pb_orders
    orders = list_pb_orders(limit=200)
    _json_response(h, 200, {"orders": orders, "count": len(orders)})


def _handle_pb_order_resend(h, order_id: str) -> None:
    """POST /project-builder/orders/{id}/resend — re-send WhatsApp (admin only)."""
    admin_pw = h.headers.get("X-Admin-Password", "").strip()
    if not _auth_admin_pw(admin_pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    from db_cloud import get_pb_order
    order = get_pb_order(order_id, whatsapp_phone=None)
    if not order:
        _json_response(h, 404, {"error": "Order not found"})
        return
    phone = order.get("whatsapp_phone", "")
    dl    = order.get("download_url", "")
    if not phone:
        _json_response(h, 400, {"error": "No WhatsApp phone on record for this order"})
        return
    if not dl:
        _json_response(h, 400, {"error": "No download URL on record for this order"})
        return
    _send_pb_whatsapp(phone, order_id, dl)
    _json_response(h, 200, {"ok": True, "sent_to": phone})


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

        # ── Public order tracker (no auth) ───────────────────────────────────
        if self.path.startswith("/api/track/"):
            m = re.match(r"^/api/track/([^/?]+)$", self.path.split("?")[0])
            if m:
                _handle_track(self, m.group(1))
            else:
                _json_response(self, 400, {"error": "missing pickup code"})
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

        if self.path.startswith("/api/health"):
            _handle_health(self)
            return

        # ── Project Builder ──────────────────────────────────────────────────
        if self.path == "/project-builder/templates":
            _handle_pb_templates_get(self)
            return

        _pb = re.match(r"^/project-builder/templates/([^/?]+)$", self.path.split("?")[0])
        if _pb:
            _handle_pb_template_download(self, _pb.group(1))
            return

        # ── Project Builder — order retrieval ────────────────────────────────
        if self.path == "/project-builder/orders":
            _handle_pb_orders_admin(self)
            return

        _pb_order = re.match(r"^/project-builder/orders/([^/?]+)$", self.path.split("?")[0])
        if _pb_order:
            _handle_pb_order_get(self, _pb_order.group(1))
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
        if self.path == "/api/internal/notify-owner":
            _handle_internal_notify_owner(self, body)
            return

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

        if self.path == "/admin/format-fixer":
            _handle_admin_format_fixer(self, body)
            return

        # ── Referral store-credit redemption (staff) ─────────────────────────
        if self.path == "/referrals/redeem":
            _handle_referrals_redeem(self, body)
            return

        # ── Project Builder ──────────────────────────────────────────────────
        if self.path == "/project-builder/analyse":
            _handle_pb_analyse(self, body)
            return

        if self.path == "/project-builder/format-preview":
            _handle_pb_format_preview(self, body)
            return

        if self.path == "/project-builder/create-order":
            _handle_pb_create_order(self, body)
            return

        if self.path == "/project-builder/process":
            _handle_pb_process(self, body)
            return

        _pb_resend = re.match(r"^/project-builder/orders/([^/?]+)/resend$", self.path.split("?")[0])
        if _pb_resend:
            _handle_pb_order_resend(self, _pb_resend.group(1))
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
