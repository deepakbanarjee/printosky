"""Academic-project order HTTP handlers — extracted from api/index.py.

Backs the /academic/orders* and /academic/razorpay-webhook endpoints (order
CRUD, generate/approve/finalize/revise/deliver lifecycle, payment webhook).
Each is a plain handler taking the BaseHTTPRequestHandler instance `h`; the
router in api/index.py imports the entry points and dispatches to them.

Second slice of the api/index.py split. Shared helpers are imported back from
api.index (single source of truth during this incremental extraction).
"""
import hashlib
import hmac
import json
import logging
import os
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger("api.webhook")

from api.index import (  # noqa: E402  (api.index mid-import; names below defined above the import site)
    _json_response,
    _acad_auth_student,
    _mark_webhook_processed,
)

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
