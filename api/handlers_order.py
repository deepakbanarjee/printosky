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
from urllib.parse import parse_qs, urlparse

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


def _handle_order_scale_rect(h, path: str) -> None:
    """GET /order/scale-rect?page_w=&page_h=&sheet=A4&mode=fit&percent=

    Hands the customer's browser the printer's own geometry, so the preview on
    order-v2 draws the page exactly where it will land instead of guessing at
    it. Pure numbers — no file is uploaded, nothing is stored, and the same
    pdf_scaler.scale_rect() the print path bakes with produces the answer.

    A preview drawn by different code than the printer gets is a preview that
    can lie, so there is no second implementation of this in JavaScript.

    Responds 200 with {"scale": null} when the settings are a no-op (an absent
    mode, or Actual on a page already the sheet size) — the caller then knows to
    draw the page unchanged, which is what would print.
    """
    qs = parse_qs(urlparse(path).query)

    def _num(key):
        try:
            return float(qs.get(key, [""])[0])
        except (TypeError, ValueError):
            return None

    page_w, page_h = _num("page_w"), _num("page_h")
    if not page_w or not page_h or page_w <= 0 or page_h <= 0:
        _json_response(h, 400, {"error": "page_w and page_h required, in points"})
        return

    sheet = (qs.get("sheet", ["A4"])[0] or "A4").strip()
    if sheet not in _VALID_SIZE:
        _json_response(h, 400, {"error": f"unknown sheet {sheet!r}"})
        return

    mode = (qs.get("mode", [""])[0] or "").strip().lower()
    percent = qs.get("percent", [None])[0]

    try:
        import pdf_scaler
        rect = pdf_scaler.scale_rect(page_w, page_h, sheet, mode, percent)
    except Exception as exc:
        logger.error("scale-rect failed %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "could not compute scale"})
        return

    _json_response(h, 200, {"scale": rect})


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


# ── Post-press services, booked from order-v2 staff mode ─────────────────────
#
# Owner decision, 2026-09-02: staff book services through the Vercel API, not
# the store PC's print_server, so they can work off-site. That makes this the
# SECOND caller of the service logic, and the reason every decision it makes
# lives in `service_jobs` rather than here — a price or a deposit that depends
# on which machine the counter used is a split that takes months to notice.
#
# What a service job is (plan §4.2 B1, unchanged from the store-PC path): an
# ordinary `jobs` row with `service_kind` set. Not a new table, so revenue,
# payment, pickup codes, WhatsApp notify, the daily summary and MIS all keep
# working without being taught anything.
#
# Two isolation properties this path must preserve, and does structurally:
#   * NO file_url — store_puller pulls only rows with a non-empty file_url, so
#     a service job can never be downloaded or auto-printed;
#   * NO printed_by — which is what keeps services out of the MIS printer and
#     staff panels (tests/test_service_ui.py).

def _service_job_id() -> str:
    """A counter-issued job id, cloud-side.

    The store PC numbers today's jobs by counting its own rows; the cloud cannot
    see a store's local sequence, so it uses the same id shape with a random
    suffix — exactly what /order/staff-create already does for walk-in prints.
    """
    return f"OSKY-{datetime.now().strftime('%Y%m%d')}-{_uuid.uuid4().hex[:4]}"


def _handle_order_service_quote(h, path: str) -> None:
    """GET /order/service-quote?kind=laminate&sheets=6&lam_type=pouch

    Price one post-press service. Writes nothing. Never raises: a quote the shop
    cannot compute comes back as needs_manual_price with a reason, so the
    counter types a price instead of watching a spinner.
    """
    import rate_card
    import service_jobs

    qs = parse_qs(urlparse(path).query)
    kind = (qs.get("kind", [""])[0] or "").strip().lower()
    if kind not in rate_card.SERVICE_KINDS:
        _json_response(h, 400, {"error": f"Unknown service {kind!r}",
                                "kinds": sorted(rate_card.SERVICE_KINDS)})
        return
    try:
        meta = service_jobs.meta_from_params(qs)
    except ValueError as exc:
        # A non-numeric quantity is a UI bug. Quoting it at the default would
        # bill the wrong number quietly, so it is refused out loud.
        _json_response(h, 400, {"error": str(exc)})
        return
    try:
        quote = rate_card.calculate_service_quote(kind, meta)
    except Exception as exc:
        logger.error("service-quote(%s) failed for %r: %r", kind, meta, str(exc))
        _json_response(h, 200, {
            "ok": False, "needs_manual_price": True, "total": 0,
            "breakdown": [f"could not price this: {exc}"],
            "label": kind, "kind": kind, "meta": meta,
        })
        return

    _json_response(h, 200, {
        "ok": True,
        "kind": kind,
        "meta": meta,
        "total": quote["total"],
        "label": quote["label"],
        "breakdown": quote["breakdown"],
        "needs_manual_price": quote["needs_manual_price"],
        "deposit_due": service_jobs.deposit_for(quote["total"]),
    })


def _handle_order_staff_service(h, body: bytes) -> None:
    """POST /order/staff-service — book a post-press service from staff mode.

    Body: {kind, meta{}, store_id, customer_name?, phone?, notes?,
           amount_quoted?, amount_collected?, amount_partial?, payment_mode?,
           override_reason?}

    Mirrors print_server.handle_new_service; every decision comes from
    `service_jobs`, so the two agree by construction.
    """
    from api.index import _acad_auth_staff  # lazy — avoid load-time circular import
    import rate_card
    import service_jobs

    if not _acad_auth_staff(h):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    kind = str(data.get("kind") or data.get("service_kind") or "").strip().lower()
    if kind not in rate_card.SERVICE_KINDS:
        _json_response(h, 400, {"error": f"Unknown service {kind!r}",
                                "kinds": sorted(rate_card.SERVICE_KINDS)})
        return

    meta = data.get("meta") or {}
    if not isinstance(meta, dict):
        _json_response(h, 400, {"error": "meta must be an object"})
        return

    try:
        quote = rate_card.calculate_service_quote(kind, meta)
    except Exception as exc:
        logger.error("staff-service(%s) could not price %r: %r", kind, meta, str(exc))
        _json_response(h, 400, {"error": f"Could not price this service: {exc}"})
        return

    amount_quoted = service_jobs.amount_or_none(data.get("amount_quoted"))
    if amount_quoted is None:
        amount_quoted = float(quote["total"])
    amount_collected = service_jobs.amount_or_none(data.get("amount_collected")) or 0.0
    amount_partial   = service_jobs.amount_or_none(data.get("amount_partial")) or 0.0
    override_reason  = str(data.get("override_reason") or "").strip()

    status = service_jobs.service_status(
        amount_quoted, amount_collected + amount_partial, override_reason)
    mode = service_jobs.payment_mode(data.get("payment_mode"))

    store_id = str(data.get("store_id") or "").strip().upper()
    if store_id not in _STORE_LABEL:
        store_id = _DEFAULT_STORE_ID

    # The store PC's SQLite has `amount_partial`, `override_reason` and
    # `queued_at`; the cloud `jobs` table has none of the three, and PostgREST
    # rejects the WHOLE insert on an unknown column (the same trap documented on
    # _persist_settings). Nothing is lost by mapping rather than migrating:
    #   * money taken is money taken — a deposit IS amount_collected below
    #     amount_quoted, which the schema already says;
    #   * a waiver's whole purpose is being readable later, and `notes` is what
    #     the operator actually reads;
    #   * `status` already says Queued, and `received_at` is the same instant.
    # tests/test_service_parity.py pins every column here against the manifest.
    taken = amount_collected + amount_partial
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    job_id = _service_job_id()
    label = quote["label"]

    note = str(data.get("notes", "")).strip()
    if override_reason:
        note = (note + " | deposit waived: " + override_reason).strip(" |")
    if amount_partial > 0 and amount_collected > 0:
        note = (note + f" | part payment Rs.{amount_partial:.0f}").strip(" |")

    row = {
        "job_id":            job_id,
        "received_at":       now,
        "filename":          label,
        "source":            str(data.get("source") or "Service"),
        "sender":            _norm_phone(data.get("phone") or "") or None,
        "customer_name":     str(data.get("customer_name", "")).strip() or None,
        "service_type":      label,
        "status":            status,
        "amount_quoted":     amount_quoted,
        "amount_collected":  taken if taken > 0 else None,
        "payment_mode":      mode if taken > 0 else None,
        "notes":             note or None,
        "assigned_store_id": store_id,
        "service_kind":      kind,
        "service_meta":      meta,
        # No file_url and no printed_by, deliberately — see the note above.
    }

    try:
        from db_cloud import _client
        _client().table("jobs").insert(row).execute()
    except Exception as exc:
        logger.error("staff-service db error %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "could not create the service job"})
        return

    _json_response(h, 200, {
        "ok": True, "job_id": job_id, "kind": kind, "label": label,
        "status": status, "amount_quoted": amount_quoted,
        "amount_collected": taken,
        "deposit_due": service_jobs.deposit_for(amount_quoted),
        "breakdown": quote["breakdown"],
        "needs_manual_price": quote["needs_manual_price"],
    })


def _handle_order_staff_photocopy(h, body: bytes) -> None:
    """POST /order/staff-photocopy — file a counter photocopy, already done.

    Mirrors print_server.handle_new_photocopy, including the two things that
    look like omissions and are not:

    * **No `service_kind`.** A photocopy is work the Konica actually did, so it
      stays inside the printer counts — which is what makes the B-10 copy/scan
      reconciliation possible at all. Giving it a service_kind would remove it
      from the very comparison that catches unbilled copying.
    * **Refuses rather than filing ₹0.** A photocopy the shop cannot price is a
      job someone has to price; a free one is a sale that silently vanished.
    """
    from api.index import _acad_auth_staff  # lazy — avoid load-time circular import
    import rate_card
    import service_jobs

    if not _acad_auth_staff(h):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "invalid JSON"})
        return

    meta = service_jobs.photocopy_meta(data)
    try:
        quote = rate_card.calculate_service_quote("copy", meta)
        quoted = None if quote["needs_manual_price"] else float(quote["total"])
        breakdown = quote["breakdown"]
    except Exception as exc:
        logger.error("staff-photocopy could not price %r: %r", meta, str(exc))
        quoted, breakdown = None, [f"could not price this: {exc}"]

    typed = service_jobs.amount_or_none(data.get("amount_collected"))
    money = service_jobs.resolve_amount(quoted, typed)
    if not money["billable"]:
        _json_response(h, 200, {
            "ok": False, "needs_manual_price": True,
            "error": "Could not price this photocopy — enter the amount. "
                     + "; ".join(breakdown),
            "breakdown": breakdown,
        })
        return

    mode = service_jobs.payment_mode(data.get("payment_mode"))
    staff_id = str(data.get("staff_id", "")).strip()
    store_id = str(data.get("store_id") or "").strip().upper()
    if store_id not in _STORE_LABEL:
        store_id = _DEFAULT_STORE_ID

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    job_id = _service_job_id()

    note = f"Photocopy job created at {now} by {staff_id or 'staff'}"
    if breakdown:
        note += " | " + " | ".join(breakdown)
    if money["overridden"]:
        note += f" | staff set Rs.{money['amount']:.0f} over the quoted Rs.{quoted:.0f}"

    row = {
        "job_id":           job_id,
        "received_at":      now,
        "filename":         "Photocopy Job",
        "source":           "Photocopy",
        "sender":           _norm_phone(data.get("phone") or "") or None,
        "customer_name":    str(data.get("customer_name", "")).strip() or None,
        "service_type":     "Photocopy",
        "page_count":       meta["sheets"],
        "colour":           meta["colour"],
        "copies":           meta["copies"],
        "status":           "Completed",
        "amount_collected": money["amount"],
        "amount_quoted":    money["quoted"],
        "payment_mode":     mode,
        "completed_at":     now,
        "printed_by":       staff_id or None,
        "notes":            note,
        "assigned_store_id": store_id,
    }

    try:
        from db_cloud import _client
        _client().table("jobs").insert(row).execute()
    except Exception as exc:
        logger.error("staff-photocopy db error %s: %r", type(exc).__name__, str(exc))
        _json_response(h, 500, {"error": "could not file the photocopy"})
        return

    _json_response(h, 200, {
        "ok": True, "job_id": job_id, "amount": money["amount"],
        "amount_quoted": money["quoted"], "breakdown": breakdown,
        "overridden": money["overridden"],
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
