"""Online print-order HTTP handlers (website/order-v2.html backend).

Backs the /order/* endpoints used by the Acrobat-style order page:
  POST /order/upload-sign   -> mint a Supabase signed PUT URL under orders/
  POST /order/quote         -> authoritative price via rate_card.calculate_quote
  POST /order/create        -> create a Pending web job + persist settings + WhatsApp
  POST /order/convert-docx  -> v1 stub (PDF-first; non-PDF handled by operator)

Each handler takes the BaseHTTPRequestHandler instance `h` plus the raw request
body; the router in api/index.py dispatches to these entry points. Mirrors the
structure of api/handlers_pb.py.

rate_card token contract (verified against rate_card.py):
  - B&W paper_type  -> "<SIZE>_BW"  (e.g. "A4_BW", "A3_BW", "Legal_BW")
  - colour paper_type -> "<SIZE>_col" (LOWERCASE "col" — "A4_col" is the only
    tiered key; "A3_col"/"Legal_col" are flat). Using "<SIZE>_COL" would fall
    through to the flat-rate lookup and silently bill at the B&W rate, so the
    lowercase token is load-bearing.
"""
import json
import logging
import re as _re
import uuid as _uuid
from datetime import datetime

logger = logging.getLogger("api.webhook")

try:
    from api.index import _json_response  # CORS-aware
except Exception:  # during isolated unit tests this is monkeypatched
    def _json_response(h, status, data):  # pragma: no cover
        raise RuntimeError("_json_response not bound")


def _get_client_bucket():
    from db_cloud import _client, INCOMING_BUCKET
    return _client(), INCOMING_BUCKET


# ── /order/upload-sign ───────────────────────────────────────────────────────

def _handle_order_upload_sign(h, body: bytes) -> None:
    """POST /order/upload-sign — issue a Supabase signed PUT URL under orders/."""
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return
    raw = str(data.get("filename") or "").strip()
    safe = _re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:120] or "upload.bin"
    storage_path = f"orders/{_uuid.uuid4()}/{safe}"
    try:
        client, bucket = _get_client_bucket()
        result = client.storage.from_(bucket).create_signed_upload_url(storage_path)
    except Exception as exc:
        logger.error("order upload-sign error %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "signed-url mint failed"})
        return
    _json_response(h, 200, {
        "signed_url": result.get("signed_url") or result.get("signedUrl"),
        "storage_path": storage_path,
        "expires_in": 7200,
    })


# ── /order/quote ─────────────────────────────────────────────────────────────

_VALID_FINISHING = {"none", "staple", "spiral", "wiro"}


def _handle_order_quote(h, body: bytes) -> None:
    """POST /order/quote — authoritative price via rate_card.calculate_quote."""
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return
    items = data.get("print_items") or []
    if not isinstance(items, list) or not items:
        _json_response(h, 400, {"error": "print_items required"})
        return
    finishing = data.get("finishing", "none")
    if finishing not in _VALID_FINISHING:
        finishing = "none"
    paper_size = data.get("paper_size", "A4")
    try:
        import rate_card
        result = rate_card.calculate_quote(items, finishing=finishing, paper_size=paper_size)
    except Exception as exc:
        logger.error("order quote error %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "quote failed"})
        return
    _json_response(h, 200, {
        "total": result["total"],
        "total_sheets": result["total_sheets"],
        "breakdown": result["breakdown"],
    })


# ── /order/create ────────────────────────────────────────────────────────────

def _insert_job(job_id, sender, filename, file_url):
    from db_cloud import insert_job_from_webhook
    insert_job_from_webhook(job_id, sender, filename, file_url)


def _apply_settings(job_id, amount_quoted, copies, finishing, size, colour, layout):
    from db_cloud import update_job_settings
    update_job_settings(job_id, amount_quoted, copies, finishing, size, colour, layout)


def _update_extras(job_id, operator_note, delivery):
    """Persist web-order extras (operator note, delivery flag, source) onto the job row.

    Best-effort: the columns may not all exist on every deployment, so a failure
    here must not fail the order — the job row + settings are already committed.
    """
    from db_cloud import _client
    _client().table("jobs").update({
        "instructions": operator_note,
        "delivery": int(delivery),
        "source": "web",
    }).eq("job_id", job_id).execute()


def _send_confirmation(sender, job_id, total, operator_note):
    """Best-effort WhatsApp order-received confirmation (Meta 24h window)."""
    try:
        from whatsapp_notify import _send
        _send(sender, f"📋 Order *{job_id}* received!\n"
                      f"Est. ₹{total:.0f}. We'll confirm on WhatsApp shortly. 🙏")
    except Exception as exc:
        logger.error("order confirm send failed for %s: %r", job_id, str(exc))


def _quote_total(items, finishing, size):
    import rate_card
    return rate_card.calculate_quote(items, finishing=finishing, paper_size=size)["total"]


_PHONE_RE = _re.compile(r"^91\d{10}$")
_VALID_SIZE = {"A4", "A3", "A5", "Letter"}
_VALID_COLOUR = {"bw", "col", "mixed"}


def _handle_order_create(h, body: bytes) -> None:
    """POST /order/create — create a Pending web job + persist settings + WhatsApp."""
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return
    cust = data.get("customer") or {}
    phone = _re.sub(r"\D", "", str(cust.get("whatsapp", "")))
    if phone.startswith("0"):
        phone = "91" + phone[1:]
    if len(phone) == 10:
        phone = "91" + phone
    if not _PHONE_RE.match(phone):
        _json_response(h, 400, {"error": "invalid WhatsApp number"})
        return
    spec = data.get("print_spec") or {}
    size = spec.get("paper_size", "A4")
    colour = spec.get("colour_mode", "bw")
    if size not in _VALID_SIZE or colour not in _VALID_COLOUR:
        _json_response(h, 400, {"error": "invalid print spec"})
        return
    file_url = str(data.get("file_url") or "")
    file_name = str(data.get("file_name") or "order")
    if not file_url:
        _json_response(h, 400, {"error": "file_url required"})
        return

    inc = len(spec.get("pages_included") or [])
    col_n = len(spec.get("colour_pages") or []) if colour == "mixed" else (inc if colour == "col" else 0)
    bw_n = inc - col_n
    sides = "ds" if spec.get("sides") == "duplex" else "ss"
    layout_map = {1: "1-up", 2: "2-up", 4: "4-up", 6: "4-up", 9: "4-up"}
    layout = layout_map.get(int(spec.get("nup", 1)), "1-up")
    copies = int(spec.get("copies", 1))
    items = []
    if bw_n > 0 or col_n == 0:
        items.append({"pages": bw_n, "paper_type": f"{size}_BW", "sides": sides, "layout": layout, "copies": copies})
    if col_n > 0:
        items.append({"pages": col_n, "paper_type": f"{size}_col", "sides": sides, "layout": layout, "copies": copies})
    finishing = spec.get("binding", "none")
    if finishing not in _VALID_FINISHING:
        finishing = "none"

    try:
        total = _quote_total(items, finishing, size)
    except Exception:
        total = 0.0

    job_id = f"OSP-{datetime.now().strftime('%Y%m%d')}-{phone[-4:]}-{_uuid.uuid4().hex[:4]}"
    try:
        _insert_job(job_id=job_id, sender=phone, filename=file_name, file_url=file_url)
        _apply_settings(
            job_id=job_id, amount_quoted=total, copies=copies, finishing=finishing,
            size=size, colour=colour, layout=f"{spec.get('nup', 1)}up-{sides}",
        )
    except Exception as exc:
        logger.error("order create db error %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "could not create order"})
        return

    try:
        _update_extras(job_id, data.get("operator_note", ""), cust.get("delivery", 0))
    except Exception as exc:
        logger.warning("order note/delivery update skipped for %s: %r", job_id, str(exc))

    _send_confirmation(phone, job_id, total, data.get("operator_note", ""))
    _json_response(h, 200, {"job_id": job_id, "total": total})


# ── /order/convert-docx ──────────────────────────────────────────────────────

def _handle_order_convert_docx(h, body: bytes) -> None:
    """POST /order/convert-docx — v1 stub. PDF-first; non-PDF handled by operator."""
    _json_response(h, 501, {"error": "DOCX conversion not enabled — please upload a PDF."})
