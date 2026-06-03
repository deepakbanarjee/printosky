"""Xtraa book campaign — WhatsApp conversational order flow (button-driven).

Cloud-only (runs in the Vercel webhook). Order data lives in the `book_orders`
table; the `bot_sessions.step` column drives state (book_* steps). Pure pricing
and parsing logic lives in book_catalog.py.

The customer drives the order by tapping WhatsApp buttons / list rows rather
than typing numbers (typed fallbacks are still accepted). Flow:

    enquiry → book_select (list) → book_qty (buttons) → book_addmore (buttons)
            → book_address (typed) → book_phone (buttons) → book_summary (buttons)
            → book_pay → (screenshot) → payment_review → (owner) → confirmed

Entry points used by the webhook / admin:
    is_book_trigger(text)                 — cheap trigger check
    maybe_handle_book(phone, text, name)  — returns reply list ([] = already sent), or None
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
    "foundation", "aksharamrutham", "vidyamrut", "അടിത്തറ",
}

_AFFIRM = {
    "yes", "y", "ok", "okay", "confirm", "confirmed", "correct", "right",
    "sure", "ya", "yeah", "yep", "proceed", "done", "✓", "👍",
}
_NEGATE = {"no", "n", "change", "edit", "cancel", "wrong", "restart", "redo", "back"}

# Path to the branded Printosky "Scan to Pay" QR, bundled via vercel.json includeFiles.
_QR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "brand-kit", "printosky-payment-qr.png")

_BOOK_STEPS = {"book_select", "book_qty", "book_addmore", "book_address",
               "book_phone", "book_summary", "book_pay"}

# Button / list-row id maps.
_SELECT_IDS = {"bk_ml": "malayalam", "bk_hi": "hindi", "bk_en": "english", "bk_set": "__set__"}
_QTY_IDS = {"qty_1": 1, "qty_2": 2, "qty_3": 3}
_SET = "__set__"


# ── helpers ───────────────────────────────────────────────────────────────────

def is_book_trigger(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
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
    # whatsapp_notify._send_meta logs to conversation_log on success; don't double-log.
    from whatsapp_notify import _send
    _send(phone, message)


def _send_buttons(phone: str, body: str, buttons: list, header: str | None = None) -> bool:
    try:
        from whatsapp_notify import send_buttons
        return send_buttons(phone, body, buttons, header=header)
    except Exception as exc:
        logger.error("book_bot send_buttons failed: %s", exc)
        # Fallback to plain text so the customer is never left hanging.
        _send_text(phone, body + "\n\n" + " / ".join(t for _, t in buttons))
        return False


def _send_list(phone: str, body: str, button_text: str, rows: list,
               header: str | None = None) -> bool:
    try:
        from whatsapp_notify import send_list
        return send_list(phone, body, button_text, rows, header=header,
                         section_title="Foundation series")
    except Exception as exc:
        logger.error("book_bot send_list failed: %s", exc)
        lines = "\n".join(f"• {r['title']} — {r.get('description', '')}" for r in rows)
        _send_text(phone, body + "\n\n" + lines + "\n\n_Reply 1, 2, 3 or 4._")
        return False


def _format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return f"+{digits}" if digits else phone


def _extract_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if 10 <= len(digits) <= 13:
        return digits
    return None


def _parse_choice(text: str) -> str | None:
    """Map a tapped id or typed reply to a book key, or '__set__', or None."""
    t = (text or "").strip().lower()
    if t in _SELECT_IDS:
        return _SELECT_IDS[t]
    if t in {"1", "1.", "1)"}:
        return "malayalam"
    if t in {"2", "2.", "2)"}:
        return "hindi"
    if t in {"3", "3.", "3)"}:
        return "english"
    if t in {"4", "4.", "4)"}:
        return _SET
    if "akshara" in t or "malayalam" in t:
        return "malayalam"
    if "vidya" in t or "hindi" in t:
        return "hindi"
    if "english" in t:
        return "english"
    if "all" in t or t == "set" or "three" in t or "3 book" in t:
        return _SET
    return None


def _parse_qty(text: str):
    t = (text or "").strip().lower()
    if t in _QTY_IDS:
        return _QTY_IDS[t]
    return bc.parse_qty(text)


def _is_add(text: str) -> bool:
    t = (text or "").strip().lower()
    return t == "bk_add" or "add" in t or t in {"more", "another"}


def _is_checkout(text: str) -> bool:
    t = (text or "").strip().lower()
    return t == "bk_checkout" or "checkout" in t or t in {"done", "pay", "next", "finish"}


# ── message senders (interactive) ─────────────────────────────────────────────

def _send_select_list(phone: str, addmore: bool = False) -> None:
    b = bc.BOOKS
    rows = [
        {"id": "bk_ml", "title": "Aksharamrutham", "description": f"Malayalam · ₹{b['malayalam']['price']:.0f}"},
        {"id": "bk_hi", "title": "Vidyamrut",      "description": f"Hindi · ₹{b['hindi']['price']:.0f}"},
        {"id": "bk_en", "title": "Easy English",   "description": f"English · ₹{b['english']['price']:.0f}"},
        {"id": "bk_set", "title": "All 3 — Set",   "description": f"All three books · ₹{bc.SET_PRICE}"},
    ]
    body = ("Tap to add another book:" if addmore
            else "📚 *Xtraa — Adithara Balappeduthu*\nFoundation books for early readers.\n\n"
                 f"Tap below to choose a book. _+ ₹{bc.COURIER} courier per order._")
    _send_list(phone, body, "📚 Choose a book", rows, header="Xtraa Books")


def _send_qty_buttons(phone: str, label: str) -> None:
    _send_buttons(
        phone,
        f"How many copies of *{label}*?\n\n_Tap a number, or type one (e.g. 5)._",
        [("qty_1", "1"), ("qty_2", "2"), ("qty_3", "3")],
    )


def _send_set_qty_buttons(phone: str) -> None:
    _send_buttons(
        phone,
        f"How many *sets* (all 3 books, ₹{bc.SET_PRICE}/set)?\n\n_Tap a number, or type one._",
        [("qty_1", "1"), ("qty_2", "2"), ("qty_3", "3")],
    )


def _cart_summary(items: dict) -> str:
    parts = []
    for ln in bc.line_items(items):
        name = ln["label"].split(" (")[0]
        parts.append(f"{name} × {ln['qty']}")
    return ", ".join(parts) if parts else "—"


def _send_addmore_buttons(phone: str, items: dict) -> None:
    totals = bc.compute_totals(items)
    _send_buttons(
        phone,
        f"🛒 *Your cart:* {_cart_summary(items)}\nSubtotal: ₹{totals['books_total']:.0f}\n\n"
        "Add another book, or checkout?",
        [("bk_add", "➕ Add book"), ("bk_checkout", "✅ Checkout")],
    )


def _send_phone_buttons(phone: str) -> None:
    _send_buttons(
        phone,
        f"Is *{_format_phone(phone)}* the right number for delivery updates?",
        [("ph_yes", "✅ Use this"), ("ph_edit", "✏️ Other number")],
    )


def _send_summary(phone: str, order: dict) -> None:
    items = order.get("items") or {}
    totals = bc.compute_totals(items)
    lines = "\n".join(
        f"• {ln['label'].split(' (')[0]} × {ln['qty']} = ₹{ln['line_total']:.0f}"
        for ln in bc.line_items(items)
    )
    body = (
        "🧾 *Order summary*\n\n"
        f"{lines}\n\n"
        f"Books: ₹{totals['books_total']:.0f}\n"
        f"Courier: ₹{totals['courier']:.0f}\n"
        f"*Total: ₹{totals['grand_total']:.0f}*\n\n"
        f"📍 {order.get('address', '')}\n"
        f"📞 {_format_phone(order.get('contact_phone', ''))}"
    )
    _send_buttons(phone, body, [("ord_yes", "✅ Confirm"), ("ord_no", "❌ Start over")])


def _payment_caption(order: dict) -> str:
    totals = bc.compute_totals(order.get("items") or {})
    return (
        f"💳 *Pay ₹{totals['grand_total']:.0f}* by scanning this UPI QR.\n\n"
        f"Order: {order.get('order_code')}\n\n"
        "After paying, *send a screenshot* of the payment confirmation here. "
        "We'll verify and confirm your order. 🙏"
    )


def _send_qr(phone: str, order: dict) -> bool:
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
                _dbc.log_message(phone, "outbound", "[UPI QR sent]", message_type="image")
            except Exception:
                pass
        return bool(ok)
    except Exception as exc:
        logger.error("book_bot: send_file QR failed: %s", exc)
        return False


# ── flow ──────────────────────────────────────────────────────────────────────

def _address_prompt(totals: dict) -> str:
    return (
        f"Great! Your order comes to *₹{totals['grand_total']:.0f}* "
        f"(incl. ₹{totals['courier']:.0f} courier).\n\n"
        "📍 Please *type your full delivery address* (house/street, place, "
        "district, PIN code)."
    )


def _start(phone: str, name: str | None, force_new: bool = False) -> list[str]:
    """Begin (or restart) a book order — sends the selection list."""
    active = {} if force_new else _dbc.get_active_book_order(phone)
    if active and active.get("status") in ("collecting", "awaiting_payment"):
        code = active["order_code"]
        _dbc.update_book_order(code, items={}, flow_cursor={}, status="collecting",
                               books_total=0, courier=bc.COURIER, grand_total=0,
                               address=None, contact_phone=None, payment_proof_url=None)
    elif active and active.get("status") == "payment_review":
        return [
            f"Your previous order *{active['order_code']}* is being confirmed. "
            "We'll message you shortly. To place a *new* order, reply *NEW*."
        ]
    else:
        code = _new_order_code()
        created = _dbc.create_book_order(code, phone, name)
        if not created:
            code = _new_order_code()
            _dbc.create_book_order(code, phone, name)

    _dbc.save_session(DB, phone, step="book_select", needs_human=False)
    _send_select_list(phone)
    return []


def _handle_select(phone: str, text: str, order: dict) -> list[str]:
    key = _parse_choice(text)
    if not key:
        _send_select_list(phone)
        return []
    code = order["order_code"]
    if key == _SET:
        _dbc.update_book_order(code, flow_cursor={"current": _SET})
        _dbc.save_session(DB, phone, step="book_qty")
        _send_set_qty_buttons(phone)
        return []
    _dbc.update_book_order(code, flow_cursor={"current": key})
    _dbc.save_session(DB, phone, step="book_qty")
    _send_qty_buttons(phone, bc.BOOKS[key]["label"])
    return []


def _handle_qty(phone: str, text: str, order: dict) -> list[str]:
    code = order["order_code"]
    cursor = order.get("flow_cursor") or {}
    current = cursor.get("current")
    if not current:
        _dbc.save_session(DB, phone, step="book_select")
        _send_select_list(phone)
        return []

    qty = _parse_qty(text)
    if qty is None:
        if current == _SET:
            _send_set_qty_buttons(phone)
        else:
            _send_qty_buttons(phone, bc.BOOKS[current]["label"])
        return []

    items = dict(order.get("items") or {})

    if current == _SET:
        items = {"malayalam": qty, "hindi": qty, "english": qty}
        totals = bc.compute_totals(items)
        _dbc.update_book_order(code, items=items, flow_cursor={},
                               books_total=totals["books_total"],
                               courier=totals["courier"],
                               grand_total=totals["grand_total"])
        _dbc.save_session(DB, phone, step="book_address")
        _send_text(phone, _address_prompt(totals))
        return []

    items[current] = qty
    _dbc.update_book_order(code, items=items, flow_cursor={})
    _dbc.save_session(DB, phone, step="book_addmore")
    _send_addmore_buttons(phone, items)
    return []


def _handle_addmore(phone: str, text: str, order: dict) -> list[str]:
    code = order["order_code"]
    if _is_add(text):
        _dbc.save_session(DB, phone, step="book_select")
        _send_select_list(phone, addmore=True)
        return []
    if _is_checkout(text):
        items = order.get("items") or {}
        totals = bc.compute_totals(items)
        _dbc.update_book_order(code, books_total=totals["books_total"],
                               courier=totals["courier"],
                               grand_total=totals["grand_total"])
        _dbc.save_session(DB, phone, step="book_address")
        _send_text(phone, _address_prompt(totals))
        return []
    _send_addmore_buttons(phone, order.get("items") or {})
    return []


def _handle_address(phone: str, text: str, order: dict) -> list[str]:
    address = (text or "").strip()
    if len(address) < 10:
        _send_text(phone, "That address looks too short. Please type your *full "
                          "delivery address* including place and PIN code. 📍")
        return []
    _dbc.update_book_order(order["order_code"], address=address)
    _dbc.save_session(DB, phone, step="book_phone")
    _send_phone_buttons(phone)
    return []


def _handle_phone(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip().lower()
    if t == "ph_edit" or t in {"other", "change", "edit", "✏️ other number"}:
        _send_text(phone, "Please *type the correct phone number* (10 digits). 📞")
        return []
    if t == "ph_yes" or t in _AFFIRM or t == "✅ use this":
        contact = re.sub(r"\D", "", phone)
    else:
        extracted = _extract_phone(text)
        if not extracted:
            _send_phone_buttons(phone)
            return []
        contact = extracted
    _dbc.update_book_order(order["order_code"], contact_phone=contact)
    _dbc.save_session(DB, phone, step="book_summary")
    _send_summary(phone, _dbc.get_book_order(order["order_code"]))
    return []


def _handle_summary(phone: str, text: str, order: dict) -> list[str]:
    code = order["order_code"]
    t = (text or "").strip().lower()
    if t == "ord_no" or t in _NEGATE:
        _dbc.update_book_order(code, items={}, flow_cursor={},
                               books_total=0, courier=bc.COURIER, grand_total=0,
                               address=None, contact_phone=None)
        _dbc.save_session(DB, phone, step="book_select")
        _send_text(phone, "No problem — let's start over.")
        _send_select_list(phone)
        return []
    if t == "ord_yes" or t in _AFFIRM:
        _dbc.update_book_order(code, status="awaiting_payment")
        _dbc.save_session(DB, phone, step="book_pay")
        refreshed = _dbc.get_book_order(code)
        if not _send_qr(phone, refreshed):
            totals = bc.compute_totals(refreshed.get("items") or {})
            _send_text(phone, f"Please pay *₹{totals['grand_total']:.0f}* to our UPI "
                              "and send a screenshot here. (Reply *QR* to retry the image.)")
        return []
    _send_summary(phone, order)
    return []


def _handle_pay(phone: str, text: str, order: dict) -> list[str]:
    if (text or "").strip().lower() == "qr":
        _send_qr(phone, order)
        return []
    _send_text(phone, "Please complete the UPI payment and *send a screenshot* of "
                      "the confirmation here. 🙏\n\nReply *QR* if you need the QR again.")
    return []


# ── public entry points ───────────────────────────────────────────────────────

def maybe_handle_book(phone: str, text: str, name: str | None = None) -> list[str] | None:
    """Route a message through the book flow.

    Returns [] if handled (replies sent as side effects via interactive senders),
    or None if the message is not part of a book conversation.
    """
    session = _dbc.get_session(DB, phone) or {}
    step = session.get("step") or ""

    if step not in _BOOK_STEPS:
        if is_book_trigger(text) and not _in_print_flow(session):
            return _start(phone, name)
        return None

    if (text or "").strip().lower() == "new":
        return _start(phone, name, force_new=True)

    order = _dbc.get_active_book_order(phone)
    if not order:
        _dbc.save_session(DB, phone, step="book_select")
        return _start(phone, name)

    handlers = {
        "book_select":  _handle_select,
        "book_qty":     _handle_qty,
        "book_addmore": _handle_addmore,
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
    """Handle an incoming image while the customer is awaiting payment."""
    session = _dbc.get_session(DB, phone) or {}
    if session.get("step") != "book_pay":
        return None
    order = _dbc.get_active_book_order(phone)
    if not order or order.get("status") not in ("awaiting_payment", "payment_review"):
        return None

    url = _dbc.upload_book_payment_proof(order["order_code"], content, mime_type)
    _dbc.update_book_order(order["order_code"], status="payment_review",
                           payment_proof_url=url or None)
    _dbc.save_session(DB, phone, step="book_pay", needs_human=True)
    return [
        f"✅ Got your payment screenshot for *{order['order_code']}*.\n\n"
        "We're verifying it now — you'll receive your order confirmation "
        "shortly. Thank you! 🙏"
    ]


def confirm_book_order(order_code: str) -> dict:
    """Owner confirms payment for an order. Sends the customer confirmation."""
    order = _dbc.get_book_order(order_code)
    if not order:
        return {"ok": False, "error": "Order not found"}
    if order.get("status") == "confirmed":
        return {"ok": True, "already_confirmed": True, "order": order}

    from datetime import datetime, timezone
    _dbc.update_book_order(order_code, status="confirmed",
                           confirmed_at=datetime.now(timezone.utc).isoformat())

    phone = order["phone"]
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
