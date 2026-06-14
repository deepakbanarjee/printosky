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
                        "brand-kit", "printosky-payment-qr-v2.png")

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

_BOOK_STEPS = {"book_select", "book_qty", "book_name", "book_address", "book_dtdc",
               "book_phone", "book_summary", "book_pay", "book_edit",
               "book_edit_name", "book_edit_address", "book_edit_dtdc",
               "book_edit_phone", "post_order", "post_order_ask"}

_DTDC_SKIP = {"no", "none", "skip", "nope", "any", "no preference", "don't know",
              "dont know", "na", "n/a", "-", "--", "nil", "നോ", "ഇല്ല", "dtdc_skip"}

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


# Ad-channel codes embedded in a tracked WhatsApp deep link, e.g.
#   wa.me/919495706405?text=BOOKS%20ig   (Instagram ad)
#   wa.me/919495706405?text=BOOKS%20fb   (Facebook ad)
# The customer taps the link, the pre-filled text arrives, and we learn the
# discovery channel WITHOUT asking. Only known codes are honoured, so ordinary
# text ("books please") never mis-tags.
_ACQ_CODES = {
    "ig": "instagram", "insta": "instagram", "instagram": "instagram",
    "fb": "facebook", "facebook": "facebook",
    "yt": "youtube", "youtube": "youtube",
    "divya": "divya", "teacher": "divya",
    "ref": "referral", "friend": "friend",
}


def _parse_acq(text: str) -> tuple:
    """Return ``(channel, campaign)`` from a tracked 'BOOKS …' message.

    - 'BOOKS #ig-reel-jan' → ('instagram', 'ig-reel-jan')  — per-campaign tag
    - 'BOOKS ig'           → ('instagram', None)           — fixed channel link
    - 'books please'       → (None, None)                  — untagged → ask later

    A '#tag' (emitted by the tag generator) is captured verbatim as the campaign
    and rolled up to a channel via its first segment; only known short codes are
    honoured otherwise, so ordinary chatter never mis-tags.
    """
    if not text:
        return (None, None)
    low = text.strip().lower()
    m = re.search(r"#([a-z0-9][a-z0-9\-]{0,39})", low)
    if m:
        tag = m.group(1).strip("-")
        if tag:
            from book_catalog import tag_channel
            return (tag_channel(tag), tag)
    for w in re.split(r"[^\w]+", low):
        chan = _ACQ_CODES.get(w)
        if chan:
            return (chan, None)
    return (None, None)


def _parse_acq_source(text: str) -> str | None:
    """Back-compat: the discovery channel only (see _parse_acq for the campaign)."""
    return _parse_acq(text)[0]


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
               header: str | None = None,
               section_title: str = "Choose books") -> bool:
    try:
        from whatsapp_notify import send_list
        return send_list(phone, body, button_text, rows, header=header,
                         section_title=section_title)
    except Exception as exc:
        logger.error("book_bot send_list failed: %s", exc)
        lines = "\n".join(f"• {r['title']} — {r.get('description', '')}" for r in rows)
        _send_text(phone, body + "\n\n" + lines + "\n\n_Reply with your choice._")
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
                f"_+ courier charged by weight (₹75+)._")
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
    name = order.get("name") or "—"
    dtdc = order.get("dtdc_center")
    dtdc_line = f"\n🏢 DTDC: {dtdc}" if dtdc else ""
    return (
        "🧾 *Order summary*\n\n"
        f"{lines}\n\n"
        f"Books: ₹{totals['books_total']:.0f}\n"
        f"Courier: ₹{totals['courier']:.0f}\n"
        f"*Total: ₹{totals['grand_total']:.0f}*\n\n"
        f"👤 {name}\n"
        f"📍 {order.get('address', '')}"
        f"{dtdc_line}\n"
        f"📞 {_format_phone(order.get('contact_phone', ''))}"
    )


def _send_summary(phone: str, order: dict) -> None:
    _send_buttons(phone, _summary_text(order),
                  [("ord_yes", "✅ Confirm"), ("ord_edit", "✏️ Edit"), ("ord_no", "❌ Cancel")])


def _send_edit_menu(phone: str) -> None:
    _send_list(
        phone, "What would you like to edit?", "✏️ Edit",
        [
            {"id": "ed_name",  "title": "👤 Recipient name"},
            {"id": "ed_books", "title": "📚 Books & qty"},
            {"id": "ed_addr",  "title": "📍 Delivery address"},
            {"id": "ed_dtdc",  "title": "🏢 DTDC center"},
            {"id": "ed_phone", "title": "📞 Phone number"},
        ],
        header="Edit order",
        section_title="Choose what to edit",
    )


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
        ok = send_file(phone, data, "image/png", "printosky-payment-qr-v2.png",
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


def _forward_to_verifier(order: dict, payment: dict, content: bytes, mime_type: str) -> None:
    """Forward ONE payment screenshot to Anu with Full / Part / Not-received.

    Buttons are keyed by the *payment row id* (not the order), so Anu can act on
    each screenshot independently and in any order — fixing the old behaviour
    where only the last screenshot was effectively confirmable.
    """
    code    = order.get("order_code")
    payid   = payment.get("id")
    totals  = bc.compute_totals(order.get("items") or {})
    grand   = totals["grand_total"]
    paid    = _dbc.book_amount_paid(code)          # verified so far (excludes this pending one)
    balance = grand - paid
    n       = len(_dbc.get_book_payments(code))     # which screenshot this is
    dtdc = order.get("dtdc_center")
    dtdc_line = f"\n🏢 DTDC: {dtdc}" if dtdc else ""
    caption = (
        "💳 *Payment to verify*\n"
        f"Order: {code}  (screenshot #{n})\n"
        f"Total ₹{grand:.0f} · Paid ₹{paid:.0f} · *Balance ₹{balance:.0f}*\n"
        f"Customer: {order.get('name') or '—'} "
        f"+{re.sub(r'[^0-9]', '', order.get('phone', ''))}\n"
        f"Items: {_cart_line(order.get('items') or {})}\n"
        f"📍 {order.get('address', '')}{dtdc_line}"
    )
    try:
        from whatsapp_notify import send_file
        send_file(VERIFIER_PHONE, content, mime_type or "image/jpeg",
                  "payment.jpg", caption=caption)
    except Exception as exc:
        logger.error("verifier screenshot forward failed: %s", exc)
    _send_buttons(
        VERIFIER_PHONE, f"Full or part payment for *{code}*?",
        [(f"pf_{payid}", f"✅ Full ₹{balance:.0f}"),
         (f"pp_{payid}", "➗ Part payment"),
         (f"pr_{payid}", "❌ Not received")],
    )


def _forward_payment_text_to_verifier(order: dict, payment: dict,
                                      raw_text: str, parsed: dict) -> None:
    """Forward a *pasted* payment confirmation (no screenshot) to Anu.

    Mirrors _forward_to_verifier but sends the customer's text instead of an
    image, reusing the same Full / Part / Not-received buttons so Anu's tap
    drives the existing verify -> confirm path.
    """
    code    = order.get("order_code")
    payid   = payment.get("id")
    grand   = bc.compute_totals(order.get("items") or {})["grand_total"]
    paid    = _dbc.book_amount_paid(code)
    balance = grand - paid
    ref     = (parsed or {}).get("ref")
    dtdc    = order.get("dtdc_center")
    dtdc_line = f"\n🏢 DTDC: {dtdc}" if dtdc else ""
    ref_line  = f"\n🔖 Ref: {ref}" if ref else ""
    caption = (
        "💳 *Payment to verify* — customer pasted details (no screenshot)\n"
        f"Order: {code}\n"
        f"Total ₹{grand:.0f} · Paid ₹{paid:.0f} · *Balance ₹{balance:.0f}*{ref_line}\n"
        f"Customer: {order.get('name') or '—'} "
        f"+{re.sub(r'[^0-9]', '', order.get('phone', ''))}\n"
        f"Items: {_cart_line(order.get('items') or {})}\n"
        f"📍 {order.get('address', '')}{dtdc_line}\n\n"
        f"— pasted message —\n{(raw_text or '').strip()[:600]}"
    )
    _send_text(VERIFIER_PHONE, caption)
    _send_buttons(
        VERIFIER_PHONE, f"Full or part payment for *{code}*?",
        [(f"pf_{payid}", f"✅ Full ₹{balance:.0f}"),
         (f"pp_{payid}", "➗ Part payment"),
         (f"pr_{payid}", "❌ Not received")],
    )


# ── flow ──────────────────────────────────────────────────────────────────────

def _address_prompt() -> str:
    return (
        "📍 Please type the *full delivery address* — "
        "house/flat no., street, place, district and *PIN code*."
    )


def _is_valid_name(text: str) -> bool:
    """Return True if text looks like a real recipient name (not a placeholder)."""
    t = (text or "").strip()
    if len(t) < 3:
        return False
    # Must have at least one letter (Latin or Malayalam)
    return bool(re.search(r"[A-Za-zഀ-ൿ]", t))


def _send_dtdc_prompt(phone: str) -> None:
    _send_buttons(
        phone,
        "📦 Books will be shipped via *DTDC courier*.\n\n"
        "⚠️ Home delivery is not guaranteed — depends on your local DTDC branch.\n\n"
        "Do you have a *preferred DTDC center* (e.g. *DTDC Kozhikode City*)? "
        "If yes, type it. If not, tap *Skip*.",
        [("dtdc_skip", "⏭️ No preference")],
    )


def _begin_counting(phone: str, order_code: str, keys: list, editing: bool) -> None:
    """Initialise the per-book quantity queue and ask for the first book."""
    items = {k: 0 for k in keys}
    _dbc.update_book_order(order_code, items=items,
                           flow_cursor={"queue": keys[1:], "current": keys[0],
                                        "editing": editing})
    _dbc.save_session(DB, phone, step="book_qty")
    _send_qty_buttons(phone, bc.BOOKS[keys[0]]["label"])


def _start(phone: str, name: str | None, force_new: bool = False,
           acq_source: str | None = None, acq_campaign: str | None = None) -> list[str]:
    active = {} if force_new else _dbc.get_active_book_order(phone)
    if active and active.get("status") == "collecting":
        code = active["order_code"]
        _dbc.update_book_order(code, items={}, flow_cursor={}, status="collecting",
                               books_total=0, courier=0, grand_total=0,
                               address=None, contact_phone=None, payment_proof_url=None)
    elif active and active.get("status") in ("awaiting_payment", "payment_review",
                                             "partially_paid"):
        # Do NOT wipe an order the customer already confirmed (and may have paid) —
        # re-typing "books" must not destroy items/address. Point them at payment
        # or an explicit fresh start.
        return [
            f"Your order *{active['order_code']}* is awaiting payment confirmation. "
            "Send your payment screenshot (or the payment details) here, or reply "
            "*NEW* to start a fresh order. 🙏"
        ]
    else:
        code = _new_order_code()
        created = _dbc.create_book_order(code, phone, name)
        if not created:
            code = _new_order_code()
            _dbc.create_book_order(code, phone, name)
        # Best-effort acquisition stamp on a NEW order only (the resets above keep
        # the original attribution). No-op until the acq_source migration is
        # applied; update_book_order swallows errors so ordering never breaks here.
        _acq = {"acq_entry": "whatsapp"}
        if acq_source:
            _acq["acq_source"] = acq_source
        if acq_campaign:
            _acq["acq_campaign"] = acq_campaign
        _dbc.update_book_order(code, **_acq)

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
    _dbc.save_session(DB, phone, step="book_name")
    _send_text(
        phone,
        f"Your order comes to *₹{totals['grand_total']:.0f}* "
        f"(incl. ₹{totals['courier']:.0f} courier).\n\n"
        "👤 Please type the *full name* of the person receiving the parcel.",
    )
    return []


def _handle_name(phone: str, text: str, order: dict) -> list[str]:
    name = (text or "").strip()
    if not _is_valid_name(name):
        _send_text(phone, "Please type the *full name* of the person receiving the parcel "
                          "(e.g. Priya Krishnan, John Thomas). 👤")
        return []
    _dbc.update_book_order(order["order_code"], name=name)
    _dbc.save_session(DB, phone, step="book_address")
    _send_text(phone, _address_prompt())
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
    _dbc.save_session(DB, phone, step="book_dtdc")
    _send_dtdc_prompt(phone)
    return []


def _handle_dtdc(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip()
    dtdc = None if (t.lower() in _DTDC_SKIP or len(t) < 2) else t
    _dbc.update_book_order(order["order_code"], dtdc_center=dtdc)
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
                               books_total=0, courier=0, grand_total=0,
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
    if t == "ed_name" or t in {"name", "recipient"}:
        _dbc.save_session(DB, phone, step="book_edit_name")
        _send_text(phone, "👤 Please type the *new recipient name*.")
        return []
    if t == "ed_books" or "book" in t:
        _dbc.update_book_order(code, flow_cursor={"editing": True})
        _dbc.save_session(DB, phone, step="book_select")
        _send_select_list(phone, edit=True)
        return []
    if t == "ed_addr" or "address" in t:
        _dbc.save_session(DB, phone, step="book_edit_address")
        _send_text(phone, "📍 Please *type the new delivery address* (include PIN code).")
        return []
    if t == "ed_dtdc" or "dtdc" in t or "center" in t or "centre" in t:
        _dbc.save_session(DB, phone, step="book_edit_dtdc")
        _send_dtdc_prompt(phone)
        return []
    if t == "ed_phone" or "phone" in t or "number" in t:
        _dbc.save_session(DB, phone, step="book_edit_phone")
        _send_phone_buttons(phone)
        return []
    _send_edit_menu(phone)
    return []


def _handle_edit_name(phone: str, text: str, order: dict) -> list[str]:
    name = (text or "").strip()
    if not _is_valid_name(name):
        _send_text(phone, "Please type the *full name* of the person receiving the parcel. 👤")
        return []
    _dbc.update_book_order(order["order_code"], name=name)
    _dbc.save_session(DB, phone, step="book_summary")
    _send_summary(phone, _dbc.get_book_order(order["order_code"]))
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


def _handle_edit_dtdc(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip()
    dtdc = None if (t.lower() in _DTDC_SKIP or len(t) < 2) else t
    _dbc.update_book_order(order["order_code"], dtdc_center=dtdc)
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
    pay = bc.parse_payment_text(text)
    if pay:
        # Customer pasted their bank/UPI confirmation instead of a screenshot.
        # Record a pending payment row and route it to Anu to confirm, exactly
        # like a screenshot — never loop "send a screenshot" on a paid customer.
        payment = _dbc.add_book_payment(code, None)
        _dbc.update_book_order(code, status="payment_review",
                               payment_ref=pay.get("ref") or None)
        _dbc.save_session(DB, phone, step="book_pay", needs_human=True)
        try:
            _forward_payment_text_to_verifier(_dbc.get_book_order(code) or order,
                                              payment, text, pay)
        except Exception as exc:
            logger.error("forward payment text to verifier failed for %s: %s", code, exc)
        _send_text(phone,
                   f"✅ Got your payment details for *{code}*.\n\n"
                   "We're confirming it now — you'll get an update shortly. Thank you! 🙏")
        return []
    _send_text(phone, "Please complete the UPI payment and *send a screenshot* of "
                      "the confirmation here. 🙏")
    _send_pay_buttons(phone)
    return []


# ── public entry points ───────────────────────────────────────────────────────

def _try_website_order(phone: str, text: str, name: str | None) -> list[str] | None:
    """Ingest a complete ORDER-template message from the website checkout.

    The website collects books, quantities and the delivery address, then sends a
    fixed ORDER template (book_catalog.parse_anu_order). We create/populate the
    order in one shot — no re-asking — and drop the customer at the summary step,
    where the existing Confirm / Edit / Cancel buttons take over (Confirm → QR).
    Returns [] when handled, or None when the message is not a website order.
    """
    parsed = bc.parse_anu_order(text)
    if parsed is None or not parsed.get("ok"):
        return None

    existing = _dbc.get_active_book_order(phone)
    if existing:
        code = existing["order_code"]
    else:
        code = _new_order_code()
        if not _dbc.create_book_order(code, phone, parsed["name"] or name):
            code = _new_order_code()
            _dbc.create_book_order(code, phone, parsed["name"] or name)

    _dbc.update_book_order(
        code,
        items=parsed["items"],
        name=parsed["name"] or name,
        address=parsed["address"],
        contact_phone=parsed["phone"],
        flow_cursor={},
        status="collecting",
    )
    order = _dbc.get_book_order(code)
    _dbc.save_session(DB, phone, step="book_summary", needs_human=False)
    _send_summary(phone, order)
    return []


# ── Order tracking (re-share DTDC tracking on request) ────────────────────────
# After dispatch, customers ask "where's my order / what's the courier / when
# will I get it". The dispatch flow already stored tracking_no + courier_name and
# messaged it once; this re-shares it on demand instead of re-showing the catalog.
# DTDC's site has no verified auto-prefill URL param, so we give the number to
# paste. Override DTDC_TRACK_URL via env if a prefill link becomes available.
DTDC_TRACK_URL = os.environ.get("DTDC_TRACK_URL", "https://www.dtdc.com/track-your-shipment/")

_TRACKING_WORDS = {
    "track", "tracking", "status", "courier", "consignment", "shipped",
    "shipping", "dispatch", "dispatched", "parcel", "reference", "awb",
}
_TRACKING_PHRASES = (
    "when will i get", "when get", "when do i get", "where is my", "where's my",
    "where is the", "how do i get", "not received", "not delivered", "reached",
    "എന്ന് കിട്ടും", "എവിടെ", "എത്തി",   # Malayalam: when will I get / where / arrived
)


def is_tracking_question(text: str) -> bool:
    """Heuristic: does this message ask about an existing order's whereabouts?"""
    t = (text or "").strip().lower()
    if not t:
        return False
    if any(p in t for p in _TRACKING_PHRASES):
        return True
    return bool(set(re.findall(r"\w+", t)) & _TRACKING_WORDS)


def compose_tracking_reply(order: dict) -> str:
    """Build the tracking message for a dispatched order."""
    code = order.get("order_code", "")
    courier = order.get("courier_name") or "DTDC"
    tn = order.get("tracking_no")
    if not tn:
        return (f"📦 Your order *{code}* has been *dispatched* via {courier}. "
                "Your tracking number will be shared shortly — reply here if you "
                "need help.")
    return (
        f"📦 Your order *{code}* shipped via *{courier}*.\n"
        f"🔖 Tracking / Reference no: *{tn}*\n"
        f"🔗 Track here: {DTDC_TRACK_URL}\n"
        f"On that page, paste *{tn}* and tap search."
    )


def _maybe_tracking_reply(phone: str, text: str) -> list[str] | None:
    """If `text` asks about an already-dispatched order, re-share its tracking."""
    if not is_tracking_question(text):
        return None
    order = _dbc.get_dispatched_book_order(phone)
    if not order:
        return None
    _send_text(phone, compose_tracking_reply(order))
    return []


def maybe_handle_book(phone: str, text: str, name: str | None = None) -> list[str] | None:
    session = _dbc.get_session(DB, phone) or {}
    step = session.get("step") or ""

    if step not in _BOOK_STEPS:
        if not _in_print_flow(session):
            web = _try_website_order(phone, text, name)
            if web is not None:
                return web
            # A customer asking about an already-shipped order → re-share tracking
            # (must come BEFORE is_book_trigger, since "how do I get the book" etc.
            # would otherwise re-open the catalog).
            track = _maybe_tracking_reply(phone, text)
            if track is not None:
                return track
        if is_book_trigger(text) and not _in_print_flow(session):
            _chan, _camp = _parse_acq(text)
            return _start(phone, name, acq_source=_chan, acq_campaign=_camp)
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
        "book_name":         _handle_name,
        "book_address":      _handle_address,
        "book_dtdc":         _handle_dtdc,
        "book_phone":        _handle_phone,
        "book_summary":      _handle_summary,
        "book_edit":         _handle_edit,
        "book_edit_name":    _handle_edit_name,
        "book_edit_address": _handle_edit_address,
        "book_edit_dtdc":    _handle_edit_dtdc,
        "book_edit_phone":   _handle_edit_phone,
        "book_pay":          _handle_pay,
    }
    handler = handlers.get(step)
    if not handler:
        _dbc.save_session(DB, phone, step="book_select")
        return _start(phone, name)
    return handler(phone, text, order)


def handle_payment_proof(phone: str, content: bytes, mime_type: str) -> list[str] | None:
    # Gate on the authoritative ORDER state, not the volatile session step.
    # An out-of-band QR resend (e.g. a manual broadcast) or session drift can
    # leave step != "book_pay" while the order is legitimately awaiting payment;
    # the screenshot must still be treated as a payment proof, otherwise it
    # falls through to print-job intake (the "considered it as a print" bug).
    order = _dbc.get_active_book_order(phone)
    if not order or order.get("status") not in (
            "awaiting_payment", "payment_review", "partially_paid"):
        return None

    code = order["order_code"]
    url = _dbc.upload_book_payment_proof(code, content, mime_type)
    # One ledger row per screenshot — earlier proofs are never overwritten.
    payment = _dbc.add_book_payment(code, url or None)
    _dbc.update_book_order(code, status="payment_review",
                           payment_proof_url=url or None)
    _dbc.save_session(DB, phone, step="book_pay", needs_human=True)
    # Forward THIS screenshot to Anu for full/part validation.
    try:
        _forward_to_verifier(_dbc.get_book_order(code), payment, content, mime_type)
    except Exception as exc:
        logger.error("forward to verifier failed for %s: %s", code, exc)
    return [
        f"✅ Got your payment screenshot for *{code}*.\n\n"
        "We're verifying it now — you'll receive an update shortly. Thank you! 🙏"
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


# ── Free-form intake: auto-combine across messages, confirm before saving ─────
ANU_BUFFER_TTL_MIN = 20
_BOOK_TITLE = {"malayalam": "Aksharamrutham", "hindi": "Vidyamrut", "english": "Easy English"}


def _anu_session() -> dict:
    return _dbc.get_session(DB, VERIFIER_PHONE) or {}


def _anu_buffer(sess: dict) -> str:
    """Anu's fresh accumulated raw text, or '' if none / stale."""
    import json
    from datetime import datetime, timedelta, timezone
    try:
        blob = json.loads(sess.get("saved_json") or "{}")
        ts = blob.get("ts")
        if ts:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
            if age <= timedelta(minutes=ANU_BUFFER_TTL_MIN):
                return blob.get("raw") or ""
    except Exception:
        pass
    return ""


def _anu_save_buffer(raw: str, step: str, order: dict | None = None) -> None:
    import json
    from datetime import datetime, timezone
    blob = {"raw": raw, "ts": datetime.now(timezone.utc).isoformat()}
    if order is not None:
        blob["order"] = order
    _dbc.save_session(DB, VERIFIER_PHONE, step=step,
                      saved_json=json.dumps(blob, ensure_ascii=True))


def _anu_clear_buffer() -> None:
    _dbc.save_session(DB, VERIFIER_PHONE, step="", saved_json="")


# ── part-payment verification (per-screenshot) ────────────────────────────────

def _anu_set_pending_payment(payid, code: str, balance: float) -> None:
    """Remember that Anu owes a typed amount for one specific screenshot."""
    import json
    from datetime import datetime, timezone
    blob = {"pending_payment_id": payid, "code": code, "balance": balance,
            "ts": datetime.now(timezone.utc).isoformat()}
    _dbc.save_session(DB, VERIFIER_PHONE, step="await_part_amount",
                      saved_json=json.dumps(blob, ensure_ascii=True))


def _anu_get_pending_payment() -> dict | None:
    """The screenshot awaiting a typed amount, or None if none / stale."""
    import json
    from datetime import datetime, timedelta, timezone
    sess = _anu_session()
    if sess.get("step") != "await_part_amount":
        return None
    try:
        blob = json.loads(sess.get("saved_json") or "{}")
        ts = blob.get("ts")
        if ts and datetime.now(timezone.utc) - datetime.fromisoformat(ts) <= timedelta(minutes=30):
            return blob
    except Exception:
        pass
    return None


def _after_payment_change(code: str, rejected: bool = False) -> None:
    """Recompute paid/balance from the ledger and drive the order's status.

    Fully paid -> confirm (notifies customer). Otherwise -> partially_paid (or
    awaiting_payment if nothing verified yet) and tell the customer the balance.
    """
    order = _dbc.get_book_order(code) or {}
    grand = bc.compute_totals(order.get("items") or {})["grand_total"]
    paid = _dbc.book_amount_paid(code)
    _dbc.update_book_order(code, amount_paid=paid)
    cust = order.get("phone")
    balance = grand - paid

    if grand > 0 and paid >= grand:
        res = confirm_book_order(code)            # notifies the customer
        if not res.get("ok"):
            _send_text(VERIFIER_PHONE,
                       f"⚠️ *{code}* is fully paid but the confirm did not save — "
                       "please tap Confirm again.")
            return
        over = paid - grand
        msg = f"✅ *{code}* fully paid (₹{paid:.0f}) — confirmed, customer notified."
        if over > 0:
            msg += f"\n⚠️ Overpaid by ₹{over:.0f}; refund manually."
        _send_text(VERIFIER_PHONE, msg)
        return

    _dbc.update_book_order(code, status="partially_paid" if paid > 0 else "awaiting_payment")
    if cust:
        _dbc.save_session(DB, cust, step="book_pay", needs_human=False)
        if rejected:
            tail = (f"You've paid ₹{paid:.0f} so far; balance ₹{balance:.0f}. "
                    "Please send a clear screenshot of the remaining payment. 🙏"
                    if paid > 0 else
                    "Please send a clear screenshot of the payment. 🙏")
            _send_text(cust, f"⚠️ We couldn't verify that payment for *{code}*. " + tail)
        else:
            _send_text(cust,
                       f"✅ Received ₹{paid:.0f} for *{code}*. Balance ₹{balance:.0f}. "
                       "Please pay the rest and send a screenshot. 🙏")
    if rejected:
        _send_text(VERIFIER_PHONE,
                   f"❌ Marked not received for *{code}* — paid ₹{paid:.0f}, "
                   f"balance ₹{balance:.0f}. Customer asked to (re)send.")
    else:
        _send_text(VERIFIER_PHONE,
                   f"➗ Recorded ₹{paid:.0f} for *{code}* — balance ₹{balance:.0f}.")


def _handle_payment_action(kind: str, payid: int) -> None:
    """Anu tapped Full / Part / Not-received on a specific screenshot."""
    pay = _dbc.get_book_payment(payid)
    if not pay:
        _send_text(VERIFIER_PHONE, "⚠️ Couldn't find that screenshot.")
        return
    if pay.get("status") != "pending":
        _send_text(VERIFIER_PHONE, f"That screenshot was already {pay.get('status')}.")
        return
    code = pay["order_code"]
    order = _dbc.get_book_order(code) or {}
    grand = bc.compute_totals(order.get("items") or {})["grand_total"]
    balance = grand - _dbc.book_amount_paid(code)

    if kind == "pr":                      # not received
        _dbc.reject_book_payment(payid)
        _after_payment_change(code, rejected=True)
    elif kind == "pf":                    # full: this screenshot clears the balance
        _dbc.verify_book_payment(payid, max(0.0, balance))
        _after_payment_change(code)
    else:                                 # pp → part: ask Anu for the amount
        _anu_set_pending_payment(payid, code, balance)
        _send_text(VERIFIER_PHONE,
                   f"How much is this screenshot for *{code}*? "
                   f"Reply just the amount (e.g. 500). Balance is ₹{balance:.0f}.")


def _assemble(parsed: dict) -> dict:
    """Normalise an LLM parse into order fields."""
    copies = int(parsed.get("copies") or 1) or 1
    items: dict[str, int] = {}
    for b in (parsed.get("books") or []):
        if b.get("title"):
            items[b["title"]] = items.get(b["title"], 0) + int(b.get("qty") or copies or 1)
    address = ", ".join(p for p in [(parsed.get("address") or "").strip(),
                                    (parsed.get("pincode") or "").strip()] if p)
    return {
        "name":          (parsed.get("name") or "").strip(),
        "phone":         re.sub(r"\D", "", parsed.get("phone") or ""),
        "address":       address,
        "copies":        copies,
        "items":         items,
        "book_explicit": bool(parsed.get("book_explicit")),
    }


def _handle_anu_freeform(text: str) -> None:
    """Auto-combine Anu's messages into one order; ask for gaps; confirm before save."""
    msg = (text or "").strip()
    # A book-button tap becomes plain text the LLM understands when re-parsed.
    bm = re.match(r"^abook_(malayalam|hindi|english)$", msg)
    if bm:
        msg = f"Book: {_BOOK_TITLE[bm.group(1)]}"

    sess = _anu_session()
    prev = _anu_buffer(sess)
    raw  = (prev + "\n" + msg).strip() if prev else msg

    # Cheap gate only with no buffer yet — don't drop a short continuation.
    if not prev and len(raw) < 12 and len(re.findall(r"\d", raw)) < 8:
        return

    o = _assemble(anu_parser.parse_order_message(raw))

    if not o["name"]:                                   # can't identify a customer yet
        _anu_save_buffer(raw, "anu_intake")
        _send_text(VERIFIER_PHONE,
                   "👍 Got it — send the rest of the order (name + address + book).")
        return
    if len(o["phone"]) < 10:                            # missing phone → ask
        _anu_save_buffer(raw, "anu_intake")
        _send_text(VERIFIER_PHONE, f"📞 What's the phone number for *{o['name']}*?")
        return
    if not o["items"] or not o["book_explicit"]:        # book not named → ask
        _anu_save_buffer(raw, "anu_intake")
        _send_buttons(VERIFIER_PHONE,
                      f"📕 Which book for *{o['name']}* ({o['copies']} copy/s)?",
                      _BOOK_BUTTONS)
        return

    # Complete → stage for confirmation (NOT created yet).
    _anu_save_buffer(raw, "anu_staged", order=o)
    totals     = bc.compute_totals(o["items"])
    courier    = totals["courier"]
    grand      = totals["books_total"] + courier
    commission = bc.commission_for(o["items"])
    _send_buttons(
        VERIFIER_PHONE,
        "📋 *Confirm this order?*\n"
        f"{o['name']} · +{o['phone']}\n"
        f"{o['address'] or '—'}\n"
        f"{_cart_line(o['items'])}\n"
        f"Books ₹{totals['books_total']:.0f} + Courier ₹{courier:.0f} = *₹{grand:.0f}*\n"
        f"Divya commission: ₹{commission:.0f}",
        [("aok", "✅ Confirm & print"), ("axx", "❌ Cancel")],
    )


def _confirm_staged() -> None:
    """Anu tapped Confirm — create the staged order."""
    import json
    sess = _anu_session()
    try:
        o = json.loads(sess.get("saved_json") or "{}").get("order")
    except Exception:
        o = None
    if not o or not o.get("items"):
        _anu_clear_buffer()
        _send_text(VERIFIER_PHONE, "⚠️ That order expired — please forward it again.")
        return
    _anu_clear_buffer()
    _create_divya_confirmed(_new_order_code(), o["name"], o["phone"],
                            o.get("address") or "", o["items"])


def _discard_staged() -> None:
    _anu_clear_buffer()
    _send_text(VERIFIER_PHONE, "❌ Discarded. Forward the order again when ready.")


def _handle_payment_verdict(action: str, code: str) -> None:
    """Apply Anu's Confirm/Reject on a customer payment screenshot."""
    if action == "vconf":
        res = confirm_book_order(code)
        if res.get("ok"):
            _send_text(VERIFIER_PHONE, f"✅ Confirmed *{code}* — customer notified. Thanks Anu! 🙏")
        elif res.get("error") == "status_not_persisted":
            _send_text(VERIFIER_PHONE, f"⚠️ Couldn't save the confirmation for *{code}* — "
                                       "it is still NOT confirmed. Please tap Confirm again.")
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

    Precedence: payment Confirm/Reject, staged-order Confirm/Cancel, order Cancel,
    the rigid ORDER template, then a free-form teacher forward (auto-combined +
    LLM-parsed). Every message from Anu's number is consumed here (returns True)
    so it never falls into the customer flow.
    """
    if re.sub(r"\D", "", sender or "") != re.sub(r"\D", "", VERIFIER_PHONE):
        return False
    t = (text or "").strip()

    # 0. Per-screenshot part-payment validation (Full / Part / Not-received).
    pm = re.match(r"^(pf|pp|pr)_(\d+)$", t)
    if pm:
        _handle_payment_action(pm.group(1), int(pm.group(2)))
        return True
    pend = _anu_get_pending_payment()
    if pend:
        am = re.match(r"^\s*(?:₹|rs\.?\s*)?(\d+(?:\.\d+)?)\s*$", t, re.I)
        if am:
            payid = pend.get("pending_payment_id")
            pay = _dbc.get_book_payment(payid)
            _anu_clear_buffer()
            if not pay or pay.get("status") != "pending":
                _send_text(VERIFIER_PHONE, "That screenshot was already handled.")
                return True
            _dbc.verify_book_payment(payid, float(am.group(1)))
            _after_payment_change(pay["order_code"])
            return True
        _send_text(VERIFIER_PHONE,
                   "Please reply just the amount (e.g. 500), or tap a button on the screenshot.")
        return True

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

    # 2. Confirm / Cancel a STAGED order (the auto-combine confirmation gate).
    if t == "aok":
        _confirm_staged()
        return True
    if t == "axx":
        _discard_staged()
        return True

    # 3. Cancel an already-created Divya order.
    cm = re.match(r"^acanc_(\S+)$", t) or re.match(r"(?i)^cancel\s+(XTR-\S+)", t)
    if cm:
        _cancel_divya_order(cm.group(1).upper())
        return True

    # 4. Rigid ORDER template (backward-compatible).
    parsed = bc.parse_anu_order(t)
    if parsed is not None:
        _handle_anu_order(parsed)
        return True

    # 5. Free-form teacher forward → auto-combine + confirm before save.
    _handle_anu_freeform(t)
    return True


def confirm_book_order(order_code: str) -> dict:
    order = _dbc.get_book_order(order_code)
    if not order:
        return {"ok": False, "error": "Order not found"}
    if order.get("status") == "confirmed":
        return {"ok": True, "already_confirmed": True, "order": order}

    from datetime import datetime, timezone
    ok = _dbc.update_book_order(order_code, status="confirmed",
                                commission=bc.commission_for(order.get("items") or {}),
                                confirmed_at=datetime.now(timezone.utc).isoformat())

    # Verify the status actually persisted BEFORE telling the customer it is
    # confirmed. A swallowed/transient DB write failure must NOT produce a false
    # "Order confirmed!" message while the row stays unpaid (the bug where a
    # manual accept "succeeded" but the order still read awaiting_payment).
    fresh = _dbc.get_book_order(order_code) or {}
    if not ok or fresh.get("status") != "confirmed":
        logger.error("confirm_book_order: status did not persist for %s "
                     "(ok=%s, status=%s)", order_code, ok, fresh.get("status"))
        return {"ok": False, "error": "status_not_persisted", "order": fresh or order}

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
    return {"ok": True, "order": fresh}
