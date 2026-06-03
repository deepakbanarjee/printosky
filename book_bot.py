"""Xtraa book campaign — WhatsApp conversational order flow (button-driven).

Cloud-only (runs in the Vercel webhook). Order data lives in the `book_orders`
table; the `bot_sessions.step` column drives state (book_* steps). Pure pricing
and parsing logic lives in book_catalog.py.

The customer taps WhatsApp buttons / list rows (typed fallbacks still accepted):

    enquiry → book_select (combo list: pick 1, 2 or all 3 in one tap)
            → book_qty   (per-book quantity, buttons, one book at a time)
            → book_address (typed)
            → book_phone (buttons)
            → book_summary (buttons: Confirm / Edit / Cancel)
                 └─ Edit → book_edit → edit books / address / phone → back to summary
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
import anu_parser

logger = logging.getLogger(__name__)

DB = "supabase"  # db_path is ignored in cloud mode

_TRIGGER_WORDS = {
    "book", "books", "xtraa", "xtra", "adithara", "balappeduthu",
    "foundation", "aksharamrutham", "vidyamrut", "അടിത്തറ",
}

_AFFIRM = {
    "yes", "y", "ok", "okay", "confirm", "confirmed", "correct", "right",
    "sure", "ya", "yeah", "yep", "proceed", "done", "✓", "👍",
}
_NEGATE = {"no", "cancel", "wrong", "restart", "redo", "start over"}

_QR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "brand-kit", "printosky-payment-qr.png")

# Staff member who verifies UPI payments. Payment screenshots are forwarded here;
# she taps Confirm/Reject. International format, no '+'.
# Anu is ALSO the Xtraa/Divya order forwarder — she sends new orders here using
# the ORDER template below (see _handle_anu_order / book_catalog.parse_anu_order).
VERIFIER_PHONE = os.environ.get("PAYMENT_VERIFIER_PHONE", "919072034907")

# The template Anu uses to forward a Divya order (echoed back to her on errors).
ANU_TEMPLATE = (
    "ORDER\n"
    "Name: <customer name>\n"
    "Phone: <10-digit number>\n"
    "Address: <full address + PIN>\n"
    "Aksharamrutham: <qty>\n"
    "Vidyamrut: <qty>\n"
    "Easy English: <qty>\n"
    "Payment: pending / divya\n"
    "Delivery: courier / office"
)

_BOOK_STEPS = {"book_select", "book_qty", "book_address", "book_phone",
               "book_summary", "book_pay", "book_edit", "book_edit_address",
               "book_edit_phone", "post_order", "post_order_ask"}

# Selection list-row ids → the set of book keys they choose (one tap = multi-select).
_SELECT_IDS = {
    "bk_ml":     ["malayalam"],
    "bk_hi":     ["hindi"],
    "bk_en":     ["english"],
    "bk_ml_hi":  ["malayalam", "hindi"],
    "bk_ml_en":  ["malayalam", "english"],
    "bk_hi_en":  ["hindi", "english"],
    "bk_all":    ["malayalam", "hindi", "english"],
}
_QTY_IDS = {"qty_1": 1, "qty_2": 2, "qty_3": 3}


# ── helpers ───────────────────────────────────────────────────────────────────

def is_book_trigger(text: str) -> bool:
    if not text:
        return False
    t = text.strip().lower()
    words = set(re.split(r"[^\wഀ-ൿ]+", t))
    return bool(words & _TRIGGER_WORDS)


def _in_print_flow(session: dict) -> bool:
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
    from whatsapp_notify import _send
    _send(phone, message)


def _send_buttons(phone: str, body: str, buttons: list, header: str | None = None) -> bool:
    try:
        from whatsapp_notify import send_buttons
        return send_buttons(phone, body, buttons, header=header)
    except Exception as exc:
        logger.error("book_bot send_buttons failed: %s", exc)
        _send_text(phone, body + "\n\n" + " / ".join(t for _, t in buttons))
        return False


def _send_list(phone: str, body: str, button_text: str, rows: list,
               header: str | None = None) -> bool:
    try:
        from whatsapp_notify import send_list
        return send_list(phone, body, button_text, rows, header=header,
                         section_title="Choose books")
    except Exception as exc:
        logger.error("book_bot send_list failed: %s", exc)
        lines = "\n".join(f"• {r['title']} — {r.get('description', '')}" for r in rows)
        _send_text(phone, body + "\n\n" + lines + "\n\n_Reply 1, 2, 3, '1,3' or 'all'._")
        return False


def _format_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return f"+{digits}" if digits else phone


def _extract_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", text or "")
    if 10 <= len(digits) <= 13:
        return digits
    return None


def _has_pincode(text: str) -> bool:
    """True if the text contains an isolated 6-digit Indian PIN code."""
    return bool(re.search(r"(?<!\d)[1-9]\d{5}(?!\d)", text or ""))


def _parse_choice(text: str) -> list[str] | None:
    """A selection (button id or typed) → ordered list of book keys, or None."""
    t = (text or "").strip().lower()
    if t in _SELECT_IDS:
        return list(_SELECT_IDS[t])
    keys = bc.parse_selection(text)   # handles "1", "1,3", "all", "4"
    if keys:
        return keys
    if "akshara" in t or "malayalam" in t:
        return ["malayalam"]
    if "vidya" in t or "hindi" in t:
        return ["hindi"]
    if "english" in t:
        return ["english"]
    return None


def _parse_qty(text: str):
    t = (text or "").strip().lower()
    if t in _QTY_IDS:
        return _QTY_IDS[t]
    return bc.parse_qty(text)


# ── interactive senders ───────────────────────────────────────────────────────

def _send_select_list(phone: str, edit: bool = False) -> None:
    b = bc.BOOKS
    p = {k: b[k]["price"] for k in ("malayalam", "hindi", "english")}
    rows = [
        {"id": "bk_ml", "title": "Aksharamrutham", "description": f"Malayalam · ₹{p['malayalam']:.0f}"},
        {"id": "bk_hi", "title": "Vidyamrut",      "description": f"Hindi · ₹{p['hindi']:.0f}"},
        {"id": "bk_en", "title": "Easy English",   "description": f"English · ₹{p['english']:.0f}"},
        {"id": "bk_ml_hi", "title": "Malayalam + Hindi",
         "description": f"Aksharamrutham + Vidyamrut · ₹{p['malayalam'] + p['hindi']:.0f}"},
        {"id": "bk_ml_en", "title": "Malayalam + English",
         "description": f"Aksharamrutham + Easy English · ₹{p['malayalam'] + p['english']:.0f}"},
        {"id": "bk_hi_en", "title": "Hindi + English",
         "description": f"Vidyamrut + Easy English · ₹{p['hindi'] + p['english']:.0f}"},
        {"id": "bk_all", "title": "All 3 books (Set)",
         "description": f"All three · ₹{bc.SET_PRICE} when 1 of each"},
    ]
    if edit:
        body = "Pick your books again (single, a pair, or all three):"
    else:
        body = ("📚 *Xtraa — Adithara Balappeduthu*\nFoundation books for early "
                "readers.\n\nChoose the books you'd like — pick *one row* for a "
                "single book, a pair, or all three.\n\n"
                f"_+ ₹{bc.COURIER} courier per order._")
    _send_list(phone, body, "📚 Choose books", rows, header="Xtraa Books")


def _send_qty_buttons(phone: str, label: str) -> None:
    _send_buttons(
        phone,
        f"How many copies of *{label}*?\n\n_Tap a number, or type one (e.g. 5)._",
        [("qty_1", "1"), ("qty_2", "2"), ("qty_3", "3")],
    )


def _send_phone_buttons(phone: str) -> None:
    _send_buttons(
        phone,
        f"Is *{_format_phone(phone)}* the right number for delivery updates?",
        [("ph_yes", "✅ Use this"), ("ph_edit", "✏️ Other number")],
    )


def _summary_text(order: dict) -> str:
    items = order.get("items") or {}
    totals = bc.compute_totals(items)
    lines = "\n".join(
        f"• {ln['label'].split(' (')[0]} × {ln['qty']} = ₹{ln['line_total']:.0f}"
        for ln in bc.line_items(items)
    )
    return (
        "🧾 *Order summary*\n\n"
        f"{lines}\n\n"
        f"Books: ₹{totals['books_total']:.0f}\n"
        f"Courier: ₹{totals['courier']:.0f}\n"
        f"*Total: ₹{totals['grand_total']:.0f}*\n\n"
        f"📍 {order.get('address', '')}\n"
        f"📞 {_format_phone(order.get('contact_phone', ''))}"
    )


def _send_summary(phone: str, order: dict) -> None:
    _send_buttons(phone, _summary_text(order),
                  [("ord_yes", "✅ Confirm"), ("ord_edit", "✏️ Edit"), ("ord_no", "❌ Cancel")])


def _send_edit_menu(phone: str) -> None:
    _send_buttons(phone, "What would you like to edit?",
                  [("ed_books", "📚 Books & qty"), ("ed_addr", "📍 Address"), ("ed_phone", "📞 Phone")])


def _send_pay_buttons(phone: str) -> None:
    _send_buttons(
        phone,
        "After paying, *send a screenshot* here.\n\nNeed to change something "
        "before paying?",
        [("pay_edit", "✏️ Edit order"), ("pay_cancel", "❌ Cancel order")],
    )


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


def _cart_line(items: dict) -> str:
    parts = [f"{bc.BOOKS[k]['label'].split(' (')[0]} × {q}"
             for k, q in (items or {}).items() if q and k in bc.BOOKS]
    return ", ".join(parts) if parts else "—"


def _forward_to_verifier(order: dict, content: bytes, mime_type: str) -> None:
    """Forward a payment screenshot to the verifier (Anu) with Confirm/Reject."""
    code   = order.get("order_code")
    totals = bc.compute_totals(order.get("items") or {})
    caption = (
        "💳 *Payment to verify*\n"
        f"Order: {code}\n"
        f"Amount: ₹{totals['grand_total']:.0f}\n"
        f"Customer: +{re.sub(r'[^0-9]', '', order.get('phone', ''))}\n"
        f"Items: {_cart_line(order.get('items') or {})}\n"
        f"📍 {order.get('address', '')}"
    )
    try:
        from whatsapp_notify import send_file
        send_file(VERIFIER_PHONE, content, mime_type or "image/jpeg",
                  "payment.jpg", caption=caption)
    except Exception as exc:
        logger.error("verifier screenshot forward failed: %s", exc)
    _send_buttons(
        VERIFIER_PHONE, f"Is this payment received for *{code}*?",
        [(f"vconf_{code}", "✅ Confirm"), (f"vrej_{code}", "❌ Reject")],
    )


# ── flow ──────────────────────────────────────────────────────────────────────

def _address_prompt(totals: dict) -> str:
    return (
        f"Great! Your order comes to *₹{totals['grand_total']:.0f}* "
        f"(incl. ₹{totals['courier']:.0f} courier).\n\n"
        "📍 Please *type your full delivery address* (house/street, place, "
        "district, PIN code)."
    )


def _begin_counting(phone: str, order_code: str, keys: list, editing: bool) -> None:
    """Initialise the per-book quantity queue and ask for the first book."""
    items = {k: 0 for k in keys}
    _dbc.update_book_order(order_code, items=items,
                           flow_cursor={"queue": keys[1:], "current": keys[0],
                                        "editing": editing})
    _dbc.save_session(DB, phone, step="book_qty")
    _send_qty_buttons(phone, bc.BOOKS[keys[0]]["label"])


def _start(phone: str, name: str | None, force_new: bool = False) -> list[str]:
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
    keys = _parse_choice(text)
    if not keys:
        _send_select_list(phone)
        return []
    editing = bool((order.get("flow_cursor") or {}).get("editing"))
    _begin_counting(phone, order["order_code"], keys, editing)
    return []


def _handle_qty(phone: str, text: str, order: dict) -> list[str]:
    code = order["order_code"]
    cursor = order.get("flow_cursor") or {}
    current = cursor.get("current")
    queue = list(cursor.get("queue") or [])
    editing = bool(cursor.get("editing"))
    if not current:
        _dbc.save_session(DB, phone, step="book_select")
        _send_select_list(phone)
        return []

    qty = _parse_qty(text)
    if qty is None:
        _send_qty_buttons(phone, bc.BOOKS[current]["label"])
        return []

    items = dict(order.get("items") or {})
    items[current] = qty

    if queue:
        nxt = queue.pop(0)
        _dbc.update_book_order(code, items=items,
                               flow_cursor={"queue": queue, "current": nxt, "editing": editing})
        _dbc.save_session(DB, phone, step="book_qty")
        _send_qty_buttons(phone, bc.BOOKS[nxt]["label"])
        return []

    # All books counted.
    totals = bc.compute_totals(items)
    _dbc.update_book_order(code, items=items, flow_cursor={},
                           books_total=totals["books_total"],
                           courier=totals["courier"],
                           grand_total=totals["grand_total"])
    if editing:
        _dbc.save_session(DB, phone, step="book_summary")
        _send_summary(phone, _dbc.get_book_order(code))
        return []
    _dbc.save_session(DB, phone, step="book_address")
    _send_text(phone, _address_prompt(totals))
    return []


def _handle_address(phone: str, text: str, order: dict) -> list[str]:
    address = (text or "").strip()
    if len(address) < 10:
        _send_text(phone, "That address looks too short. Please type your *full "
                          "delivery address* including place and PIN code. 📍")
        return []
    if not _has_pincode(address):
        _send_text(phone, "Please include your *6-digit PIN code* in the address "
                          "(we can't ship without it). 📍")
        return []
    _dbc.update_book_order(order["order_code"], address=address)
    _dbc.save_session(DB, phone, step="book_phone")
    _send_phone_buttons(phone)
    return []


def _resolve_phone(phone: str, text: str) -> str | None:
    t = (text or "").strip().lower()
    if t == "ph_yes" or t in _AFFIRM or t == "✅ use this":
        return re.sub(r"\D", "", phone)
    return _extract_phone(text)


def _handle_phone(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip().lower()
    if t == "ph_edit" or t in {"other", "change", "edit", "✏️ other number"}:
        _send_text(phone, "Please *type the correct phone number* (10 digits). 📞")
        return []
    contact = _resolve_phone(phone, text)
    if not contact:
        _send_phone_buttons(phone)
        return []
    _dbc.update_book_order(order["order_code"], contact_phone=contact)
    _dbc.save_session(DB, phone, step="book_summary")
    _send_summary(phone, _dbc.get_book_order(order["order_code"]))
    return []


def _handle_summary(phone: str, text: str, order: dict) -> list[str]:
    code = order["order_code"]
    t = (text or "").strip().lower()
    if t == "ord_edit" or "edit" in t:
        _dbc.save_session(DB, phone, step="book_edit")
        _send_edit_menu(phone)
        return []
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
        _send_pay_buttons(phone)
        return []
    _send_summary(phone, order)
    return []


def _handle_edit(phone: str, text: str, order: dict) -> list[str]:
    code = order["order_code"]
    t = (text or "").strip().lower()
    if t == "ed_books" or "book" in t:
        _dbc.update_book_order(code, flow_cursor={"editing": True})
        _dbc.save_session(DB, phone, step="book_select")
        _send_select_list(phone, edit=True)
        return []
    if t == "ed_addr" or "address" in t:
        _dbc.save_session(DB, phone, step="book_edit_address")
        _send_text(phone, "📍 Please *type the new delivery address*.")
        return []
    if t == "ed_phone" or "phone" in t or "number" in t:
        _dbc.save_session(DB, phone, step="book_edit_phone")
        _send_phone_buttons(phone)
        return []
    _send_edit_menu(phone)
    return []


def _handle_edit_address(phone: str, text: str, order: dict) -> list[str]:
    address = (text or "").strip()
    if len(address) < 10:
        _send_text(phone, "That address looks too short. Please type your *full "
                          "delivery address* including place and PIN code. 📍")
        return []
    if not _has_pincode(address):
        _send_text(phone, "Please include your *6-digit PIN code* in the address "
                          "(we can't ship without it). 📍")
        return []
    _dbc.update_book_order(order["order_code"], address=address)
    _dbc.save_session(DB, phone, step="book_summary")
    _send_summary(phone, _dbc.get_book_order(order["order_code"]))
    return []


def _handle_edit_phone(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip().lower()
    if t == "ph_edit" or t in {"other", "change", "edit", "✏️ other number"}:
        _send_text(phone, "Please *type the correct phone number* (10 digits). 📞")
        return []
    contact = _resolve_phone(phone, text)
    if not contact:
        _send_phone_buttons(phone)
        return []
    _dbc.update_book_order(order["order_code"], contact_phone=contact)
    _dbc.save_session(DB, phone, step="book_summary")
    _send_summary(phone, _dbc.get_book_order(order["order_code"]))
    return []


def _handle_post_order(phone: str, text: str) -> list[str]:
    """First message after a confirmed order → offer further help."""
    _dbc.save_session(DB, phone, step="post_order_ask")
    _send_buttons(phone, "Is there anything else we can help you with?",
                  [("po_yes", "✅ Yes"), ("po_no", "❌ No, I'm done")])
    return []


def _handle_post_order_ask(phone: str, text: str, name: str | None) -> list[str]:
    t = (text or "").strip().lower()
    if t == "po_no" or t in _NEGATE:
        try:
            _dbc.clear_session(DB, phone)
        except Exception:
            pass
        _send_text(phone, "Thank you for shopping with *Printosky × Xtraa*! 🙏 "
                          "Have a great day. 📚")
        return []
    if t == "po_yes" or t in _AFFIRM:
        # Start from the top: clear state and show the general help menu.
        try:
            _dbc.clear_session(DB, phone)
        except Exception:
            pass
        _send_text(phone, "👍 *How can we help?*\n\n"
                          "📄 *Printouts* — send your PDF or document here.\n"
                          "📚 *Xtraa books* — reply *books*.\n"
                          "🧑‍💼 *Talk to staff* — reply *agent*.")
        return []
    _send_buttons(phone, "Anything else? Tap *Yes* or *No*.",
                  [("po_yes", "✅ Yes"), ("po_no", "❌ No, I'm done")])
    return []


def _handle_pay(phone: str, text: str, order: dict) -> list[str]:
    code = order["order_code"]
    t = (text or "").strip().lower()
    if t == "qr":
        _send_qr(phone, order)
        _send_pay_buttons(phone)
        return []
    if t == "pay_edit" or t == "ord_edit" or "edit" in t:
        # Re-open the order for editing before payment.
        _dbc.update_book_order(code, status="collecting")
        _dbc.save_session(DB, phone, step="book_edit")
        _send_edit_menu(phone)
        return []
    if t == "pay_cancel" or "cancel" in t or t in _NEGATE:
        _dbc.update_book_order(code, status="cancelled")
        try:
            _dbc.clear_session(DB, phone)
        except Exception:
            pass
        _send_text(phone, "❌ Your order has been *cancelled*. Reply *books* "
                          "anytime to start a new order. 🙏")
        return []
    _send_text(phone, "Please complete the UPI payment and *send a screenshot* of "
                      "the confirmation here. 🙏")
    _send_pay_buttons(phone)
    return []


# ── public entry points ───────────────────────────────────────────────────────

def maybe_handle_book(phone: str, text: str, name: str | None = None) -> list[str] | None:
    session = _dbc.get_session(DB, phone) or {}
    step = session.get("step") or ""

    if step not in _BOOK_STEPS:
        if is_book_trigger(text) and not _in_print_flow(session):
            return _start(phone, name)
        return None

    if step == "post_order":
        return _handle_post_order(phone, text)
    if step == "post_order_ask":
        return _handle_post_order_ask(phone, text, name)

    if (text or "").strip().lower() == "new":
        return _start(phone, name, force_new=True)

    order = _dbc.get_active_book_order(phone)
    if not order:
        _dbc.save_session(DB, phone, step="book_select")
        return _start(phone, name)

    handlers = {
        "book_select":       _handle_select,
        "book_qty":          _handle_qty,
        "book_address":      _handle_address,
        "book_phone":        _handle_phone,
        "book_summary":      _handle_summary,
        "book_edit":         _handle_edit,
        "book_edit_address": _handle_edit_address,
        "book_edit_phone":   _handle_edit_phone,
        "book_pay":          _handle_pay,
    }
    handler = handlers.get(step)
    if not handler:
        _dbc.save_session(DB, phone, step="book_select")
        return _start(phone, name)
    return handler(phone, text, order)


def handle_payment_proof(phone: str, content: bytes, mime_type: str) -> list[str] | None:
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
    # Forward the screenshot to the verifier (Anu) for Confirm/Reject.
    try:
        _forward_to_verifier(_dbc.get_book_order(order["order_code"]), content, mime_type)
    except Exception as exc:
        logger.error("forward to verifier failed for %s: %s", order["order_code"], exc)
    return [
        f"✅ Got your payment screenshot for *{order['order_code']}*.\n\n"
        "We're verifying it now — you'll receive your order confirmation "
        "shortly. Thank you! 🙏"
    ]


def _abandoned_message(order: dict) -> str:
    items = order.get("items") or {}
    have = [(k, q) for k, q in items.items() if q and k in bc.BOOKS]
    line = ""
    if have:
        cart = ", ".join(f"{bc.BOOKS[k]['label'].split(' (')[0]} × {q}" for k, q in have)
        line = f"\n\n🛒 In your cart: {cart}"
    return (
        "👋 *Did you still want the Xtraa books?*\n"
        "You started an order but didn't finish it." + line +
        "\n\nReply *books* to continue — it only takes a minute! 🙏\n"
        "_Aksharamrutham · Vidyamrut · Easy English_"
    )


def send_abandoned_reminders(idle_hours: int = 2) -> dict:
    """Nudge customers who started a book order but went quiet (within the 24h
    WhatsApp window). One reminder per cart. Returns {carts, reminded}."""
    carts = _dbc.find_abandoned_book_carts(idle_hours=idle_hours)
    reminded = 0
    for o in carts:
        try:
            _send_text(o["phone"], _abandoned_message(o))
            _dbc.mark_abandoned_reminded(o["order_code"])
            reminded += 1
        except Exception as exc:
            logger.error("abandoned reminder failed for %s: %s", o.get("order_code"), exc)
    return {"carts": len(carts), "reminded": reminded}


def _handle_anu_order(parsed: dict) -> None:
    """Create a Divya-forwarded order from a parsed ORDER template, reply to Anu.

    Divya's orders print on receipt — they are created as 'confirmed' immediately
    and never wait for payment. Anu gets a summary + a one-tap Cancel.
    """
    if not parsed.get("ok"):
        problems = "\n".join(f"• {e}" for e in (parsed.get("errors") or ["Couldn't read the order"]))
        _send_text(VERIFIER_PHONE,
                   "⚠️ I couldn't save that order:\n" + problems +
                   "\n\nPlease resend using this template:\n\n" + ANU_TEMPLATE)
        return

    items   = parsed["items"]
    deliv   = parsed["delivery_method"]
    totals  = bc.compute_totals(items)
    courier = 0.0 if deliv == "xtraa_office" else totals["courier"]
    grand   = totals["books_total"] + courier
    commission = bc.commission_for(items)

    from datetime import datetime
    code = f"XTR-{datetime.now().strftime('%Y%m%d')}-{os.urandom(4).hex().upper()}"
    row = _dbc.create_walk_in_order(
        code, parsed["name"], parsed["phone"], parsed["address"], items,
        totals["books_total"], courier, grand,
        payment_mode="", status="confirmed",
        commission=commission, payment_collected_by=parsed["payment_collected_by"],
        delivery_method=deliv, via_divya=True, source="divya",
    )
    if not row:
        _send_text(VERIFIER_PHONE, "⚠️ Something went wrong saving the order — please try again.")
        return

    pay_label = {"divya":   "Divya collected",
                 "oxygen":  "Oxygen collected",
                 "pending": "Pending (we collect)"}.get(parsed["payment_collected_by"],
                                                        parsed["payment_collected_by"])
    deliv_label = "Xtraa office" if deliv == "xtraa_office" else "Courier"
    summary = (
        f"✅ *Order saved & queued:* {code}\n"
        f"{parsed['name']} · +{parsed['phone']}\n"
        f"{_cart_line(items)}\n"
        f"Books ₹{totals['books_total']:.0f}"
        + (f" + Courier ₹{courier:.0f}" if courier else "")
        + f" = *₹{grand:.0f}*\n"
        f"Delivery: {deliv_label}\n"
        f"Payment: {pay_label}\n"
        f"Divya commission: ₹{commission:.0f}\n\n"
        "Printing now — no need to wait for payment. If anything's wrong, tap Cancel."
    )
    _send_buttons(VERIFIER_PHONE, summary, [(f"acanc_{code}", "❌ Cancel order")])


def _cancel_divya_order(code: str) -> None:
    """Cancel a just-created Divya order at Anu's request."""
    order = _dbc.get_book_order(code)
    if not order:
        _send_text(VERIFIER_PHONE, f"⚠️ Couldn't find order *{code}* to cancel.")
        return
    _dbc.update_book_order(code, status="cancelled")
    _send_text(VERIFIER_PHONE, f"🗑️ Cancelled *{code}* — it won't be printed or couriered.")


def _new_order_code() -> str:
    from datetime import datetime
    return f"XTR-{datetime.now().strftime('%Y%m%d')}-{os.urandom(4).hex().upper()}"


def _divya_summary(code, name, phone, items, courier, grand, commission,
                   payment_collected_by, delivery_method) -> str:
    pay_label = {"divya": "Divya collected", "oxygen": "Oxygen collected",
                 "pending": "Pending (we collect)"}.get(payment_collected_by, payment_collected_by)
    deliv_label = "Xtraa office" if delivery_method == "xtraa_office" else "Courier"
    books_total = grand - courier
    return (
        f"✅ *Order saved & queued:* {code}\n"
        f"{name} · +{phone}\n"
        f"{_cart_line(items)}\n"
        f"Books ₹{books_total:.0f}"
        + (f" + Courier ₹{courier:.0f}" if courier else "")
        + f" = *₹{grand:.0f}*\n"
        f"Delivery: {deliv_label}\n"
        f"Payment: {pay_label}\n"
        f"Divya commission: ₹{commission:.0f}\n\n"
        "Printing now — no need to wait for payment. If anything's wrong, tap Cancel."
    )


def _create_divya_confirmed(code, name, phone, address, items,
                            payment_collected_by="pending", delivery_method="courier") -> None:
    """Create a confirmed Divya order from a clear, complete parse, and ping Anu."""
    totals  = bc.compute_totals(items)
    courier = 0.0 if delivery_method == "xtraa_office" else totals["courier"]
    grand   = totals["books_total"] + courier
    commission = bc.commission_for(items)
    row = _dbc.create_walk_in_order(
        code, name, phone, address, items, totals["books_total"], courier, grand,
        payment_mode="", status="confirmed", commission=commission,
        payment_collected_by=payment_collected_by, delivery_method=delivery_method,
        via_divya=True, source="divya",
    )
    if not row:
        _send_text(VERIFIER_PHONE, "⚠️ Something went wrong saving the order — please try again.")
        return
    _send_buttons(
        VERIFIER_PHONE,
        _divya_summary(code, name, phone, items, courier, grand, commission,
                       payment_collected_by, delivery_method),
        [(f"acanc_{code}", "❌ Cancel order")],
    )


# Buttons offered when asking Anu which book a forwarded order is for.
_BOOK_BUTTONS = [("abook_malayalam", "📕 Aksharamrutham"),
                 ("abook_hindi",     "📗 Vidyamrut"),
                 ("abook_english",   "📘 Easy English")]


def _parse_book_choice(text: str, copies: int) -> dict:
    """Parse Anu's 'which book' reply into an items dict. Returns {} if unclear."""
    t = (text or "").strip().lower()
    bm = re.match(r"^abook_(malayalam|hindi|english)$", t)
    if bm:
        return {bm.group(1): copies}
    keys = bc.parse_selection(t)
    if not keys:
        alias = {"aksharamrutham": "malayalam", "akshara": "malayalam",
                 "vidyamrut": "hindi", "vidya": "hindi",
                 "easy english": "english", "english": "english",
                 "malayalam": "malayalam", "hindi": "hindi"}
        keys = list(dict.fromkeys(v for k, v in alias.items() if k in t)) or None
    if not keys:
        return {}
    return {k: copies for k in keys}


def _handle_anu_order_freeform(text: str) -> None:
    """Auto-detect + LLM-parse a free-form teacher forward, then create or clarify."""
    # Cheap gate: skip the LLM on obvious non-orders (short chit-chat).
    if len(text.strip()) < 15 or (len(re.findall(r"\d", text)) < 8 and text.count("\n") < 2):
        return
    parsed = anu_parser.parse_order_message(text)
    if not parsed.get("is_order"):
        return
    name    = (parsed.get("name") or "").strip()
    phone   = re.sub(r"\D", "", parsed.get("phone") or "")
    address = ", ".join(p for p in [(parsed.get("address") or "").strip(),
                                    (parsed.get("pincode") or "").strip()] if p)
    copies  = int(parsed.get("copies") or 1) or 1
    if not name or len(phone) < 10:
        _send_text(VERIFIER_PHONE,
                   "📕 I read an order but couldn't get a clear name + 10-digit phone — "
                   "please resend it.")
        return
    books_list = [b for b in (parsed.get("books") or []) if b.get("title")]
    code = _new_order_code()

    if parsed.get("needs_clarification") or not books_list:
        # Park the order (no book yet) and ask Anu which book.
        courier = float(bc.COURIER)
        _dbc.create_walk_in_order(
            code, name, phone, address, {}, 0.0, courier, courier,
            payment_mode="", status="awaiting_clarification", commission=0.0,
            payment_collected_by="pending", delivery_method="courier",
            via_divya=True, source="divya",
        )
        _dbc.save_session(DB, VERIFIER_PHONE, step="anu_clarify_book", job_id=code, copies=copies)
        q = (parsed.get("clarification") or f"Which book for {name}?").strip()
        _send_buttons(
            VERIFIER_PHONE,
            f"📕 Order for *{name}* · {copies} copy(s)\n{address}\n\n{q}\n"
            "_Tap the book — or for more than one reply like_ `1 3`.",
            _BOOK_BUTTONS,
        )
        return

    # Book is clear → create immediately.
    items: dict[str, int] = {}
    for b in books_list:
        items[b["title"]] = items.get(b["title"], 0) + int(b.get("qty") or copies or 1)
    _create_divya_confirmed(code, name, phone, address, items)


def _handle_anu_clarification(text: str, code: str) -> None:
    """Anu answered 'which book?' for a parked order — fill it in and confirm."""
    order = _dbc.get_book_order(code)
    if not order or order.get("status") != "awaiting_clarification":
        # Stale pointer — reset and treat the message as a fresh forward.
        _dbc.save_session(DB, VERIFIER_PHONE, step="", job_id="")
        _handle_anu_order_freeform(text)
        return
    sess   = _dbc.get_session(DB, VERIFIER_PHONE) or {}
    copies = int(sess.get("copies") or 1) or 1
    items  = _parse_book_choice(text, copies)
    if not items:
        _send_buttons(
            VERIFIER_PHONE,
            "Sorry, I didn't catch the book. Tap one — or reply like `1 3` for more than one:",
            _BOOK_BUTTONS,
        )
        return
    deliv      = order.get("delivery_method") or "courier"
    totals     = bc.compute_totals(items)
    courier    = 0.0 if deliv == "xtraa_office" else totals["courier"]
    grand      = totals["books_total"] + courier
    commission = bc.commission_for(items)
    _dbc.update_book_order(
        code, items=items, books_total=totals["books_total"], courier=courier,
        grand_total=grand, commission=commission, status="confirmed",
    )
    _dbc.save_session(DB, VERIFIER_PHONE, step="", job_id="")
    name  = order.get("name") or ""
    phone = order.get("contact_phone") or order.get("phone") or ""
    _send_buttons(
        VERIFIER_PHONE,
        _divya_summary(code, name, phone, items, courier, grand, commission,
                       order.get("payment_collected_by") or "pending", deliv),
        [(f"acanc_{code}", "❌ Cancel order")],
    )


def _handle_payment_verdict(action: str, code: str) -> None:
    """Apply Anu's Confirm/Reject on a customer payment screenshot."""
    if action == "vconf":
        res = confirm_book_order(code)
        if res.get("ok"):
            _send_text(VERIFIER_PHONE, f"✅ Confirmed *{code}* — customer notified. Thanks Anu! 🙏")
        else:
            _send_text(VERIFIER_PHONE, f"⚠️ Couldn't find order *{code}*.")
        return
    order = _dbc.get_book_order(code)
    if not order:
        _send_text(VERIFIER_PHONE, f"⚠️ Couldn't find order *{code}*.")
        return
    _dbc.update_book_order(code, status="awaiting_payment")
    cust = order.get("phone")
    if cust:
        _dbc.save_session(DB, cust, step="book_pay", needs_human=False)
        _send_text(cust, f"⚠️ We couldn't verify your payment for *{code}*. "
                         "Please *resend a clear screenshot* of the payment, "
                         "or contact us if you've already paid. 🙏")
    _send_text(VERIFIER_PHONE, f"❌ Rejected *{code}* — customer asked to resend.")


def handle_verifier_reply(sender: str, text: str) -> bool:
    """Route a message from Anu (payment verifier + Divya order forwarder).

    Precedence: payment Confirm/Reject, order Cancel, a pending 'which book?'
    clarification, the rigid ORDER template, then a free-form teacher forward
    (auto-detected + LLM-parsed). Every message from Anu's number is consumed
    here (returns True) so it never falls into the customer flow.
    """
    if re.sub(r"\D", "", sender or "") != re.sub(r"\D", "", VERIFIER_PHONE):
        return False
    t = (text or "").strip()

    # 1. Payment verification (buttons or "confirm/reject XTR-...").
    action = code = None
    m = re.match(r"^(vconf|vrej)_(\S+)$", t)
    if m:
        action, code = m.group(1), m.group(2)
    else:
        m = re.match(r"(?i)^(confirm|approve|reject|deny)\s+(XTR-\S+)", t)
        if m:
            action = "vconf" if m.group(1).lower() in ("confirm", "approve") else "vrej"
            code = m.group(2).upper()
    if action and code:
        _handle_payment_verdict(action, code)
        return True

    # 2. Cancel a just-created Divya order.
    cm = re.match(r"^acanc_(\S+)$", t) or re.match(r"(?i)^cancel\s+(XTR-\S+)", t)
    if cm:
        _cancel_divya_order(cm.group(1).upper())
        return True

    # 3. Pending 'which book?' clarification for a parked order.
    sess = _dbc.get_session(DB, VERIFIER_PHONE) or {}
    if sess.get("step") == "anu_clarify_book" and sess.get("job_id"):
        _handle_anu_clarification(t, sess["job_id"])
        return True

    # 4. Rigid ORDER template (backward-compatible).
    parsed = bc.parse_anu_order(t)
    if parsed is not None:
        _handle_anu_order(parsed)
        return True

    # 5. Free-form teacher forward → LLM parse + create / clarify.
    _handle_anu_order_freeform(t)
    return True


def confirm_book_order(order_code: str) -> dict:
    order = _dbc.get_book_order(order_code)
    if not order:
        return {"ok": False, "error": "Order not found"}
    if order.get("status") == "confirmed":
        return {"ok": True, "already_confirmed": True, "order": order}

    from datetime import datetime, timezone
    _dbc.update_book_order(order_code, status="confirmed",
                           commission=bc.commission_for(order.get("items") or {}),
                           confirmed_at=datetime.now(timezone.utc).isoformat())

    phone = order["phone"]
    # Move the customer into the post-order state: their next message triggers
    # the "anything else?" follow-up (instead of leaving them with no session).
    try:
        _dbc.save_session(DB, phone, step="post_order", needs_human=False)
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
