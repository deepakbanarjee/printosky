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
        # _send_meta logs the outbound to conversation_log on success; don't
        # double-log here.
        _send(sender, ack)
    except Exception as exc:
        logger.warning(f"_handle_help_request ack send failed for {sender}: {exc}")


WELCOME_MESSAGE = (
    "👋 *Welcome to Printosky!*\n"
    "_Oxygen Students Paradise, Thrissur_\n\n"
    "Happy to help — what do you need today?\n\n"
    "📄 *Printouts / photocopies* — just send your PDF or document here and "
    "we'll quote you instantly.\n"
    "📚 *Xtraa books* — reply *BOOKS* to order the Adithara Balappeduthu set.\n"
    "🧑‍💼 *Talk to our staff* — reply *AGENT* anytime.\n\n"
    "Send your file or reply with one of the options above. 🙏"
)

# Shorter reply for a *returning* customer who greets — prompts them to say
# what they need rather than re-introducing the shop.
GREETING_MESSAGE = (
    "🙏 *Hi! How can we help you today?*\n\n"
    "📄 *Printouts / photocopies* — send your PDF or document here and we'll "
    "quote you instantly.\n"
    "📚 *Xtraa books* — reply *BOOKS*.\n"
    "🧑‍💼 *Talk to staff* — reply *AGENT*.\n\n"
    "Send your file, or tell us what you need. 🙏"
)

_GREETING_WORDS = {
    "hi", "hii", "hiii", "hello", "helo", "hey", "heyy", "hai", "hlo", "hloo",
    "yo", "start", "menu", "namaskaram", "namaste", "vanakkam", "hru",
}


def _is_greeting(text: str) -> bool:
    """True if the message is a greeting / conversation-opener (hi, hello, …)."""
    t = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()
    if not t:
        return False
    if t.startswith(("good morning", "good afternoon", "good evening", "good day")):
        return True
    words = t.split()
    return bool(words) and words[0] in _GREETING_WORDS


# A bot session older than this is treated as abandoned — a greeting resets it
# rather than being silently ignored (prevents a forgotten staff_hold or a
# half-finished print flow from black-holing the customer forever). Matches the
# 1-hour SLA so a single help-trigger never holds a reply longer than that.
STALE_SESSION_HOURS = 1

# Owner phone for SLA escalations. Must be in international format, no '+'.
# IMPORTANT: Meta Cloud API only sends free-form text to a number that has
# messaged us in the past 24h. The owner must keep an open conversation with
# the Printosky WhatsApp (919495706405) or a utility template must be approved.
OWNER_ALERT_PHONE = os.environ.get("OWNER_ALERT_PHONE", "918089699436")


def _session_is_stale(session: dict) -> bool:
    """True if the session's last update is older than STALE_SESSION_HOURS."""
    ts = session.get("updated_at")
    if not ts:
        return True
    try:
        from datetime import datetime, timezone
        dt = datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        return age_hours >= STALE_SESSION_HOURS
    except Exception:
        return False


def _handle_text(sender: str, text: str, name: str | None = None) -> None:
    """Route a customer text through the bot state machine and send replies."""
    from whatsapp_bot import handle_message
    from whatsapp_notify import _send, send_staff_alert

    # Payment verifier (Anu) tapping Confirm/Reject on a forwarded screenshot —
    # handle before any customer-flow routing.
    try:
        from book_bot import handle_verifier_reply
        if handle_verifier_reply(sender, text):
            return
    except Exception as e:
        logger.error(f"Verifier reply error for {sender}: {e}")

    # Help escape hatch: short-circuit before any state-machine work.
    # Customer typed `help` / `support` / `human` / `agent` → flag session,
    # alert staff, ack the customer. TASK-009.
    if _is_help_keyword(text):
        _handle_help_request(sender, text.strip().lower())
        return

    # ── Pre-clear stale sessions ──────────────────────────────────────────────
    # If the customer has an old session (e.g. forgotten staff_hold from days
    # ago, or a half-finished print flow), wipe it BEFORE the book / print bot
    # state machine sees it. Otherwise a stale staff_hold black-holes every new
    # message (book triggers, file uploads, everything).
    try:
        from db_cloud import get_session as _get_session, clear_session as _clear_session
        _stale_check = _get_session("supabase", sender) or {}
        if _stale_check.get("step") and _session_is_stale(_stale_check):
            _clear_session("supabase", sender)
    except Exception as e:
        logger.error(f"Stale-session pre-clear error for {sender}: {e}")

    # ── Xtraa book campaign: separate flow + book_orders table ────────────────
    # Runs before the print state machine. Only takes over when the customer is
    # mid book-order or explicitly enquires about books (never mid print-job).
    try:
        from book_bot import maybe_handle_book
        book_replies = maybe_handle_book(sender, text, name=name)
    except Exception as e:
        logger.error(f"Book flow error for {sender}: {e}")
        book_replies = None
    if book_replies is not None:
        # _send_meta logs the outbound to conversation_log on success; don't
        # double-log here.
        for reply in book_replies:
            _send(sender, reply)
        return

    # Capture referral code; treat ref_CODE message as a plain greeting
    _capture_referral_code(sender, text)

    # Intercept MY CREDITS query - return balance, don't pass to bot
    norm = re.sub(r"\s+", " ", text.strip()).upper()
    if norm in ("MY CREDITS", "MYCREDITS", "MY CREDIT", "BALANCE", "CREDITS"):
        _send_credits_balance(sender)
        return

    # ── Customer welcome / greeting ───────────────────────────────────────────
    # First-time contacts get the full welcome menu (any first message).
    # Returning customers who greet ("hi"/"hello"/…) get a shorter "how can we
    # help" that prompts them to say what they need. Never fires mid print/book
    # flow (no-active-session check); stray non-greeting text from a returning
    # customer stays silent.
    try:
        from db_cloud import get_session as _get_session, is_new_contact
        _session = _get_session("supabase", sender) or {}
        _step = _session.get("step")
        # Idle customer (no session) or a stale/abandoned session → eligible for
        # a welcome/greeting. A recent active flow is protected (not interrupted).
        if (not _step) or _session_is_stale(_session):
            reply_msg = None
            if is_new_contact(sender):
                reply_msg = WELCOME_MESSAGE      # first-time customer
            elif _is_greeting(text):
                reply_msg = GREETING_MESSAGE     # returning customer says hi
            if reply_msg:
                if _step:
                    # Abandoned session (e.g. forgotten staff_hold) — reset it
                    # so the customer isn't stuck being ignored.
                    try:
                        from db_cloud import clear_session
                        clear_session("supabase", sender)
                    except Exception:
                        pass
                # _send_meta logs the outbound; don't double-log here.
                _send(sender, reply_msg)
                return
    except Exception as e:
        logger.error(f"Welcome handler error for {sender}: {e}")

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
            # _send_meta logs the outbound to conversation_log on success;
            # don't double-log here.
            _send(sender, reply)
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

    # ── Book campaign: an image sent during book_pay is a payment screenshot, ──
    # NOT a print job. Intercept before any print-job side effects.
    if msg_type == "image":
        try:
            from db_cloud import get_active_book_order
            from book_bot import handle_payment_proof
            # Source of truth is the ORDER state, NOT the volatile session step.
            # If the sender has a book order awaiting payment / in review, any
            # image is a payment screenshot — route it to the verifier (Anu),
            # never to print-job intake. (Gating on step=="book_pay" misrouted
            # screenshots to print whenever the session drifted or the QR was
            # sent out-of-band.)
            _bo = get_active_book_order(sender) or {}
            if _bo.get("status") in ("awaiting_payment", "payment_review"):
                from whatsapp_notify import _send
                content = _download_meta_media(media_id)
                replies = None
                if content is not None:
                    replies = handle_payment_proof(sender, content, mime_type or "image/jpeg")
                if replies is None:
                    # Download failed or proof not accepted. Ask the customer to
                    # resend — but do NOT fall through to print-job creation,
                    # which would create a spurious job and send wrong prompts.
                    replies = ["We couldn't read that image. Please resend a clear "
                               "screenshot of your payment confirmation. 🙏"]
                # _send_meta logs the outbound; don't double-log here.
                for reply in replies:
                    _send(sender, reply)
                return None
        except Exception as e:
            logger.error(f"Book payment-proof handling failed for {sender}: {e}")

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

            # Outbound status callbacks (sent/delivered/read/failed) carry a
            # `pricing` object — Meta's billing category + billable flag. Record
            # per-message cost telemetry (wa_message_costs). Best-effort: never
            # blocks message handling. A webhook value holds messages OR statuses.
            for st in value.get("statuses", []):
                try:
                    from db_cloud import record_wa_message_cost
                    record_wa_message_cost(
                        st.get("id", ""), st.get("recipient_id"), st.get("status"),
                        pricing=st.get("pricing"), conversation=st.get("conversation"),
                    )
                except Exception as exc:
                    logger.error("wa status cost record failed: %s", exc)

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
                            _handle_text(sender, text, name=pushname)
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
                elif msg_type == "interactive":
                    # Customer tapped a reply button or list row. Extract the id
                    # (e.g. 'bk_ml', 'qty_2', 'ord_yes') and route it through the
                    # same text handler — book_bot interprets these ids.
                    inter = msg.get("interactive", {})
                    itype = inter.get("type", "")
                    reply = inter.get(itype, {}) if itype else {}
                    reply_id    = reply.get("id", "")
                    reply_title = reply.get("title", "")
                    if reply_id:
                        try:
                            from db_cloud import log_message, upsert_contact
                            log_message(sender, "inbound", reply_title or reply_id,
                                        message_type="text")
                            upsert_contact(sender, name=pushname)
                        except Exception:
                            pass
                        _handle_text(sender, reply_id, name=pushname)


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


# ── Admin handlers (extracted to api/handlers_admin.py) ─────────────────────
from api.handlers_admin import (  # noqa: E402
    _handle_admin_book_order_confirm,
    _handle_admin_book_order_create,
    _handle_admin_book_order_deliver,
    _handle_admin_book_order_dispatch,
    _handle_admin_book_order_edit,
    _handle_admin_book_order_settle_divya,
    _handle_admin_book_orders_list,
    _handle_admin_contacts_seen,
    _handle_admin_conversations,
    _handle_admin_divya_ledger,
    _handle_admin_format_fixer,
    _handle_admin_health_models,
    _handle_admin_operator_queue_claim,
    _handle_admin_operator_queue_deliver,
    _handle_admin_operator_queue_depth,
    _handle_admin_operator_queue_get,
    _handle_admin_operator_queue_list,
    _handle_admin_reset_pin,
    _handle_admin_send,
    _handle_admin_send_file,
    _handle_admin_thread,
    _handle_admin_upload_token,
)





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

# ── Referral handlers (extracted to api/handlers_referrals.py) ──────────────
from api.handlers_referrals import (  # noqa: E402
    _handle_referrals_balance,
    _handle_referrals_credits,
    _handle_referrals_leaderboard,
    _handle_referrals_redeem,
)









# ── Academic handlers (extracted to api/handlers_academic.py) ───────────────
from api.handlers_academic import (  # noqa: E402
    _handle_acad_approve_chapters,
    _handle_acad_approve_final,
    _handle_acad_deliver,
    _handle_acad_finalize,
    _handle_acad_generate,
    _handle_acad_order_get,
    _handle_acad_orders_get,
    _handle_acad_orders_post,
    _handle_acad_razorpay_webhook,
    _handle_acad_revise,
)



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


























# ── Operator queue (P0 Day 2.5) ──────────────────────────────────────────────





def _handle_cron_sla_check(h) -> None:
    """GET /cron/sla-check — Vercel cron entry point.

    Finds customers whose latest inbound is > 1h old without a reply, and pings
    OWNER_ALERT_PHONE so the owner knows to check the admin Conversations tab.
    Each customer is alerted at most once per 6h (cooldown).

    Auth: Vercel sends `Authorization: Bearer ${CRON_SECRET}` on cron requests
    when CRON_SECRET is set. If unset, the endpoint is reachable but harmless
    (it only reads + alerts).
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected:
        if h.headers.get("Authorization", "") != f"Bearer {expected}":
            _json_response(h, 401, {"error": "Unauthorized"})
            return
    try:
        from db_cloud import find_sla_breaches, mark_sla_alerted
        from whatsapp_notify import _send

        breaches = find_sla_breaches(threshold_hours=1, alert_cooldown_hours=6)
        sent = False
        if breaches:
            count = len(breaches)
            sample = ", ".join("…" + (b["phone"] or "")[-4:] for b in breaches[:5])
            msg = (
                "⏰ *Printosky SLA alert*\n\n"
                f"{count} customer{'s' if count != 1 else ''} waiting > 1 hour for a reply.\n"
                f"Numbers (last 4): {sample}\n\n"
                "Please check oxygen admin → *Conversations* tab and reply, "
                "or visit printosky.com/admin."
            )
            sent = _send(OWNER_ALERT_PHONE, msg)
            if sent:
                for b in breaches:
                    mark_sla_alerted(b["phone"])
            else:
                logger.warning("SLA alert send failed (Meta 24h window? template needed?)")
        _json_response(h, 200, {
            "ok": True,
            "breaches": len(breaches),
            "alerted": bool(sent and breaches),
        })
    except Exception as exc:
        logger.error("SLA cron error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_cron_abandoned_carts(h) -> None:
    """GET /cron/abandoned-carts — nudge customers who left a book order unfinished.

    Sweeps book_orders for open carts (collecting / awaiting_payment) that have
    been idle a couple of hours but are still within WhatsApp's 24-hour window,
    and sends each a one-time 'finish your order' reminder.
    Auth: optional `Authorization: Bearer ${CRON_SECRET}` when CRON_SECRET is set.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and h.headers.get("Authorization", "") != f"Bearer {expected}":
        _json_response(h, 401, {"error": "Unauthorized"})
        return
    try:
        from book_bot import send_abandoned_reminders
        result = send_abandoned_reminders()
        _json_response(h, 200, {"ok": True, **result})
    except Exception as exc:
        logger.error("abandoned-carts cron error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})
































# ── Project Builder helpers ──────────────────────────────────────────────────





# ── Project-builder handlers (extracted to api/handlers_pb.py) ───────────────
from api.handlers_pb import (  # noqa: E402
    _handle_pb_analyse,
    _handle_pb_availability,
    _handle_pb_create_order,
    _handle_pb_format_job_create,
    _handle_pb_format_job_status,
    _handle_pb_format_preview,
    _handle_pb_format_preview_v2,
    _handle_pb_format_preview_v3,
    _handle_pb_order_get,
    _handle_pb_order_resend,
    _handle_pb_orders_admin,
    _handle_pb_process,
    _handle_pb_template_download,
    _handle_pb_templates_get,
    _handle_pb_upload_sign,
)

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

        # ── v4 job status poll ──────────────────────────────────────────────
        if self.path.startswith("/project-builder/format-job-status"):
            qs = parse_qs(urlparse(self.path).query)
            _handle_pb_format_job_status(self, qs)
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
        if self.path.startswith("/admin/health/models"):
            _handle_admin_health_models(self)
            return

        # ── Xtraa book orders ────────────────────────────────────────────────
        # Exact path (+ optional query) only, so a GET to a sub-route like
        # /admin/book-orders/<code>/confirm is not swallowed by the list handler.
        if self.path == "/admin/book-orders" or self.path.startswith("/admin/book-orders?"):
            _handle_admin_book_orders_list(self)
            return

        # Divya teacher settlement statement (admin-only).
        if self.path == "/admin/book-orders/divya-ledger" or self.path.startswith("/admin/book-orders/divya-ledger?"):
            _handle_admin_divya_ledger(self)
            return

        # ── SLA watchdog (GitHub Actions cron, every 30 min) ──────────────────
        if self.path == "/cron/sla-check" or self.path.startswith("/cron/sla-check?"):
            _handle_cron_sla_check(self)
            return

        # ── Abandoned book-cart reminders (GitHub Actions cron) ───────────────
        if self.path == "/cron/abandoned-carts" or self.path.startswith("/cron/abandoned-carts?"):
            _handle_cron_abandoned_carts(self)
            return

        # ── Operator queue (P0 Day 2.5) ──────────────────────────────────────
        # Order matters: more-specific paths first.
        if self.path.startswith("/admin/operator-queue/depth"):
            _handle_admin_operator_queue_depth(self)
            return
        _opq_match = re.match(
            r"^/admin/operator-queue/([0-9a-f-]{36})(\?.*)?$",
            self.path,
        )
        if _opq_match:
            _handle_admin_operator_queue_get(self, _opq_match.group(1))
            return
        if self.path.startswith("/admin/operator-queue"):
            _handle_admin_operator_queue_list(self)
            return

        if self.path.startswith("/api/health"):
            _handle_health(self)
            return

        # ── Project Builder ──────────────────────────────────────────────────
        if self.path == "/project-builder/availability":
            _handle_pb_availability(self)
            return
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

        # ── Operator queue claim / deliver (P0 Day 2.5) ──────────────────────
        _opq_claim = re.match(
            r"^/admin/operator-queue/([0-9a-f-]{36})/claim$", self.path,
        )
        if _opq_claim:
            _handle_admin_operator_queue_claim(self, body, _opq_claim.group(1))
            return
        _opq_deliver = re.match(
            r"^/admin/operator-queue/([0-9a-f-]{36})/deliver$", self.path,
        )
        if _opq_deliver:
            _handle_admin_operator_queue_deliver(self, body, _opq_deliver.group(1))
            return

        # ── Xtraa book order: owner confirms payment ─────────────────────────
        if self.path == "/admin/book-orders/create":
            _handle_admin_book_order_create(self, body)
            return
        _book_confirm = re.match(
            r"^/admin/book-orders/([A-Za-z0-9\-]+)/confirm$", self.path,
        )
        if _book_confirm:
            _handle_admin_book_order_confirm(self, _book_confirm.group(1))
            return
        _book_dispatch = re.match(
            r"^/admin/book-orders/([A-Za-z0-9\-]+)/dispatch$", self.path,
        )
        if _book_dispatch:
            _handle_admin_book_order_dispatch(self, body, _book_dispatch.group(1))
            return
        _book_deliver = re.match(
            r"^/admin/book-orders/([A-Za-z0-9\-]+)/deliver$", self.path,
        )
        if _book_deliver:
            _handle_admin_book_order_deliver(self, _book_deliver.group(1))
            return
        _book_settle = re.match(
            r"^/admin/book-orders/([A-Za-z0-9\-]+)/settle-divya$", self.path,
        )
        if _book_settle:
            _handle_admin_book_order_settle_divya(self, body, _book_settle.group(1))
            return
        _book_edit = re.match(
            r"^/admin/book-orders/([A-Za-z0-9\-]+)/edit$", self.path,
        )
        if _book_edit:
            _handle_admin_book_order_edit(self, body, _book_edit.group(1))
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

        if self.path == "/project-builder/format-preview-v2":
            _handle_pb_format_preview_v2(self, body)
            return

        if self.path == "/project-builder/format-preview-v3":
            _handle_pb_format_preview_v3(self, body)
            return

        if self.path == "/project-builder/format-job-create":
            _handle_pb_format_job_create(self, body)
            return

        if self.path == "/project-builder/upload-sign":
            _handle_pb_upload_sign(self, body)
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
