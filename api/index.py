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


def _maybe_credit_note_uploader(job: dict) -> None:
    """Credit the notes uploader when a marketplace-sourced print job is paid.

    Triggered from _process_razorpay_payment after update_job_paid().
    Only fires when job's file_name starts with "[NOTE-" (set at job-creation
    time when a customer prints a marketplace note).
    Pattern: "[NOTE-YYYYMMDD-XXXX] <title>.pdf"
    """
    import re as _re
    from db_cloud import get_note, wallet_add_credit, NOTE_CREDIT_PAISE_PER_PAGE

    file_name = (job.get("file_name") or job.get("filename") or "")
    m = _re.match(r"^\[NOTE-([A-Z0-9\-]+)\]", file_name)
    if not m:
        return

    note_code = m.group(1)
    note = get_note(note_code)
    if not note:
        logger.warning("_maybe_credit_note_uploader: note %s not found", note_code)
        return

    uploader_phone = note.get("uploader_phone", "")
    pages = int(note.get("page_count") or 0)
    credit_paise = pages * NOTE_CREDIT_PAISE_PER_PAGE
    job_id = job.get("job_id") or job.get("id") or ""

    if uploader_phone and credit_paise > 0:
        ok = wallet_add_credit(note_code, uploader_phone, job_id, pages, credit_paise)
        if ok:
            logger.info(
                "Notes credit ₹%.2f → %s for %s (job %s)",
                credit_paise / 100, uploader_phone, note_code, job_id,
            )
        else:
            logger.error(
                "wallet_add_credit failed for %s / %s", uploader_phone, note_code
            )


# ── Notes website auth + upload handlers ─────────────────────────────────────

WA_STORE_NUMBER = "919495706405"


def _notes_auth(h) -> tuple[str | None, str | None]:
    """Validate Authorization header. Returns (identity, None) or (None, error_msg).

    Accepts two token types:
      1. WhatsApp OTP web_token (issued by /auth/wa-otp/verify) — identity = phone
      2. Supabase JWT (Google / email magic link) — identity = email
    """
    auth = h.headers.get("Authorization", "").strip()
    if not auth.startswith("Bearer "):
        return None, "Authorization required"
    token = auth[7:].strip()
    if not token:
        return None, "Empty token"

    # Try WhatsApp OTP token first (fast — DB lookup only)
    try:
        from db_cloud import get_otp_session_by_web_token
        otp_sess = get_otp_session_by_web_token(token)
        if otp_sess:
            return otp_sess.get("phone", ""), None
    except Exception as _e:
        logger.error("_notes_auth OTP check error: %s", _e)

    # Try Supabase JWT (Google / email magic link)
    try:
        from db_cloud import _client
        user_resp = _client().auth.get_user(token)
        if user_resp and getattr(user_resp, "user", None):
            return user_resp.user.email or "", None
    except Exception as _e:
        logger.debug("_notes_auth Supabase check: %s", _e)

    return None, "Invalid or expired token"


def _mask_phone(phone: str) -> str:
    """Mask a phone for display: keep last 4 digits."""
    p = (phone or "").strip()
    return p if len(p) <= 4 else "•••• •••• " + p[-4:]


def _resolve_account(h) -> dict:
    """Resolve the Authorization header to a canonical, phone-anchored account.

    Phone is the real customer identity (wallet, credits, print jobs are all
    keyed on it). Two ways in:
      • WhatsApp OTP web_token  → phone is known and verified.
      • Supabase JWT (Google/email) → email known; phone only if they've linked
        one. Until then needs_phone_link=True and phone-keyed features are blocked.

    Returns a dict:
      {ok: False, error: str}                                  on auth failure, or
      {ok: True, kind: 'phone'|'email', phone: str|None,
       email: str|None, name: str, needs_phone_link: bool}
    """
    auth = h.headers.get("Authorization", "").strip()
    if not auth.startswith("Bearer "):
        return {"ok": False, "error": "Authorization required"}
    token = auth[7:].strip()
    if not token:
        return {"ok": False, "error": "Empty token"}

    # WhatsApp OTP web_token → phone (verified)
    try:
        from db_cloud import get_otp_session_by_web_token
        sess = get_otp_session_by_web_token(token)
        if sess:
            return {
                "ok": True, "kind": "phone",
                "phone": sess.get("phone", ""), "email": None,
                "name": "", "needs_phone_link": False,
            }
    except Exception as _e:
        logger.error("_resolve_account OTP check: %s", _e)

    # Supabase JWT (Google / email magic link) → email, plus linked phone if any
    try:
        from db_cloud import _client, get_linked_phone
        user_resp = _client().auth.get_user(token)
        user = getattr(user_resp, "user", None) if user_resp else None
        if user:
            email = (user.email or "").lower()
            meta = getattr(user, "user_metadata", None) or {}
            name = meta.get("full_name") or meta.get("name") or ""
            phone = get_linked_phone(email) if email else None
            return {
                "ok": True, "kind": "email",
                "phone": phone, "email": email, "name": name,
                "needs_phone_link": not bool(phone),
            }
    except Exception as _e:
        logger.debug("_resolve_account Supabase check: %s", _e)

    return {"ok": False, "error": "Invalid or expired token"}


def _handle_account_summary(h, body: bytes) -> None:
    """POST /account/summary — the personal space payload for the logged-in user.

    Returns wallet balance, the user's own uploaded notes (any status, with
    per-note earnings) and subscription status. If the account is a Google/email
    login without a linked phone, returns needs_phone_link=True instead.
    """
    acct = _resolve_account(h)
    if not acct.get("ok"):
        _json_response(h, 401, {"error": acct.get("error", "Unauthorized")})
        return

    if acct.get("needs_phone_link"):
        _json_response(h, 200, {
            "needs_phone_link": True,
            "kind": acct["kind"],
            "email": acct.get("email"),
            "name": acct.get("name") or "",
        })
        return

    phone = acct["phone"]
    try:
        from db_cloud import (
            wallet_balance, list_notes_by_uploader, note_subscription_status,
            list_jobs_by_sender,
        )
        balance_paise = wallet_balance(phone)
        notes = list_notes_by_uploader(phone)
        sub = note_subscription_status(phone)
        orders = list_jobs_by_sender(phone, limit=20)

        out_notes = []
        for n in notes:
            pages = int(n.get("page_count") or 0)
            prints = int(n.get("print_count") or 0)
            out_notes.append({
                "note_code":   n.get("note_code"),
                "title":       n.get("title"),
                "category":    n.get("category"),
                "subject":     n.get("subject"),
                "status":      n.get("status"),
                "page_count":  pages,
                "print_count": prints,
                "earned_rs":   round(prints * pages * 10 / 100, 2),
            })

        out_orders = []
        for j in orders:
            out_orders.append({
                "job_id":      j.get("job_id"),
                "placed_at":   j.get("received_at"),
                "filename":    j.get("filename"),
                "status":      j.get("status") or "Pending",
                "pages":       int(j.get("page_count") or 0),
                "copies":      int(j.get("copies") or 1),
                "colour":      j.get("colour") or "",
                "finishing":   j.get("finishing") or "",
                "amount_rs":   round(float(j.get("amount_collected") or j.get("amount_quoted") or 0), 2),
                "pickup_code": j.get("pickup_code") or "",
                "delivery":    int(j.get("delivery") or 0),
            })

        _json_response(h, 200, {
            "needs_phone_link": False,
            "kind":         acct["kind"],
            "phone_masked": _mask_phone(phone),
            "name":         acct.get("name") or "",
            "wallet_rs":    round(balance_paise / 100, 2),
            "notes":        out_notes,
            "orders":       out_orders,
            "subscription": sub or {},
        })
    except Exception as exc:
        logger.error("account/summary error: %s", exc)
        _json_response(h, 500, {"error": "Internal error"})


def _handle_account_link_phone(h, body: bytes) -> None:
    """POST /account/link-phone/verify — link a verified phone to an email account.

    The user logged in with Google/email (Authorization = Supabase JWT), then
    verified a WhatsApp number via the OTP flow. Body: { request_token, otp }.
    On success the email is permanently anchored to that phone.
    """
    acct = _resolve_account(h)
    if not acct.get("ok"):
        _json_response(h, 401, {"error": acct.get("error", "Unauthorized")})
        return
    if acct.get("kind") != "email" or not acct.get("email"):
        _json_response(h, 400, {"error": "Only Google/email accounts link a phone"})
        return
    try:
        payload = json.loads(body) if body else {}
        req_token = (payload.get("request_token") or "").strip().upper()
        otp = (payload.get("otp") or "").strip()
        if not req_token or not otp:
            _json_response(h, 400, {"error": "request_token and otp required"})
            return
        from db_cloud import verify_otp_session, link_account_email
        sess = verify_otp_session(req_token, otp)
        if not sess:
            _json_response(h, 401, {"error": "Invalid or expired OTP"})
            return
        phone = sess["phone"]
        if not link_account_email(acct["email"], phone, acct.get("name")):
            _json_response(h, 500, {"error": "Could not link account"})
            return
        _json_response(h, 200, {"phone": phone, "phone_masked": _mask_phone(phone), "linked": True})
    except Exception as exc:
        logger.error("account/link-phone error: %s", exc)
        _json_response(h, 500, {"error": "Internal error"})


def _handle_account_referral(h, body: bytes) -> None:
    """POST /account/referral — the logged-in user's Refer & Earn payload.

    Get-or-creates the user's referrer code (keyed on their canonical phone),
    then returns the share link, unredeemed credit balance, and how many
    friends they've referred. Reuses the existing referrers/referral_credits
    engine — just surfaces it to the account hub instead of WhatsApp-only.
    """
    acct = _resolve_account(h)
    if not acct.get("ok"):
        _json_response(h, 401, {"error": acct.get("error", "Unauthorized")})
        return
    if acct.get("needs_phone_link"):
        _json_response(h, 403, {"error": "link_phone_required", "needs_phone_link": True})
        return
    phone = acct["phone"]
    try:
        import random as _r, string as _s
        from db_cloud import _client
        sb = _client()

        # get-or-create the referrer code for this phone (label = canonical phone)
        existing = sb.table("referrers").select("code").eq("label", phone).execute()
        if existing.data:
            code = existing.data[0]["code"]
        else:
            code = None
            tail = phone[-4:] if len(phone) >= 4 else phone.rjust(4, "0")
            for _ in range(10):
                candidate = "REF" + tail + "".join(_r.choices(_s.ascii_uppercase, k=2))
                hit = sb.table("referrers").select("code").eq("code", candidate).execute()
                if not hit.data:
                    sb.table("referrers").insert({
                        "code": candidate,
                        "label": phone,
                        "platform": "web_account",
                    }).execute()
                    code = candidate
                    break
            if not code:
                _json_response(h, 500, {"error": "Could not create referral code"})
                return

        # balance (unredeemed credits) + distinct friends referred
        rows = (
            sb.table("referral_credits")
            .select("amount_inr,customer_phone,redeemed_at")
            .eq("referrer_code", code)
            .execute()
        )
        data = rows.data or []
        balance = sum(int(c.get("amount_inr") or 0) for c in data if not c.get("redeemed_at"))
        referred = len({c.get("customer_phone") for c in data if c.get("customer_phone")})

        _json_response(h, 200, {
            "code": code,
            "share_link": f"https://wa.me/{WA_STORE_NUMBER}?text=ref_{code}",
            "balance_inr": balance,
            "referred_count": referred,
        })
    except Exception as exc:
        logger.error("account/referral error: %s", exc)
        _json_response(h, 500, {"error": "Internal error"})


def _handle_wa_otp_request(h, body: bytes) -> None:
    """POST /auth/wa-otp/request — generate request token, return WhatsApp deep link."""
    import secrets as _sec
    try:
        payload = json.loads(body) if body else {}
        phone_raw = (payload.get("phone") or "").strip().replace("+", "").replace(" ", "").replace("-", "")
        if not phone_raw or len(phone_raw) < 10:
            _json_response(h, 400, {"error": "Valid phone number required"})
            return
        phone = ("91" + phone_raw) if len(phone_raw) == 10 else phone_raw
        req_token = "REQ-" + _sec.token_hex(4).upper()
        from db_cloud import create_otp_session
        if not create_otp_session(phone, req_token):
            _json_response(h, 500, {"error": "Session creation failed"})
            return
        wa_link = f"https://wa.me/{WA_STORE_NUMBER}?text=GETOTP+{req_token}"
        _json_response(h, 200, {"request_token": req_token, "wa_link": wa_link, "expires_in": 600})
    except Exception as exc:
        logger.error("wa-otp/request error: %s", exc)
        _json_response(h, 500, {"error": "Internal error"})


def _handle_wa_otp_verify(h, body: bytes) -> None:
    """POST /auth/wa-otp/verify — verify OTP, return web_token."""
    try:
        payload = json.loads(body) if body else {}
        req_token = (payload.get("request_token") or "").strip().upper()
        otp = (payload.get("otp") or "").strip()
        if not req_token or not otp:
            _json_response(h, 400, {"error": "request_token and otp required"})
            return
        from db_cloud import verify_otp_session
        session = verify_otp_session(req_token, otp)
        if not session:
            _json_response(h, 401, {"error": "Invalid or expired OTP"})
            return
        _json_response(h, 200, {"web_token": session["web_token"], "phone": session["phone"]})
    except Exception as exc:
        logger.error("wa-otp/verify error: %s", exc)
        _json_response(h, 500, {"error": "Internal error"})


def _gen_note_code() -> str:
    """Generate NOTE-YYYYMMDD-XXXX.

    Inlined here (mirrors handlers_notes._gen_note_code) so the web upload path
    has ZERO dependency on the handlers_notes sibling module — @vercel/python
    does not reliably bundle lazily-imported siblings, which made reserve throw
    ModuleNotFoundError in production.
    """
    import random as _r, string as _s
    from datetime import timezone as _tz
    date_part = datetime.now(_tz.utc).strftime("%Y%m%d")
    suffix = "".join(_r.choices(_s.ascii_uppercase + _s.digits, k=4))
    return f"NOTE-{date_part}-{suffix}"


def _handle_notes_reserve(h, body: bytes) -> None:
    """POST /notes/reserve — generate note_code and Supabase signed upload URL.

    Returns { note_code, upload_url, storage_path } so the browser can upload
    the PDF directly to Supabase Storage (bypasses Vercel 4.5MB limit).
    """
    acct = _resolve_account(h)
    if not acct.get("ok"):
        _json_response(h, 401, {"error": acct.get("error", "Unauthorized")})
        return
    if acct.get("needs_phone_link"):
        _json_response(h, 403, {"error": "link_phone_required", "needs_phone_link": True})
        return
    try:
        from db_cloud import _client, NOTES_BUCKET
        note_code = _gen_note_code()
        storage_path = f"notes/{note_code}.pdf"
        result = _client().storage.from_(NOTES_BUCKET).create_signed_upload_url(storage_path)
        signed_url = result.get("signedURL") or result.get("signed_url") or ""
        if not signed_url:
            _json_response(h, 500, {"error": "Could not generate upload URL"})
            return
        _json_response(h, 200, {
            "note_code": note_code,
            "upload_url": signed_url,
            "storage_path": storage_path,
        })
    except Exception as exc:
        import traceback as _tb
        logger.error("notes/reserve error: %s\n%s", exc, _tb.format_exc())
        _json_response(h, 500, {"error": "Internal error"})


def _handle_notes_submit(h, body: bytes) -> None:
    """POST /notes/submit — create the note row after PDF is uploaded to Supabase.

    Body: { note_code, storage_path, title, category, subject, page_count, attests }
    """
    acct = _resolve_account(h)
    if not acct.get("ok"):
        _json_response(h, 401, {"error": acct.get("error", "Unauthorized")})
        return
    if acct.get("needs_phone_link"):
        _json_response(h, 403, {"error": "link_phone_required", "needs_phone_link": True})
        return
    try:
        payload = json.loads(body) if body else {}
        note_code    = (payload.get("note_code") or "").strip()
        storage_path = (payload.get("storage_path") or "").strip()
        title        = (payload.get("title") or "").strip()
        category     = (payload.get("category") or "").strip()
        subject      = (payload.get("subject") or "").strip()
        page_count   = int(payload.get("page_count") or 0)
        attests      = bool(payload.get("attests", False))
        if not all([note_code, storage_path, title, category, subject]) or page_count < 1:
            _json_response(h, 400, {"error": "All fields required; page_count must be > 0"})
            return
        if not attests:
            _json_response(h, 400, {"error": "You must attest these are your original notes"})
            return

        # Uploader is always anchored to the canonical phone (credits pay a phone).
        from db_cloud import create_note
        row = create_note(
            note_code=note_code,
            uploader_phone=acct["phone"],
            title=title,
            category=category,
            subject=subject,
            page_count=page_count,
            storage_path=storage_path,
            attests=attests,
        )
        if not row:
            _json_response(h, 500, {"error": "Failed to save note"})
            return

        potential_rs = page_count * 10 / 100
        _json_response(h, 200, {
            "note_code": note_code,
            "page_count": page_count,
            "potential_credit_rs": round(potential_rs, 2),
            "status": "pending",
        })
    except Exception as exc:
        logger.error("notes/submit error: %s", exc)
        _json_response(h, 500, {"error": "Internal error"})


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
    "📝 *Sell your notes* — upload at printosky.com/account and earn store "
    "credit every time someone prints them.\n"
    "🎁 *Refer & earn ₹20* — grab your share link at printosky.com/account; "
    "you and your friend both get credit.\n"
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
    "📝 *Sell your notes & 🎁 refer friends* — earn store credit at "
    "printosky.com/account.\n"
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


# Short acknowledgements / option inputs that should NOT trigger a human handoff.
_HANDOFF_NOOP = {
    "ok", "okay", "k", "kk", "thanks", "thank you", "thankyou", "ty", "tq",
    "done", "good", "great", "nice", "fine", "cool", "yes", "no", "y", "n",
    "👍", "🙏", "ശരി", "നന്ദി", "ഓക്കെ",
}


def _should_handoff_text(text: str) -> bool:
    """Heuristic: is this free-text worth routing to a human (vs ignoring)?

    True for real questions/sentences; False for courtesy words, bare digits/
    option taps, emoji, and very short inputs.
    """
    t = (text or "").strip()
    if len(t) < 3:
        return False
    if t.lower() in _HANDOFF_NOOP:
        return False
    # Needs a couple of letters (any script) — filters bare digits, punctuation
    # and emoji, while accepting Malayalam, where combining vowel marks would
    # break a consecutive-letter run.
    return sum(1 for ch in t if ch.isalpha()) >= 2


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
    # ── WhatsApp OTP for website auth (MUST be first — before all other flows) ──
    # Message format sent by user from wa.me deep link: "GETOTP REQ-XXXXXXXX"
    if text.strip().upper().startswith("GETOTP "):
        _otp_parts = text.strip().split()
        if len(_otp_parts) >= 2:
            _req_token = _otp_parts[1].upper()
            try:
                import random as _random
                from db_cloud import get_otp_session, set_otp_code
                _otp_row = get_otp_session(_req_token)
                if _otp_row:
                    # Confirm sender phone matches the phone that started the session
                    _stored = (_otp_row.get("phone") or "").lstrip("+")
                    _snorm = sender.lstrip("+")
                    _ok = _stored.endswith(_snorm[-10:]) or _snorm.endswith(_stored[-10:])
                    if _ok and _otp_row.get("status") in ("pending", "sent"):
                        if _otp_row["status"] == "pending":
                            _code = str(_random.randint(100000, 999999))
                            set_otp_code(_req_token, _code)
                        else:
                            _code = _otp_row.get("otp_code", "")
                        # _send (whatsapp_notify._send) takes a plain STRING and
                        # builds the Meta payload itself. Passing a dict here made
                        # the message body a dict → Meta 400 → OTP never delivered.
                        _send(
                            sender,
                            f"Your Printosky verification code is: *{_code}*\n\n"
                            "Valid for 10 minutes. Do not share this code.",
                        )
            except Exception as _oe:
                logger.error("GETOTP handler error %s: %s", sender, _oe)
        return

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

    # ── Notes marketplace ──────────────────────────────────────────────────────
    # 1) Active upload flow or fresh trigger ("upload notes", "sell notes", …)
    # 2) "print note NOTE-XXXX" — look up note, send info + signed download URL
    notes_replies = None
    try:
        from handlers_notes import maybe_handle_notes, is_print_note_trigger
        notes_replies = maybe_handle_notes(sender, text, name=name or "")
        if notes_replies is None:
            note_code = is_print_note_trigger(text)
            if note_code:
                from db_cloud import get_note, note_signed_url
                note = get_note(note_code)
                if note and note.get("status") == "approved":
                    pages = note.get("page_count", 0)
                    price = pages  # ₹1/page
                    url = note_signed_url(note.get("storage_path", ""), ttl=1800)
                    if url:
                        body = (
                            f"*{note['title']}*\n"
                            f"{note.get('subject','')} · {pages} pages\n\n"
                            f"Print price: *₹{price}* (₹1/page)\n\n"
                            f"Download to bring to the store:\n{url}\n\n"
                            "_Link valid for 30 minutes._"
                        )
                    else:
                        body = f"Sorry, couldn't generate a link for *{note_code}* right now. Try again shortly."
                    notes_replies = [{"messaging_product": "whatsapp", "to": sender,
                                      "type": "text", "text": {"body": body}}]
                elif note:
                    notes_replies = [{"messaging_product": "whatsapp", "to": sender,
                                      "type": "text", "text": {"body": f"*{note_code}* is pending admin review. Check back soon!"}}]
                else:
                    notes_replies = [{"messaging_product": "whatsapp", "to": sender,
                                      "type": "text", "text": {"body": f"Notes *{note_code}* not found."}}]
    except Exception as _exc:
        logger.error("Notes flow error for %s: %s", sender, _exc)
        notes_replies = None
    if notes_replies is not None:
        for _msg in notes_replies:
            _send(sender, _msg)
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
    customer_is_idle = False
    customer_is_new = False
    try:
        from db_cloud import get_session as _get_session, is_new_contact
        _session = _get_session("supabase", sender) or {}
        _step = _session.get("step")
        # Idle customer (no session) or a stale/abandoned session → eligible for
        # a welcome/greeting. A recent active flow is protected (not interrupted).
        if (not _step) or _session_is_stale(_session):
            customer_is_idle = True
            customer_is_new = is_new_contact(sender)
            reply_msg = None
            if customer_is_new:
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
    handled = False
    for reply in replies:
        if isinstance(reply, str):
            # _send_meta logs the outbound to conversation_log on success;
            # don't double-log here.
            _send(sender, reply)
            handled = True
        elif isinstance(reply, tuple) and reply:
            tag = reply[0]
            if tag in ("STAFF_QUOTE", "STAFF_MIXED_TIMEOUT"):
                msg = reply[1] if len(reply) > 1 else str(reply)
                send_staff_alert(msg)
                handled = True

    # ── Human handoff fallback ────────────────────────────────────────────────
    # An idle, returning customer said something no handler understood (not a
    # greeting, book/print command, tracking question, or help keyword). Don't
    # leave them ignored or dump a canned menu — hold for a human, ack once, and
    # flag the conversation. staff_hold makes the bot stay quiet on follow-ups
    # until staff resume (and auto-clears once the session goes stale).
    if (not handled) and customer_is_idle and (not customer_is_new) \
            and _should_handoff_text(text):
        try:
            from db_cloud import save_session
            _mark_session_needs_human(sender)
            save_session("supabase", sender, step="staff_hold", needs_human=True)
            _send(sender, "🙏 Thanks for your message — a team member will reply to "
                          "you shortly. You can keep typing in the meantime.")
            send_staff_alert(
                f"🤖→🧑 Bot couldn't handle a message from {_fmt_phone(sender)}: "
                f"\"{text.strip()[:80]}\". Open Conversations → 'Needs human'."
            )
        except Exception as e:
            logger.error(f"Human handoff error for {sender}: {e}")


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

    # ── Notes marketplace: PDF during upload flow ──────────────────────────────
    # If the customer is in the note_await_pdf step, any PDF they send is their
    # notes document — route to the notes handler, never to print-job intake.
    if mime_type == "application/pdf":
        try:
            from db_cloud import get_session as _gs_notes
            _note_sess = _gs_notes(sender) or {}
            if _note_sess.get("step") == "note_await_pdf":
                from handlers_notes import handle_notes_pdf
                from whatsapp_notify import _send as _wa_send
                _pdf_bytes = _download_meta_media(media_id)
                if _pdf_bytes is not None:
                    _note_replies = handle_notes_pdf(sender, _pdf_bytes, orig_filename or "notes.pdf")
                else:
                    _note_replies = [{"messaging_product": "whatsapp", "to": sender,
                                      "type": "text", "text": {"body": "Couldn't download that PDF. Please try again."}}]
                for _nr in _note_replies:
                    _wa_send(sender, _nr)
                return None
        except Exception as _exc:
            logger.error("Notes PDF handling failed for %s: %s", sender, _exc)

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
        # Notes marketplace: credit uploader when a marketplace note is printed.
        # Filename format set at print-job creation: "[NOTE-XXXX] title.pdf"
        try:
            _maybe_credit_note_uploader(job)
        except Exception as _nc_exc:
            logger.error("notes credit error for job %s: %s", ref_id, _nc_exc)
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
    h.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Hub-Signature-256, X-Razorpay-Signature, X-Staff-Pin, X-Student-Phone, X-Admin-Password, X-Whatsapp-Phone")
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
    _handle_admin_dispatch_sheet,
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
    _handle_admin_notes_queue,
    _handle_admin_notes_moderate,
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


# ── Store-PC liveness watcher ─────────────────────────────────────────────────
# The store PC writes daily_summary.synced_at (naive IST wall-clock text) every
# ~5 min. This cron treats that as a heartbeat: if it goes stale the PC is down.
# PRIOFF is the dev/test box; OSP is the real store PC, so we monitor OSP.
STORE_PC_MONITOR_ID  = os.environ.get("STORE_PC_MONITOR_ID", "OSP")
STORE_PC_OFFLINE_MIN = int(os.environ.get("STORE_PC_OFFLINE_MIN", "20"))


def _sd_week_start(d):
    """Monday of d's week as 'YYYY-MM-DD'."""
    from datetime import timedelta
    return (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")


def _sd_summary_row(c, store_id, date_str):
    r = (c.table("daily_summary").select("*")
          .eq("store_id", store_id).eq("date", date_str).limit(1).execute())
    if r.data:
        return r.data[0]
    return {"date": date_str, "total_jobs": 0, "completed": 0,
            "pending": 0, "revenue": 0, "cash": 0, "upi": 0}


def _sd_summary_range(c, store_id, start_str, end_str):
    r = (c.table("daily_summary").select("*")
          .eq("store_id", store_id)
          .gte("date", start_str).lte("date", end_str).execute())
    return r.data or []


def _handle_cron_store_pc_check(h) -> None:
    """GET /cron/store-pc-check — store-PC liveness watcher (heartbeat absence).

    Reads the monitored store's heartbeat (latest daily_summary.synced_at),
    compares to now (IST), and on an up<->down transition messages the owner:
      - down -> up : opening message
      - up -> down : closing message + day log (+ weekly on Sat, + monthly on the
                     last working day of the month)
    Catches every shutdown type (clean, crash, power cut) — not just clean ones.

    Auth: optional `Authorization: Bearer ${CRON_SECRET}` when CRON_SECRET is set.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and h.headers.get("Authorization", "") != f"Bearer {expected}":
        _json_response(h, 401, {"error": "Unauthorized"})
        return
    try:
        from datetime import timedelta, timezone
        import store_digest as sd
        from db_cloud import _client
        from whatsapp_notify import _send

        store_id = STORE_PC_MONITOR_ID
        now_ist = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5, minutes=30)
        now_iso = datetime.now(timezone.utc).isoformat()
        c = _client()

        # Heartbeat = latest synced_at for this store (ISO text -> lexicographic max).
        hb = c.table("daily_summary").select("synced_at").eq("store_id", store_id).execute()
        vals = [d.get("synced_at") for d in (hb.data or []) if d.get("synced_at")]
        last_hb_str = max(vals) if vals else None

        def _parse(ts):
            try:
                return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
            except (ValueError, TypeError):
                return None

        last_hb = _parse(last_hb_str)
        age_min = ((now_ist - last_hb).total_seconds() / 60.0) if last_hb else None
        online = age_min is not None and age_min < STORE_PC_OFFLINE_MIN

        st = c.table("store_pc_status").select("*").eq("store_id", store_id).limit(1).execute()
        row = st.data[0] if st.data else {}
        prev_state = row.get("state", "unknown")
        clean = bool(row.get("clean_shutdown"))

        close_d = last_hb.date() if last_hb else now_ist.date()
        decision = sd.decide_transition(
            online=online, prev_state=prev_state,
            today_str=now_ist.strftime("%Y-%m-%d"),
            close_date_str=close_d.strftime("%Y-%m-%d"),
            opening_sent_date=row.get("opening_sent_date"),
            closing_sent_date=row.get("closing_sent_date"),
        )

        sent = []
        if decision["send_opening"]:
            if _send(OWNER_ALERT_PHONE, sd.compose_opening_message(now_ist.date())):
                sent.append("opening")
        if decision["send_closing"]:
            close_str = close_d.strftime("%Y-%m-%d")
            daily = _sd_summary_row(c, store_id, close_str)
            weekly = (_sd_summary_range(c, store_id, _sd_week_start(close_d), close_str)
                      if sd.is_last_working_day_of_week(close_d) else None)
            monthly = (_sd_summary_range(c, store_id, close_str[:7] + "-01", close_str)
                       if sd.is_last_working_day_of_month(close_d) else None)
            msg = sd.compose_closing_message(close_d, daily, weekly, monthly, clean=clean)
            if _send(OWNER_ALERT_PHONE, msg):
                sent.append("closing")

        update = {
            "store_id": store_id,
            "state": decision["new_state"],
            "last_heartbeat_at": last_hb_str,
            "opening_sent_date": decision["opening_sent_date"],
            "closing_sent_date": decision["closing_sent_date"],
            "updated_at": now_iso,
        }
        if online:
            update["last_up_at"] = now_iso
            update["clean_shutdown"] = False
        else:
            update["last_down_at"] = now_iso
        c.table("store_pc_status").upsert(update).execute()

        _json_response(h, 200, {
            "store_id": store_id, "online": online,
            "age_min": round(age_min, 1) if age_min is not None else None,
            "state": decision["new_state"], "sent": sent,
        })
    except Exception as exc:
        logger.error("store-pc-check cron error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_cron_pc_shutdown(h) -> None:
    """GET /cron/pc-shutdown — best-effort clean-shutdown ping from the store PC.

    The store PC curls this as it powers down so the next store-pc-check can word
    the closing message as a clean shutdown rather than an unexpected offline.
    Auth: optional Bearer CRON_SECRET.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and h.headers.get("Authorization", "") != f"Bearer {expected}":
        _json_response(h, 401, {"error": "Unauthorized"})
        return
    try:
        from datetime import timezone
        from db_cloud import _client
        store_id = STORE_PC_MONITOR_ID
        _client().table("store_pc_status").upsert({
            "store_id": store_id,
            "clean_shutdown": True,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        _json_response(h, 200, {"store_id": store_id, "clean_shutdown": True})
    except Exception as exc:
        logger.error("pc-shutdown cron error: %s", exc)
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


def _handle_cron_daily_activity(h) -> None:
    """GET /cron/daily-activity — once-daily 'is the pipeline alive?' check.

    Counts inbound WhatsApp messages (and print jobs) in the last 24h. Zero
    inbound messages on a normal day almost always means the Meta webhook has
    silently stopped delivering — a failure the store-PC heartbeat and SLA
    crons can't see. Alerts the owner on WhatsApp when inbound is zero.

    Intended schedule: once per evening (e.g. 21:00 IST) via GitHub Actions.
    Auth: optional `Authorization: Bearer ${CRON_SECRET}` when CRON_SECRET set.
    """
    expected = os.environ.get("CRON_SECRET", "")
    if expected and h.headers.get("Authorization", "") != f"Bearer {expected}":
        _json_response(h, 401, {"error": "Unauthorized"})
        return
    try:
        from db_cloud import activity_counts
        from whatsapp_notify import _send

        counts = activity_counts(hours=24)
        inbound, jobs = counts["inbound"], counts["jobs"]

        # Only alert on a confident zero (query succeeded and returned 0).
        dead = inbound == 0
        alerted = False
        if dead:
            msg = (
                "🚨 *Printosky activity alert*\n\n"
                "*Zero* inbound WhatsApp messages in the last 24 hours.\n"
                "The Meta webhook may have stopped delivering — customers' "
                "messages might not be reaching the bot.\n\n"
                "Check: printosky.com/admin → Conversations, and the Meta "
                "webhook status in Business Manager."
            )
            alerted = _send(OWNER_ALERT_PHONE, msg)
            if not alerted:
                logger.warning("daily-activity alert send failed (Meta 24h window?)")

        _json_response(h, 200, {
            "ok": True,
            "inbound_24h": inbound,
            "jobs_24h": jobs,
            "dead": dead,
            "alerted": alerted,
        })
    except Exception as exc:
        logger.error("daily-activity cron error: %s", exc)
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
from api.handlers_order import (
    _handle_order_upload_sign,
    _handle_order_quote,
    _handle_order_create,
    _handle_order_convert_docx,
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

        # Printable dispatch sheet: pick list + packing slips for confirmed orders.
        if self.path == "/admin/book-orders/dispatch-sheet" or self.path.startswith("/admin/book-orders/dispatch-sheet?"):
            _handle_admin_dispatch_sheet(self)
            return

        # ── SLA watchdog (GitHub Actions cron, every 30 min) ──────────────────
        # ── Notes marketplace: moderation queue ───────────────────────────────
        if self.path == "/admin/notes-queue" or self.path.startswith("/admin/notes-queue?"):
            _handle_admin_notes_queue(self)
            return

        if self.path == "/cron/sla-check" or self.path.startswith("/cron/sla-check?"):
            _handle_cron_sla_check(self)
            return

        # ── Abandoned book-cart reminders (GitHub Actions cron) ───────────────
        if self.path == "/cron/abandoned-carts" or self.path.startswith("/cron/abandoned-carts?"):
            _handle_cron_abandoned_carts(self)
            return

        # ── Store-PC liveness watcher (GitHub Actions cron) ───────────────────
        if self.path == "/cron/store-pc-check" or self.path.startswith("/cron/store-pc-check?"):
            _handle_cron_store_pc_check(self)
            return

        # ── Store-PC clean-shutdown ping (curled by the store PC on shutdown) ──
        if self.path == "/cron/pc-shutdown" or self.path.startswith("/cron/pc-shutdown?"):
            _handle_cron_pc_shutdown(self)
            return

        # ── Daily activity liveness (GitHub Actions cron, once/day) ───────────
        if self.path == "/cron/daily-activity" or self.path.startswith("/cron/daily-activity?"):
            _handle_cron_daily_activity(self)
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

        if self.path.startswith("/api/health") or self.path.startswith("/health"):
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
        # ── Notes marketplace: approve / reject ────────────────────────────────
        _note_moderate = re.match(
            r"^/admin/notes/([A-Za-z0-9\-]+)/(approve|reject)$", self.path,
        )
        if _note_moderate:
            _handle_admin_notes_moderate(self, body, _note_moderate.group(1), _note_moderate.group(2))
            return

        # ── Notes: WhatsApp OTP auth ────────────────────────────────────────────
        if self.path == "/auth/wa-otp/request":
            _handle_wa_otp_request(self, body)
            return
        if self.path == "/auth/wa-otp/verify":
            _handle_wa_otp_verify(self, body)
            return

        # ── Account: personal space summary + phone linking ─────────────────────
        if self.path == "/account/summary":
            _handle_account_summary(self, body)
            return
        if self.path == "/account/referral":
            _handle_account_referral(self, body)
            return
        if self.path == "/account/link-phone/verify":
            _handle_account_link_phone(self, body)
            return

        # ── Notes: reserve upload slot + finalize ───────────────────────────
        if self.path == "/notes/reserve":
            _handle_notes_reserve(self, body)
            return
        if self.path == "/notes/submit":
            _handle_notes_submit(self, body)
            return

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

        if self.path == "/order/upload-sign":
            _handle_order_upload_sign(self, body)
            return
        if self.path == "/order/quote":
            _handle_order_quote(self, body)
            return
        if self.path == "/order/create":
            _handle_order_create(self, body)
            return
        if self.path == "/order/convert-docx":
            _handle_order_convert_docx(self, body)
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
