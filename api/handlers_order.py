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

_VALID_FINISHING = {"none", "staple", "spiral", "wiro",
                    "soft", "perfect", "project", "record", "thesis"}


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


def _persist_settings(job_id, *, amount_quoted, copies, finishing, size, colour,
                      page_count, operator_note, customer_name, orientation="auto",
                      assigned_store_id="OSP", print_spec=None):
    """Persist print settings + web-order metadata onto the job row.

    Writes ONLY columns that exist on the live `jobs` schema. Notably there is
    NO `layout`, `instructions`, or `delivery` column — N-up/sides/delivery info
    is folded into the human-readable `notes` field, which is what the operator
    reads. (Do NOT route this through db_cloud.update_job_settings: that writes a
    non-existent `layout` column, which makes the whole PostgREST update fail.)

    `orientation` ('auto' | 'portrait' | 'landscape') has a dedicated column
    (added 2026-07-14); it also still appears in `notes` for at-a-glance reading.
    """
    from db_cloud import _client
    _client().table("jobs").update({
        "copies":        copies,
        "finishing":     finishing,
        "size":          size,
        "colour":        colour,          # 'bw' | 'col' | 'mixed'
        "orientation":   orientation,     # 'auto' | 'portrait' | 'landscape'
        "amount_quoted": amount_quoted,
        "page_count":    page_count,
        "notes":         operator_note,   # carries colour/skipped pages + delivery
        "source":        "web",
        "customer_name": customer_name,
        "assigned_store_id": assigned_store_id,  # which store fulfils (manual picker)
        "print_spec":    print_spec,
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


def _norm_phone(raw) -> str:
    """Normalise a phone to canonical 91XXXXXXXXXX, or '' if not a valid IN mobile."""
    p = _re.sub(r"\D", "", str(raw or ""))
    if p.startswith("0"):
        p = p[1:]
    if len(p) == 10:
        p = "91" + p
    return p if _PHONE_RE.match(p) else ""


def _resolve_request_account(h) -> dict:
    """Resolve the request's Authorization header to an account dict, or {}.

    Thin seam over api.index._resolve_account (lazy import dodges the circular
    import; failures degrade to a guest order). Returns the account dict
    ({ok, kind, phone, name, ...}) or {} when unauthenticated/unavailable.
    """
    try:
        from api.index import _resolve_account
        return _resolve_account(h) or {}
    except Exception as _e:
        logger.debug("order account resolve skipped: %r", str(_e))
        return {}
_VALID_SIZE = {"A4", "A3", "A5", "Legal", "Letter"}
_VALID_COLOUR = {"bw", "col", "mixed"}
_VALID_ORIENTATION = {"auto", "portrait", "landscape"}

# Customer pickup-location choice -> fulfilling store_id. This is the manual
# routing path: the order page asks which of our locations should handle the
# job, and we stamp assigned_store_id directly (no distance/capacity engine).
# The store PC's store_puller polls Supabase for jobs assigned to its store_id.
# Unknown/absent -> Oxygen (OSP), the default single-store behaviour.
_PICKUP_STORE_MAP = {"thriprayar": "OSP", "nattika": "PRINTK"}
_STORE_LABEL = {"OSP": "Thriprayar", "PRINTK": "Nattika"}
_DEFAULT_STORE_ID = "OSP"


def _resolve_store_id(pickup_store) -> str:
    """Map a pickup-location choice to a fulfilling store_id (default OSP)."""
    return _PICKUP_STORE_MAP.get(
        str(pickup_store or "").strip().lower(), _DEFAULT_STORE_ID
    )


def _handle_order_create(h, body: bytes) -> None:
    """POST /order/create — create a Pending web job + persist settings + WhatsApp."""
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return
    cust = data.get("customer") or {}

    # Registered account? Resolve the Authorization: Bearer token to a verified
    # phone (+ name) server-side. When present this is the trusted identity —
    # the order page hides the WhatsApp field for logged-in users. Falls back to
    # the typed details for guests (or email/Google logins without a linked phone).
    account_name = ""
    phone = ""
    _acct = _resolve_request_account(h)
    if _acct.get("ok"):
        phone = _norm_phone(_acct.get("phone"))
        account_name = (_acct.get("name") or "").strip()
    if not phone:
        phone = _norm_phone(cust.get("whatsapp"))
    if not phone:
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
    orientation = spec.get("orientation", "auto")
    if orientation not in _VALID_ORIENTATION:
        orientation = "auto"

    try:
        total = _quote_total(items, finishing, size)
    except Exception:
        total = 0.0

    # Which store fulfils this job (manual location picker on the order page).
    assigned_store_id = _resolve_store_id(cust.get("pickup_store"))

    # Fold delivery/pickup + fulfilling store (no dedicated note column) into
    # the operator-facing note.
    note = str(data.get("operator_note", "")).strip()
    if int(cust.get("delivery", 0)):
        addr = str(cust.get("address", "")).strip()
        note = (note + " · DELIVERY" + (f": {addr}" if addr else "")).strip(" ·")
    else:
        note = (note + " · PICKUP").strip(" ·")
    note = (note + " · " + _STORE_LABEL.get(assigned_store_id, assigned_store_id)).strip(" ·")
    page_count = len(spec.get("pages_included") or []) or int(spec.get("total_pages", 0))

    job_id = f"OSKY-{datetime.now().strftime('%Y%m%d')}-{phone[-4:]}-{_uuid.uuid4().hex[:4]}"
    try:
        _insert_job(job_id=job_id, sender=phone, filename=file_name, file_url=file_url)
        _persist_settings(
            job_id=job_id, amount_quoted=total, copies=copies, finishing=finishing,
            size=size, colour=colour, page_count=page_count,
            operator_note=note, orientation=orientation,
            customer_name=account_name or str(cust.get("name", "")).strip(),
            assigned_store_id=assigned_store_id,
            print_spec=spec,
        )
    except Exception as exc:
        logger.error("order create db error %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "could not create order"})
        return

    _send_confirmation(phone, job_id, total, note)
    _json_response(h, 200, {"job_id": job_id, "total": total})


def _handle_order_staff_create(h, body: bytes) -> None:
    """POST /order/staff-create — a staff member creates a walk-in print job
    with the full order-v2 print_spec, but WITHOUT a customer phone or a
    customer WhatsApp message.

    Same rich pipeline as /order/create (upload -> print_spec -> Pending job the
    store puller auto-prints once Paid), minus the customer-facing bits:
      * auth is a staff PIN (X-Staff-Pin) or admin password, not a phone,
      * no confirmation WhatsApp is sent,
      * the fulfilling store comes from the request (the machine's store_id).
    Staff take payment + print from the jobs console (mark-paid).

    Body: {file_url, file_name, print_spec, store_id, customer_name?, phone?,
           operator_note?}.
    """
    from api.index import _acad_auth_staff  # lazy — avoid load-time circular import
    if not _acad_auth_staff(h):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    spec = data.get("print_spec") or {}
    size = spec.get("paper_size", "A4")
    colour = spec.get("colour_mode", "bw")
    if size not in _VALID_SIZE or colour not in _VALID_COLOUR:
        _json_response(h, 400, {"error": "invalid print spec"})
        return
    file_url = str(data.get("file_url") or "")
    file_name = str(data.get("file_name") or "walk-in")
    if not file_url:
        _json_response(h, 400, {"error": "file_url required"})
        return

    # Derive billable items exactly like _handle_order_create.
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
    orientation = spec.get("orientation", "auto")
    if orientation not in _VALID_ORIENTATION:
        orientation = "auto"

    try:
        total = _quote_total(items, finishing, size)
    except Exception:
        total = 0.0

    # store_id here is the machine's ACTUAL store code (OSP / PRINTK), sent by
    # jobs.html staff mode — NOT a customer pickup-location name. Use it directly.
    # (_resolve_store_id maps location names like "nattika" and defaults unknown
    # inputs to OSP, which would wrongly assign every PRINTK walk-in to OSP.)
    store_id = str(data.get("store_id") or "").strip().upper()
    if store_id not in _STORE_LABEL:
        store_id = _DEFAULT_STORE_ID
    cust_name = str(data.get("customer_name", "")).strip()
    phone = _norm_phone(data.get("phone") or "")   # optional for walk-ins
    note = str(data.get("operator_note", "")).strip()
    note = (note + " · WALK-IN · " + _STORE_LABEL.get(store_id, store_id)).strip(" ·")
    page_count = len(spec.get("pages_included") or []) or int(spec.get("total_pages", 0))

    sender = phone or "walk-in"
    suffix = phone[-4:] if phone else _uuid.uuid4().hex[:4]
    job_id = f"OSKY-{datetime.now().strftime('%Y%m%d')}-{suffix}-{_uuid.uuid4().hex[:4]}"
    try:
        _insert_job(job_id=job_id, sender=sender, filename=file_name, file_url=file_url)
        _persist_settings(
            job_id=job_id, amount_quoted=total, copies=copies, finishing=finishing,
            size=size, colour=colour, page_count=page_count,
            operator_note=note, orientation=orientation,
            customer_name=cust_name, assigned_store_id=store_id, print_spec=spec,
        )
    except Exception as exc:
        logger.error("staff-create db error %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "could not create job"})
        return

    # Payment, chosen by staff at creation:
    #   cash / upi -> record the payment and flip the job to Paid, so the store
    #                 puller pulls + auto-prints it immediately (quoted amount).
    #   hold (default) -> leave it Pending; staff take payment + print later from
    #                 the jobs console (Mark Paid).
    payment_mode = str(data.get("payment_mode") or "hold").strip().lower()
    paid = False
    if payment_mode in ("cash", "upi"):
        try:
            from db_cloud import mark_job_paid_manual
            mark_job_paid_manual(job_id, total, payment_mode)
            paid = True
        except Exception as exc:
            # Job is still created (Pending) — surface that it wasn't marked paid
            # so the operator can Mark Paid from the console instead of assuming.
            logger.error("staff-create mark-paid failed for %s: %s", job_id, exc)
    _json_response(h, 200, {
        "job_id": job_id, "total": total,
        "paid": paid, "payment_mode": payment_mode if paid else "hold",
    })


def _handle_order_reorder(h, body: bytes) -> None:
    """POST /order/reorder — clone a past job into a new Pending order.

    One-tap reorder from the account hub. The original file is already in
    storage, so this re-uses its file_url + stored print settings — no upload,
    no re-quote. Login is required and the caller must own the source order
    (jobs.sender == the resolved account phone).
    """
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    acct = _resolve_request_account(h)
    if not acct.get("ok"):
        _json_response(h, 401, {"error": "login required to reorder"})
        return
    phone = _norm_phone(acct.get("phone"))
    if not phone:
        _json_response(h, 400, {"error": "no phone linked to this account"})
        return

    src_id = str(data.get("job_id") or "").strip()
    if not src_id:
        _json_response(h, 400, {"error": "job_id required"})
        return

    from db_cloud import get_job
    src = get_job(src_id) or {}
    # Ownership: only reorder your own job.
    if not src or _norm_phone(src.get("sender")) != phone:
        _json_response(h, 404, {"error": "order not found"})
        return
    file_url = str(src.get("file_url") or "")
    if not file_url:
        _json_response(h, 400, {"error": "original file is no longer available"})
        return

    total = float(src.get("amount_quoted") or 0)
    base_note = str(src.get("notes") or "").strip()
    note = ("Reorder of " + src_id + (" · " + base_note if base_note else "")).strip()

    job_id = f"OSKY-{datetime.now().strftime('%Y%m%d')}-{phone[-4:]}-{_uuid.uuid4().hex[:4]}"
    try:
        _insert_job(job_id=job_id, sender=phone,
                    filename=str(src.get("filename") or "order"), file_url=file_url)
        _persist_settings(
            job_id=job_id, amount_quoted=total,
            copies=int(src.get("copies") or 1),
            finishing=str(src.get("finishing") or "none"),
            size=str(src.get("size") or "A4"),
            colour=str(src.get("colour") or "bw"),
            page_count=int(src.get("page_count") or 0),
            operator_note=note, orientation=str(src.get("orientation") or "auto"),
            customer_name=str(src.get("customer_name") or acct.get("name") or "").strip(),
            print_spec=src.get("print_spec"),
        )
    except Exception as exc:
        logger.error("order reorder db error %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "could not place reorder"})
        return

    _send_confirmation(phone, job_id, total, note)
    _json_response(h, 200, {"job_id": job_id, "total": total})


# ── /order/convert-docx ──────────────────────────────────────────────────────

def _handle_order_convert_docx(h, body: bytes) -> None:
    """POST /order/convert-docx — v1 stub. PDF-first; non-PDF handled by operator."""
    _json_response(h, 501, {"error": "DOCX conversion not enabled — please upload a PDF."})
