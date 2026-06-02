"""Xtraa book campaign — WhatsApp conversational order flow.

Cloud-only (runs in the Vercel webhook). Order data lives in the `book_orders`
table; the `bot_sessions.step` column drives state (book_* steps). Pure pricing
and parsing logic lives in book_catalog.py.

Flow:
    enquiry → book_select → book_qty (per book) → book_address → book_phone
    → book_summary → book_pay → (screenshot) → payment_review → (owner) → confirmed

Entry points used by the webhook / admin:
    is_book_trigger(text)                 — cheap trigger check
    maybe_handle_book(phone, text, name)  — returns reply list, or None if not ours
    handle_payment_proof(phone, bytes, mime) — screenshot during book_pay
    confirm_book_order(order_code)        — owner taps Confirm in admin
"""

from __future__ import annotations

import logging
import os
import re

import book_catalog as bc
import db_cloud as _dbc

logger = logging.getLogger(__name__)

DB = "supabase"  # db_path is ignored in cloud mode

# Trigger words that start a book enquiry (only when not mid print-job).
_TRIGGER_WORDS = {
    "book", "books", "xtraa", "xtra", "adithara", "balappeduthu",
    "foundation", "അടിത്തറ",
}

_AFFIRM = {
    "yes", "y", "ok", "okay", "confirm", "confirmed", "correct", "right",
    "sure", "ya", "yeah", "yep", "proceed", "done", "✓", "👍",
}
_NEGATE = {"no", "n", "change", "edit", "cancel", "wrong", "restart", "redo", "back"}

# Path to the branded Printosky "Scan to Pay" QR, bundled into the lambda via
# vercel.json includeFiles.
_QR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "brand-kit", "printosky-payment-qr.png")

_BOOK_STEPS = {"book_select", "book_qty", "book_address", "book_phone",
               "book_summary", "book_pay"}


# ── helpers ───────────────────────────────────────────────────────────────────

def is_book_trigger(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    # Whole-word match so "facebook" / "booking a print" don't false-trigger.
    words = set(re.split(r"[^\wഀ-ൿ]+", t))
    return bool(words & _TRIGGER_WORDS)


def _in_print_flow(session: dict) -> bool:
    """True if the customer is mid print-job (has a print step or job_id)."""
    if not session:
        return False
    step = session.get("step") or ""
    if step.startswith("book_"):
        return False
    return bool(step) or bool(session.get("job_id"))


def _new_order_code() -> str:
    from datetime import datetime
    return f"XTR-{datetime.now().strftime('%Y%m%d')}-{os.urandom(4).hex().upper()}"


def _send_text(phone: str, message: str) -> None:
    # whatsapp_notify._send_meta logs to conversation_log on success; don't
    # double-log here.
    from whatsapp_notify import _send
    _send(phone, message)


def _format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return f"+{digits}" if digits else phone


def _extract_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if 10 <= len(digits) <= 13:
        return digits
    return None


# ── message builders ──────────────────────────────────────────────────────────

def _catalog_message() -> str:
    b = bc.BOOKS
    return (
        "📚 *Xtraa — Adithara Balappeduthu*\n"
        "_Foundation books for early readers._\n\n"
        f"1️⃣ *Aksharamrutham* (Malayalam) — ₹{b['malayalam']['price']} "
        f"(MRP ₹{b['malayalam']['mrp']})\n"
        f"2️⃣ *Vidyamrut* (Hindi) — ₹{b['hindi']['price']} (MRP ₹{b['hindi']['mrp']})\n"
        f"3️⃣ *Easy English* — ₹{b['english']['price']} (MRP ₹{b['english']['mrp']})\n"
        f"4️⃣ *All three (Set)* — ₹{bc.SET_PRICE}\n\n"
        "Reply with your choice:\n"
        "• one number → _1_\n"
        "• any two → _1,3_\n"
        "• all three → _4_\n\n"
        f"_+ ₹{bc.COURIER} courier per order._"
    )


def _qty_prompt(book_key: str) -> str:
    label = bc.BOOKS[book_key]["label"]
    return f"How many copies of *{label}*? (reply with a number, e.g. _1_)"


def _summary_message(order: dict) -> str:
    items = order.get("items") or {}
    lines = bc.line_items(items)
    totals = bc.compute_totals(items)

    body = ["🧾 *Order summary*", ""]
    for ln in lines:
        body.append(
            f"• {ln['label']} × {ln['qty']} = ₹{ln['line_total']:.0f}"
        )
    body.append("")
    body.append(f"Books: ₹{totals['books_total']:.0f}")
    body.append(f"Courier: ₹{totals['courier']:.0f}")
    body.append(f"*Total: ₹{totals['grand_total']:.0f}*")
    body.append("")
    body.append(f"📍 Deliver to:\n{order.get('address', '')}")
    body.append(f"📞 Contact: {_format_phone(order.get('contact_phone', ''))}")
    body.append("")
    body.append("Reply *YES* to confirm, or *NO* to start over.")
    return "\n".join(body)


def _payment_caption(order: dict) -> str:
    totals = bc.compute_totals(order.get("items") or {})
    return (
        f"💳 *Pay ₹{totals['grand_total']:.0f}* by scanning this UPI QR.\n\n"
        f"Order: {order.get('order_code')}\n\n"
        "After paying, *send a screenshot* of the payment confirmation here. "
        "We'll verify and confirm your order. 🙏"
    )


# ── flow ──────────────────────────────────────────────────────────────────────

def _start(phone: str, name: str | None, force_new: bool = False) -> list[str]:
    """Begin (or restart) a book order for this phone.

    force_new=True (customer typed NEW) always opens a fresh order, even if a
    previous one is awaiting payment confirmation.
    """
    active = {} if force_new else _dbc.get_active_book_order(phone)
    if active and active.get("status") in ("collecting", "awaiting_payment"):
        code = active["order_code"]
        _dbc.update_book_order(code, items={}, flow_cursor={}, status="collecting",
                               books_total=0, courier=bc.COURIER, grand_total=0,
                               address=None, contact_phone=None,
                               payment_proof_url=None)
    elif active and active.get("status") == "payment_review":
        # A previous order is already awaiting the owner's confirmation.
        return [
            f"Your previous order *{active['order_code']}* is being confirmed. "
            "We'll message you shortly. To place a *new* order, reply *NEW*."
        ]
    else:
        code = _new_order_code()
        created = _dbc.create_book_order(code, phone, name)
        if not created:
            # Rare collision / transient error — retry once with a fresh code.
            code = _new_order_code()
            _dbc.create_book_order(code, phone, name)

    # Clear any stale 'needs human' flag from a prior payment-review session.
    _dbc.save_session(DB, phone, step="book_select", needs_human=False)
    return [_catalog_message()]


def _handle_select(phone: str, text: str, order: dict) -> list[str]:
    keys = bc.parse_selection(text)
    if not keys:
        return [
            "Sorry, I didn't catch that. Reply with *1*, *2*, *3* (or e.g. *1,3*), "
            "or *4* for all three. 👇"
        ]
    items = {k: 0 for k in keys}
    current = keys[0]
    pending = keys[1:]
    _dbc.update_book_order(
        order["order_code"],
        items=items,
        flow_cursor={"current": current, "pending": pending},
    )
    _dbc.save_session(DB, phone, step="book_qty")
    return [_qty_prompt(current)]


def _handle_qty(phone: str, text: str, order: dict) -> list[str]:
    cursor = order.get("flow_cursor") or {}
    current = cursor.get("current")
    pending = list(cursor.get("pending") or [])
    items = dict(order.get("items") or {})

    if not current:
        # Defensive: cursor lost — restart selection.
        _dbc.save_session(DB, phone, step="book_select")
        return [_catalog_message()]

    qty = bc.parse_qty(text)
    if qty is None:
        return [_qty_prompt(current)]

    items[current] = qty

    if pending:
        nxt = pending.pop(0)
        _dbc.update_book_order(order["order_code"], items=items,
                               flow_cursor={"current": nxt, "pending": pending})
        return [_qty_prompt(nxt)]

    # All quantities collected → compute totals, move to address.
    totals = bc.compute_totals(items)
    _dbc.update_book_order(
        order["order_code"],
        items=items,
        flow_cursor={},
        books_total=totals["books_total"],
        courier=totals["courier"],
        grand_total=totals["grand_total"],
    )
    _dbc.save_session(DB, phone, step="book_address")
    return [
        f"Great! Your order comes to *₹{totals['grand_total']:.0f}* "
        f"(incl. ₹{totals['courier']:.0f} courier).\n\n"
        "📍 Please send your *full delivery address* (house/street, place, "
        "district, PIN code)."
    ]


def _handle_address(phone: str, text: str, order: dict) -> list[str]:
    address = (text or "").strip()
    if len(address) < 10:
        return [
            "That address looks too short. Please send your *full delivery "
            "address* including place and PIN code. 📍"
        ]
    _dbc.update_book_order(order["order_code"], address=address)
    _dbc.save_session(DB, phone, step="book_phone")
    return [
        f"Got it. Is *{_format_phone(phone)}* the right number for delivery "
        "updates?\n\nReply *YES*, or send the correct phone number."
    ]


def _handle_phone(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip().lower()
    if t in _AFFIRM:
        contact = re.sub(r"\D", "", phone)
    else:
        extracted = _extract_phone(text)
        if not extracted:
            return [
                "Please reply *YES* to use this number, or send a valid "
                "phone number (10 digits). 📞"
            ]
        contact = extracted

    _dbc.update_book_order(order["order_code"], contact_phone=contact)
    _dbc.save_session(DB, phone, step="book_summary")
    refreshed = _dbc.get_book_order(order["order_code"])
    return [_summary_message(refreshed)]


def _handle_summary(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip().lower()
    if t in _NEGATE:
        _dbc.update_book_order(order["order_code"], items={}, flow_cursor={},
                               books_total=0, courier=bc.COURIER, grand_total=0,
                               address=None, contact_phone=None)
        _dbc.save_session(DB, phone, step="book_select")
        return ["No problem — let's start over.\n\n" + _catalog_message()]

    if t not in _AFFIRM:
        return ["Reply *YES* to confirm your order, or *NO* to start over. 👇"]

    # Confirmed → request payment. Send the QR image, then instructions.
    _dbc.update_book_order(order["order_code"], status="awaiting_payment")
    _dbc.save_session(DB, phone, step="book_pay")
    refreshed = _dbc.get_book_order(order["order_code"])

    sent_qr = _send_qr(phone, refreshed)
    if sent_qr:
        return []  # caption already carried the instructions
    # Fallback if the image couldn't be sent — send text instructions.
    totals = bc.compute_totals(refreshed.get("items") or {})
    return [
        f"Please pay *₹{totals['grand_total']:.0f}* to our UPI and send a "
        "screenshot here. (Sending the QR image failed — reply *QR* to retry.)"
    ]


def _send_qr(phone: str, order: dict) -> bool:
    """Send the UPI QR image with a payment caption. Returns True on success."""
    try:
        with open(_QR_PATH, "rb") as f:
            data = f.read()
    except Exception as exc:
        logger.error("book_bot: cannot read QR asset %s: %s", _QR_PATH, exc)
        return False
    try:
        from whatsapp_notify import send_file
        ok = send_file(phone, data, "image/png", "printosky-payment-qr.png",
                       caption=_payment_caption(order))
        if ok:
            try:
                _dbc.log_message(phone, "outbound", "[UPI QR sent]",
                                 message_type="image")
            except Exception:
                pass
        return bool(ok)
    except Exception as exc:
        logger.error("book_bot: send_file QR failed: %s", exc)
        return False


def _handle_pay(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip().lower()
    if t == "qr":
        _send_qr(phone, order)
        return []
    return [
        "Please complete the UPI payment and *send a screenshot* of the "
        "confirmation here. 🙏\n\nReply *QR* if you need the payment QR again."
    ]


# ── public entry points ───────────────────────────────────────────────────────

def maybe_handle_book(phone: str, text: str, name: str | None = None) -> list[str] | None:
    """Route a text message through the book flow.

    Returns a list of reply strings if the message belongs to the book flow
    (possibly empty if a reply was already sent as a side effect, e.g. the QR
    image), or None if this message is not part of a book conversation.
    """
    session = _dbc.get_session(DB, phone) or {}
    step = session.get("step") or ""

    if step not in _BOOK_STEPS:
        # Not in a book flow — only start one on an explicit trigger and only
        # when the customer isn't mid print-job.
        if is_book_trigger(text) and not _in_print_flow(session):
            return _start(phone, name)
        return None

    # "NEW" lets a customer start a fresh order from any book step — even when a
    # prior order is awaiting payment confirmation.
    if (text or "").strip().lower() == "new":
        return _start(phone, name, force_new=True)

    order = _dbc.get_active_book_order(phone)
    if not order:
        # Session says book_* but no active order — reset cleanly.
        _dbc.save_session(DB, phone, step="book_select")
        return _start(phone, name)

    handlers = {
        "book_select":  _handle_select,
        "book_qty":     _handle_qty,
        "book_address": _handle_address,
        "book_phone":   _handle_phone,
        "book_summary": _handle_summary,
        "book_pay":     _handle_pay,
    }
    handler = handlers.get(step)
    if not handler:
        _dbc.save_session(DB, phone, step="book_select")
        return _start(phone, name)
    return handler(phone, text, order)


def handle_payment_proof(phone: str, content: bytes, mime_type: str) -> list[str] | None:
    """Handle an incoming image while the customer is awaiting payment.

    Returns reply strings if this was a book payment proof, else None (so the
    webhook falls through to normal print-job media handling).
    """
    session = _dbc.get_session(DB, phone) or {}
    if session.get("step") != "book_pay":
        return None
    order = _dbc.get_active_book_order(phone)
    if not order or order.get("status") not in ("awaiting_payment", "payment_review"):
        return None

    url = _dbc.upload_book_payment_proof(order["order_code"], content, mime_type)
    _dbc.update_book_order(order["order_code"], status="payment_review",
                           payment_proof_url=url or None)
    # Flag for the owner — surfaces in the admin "Needs human" filter.
    _dbc.save_session(DB, phone, step="book_pay", needs_human=True)
    return [
        f"✅ Got your payment screenshot for *{order['order_code']}*.\n\n"
        "We're verifying it now — you'll receive your order confirmation "
        "shortly. Thank you! 🙏"
    ]


def confirm_book_order(order_code: str) -> dict:
    """Owner confirms payment for an order. Sends the customer confirmation.

    Returns {ok, ...}. Idempotent: re-confirming a confirmed order is a no-op.
    """
    order = _dbc.get_book_order(order_code)
    if not order:
        return {"ok": False, "error": "Order not found"}
    if order.get("status") == "confirmed":
        return {"ok": True, "already_confirmed": True, "order": order}

    from datetime import datetime, timezone
    _dbc.update_book_order(order_code, status="confirmed",
                           confirmed_at=datetime.now(timezone.utc).isoformat())

    phone = order["phone"]
    # Clear the book session so future messages start fresh; drop the flag.
    try:
        _dbc.clear_session(DB, phone)
    except Exception:
        pass

    totals = bc.compute_totals(order.get("items") or {})
    _send_text(
        phone,
        f"🎉 *Order confirmed!*\n\n"
        f"Order: *{order_code}*\n"
        f"Amount: ₹{totals['grand_total']:.0f}\n\n"
        "Your books will be couriered to the address you shared. "
        "Thank you for ordering from Printosky × Xtraa! 📚",
    )
    return {"ok": True, "order": _dbc.get_book_order(order_code)}
