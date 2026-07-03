"""Admin-dashboard HTTP handlers — extracted from api/index.py.

Backs the /admin/* endpoints: conversations/threads, operator queue,
book-order ops (list/confirm/dispatch/deliver/create/edit/settle-divya),
Divya ledger, contacts, file upload/send, format-fixer, PIN reset, send.
Each is a plain handler taking the BaseHTTPRequestHandler instance `h`.

Fourth slice of the api/index.py split. Owns the admin-only helpers
_admin_pw_from_request and _parse_multipart. Shared helpers are imported
back from api.index; _send_pb_whatsapp comes from the pb module.
"""
import hmac
import json
import logging
import os
import re

logger = logging.getLogger("api.webhook")

from api.index import (  # noqa: E402
    _json_response,
    _send_cors_headers,
    _auth_admin_pw,
    _hash_pin,
    _fmt_phone,
    _sha256,
    ADMIN_PASSWORD_HASH,
)
from api.handlers_pb import _send_pb_whatsapp  # noqa: E402

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


def _clear_needs_human(phone: str) -> None:
    """Clear the 'needs human' (SOS) flag once staff have replied.

    Leaves the session's ``step`` untouched so an active ``staff_hold`` keeps the
    bot silent until staff hand the conversation back via /staff/resume. Best
    effort: a session-write failure must never break an already-sent reply.
    """
    try:
        from db_cloud import save_session
        save_session("supabase", phone, needs_human=False)
    except Exception as exc:
        logger.warning(f"_clear_needs_human({phone}) failed: {exc}")


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
            _clear_needs_human(phone)   # staff replied → drop the SOS pill
            _json_response(h, 200, {"ok": True})
            logger.info(f"Admin manually sent message to {phone}")
        else:
            _json_response(h, 502, {"error": "WhatsApp send failed"})
    except Exception as e:
        logger.error(f"admin-send error: {e}")
        _json_response(h, 500, {"error": "Server error"})


def _handle_admin_start_book_order(h, body: bytes) -> None:
    """POST /admin/book-orders/start — staff take over a customer's book order:
    resume a dropped cart at the step it stalled on, or send the opening
    Malayalam book list if there is no cart yet (never wipes existing items)."""
    try:
        payload  = json.loads(body)
        admin_pw = payload.get("admin_password", "").strip()
        phone    = payload.get("phone", "").strip()
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return

    if not ADMIN_PASSWORD_HASH:
        _json_response(h, 503, {"error": "Admin auth not configured"})
        return
    if not hmac.compare_digest(_sha256(admin_pw), ADMIN_PASSWORD_HASH):
        _json_response(h, 403, {"error": "Invalid admin password"})
        return
    if not phone:
        _json_response(h, 400, {"error": "phone required"})
        return
    if not phone.isdigit() or not (10 <= len(phone) <= 13):
        _json_response(h, 400, {"error": "valid phone required"})
        return

    try:
        import book_bot
        # Take over a dropped cart: resume the customer at the step they stalled
        # on (re-issues that prompt) instead of wiping their items/address. A
        # customer with no cart yet just gets the opening book list.
        relay = book_bot.resume_order(phone)
        from whatsapp_notify import _send
        from db_cloud import log_message
        for msg in (relay or []):
            try:
                if _send(phone, msg):
                    log_message(phone, "outbound", msg, message_type="text")
            except Exception:
                pass
        _clear_needs_human(phone)   # staff acted -> drop the SOS pill
        _json_response(h, 200, {"ok": True})
        logger.info(f"Admin started book order flow for {phone}")
    except Exception as e:
        logger.error(f"admin-start-book-order error: {e}")
        _json_response(h, 500, {"error": "Server error"})


def _handle_admin_run_cart_nudge(h, body: bytes) -> None:
    """POST /admin/run-cart-nudge — staff fire the abandoned-cart reminder sweep
    on demand. Same one-per-cart, 24h-window-guarded sweep the cron runs; returns
    {carts, reminded}."""
    try:
        payload  = json.loads(body)
        admin_pw = payload.get("admin_password", "").strip()
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return

    if not ADMIN_PASSWORD_HASH:
        _json_response(h, 503, {"error": "Admin auth not configured"})
        return
    if not hmac.compare_digest(_sha256(admin_pw), ADMIN_PASSWORD_HASH):
        _json_response(h, 403, {"error": "Invalid admin password"})
        return

    try:
        from book_bot import run_cart_reminders
        result = run_cart_reminders()
        _json_response(h, 200, {"ok": True, **result})
        logger.info("Admin ran cart-nudge sweep: %s", result)
    except Exception as e:
        logger.error(f"admin run-cart-nudge error: {e}")
        _json_response(h, 500, {"error": "Server error"})


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

        # Fetch contacts for names + last_seen_at + pin state. select("*") so a
        # pre-v30 schema (no pinned/pinned_at columns) still returns rows.
        contacts_data = (
            client.table("whatsapp_contacts")
            .select("*")
            .execute()
            .data
        )
        contacts_map = {c["phone"]: c for c in contacts_data}

        # Follow-up note counts per phone (for the inbox note badge). Tolerant of
        # the pre-v30 schema: missing table → {} → every count reads 0.
        from db_cloud import contact_note_counts
        note_counts = contact_note_counts()

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

        # Pinned contacts must stay reachable in the inbox even if their last
        # message is older than the recent 500 — otherwise pinning is leaky.
        # Backfill their latest rows so the loop below picks them up.
        present_phones = {r["phone"] for r in log_rows}
        missing_pinned = [
            ph for ph, c in contacts_map.items()
            if c.get("pinned") and ph not in present_phones
        ]
        if missing_pinned:
            try:
                backfill = (
                    client.table("conversation_log")
                    .select("phone,direction,message_type,body,filename,created_at")
                    .in_("phone", missing_pinned)
                    .order("created_at", desc=True)
                    .limit(500)
                    .execute()
                    .data
                    or []
                )
                log_rows = log_rows + backfill
            except Exception as exc:
                logger.debug("pinned inbox backfill skipped: %s", exc)

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
                "pinned":            bool(contact.get("pinned")),
                "pinned_at":         contact.get("pinned_at"),
                "note_count":        note_counts.get(ph, 0),
            })

        # Pinned chats first, then everyone else by message recency.
        inbox.sort(key=lambda x: (x["pinned"], x["ts"]), reverse=True)
        _json_response(h, 200, inbox)
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


def _handle_admin_health_models(h) -> None:
    """GET /admin/health/models — verify configured Claude models are reachable.

    Returns {"_status": "checked", "<model_id>": "ok"|"FAIL: ..."}.
    Hit this after every deploy. Cost ~₹0.01/call. Admin-auth only.
    """
    from urllib.parse import parse_qs, urlparse
    admin_pw = h.headers.get("X-Admin-Password", "").strip()
    if not admin_pw:
        params   = parse_qs(urlparse(h.path).query)
        admin_pw = params.get("admin_password", [""])[0]
    if not _auth_admin_pw(admin_pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return

    try:
        import docx_engine
        result = docx_engine.verify_models_available()
        _json_response(h, 200, result)
    except Exception as exc:
        logger.error("GET /admin/health/models error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _admin_pw_from_request(h) -> str:
    """Read admin password from header or ?admin_password= query string."""
    from urllib.parse import parse_qs, urlparse
    pw = h.headers.get("X-Admin-Password", "").strip()
    if not pw:
        params = parse_qs(urlparse(h.path).query)
        pw = params.get("admin_password", [""])[0]
    return pw


def _handle_admin_operator_queue_list(h) -> None:
    """GET /admin/operator-queue[?status=pending|claimed|delivered|all]
    Returns sorted-by-deadline list of operator queue rows.
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    from urllib.parse import parse_qs, urlparse
    params = parse_qs(urlparse(h.path).query)
    status = params.get("status", ["pending"])[0]
    status_filter: str | None = None if status == "all" else status
    try:
        from db_cloud import list_operator_queue
        rows = list_operator_queue(status=status_filter, limit=200)
        _json_response(h, 200, {"rows": rows, "count": len(rows)})
    except Exception as exc:
        logger.error("operator-queue list error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_book_orders_list(h) -> None:
    """GET /admin/book-orders[?status=collecting|awaiting_payment|payment_review|confirmed]
    Returns book orders newest-first. Omit status (or 'all') for everything.
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    from urllib.parse import parse_qs, urlparse
    params = parse_qs(urlparse(h.path).query)
    status = params.get("status", [None])[0]
    status_filter = None if status in (None, "", "all") else status
    try:
        from db_cloud import list_book_orders
        rows = list_book_orders(status=status_filter, limit=200)
        _json_response(h, 200, {"rows": rows, "count": len(rows)})
    except Exception as exc:
        logger.error("book-orders list error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_book_order_confirm(h, order_code: str) -> None:
    """POST /admin/book-orders/<code>/confirm — owner confirms payment received.
    Marks the order confirmed and messages the customer 'order confirmed'.
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        from book_bot import confirm_book_order
        result = confirm_book_order(order_code)
        _json_response(h, 200 if result.get("ok") else 404, result)
    except Exception as exc:
        logger.error("book-order confirm error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_book_order_dispatch(h, body, order_code: str) -> None:
    """POST /admin/book-orders/<code>/dispatch — mark shipped + notify customer.
    Body: {courier, tracking} (both optional).
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        data = json.loads(body or b"{}")
    except Exception:
        data = {}
    courier  = (data.get("courier") or "").strip()
    tracking = (data.get("tracking") or "").strip()
    try:
        from db_cloud import update_book_order, get_book_order
        from datetime import datetime, timezone
        order = get_book_order(order_code)
        if not order:
            _json_response(h, 404, {"error": "Order not found"})
            return
        update_book_order(order_code, status="dispatched",
                          dispatched_at=datetime.now(timezone.utc).isoformat(),
                          courier_name=courier or None, tracking_no=tracking or None)
        # Best-effort customer notification (subject to Meta's 24h window).
        try:
            from whatsapp_notify import _send
            extra = ""
            if courier:
                extra += f"\nCourier: {courier}"
            if tracking:
                extra += f"\nTracking: {tracking}"
            if order.get("phone"):
                _send(order["phone"], f"📦 *Order dispatched!*\nOrder: {order_code}"
                                      f"{extra}\n\nYour books are on the way. 🙏")
        except Exception:
            pass
        _json_response(h, 200, {"ok": True})
    except Exception as exc:
        logger.error("book-order dispatch error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_book_order_deliver(h, order_code: str) -> None:
    """POST /admin/book-orders/<code>/deliver — mark delivered."""
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        from db_cloud import update_book_order, get_book_order
        from datetime import datetime, timezone
        if not get_book_order(order_code):
            _json_response(h, 404, {"error": "Order not found"})
            return
        update_book_order(order_code, status="delivered",
                          delivered_at=datetime.now(timezone.utc).isoformat())
        _json_response(h, 200, {"ok": True})
    except Exception as exc:
        logger.error("book-order deliver error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_book_order_create(h, body) -> None:
    """POST /admin/book-orders/create — staff create a walk-in / in-store order.
    Body: {items:{malayalam,hindi,english}, name, phone, address, payment_mode, handed_over}.
    handed_over=true → no courier, status 'delivered'. Else → courier added, status 'confirmed'.
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        data = json.loads(body or b"{}")
    except Exception:
        data = {}
    raw = data.get("items") or {}
    items = {}
    for k in ("malayalam", "hindi", "english"):
        try:
            q = int(raw.get(k) or 0)
        except (TypeError, ValueError):
            q = 0
        if q > 0:
            items[k] = q
    if not items:
        _json_response(h, 400, {"error": "Select at least one book"})
        return
    name         = (data.get("name") or "").strip() or None
    phone        = re.sub(r"\D", "", data.get("phone") or "") or None
    address      = (data.get("address") or "").strip() or None
    payment_mode = (data.get("payment_mode") or "cash").strip().lower()
    handed_over  = bool(data.get("handed_over"))
    payment_collected_by = (data.get("payment_collected_by") or "oxygen").strip().lower()
    if payment_collected_by not in ("oxygen", "divya", "pending"):
        payment_collected_by = "oxygen"
    delivery_method = (data.get("delivery_method") or "courier").strip().lower()
    if delivery_method not in ("courier", "xtraa_office"):
        delivery_method = "courier"
    # Required-field validation (walk-in / manual order entry).
    # Name + phone are always required; address is required unless handed over.
    if not name:
        _json_response(h, 400, {"error": "Customer name is required"})
        return
    if not phone or len(phone) < 10:
        _json_response(h, 400, {"error": "A valid phone number (at least 10 digits) is required"})
        return
    if not handed_over and not address:
        _json_response(h, 400, {"error": "A delivery address is required unless the order is handed over"})
        return
    try:
        import book_catalog as bc
        from db_cloud import create_walk_in_order
        from datetime import datetime
        totals      = bc.compute_totals(items)
        books_total = totals["books_total"]
        no_courier  = handed_over or delivery_method == "xtraa_office"
        courier     = 0.0 if no_courier else totals["courier"]
        grand       = books_total + courier
        commission  = bc.commission_for(items)
        pradeep_commission = bc.pradeep_commission_for(items)
        code        = f"XTR-{datetime.now().strftime('%Y%m%d')}-{os.urandom(4).hex().upper()}"
        status      = "delivered" if handed_over else "confirmed"
        row = create_walk_in_order(code, name, phone, address, items,
                                   books_total, courier, grand, payment_mode, status,
                                   commission=commission,
                                   pradeep_commission=pradeep_commission,
                                   payment_collected_by=payment_collected_by,
                                   delivery_method=delivery_method)
        if not row:
            _json_response(h, 500, {"error": "Could not create order"})
            return
        _json_response(h, 200, {"ok": True, "order_code": code, "grand_total": grand})
        # Send payment QR via WhatsApp when payment is still pending.
        if not handed_over and payment_collected_by == "pending":
            try:
                import book_bot
                book_bot._send_qr(phone, row)
            except Exception as qr_exc:
                logger.warning("walk-in QR send failed for %s: %s", code, qr_exc)
    except Exception as exc:
        logger.error("book-order create error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_divya_ledger(h) -> None:
    """GET /admin/book-orders/divya-ledger[?from=ISO&to=ISO] — Divya settlement.

    Optional `from`/`to` (ISO-8601) restrict the period for daily/weekly/monthly
    summaries and CSV/PDF export.
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(h.path).query)
        date_from = (qs.get("from") or [None])[0]
        date_to   = (qs.get("to") or [None])[0]
        from db_cloud import divya_ledger
        _json_response(h, 200, divya_ledger(date_from=date_from, date_to=date_to))
    except Exception as exc:
        logger.error("divya-ledger error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_dispatch_sheet(h) -> None:
    """GET /admin/book-orders/dispatch-sheet — printable pick list + packing slips.

    Returns HTML. Auth via ?admin_password= query param or X-Admin-Password header.
    Only shows confirmed orders that have not yet been dispatched.

    Pick list distinguishes:
      - Aksharamrutham (malayalam) → PULL FROM STOCK (pre-printed, 100 copies)
      - Vidyamrut (hindi) / Easy English (english) → PRINT FIRST (POD)
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        from db_cloud import list_book_orders
        from datetime import datetime, timezone

        orders = list_book_orders(status="confirmed", limit=200)
        pending = [o for o in orders if not o.get("dispatched_at")]

        BOOKS: dict[str, tuple[str, str]] = {
            "malayalam": ("Aksharamrutham", "STOCK"),
            "hindi":     ("Vidyamrut",      "POD"),
            "english":   ("Easy English",   "POD"),
        }

        totals: dict[str, int] = {k: 0 for k in BOOKS}
        for order in pending:
            items = order.get("items") or {}
            for key in totals:
                totals[key] += int(items.get(key) or 0)

        generated = datetime.now(timezone.utc).strftime("%d %b %Y, %I:%M %p UTC")

        pick_rows = "".join(
            f'<tr><td>{BOOKS[k][0]}</td>'
            f'<td class="qty">{v}</td>'
            f'<td class="tag {"stock" if BOOKS[k][1] == "STOCK" else "pod"}">'
            f'{"&#9989; PULL FROM STOCK" if BOOKS[k][1] == "STOCK" else f"&#128424; PRINT FIRST &mdash; {BOOKS[k][0]}.pdf"}</td></tr>'
            for k, v in totals.items() if v
        )

        pick_section = (
            f'<section class="pick-list">'
            f'<h2>Pick List &mdash; {len(pending)} order{"s" if len(pending) != 1 else ""}</h2>'
            f'<table><thead><tr><th>Book</th><th>Qty</th><th>Action</th></tr></thead>'
            f'<tbody>{pick_rows}</tbody></table></section>'
        ) if pending else "<p>No confirmed orders pending dispatch.</p>"

        def _slip(order: dict) -> str:
            items = order.get("items") or {}
            book_lines = "<br>".join(
                f"{BOOKS[k][0]} &times; {qty}"
                for k, qty in items.items()
                if qty and k in BOOKS
            )
            is_pod = any(
                BOOKS.get(k, ("", ""))[1] == "POD"
                for k, v in items.items() if v
            )
            pod_files = [
                f"{BOOKS[k][0]}.pdf"
                for k, v in items.items()
                if v and BOOKS.get(k, ("", ""))[1] == "POD"
            ]
            pod_banner = (
                f'<div class="pod-banner">&#128424; PRINT FIRST: {", ".join(pod_files)}</div>'
                if pod_files else ""
            )
            raw_phone = order.get("phone") or order.get("contact_phone") or "&mdash;"
            phone = (
                raw_phone[2:]
                if isinstance(raw_phone, str) and raw_phone.startswith("91") and len(raw_phone) == 12
                else raw_phone
            )
            address = (order.get("address") or "&mdash;").replace("\n", "<br>")
            amount  = f"&#8377;{float(order.get('grand_total') or 0):,.0f}"
            pmode   = (order.get("payment_mode") or "&mdash;").upper()
            return (
                f'<div class="slip">'
                f'{pod_banner}'
                f'<div class="slip-header">'
                f'<span class="order-code">{order.get("order_code", "")}</span>'
                f'<span class="amount">{amount} ({pmode})</span>'
                f'</div>'
                f'<div class="customer"><strong>{order.get("name", "&mdash;")}</strong>'
                f'<br>&#128222; {phone}</div>'
                f'<div class="address">&#128205; {address}</div>'
                f'<div class="books">&#128218; {book_lines or "&mdash;"}</div>'
                f'</div>'
            )

        slips_html = "".join(_slip(o) for o in pending)

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Dispatch Sheet &mdash; Printosky</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:Arial,sans-serif;font-size:13px;color:#111;padding:16px}}
  h1{{font-size:18px;margin-bottom:4px}}
  .meta{{color:#555;font-size:11px;margin-bottom:20px}}
  .pick-list{{border:2px solid #111;padding:12px;margin-bottom:24px;max-width:600px}}
  .pick-list h2{{font-size:15px;margin-bottom:10px}}
  table{{width:100%;border-collapse:collapse}}
  th,td{{padding:6px 10px;border:1px solid #ccc;text-align:left}}
  th{{background:#f0f0f0}}
  td.qty{{font-size:18px;font-weight:bold;text-align:center;width:60px}}
  .tag{{font-size:11px;font-weight:bold}}
  .tag.stock{{color:#1a7c1a}}
  .tag.pod{{color:#b85c00}}
  .slips{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  .slip{{border:1px solid #888;border-radius:4px;padding:12px;page-break-inside:avoid}}
  .pod-banner{{background:#fff3cd;border:1px solid #f0ad4e;padding:4px 8px;
    border-radius:3px;font-size:11px;font-weight:bold;margin-bottom:8px}}
  .slip-header{{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}}
  .order-code{{font-size:10px;color:#555;font-family:monospace}}
  .amount{{font-size:13px;font-weight:bold;color:#1a7c1a}}
  .customer{{margin-bottom:6px;line-height:1.6}}
  .address{{color:#333;margin-bottom:6px;line-height:1.5}}
  .books{{font-weight:bold;margin-top:6px;color:#222}}
  @media print{{
    .no-print{{display:none}}
    body{{padding:0}}
    .slips{{grid-template-columns:1fr 1fr}}
  }}
</style>
</head>
<body>
<div class="no-print" style="margin-bottom:16px">
  <button onclick="window.print()" style="padding:8px 20px;font-size:14px;cursor:pointer">
    Print this sheet
  </button>
</div>
<h1>Dispatch Sheet</h1>
<p class="meta">Generated: {generated} &nbsp;|&nbsp; Confirmed, undispatched orders only</p>
{pick_section}
<div class="slips">{slips_html}</div>
</body>
</html>"""

        encoded = html.encode("utf-8")
        h.send_response(200)
        h.send_header("Content-Type", "text/html; charset=utf-8")
        h.send_header("Content-Length", str(len(encoded)))
        _send_cors_headers(h)   # opened cross-origin (admin.html on printosky.com) -> need ACAO
        h.end_headers()
        h.wfile.write(encoded)

    except Exception as exc:
        logger.error("dispatch-sheet error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_book_order_settle_divya(h, body, order_code: str) -> None:
    """POST /admin/book-orders/<code>/settle-divya — mark commission reconciled.
    Body: {settled: bool} (default true).
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        data = json.loads(body or b"{}")
    except Exception:
        data = {}
    settled = bool(data.get("settled", True))
    try:
        from db_cloud import mark_divya_settled, get_book_order
        if not get_book_order(order_code):
            _json_response(h, 404, {"error": "Order not found"})
            return
        mark_divya_settled(order_code, settled)
        _json_response(h, 200, {"ok": True, "settled": settled})
    except Exception as exc:
        logger.error("settle-divya error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_book_order_edit(h, body, order_code: str) -> None:
    """POST /admin/book-orders/<code>/edit — edit an existing order.

    Body may include any of: name, phone, address, payment_mode,
    payment_collected_by, delivery_method, via_divya, items{malayalam,hindi,english}.
    When items or delivery_method change, books_total/courier/grand_total/commission
    are recomputed so the ledger stays correct.
    """
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        data = json.loads(body or b"{}")
    except Exception:
        data = {}
    try:
        import book_catalog as bc
        from db_cloud import get_book_order, update_book_order
        order = get_book_order(order_code)
        if not order:
            _json_response(h, 404, {"error": "Order not found"})
            return

        fields = {}
        for k in ("name", "address", "payment_mode", "payment_collected_by", "delivery_method"):
            if data.get(k) is not None:
                fields[k] = str(data[k]).strip()
        if data.get("phone") is not None:
            ph = re.sub(r"\D", "", str(data["phone"]))
            fields["phone"] = ph
            fields["contact_phone"] = ph
        if "via_divya" in data:
            fields["via_divya"] = bool(data["via_divya"])

        if fields.get("payment_collected_by") and fields["payment_collected_by"] not in ("oxygen", "divya", "pending"):
            _json_response(h, 400, {"error": "payment_collected_by must be oxygen, divya or pending"})
            return
        if fields.get("delivery_method") and fields["delivery_method"] not in ("courier", "xtraa_office"):
            _json_response(h, 400, {"error": "delivery_method must be courier or xtraa_office"})
            return

        # Recompute money when items change.
        if isinstance(data.get("items"), dict):
            raw, items = data["items"], {}
            for k in ("malayalam", "hindi", "english"):
                try:
                    q = int(raw.get(k) or 0)
                except (TypeError, ValueError):
                    q = 0
                if q > 0:
                    items[k] = q
            if not items:
                _json_response(h, 400, {"error": "An order needs at least one book"})
                return
            totals = bc.compute_totals(items)
            deliv = fields.get("delivery_method") or order.get("delivery_method") or "courier"
            courier = 0.0 if deliv == "xtraa_office" else totals["courier"]
            fields["items"]       = items
            fields["books_total"] = totals["books_total"]
            fields["courier"]     = courier
            fields["grand_total"] = totals["books_total"] + courier
            fields["commission"]  = bc.commission_for(items)
            fields["pradeep_commission"] = bc.pradeep_commission_for(items)
        elif fields.get("delivery_method"):
            # Delivery changed without items — recompute courier from existing items.
            items = order.get("items") or {}
            totals = bc.compute_totals(items)
            courier = 0.0 if fields["delivery_method"] == "xtraa_office" else totals["courier"]
            fields["courier"]     = courier
            fields["grand_total"] = totals["books_total"] + courier

        if not fields:
            _json_response(h, 400, {"error": "Nothing to update"})
            return
        # Hard rule: Divya's own order is courier-free + commission-free (she
        # pays the book cost alone, earns no commission on herself).
        if bc.is_divya_phone(fields.get("phone") or order.get("phone")):
            _bt = fields.get("books_total", order.get("books_total") or 0.0)
            fields.update(commission=0.0, pradeep_commission=0.0, via_divya=False,
                          courier=0.0, grand_total=_bt)
        update_book_order(order_code, **fields)
        # If the operator just set a payment field on a not-yet-confirmed order,
        # hint the UI to offer moving it to Ready to Dispatch — a manual payment
        # edit otherwise leaves the order stuck pre-confirm (the Rasmi case).
        paid_touch = bool(fields.get("payment_mode")) or \
            fields.get("payment_collected_by") in ("oxygen", "divya")
        suggest_confirm = paid_touch and order.get("status") not in (
            "confirmed", "dispatched", "delivered", "cancelled")
        _json_response(h, 200, {"ok": True, "order": get_book_order(order_code),
                                "suggest_confirm": suggest_confirm})
    except Exception as exc:
        logger.error("book-order edit error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_operator_queue_depth(h) -> None:
    """GET /admin/operator-queue/depth — counts for the SLA gauge."""
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        from db_cloud import get_operator_queue_depth
        _json_response(h, 200, get_operator_queue_depth())
    except Exception as exc:
        logger.error("operator-queue depth error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_operator_queue_get(h, queue_id: str) -> None:
    """GET /admin/operator-queue/<id> — full row including input_text and partials."""
    if not _auth_admin_pw(_admin_pw_from_request(h)):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        from db_cloud import get_operator_job
        row = get_operator_job(queue_id)
        if not row:
            _json_response(h, 404, {"error": "Not found"})
            return
        _json_response(h, 200, row)
    except Exception as exc:
        logger.error("operator-queue get error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_operator_queue_claim(h, body: bytes, queue_id: str) -> None:
    """POST /admin/operator-queue/<id>/claim
    Body: {"admin_password": "...", "operator": "<email or name>"}
    Marks the row claimed by the operator. Refuses if already claimed.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return
    pw = data.get("admin_password", "") or _admin_pw_from_request(h)
    if not _auth_admin_pw(pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    operator = str(data.get("operator", "")).strip()[:120]
    if not operator:
        _json_response(h, 400, {"error": "operator field required"})
        return
    try:
        from db_cloud import claim_operator_job
        ok = claim_operator_job(queue_id, operator)
        if not ok:
            _json_response(h, 409, {
                "error": "already_claimed_or_missing",
                "message": "Job is no longer pending or does not exist."
            })
            return
        _json_response(h, 200, {"ok": True, "queue_id": queue_id,
                                  "assigned_to": operator})
    except Exception as exc:
        logger.error("operator-queue claim error: %s", exc)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_operator_queue_deliver(h, body: bytes, queue_id: str) -> None:
    """POST /admin/operator-queue/<id>/deliver
    Body: {
      "admin_password": "...",
      "docx_b64":       "<base64 of finished docx bytes>",
      "notify_customer": true   (optional, default true)
    }
    Uploads the finished DOCX, marks delivered, sends WhatsApp link.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return
    pw = data.get("admin_password", "") or _admin_pw_from_request(h)
    if not _auth_admin_pw(pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return

    docx_b64 = data.get("docx_b64", "")
    if not docx_b64:
        _json_response(h, 400, {"error": "docx_b64 required"})
        return
    try:
        import base64 as _b64
        docx_bytes = _b64.b64decode(docx_b64)
    except Exception as exc:
        _json_response(h, 400, {"error": f"docx_b64 decode failed: {exc}"})
        return
    if len(docx_bytes) < 1000:
        _json_response(h, 400, {"error": "docx too small (<1KB) — likely invalid"})
        return

    notify = bool(data.get("notify_customer", True))

    try:
        from db_cloud import deliver_operator_job, get_operator_job
        ok, url = deliver_operator_job(queue_id, docx_bytes)
        if not ok:
            _json_response(h, 500, {"error": "Storage upload failed"})
            return

        # Pull row back for the WhatsApp message (need phone)
        row = get_operator_job(queue_id) or {}
        wa_phone = row.get("customer_phone", "")
        if notify and wa_phone and url:
            try:
                _send_pb_whatsapp(wa_phone, queue_id[:8], url)
            except Exception as exc:
                logger.warning("operator-deliver WhatsApp send failed: %s", exc)

        _json_response(h, 200, {
            "ok":           True,
            "queue_id":     queue_id,
            "download_url": url,
            "notified":     bool(notify and wa_phone),
        })
    except Exception as exc:
        logger.error("operator-queue deliver error: %s", exc)
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


def _handle_admin_contact_pin(h, body: bytes) -> None:
    """POST /admin/contacts/pin — pin or unpin a conversation for follow-up.

    Body: {admin_password, phone, pinned: bool}. Pinned chats sort to the top of
    the inbox and appear in the twice-daily chat-audit digest.
    """
    try:
        payload = json.loads(body)
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return
    if not _auth_admin_pw(payload.get("admin_password", "")):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    phone = (payload.get("phone") or "").strip()
    if not phone or len(phone) > 30:
        _json_response(h, 400, {"error": "valid phone required"})
        return
    pinned = bool(payload.get("pinned"))
    try:
        from db_cloud import set_contact_pin
        ok = set_contact_pin(phone, pinned)
        _json_response(h, 200 if ok else 500, {"ok": ok, "pinned": pinned})
    except Exception as exc:
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_contact_note(h, body: bytes) -> None:
    """POST /admin/contacts/note — append a follow-up note to a conversation.

    Body: {admin_password, phone, note, by?}. Notes are append-only.
    """
    try:
        payload = json.loads(body)
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return
    if not _auth_admin_pw(payload.get("admin_password", "")):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    phone = (payload.get("phone") or "").strip()
    note  = (payload.get("note") or "").strip()
    if not phone or len(phone) > 30 or not note:
        _json_response(h, 400, {"error": "phone and note required"})
        return
    try:
        from db_cloud import add_contact_note
        row = add_contact_note(phone, note, payload.get("by"))
        if not row:
            _json_response(h, 500, {"error": "note not saved"})
            return
        _json_response(h, 200, {"ok": True, "note": row})
    except Exception as exc:
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_contact_notes(h) -> None:
    """GET /admin/contacts/notes?phone=X — list a contact's follow-up notes."""
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
    try:
        from db_cloud import list_contact_notes
        _json_response(h, 200, list_contact_notes(phone))
    except Exception as exc:
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_contact_note_delete(h, body: bytes) -> None:
    """POST /admin/contacts/note/delete — remove one follow-up note by id.

    Body: {admin_password, id}.
    """
    try:
        payload = json.loads(body)
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return
    if not _auth_admin_pw(payload.get("admin_password", "")):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    try:
        note_id = int(payload.get("id"))
    except (TypeError, ValueError):
        _json_response(h, 400, {"error": "id must be an integer"})
        return
    try:
        from db_cloud import delete_contact_note
        ok = delete_contact_note(note_id)
        _json_response(h, 200 if ok else 500, {"ok": ok})
    except Exception as exc:
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_contacts_search(h) -> None:
    """GET /admin/contacts/search?q=X — find ANY contact by name or number,
    including chats that have scrolled off the recent inbox window. Returns
    inbox-shaped rows so the UI renders + opens them like normal contacts."""
    from urllib.parse import parse_qs, urlparse
    params   = parse_qs(urlparse(h.path).query)
    admin_pw = h.headers.get("X-Admin-Password", "").strip() or params.get("admin_password", [""])[0]
    if not _auth_admin_pw(admin_pw):
        _json_response(h, 403, {"error": "Unauthorized"})
        return
    q = (params.get("q", [""])[0] or "").strip()
    if len(q) < 2:
        _json_response(h, 200, [])
        return
    try:
        from db_cloud import search_contacts
        _json_response(h, 200, search_contacts(q))
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
        # supabase-py 2.x returns snake_case; older versions used "signedURL"
        upload_url = (
            resp.get("signed_url")
            or resp.get("signedUrl")
            or resp.get("signedURL")
            or resp.get("url")
        )
        if not upload_url:
            logger.error("upload-token: bad response keys: %s", list(resp.keys()))
            _json_response(h, 500, {"error": "no signed URL in response"})
            return
        _json_response(h, 200, {
            "upload_url":   upload_url,
            "storage_path": storage_path,
        })
    except Exception as exc:
        logger.error("upload-token error %s: %s", type(exc).__name__, exc)
        _json_response(h, 500, {"error": str(exc), "exc_type": type(exc).__name__})


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

        _clear_needs_human(phone)   # staff replied with a file → drop the SOS pill
        _json_response(h, 200, {"ok": True})
    except Exception as exc:
        logger.error("send-file error for %s: %s", phone, exc)
        _json_response(h, 500, {"error": str(exc)})


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


# ── Notes marketplace admin handlers ─────────────────────────────────────────

def _handle_admin_notes_queue(h) -> None:
    """GET /admin/notes-queue — list all pending notes for moderation."""
    try:
        from db_cloud import pending_notes_queue
        notes = pending_notes_queue(limit=100)
        _json_response(h, 200, {"notes": notes, "count": len(notes)})
    except Exception as exc:
        logger.error("admin notes-queue error: %s", exc, exc_info=True)
        _json_response(h, 500, {"error": str(exc)})


def _handle_admin_notes_moderate(h, body: bytes, note_code: str, action: str) -> None:
    """POST /admin/notes/<code>/approve|reject — moderate a submitted note.

    Body (JSON): { admin_password, reason? }
    Approve: sets status=approved, notifies uploader via WhatsApp.
    Reject:  sets status=rejected, sends reason to uploader.
    """
    try:
        payload = json.loads(body) if body else {}
    except Exception:
        _json_response(h, 400, {"error": "Invalid JSON"})
        return

    if not _auth_admin_pw(payload.get("admin_password", "")):
        _json_response(h, 403, {"error": "Unauthorized"})
        return

    if action not in ("approve", "reject"):
        _json_response(h, 400, {"error": "action must be approve or reject"})
        return

    reason = (payload.get("reason") or "").strip()
    if action == "reject" and not reason:
        _json_response(h, 400, {"error": "reason is required for rejection"})
        return

    try:
        from db_cloud import get_note, publish_note, reject_note
        from whatsapp_notify import _send

        note = get_note(note_code)
        if not note:
            _json_response(h, 404, {"error": f"Note {note_code} not found"})
            return

        if action == "approve":
            ok = publish_note(note_code)
            if ok:
                _send(note["uploader_phone"], {
                    "messaging_product": "whatsapp",
                    "to": note["uploader_phone"],
                    "type": "text",
                    "text": {"body": (
                        f"Your notes *{note['title']}* have been approved and are now live!\n\n"
                        f"Code: *{note_code}*\n"
                        "Customers can order prints and you'll earn store credit for each copy."
                    )},
                })
                logger.info("Notes admin: approved %s (uploader %s)", note_code, note["uploader_phone"])
                _json_response(h, 200, {"status": "approved", "note_code": note_code})
            else:
                _json_response(h, 500, {"error": "DB update failed"})
        else:
            ok = reject_note(note_code, reason)
            if ok:
                _send(note["uploader_phone"], {
                    "messaging_product": "whatsapp",
                    "to": note["uploader_phone"],
                    "type": "text",
                    "text": {"body": (
                        f"Your notes *{note['title']}* could not be approved.\n\n"
                        f"Reason: {reason}\n\n"
                        "You're welcome to revise and resubmit. Type _\"upload notes\"_ to start."
                    )},
                })
                logger.info("Notes admin: rejected %s — %s", note_code, reason)
                _json_response(h, 200, {"status": "rejected", "note_code": note_code, "reason": reason})
            else:
                _json_response(h, 500, {"error": "DB update failed"})

    except Exception as exc:
        logger.error("admin notes moderate %s/%s: %s", note_code, action, exc, exc_info=True)
        _json_response(h, 500, {"error": str(exc)})
