"""Book campaign — WhatsApp conversational order flow (button-driven).

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

# Single-word triggers — any one of these alone fires the books flow
_TRIGGER_WORDS = {
    # Generic
    "book", "books",
    # Campaign / brand names + book titles (specific enough to stand alone)
    "xtraa", "xtra", "adithara", "balappeduthu",
    "foundation", "aksharamrutham", "vidyamrut", "vidyamrutham",
    # Malayalam script (brand / book titles)
    "അടിത്തറ", "അക്ഷരാമൃതം", "വിദ്യാമൃത്",
}
# NOTE: bare language/easy words ("hi", "en", "ml", "english", "malayalam",
# "hindi", "easy") are deliberately NOT standalone triggers — they collide with
# greetings and the print flow on this shared line. The language-qualified
# intents are caught by _TRIGGER_PHRASES below ("malayalam book", "easy
# english", "ml book", …) instead.

# Multi-word phrases that must be matched as a substring (after lowercasing)
_TRIGGER_PHRASES = [
    "easy english",
    "english book", "malayalam book", "hindi book",
    "ml book", "en book", "hi book",
    "english books", "malayalam books", "hindi books",
    "buy book", "buy books", "order book", "order books",
    "want book", "need book",
]

_AFFIRM = {
    "yes", "y", "ok", "okay", "confirm", "confirmed", "correct", "right",
    "sure", "ya", "yeah", "yep", "proceed", "done", "✓", "👍",
}
_NEGATE = {"no", "cancel", "wrong", "restart", "redo", "start over"}

_QR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "brand-kit", "printosky-payment-qr-v2.png")

# Staff member who verifies UPI payments. Payment screenshots are forwarded here;
# she taps Confirm/Reject. International format, no '+'.
# Anu is ALSO the Divya order forwarder — she sends new orders here using
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

_BOOK_STEPS = {"book_select", "book_qty", "book_confirm_parsed", "book_name", "book_address", "book_dtdc",
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
    """Return True if the customer's message should start the books flow.

    Matches:
    - Any single trigger word (book, books, malayalam, easy, ml, …)
    - Any trigger phrase as a substring ("easy english", "malayalam book", …)
    """
    if not text:
        return False
    t = text.strip().lower()
    # Single-word match
    words = set(re.split(r"[^\wഀ-ൿ]+", t))
    if words & _TRIGGER_WORDS:
        return True
    # Phrase match (substring)
    return any(phrase in t for phrase in _TRIGGER_PHRASES)


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


_PINCODE_RE = re.compile(r"(?<!\d)[1-9]\d{5}(?!\d)")
# House/door/plot numbers are the other common 6-digit token in an address and
# sit right before a number like this; a real PIN never does. Matching this
# prefix lets us tell "Door No 204568, near temple, Thrissur" (no real PIN)
# apart from "Nedumkunnam PO, Kottayam 686542" (PIN) without rejecting any
# address that doesn't mention a house-number word near a 6-digit number.
_HOUSE_NUM_PREFIX_RE = re.compile(
    r"(?:door|flat|house|plot|survey|room|floor|building|blk|block|no\.?|#)\s*[:\-]?\s*$",
    re.IGNORECASE,
)


def _has_pincode(text: str) -> bool:
    """True if the text contains a plausible 6-digit Indian PIN code.

    Accepts any isolated 6-digit number EXCEPT one immediately preceded by a
    house/door/plot-number word — that pattern means the address gave a
    building number, not a PIN, and a customer relying on it alone would ship
    with no real PIN on file (see _has_pincode false-positive backlog note).
    """
    t = text or ""
    for m in _PINCODE_RE.finditer(t):
        if not _HOUSE_NUM_PREFIX_RE.search(t[:m.start()]):
            return True
    return False


def _parse_choice(text: str) -> list[str] | None:
    """A selection → ordered list of book keys, or None.

    Accepts a button/list id ("bk_all"), a numeric pick ("1", "1,3", "all"),
    or the visible option *title* sent as text — in English OR Malayalam. The
    bilingual list shows Malayalam titles (e.g. "മൂന്നും (Set)", "മലയാളം + ഹിന്ദി"),
    and a confused customer may type/echo those instead of tapping the row, so
    the Malayalam wording must parse too — not just the English keywords.
    """
    t = (text or "").strip().lower()
    if t in _SELECT_IDS:
        return list(_SELECT_IDS[t])
    keys = bc.parse_selection(text)   # handles "1", "1,3", "all", "set", "4"
    if keys:
        return keys
    # "All three" by its visible title ("മൂന്നും (Set)") or words.
    if ("മൂന്ന" in t or "എല്ലാ" in t or "(set)" in t
            or "all three" in t or "all 3" in t):
        return list(bc.BOOK_KEYS)
    # Collect every language named in the text — English keyword OR Malayalam
    # title — in canonical order, so a combo title like "മലയാളം + ഹിന്ദി" resolves
    # to both books, not just the first match. (Short Malayalam roots avoid
    # trailing-glyph Unicode variance.)
    langs: list[str] = []
    if "akshara" in t or "malayalam" in t or "അക്ഷര" in t or "മലയാള" in t:
        langs.append("malayalam")
    if "vidya" in t or "hindi" in t or "വിദ്യ" in t or "ഹിന്ദ" in t:
        langs.append("hindi")
    if "english" in t or "ഇംഗ്ല" in t:
        langs.append("english")
    return langs or None


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
        {"id": "bk_ml", "title": "അക്ഷരാമൃതം", "description": f"മലയാളം പുസ്തകം · ₹{p['malayalam']:.0f}"},
        {"id": "bk_hi", "title": "Vidyamrut",   "description": f"ഹിന്ദി പുസ്തകം · ₹{p['hindi']:.0f}"},
        {"id": "bk_en", "title": "Easy English", "description": f"ഇംഗ്ലീഷ് പുസ്തകം · ₹{p['english']:.0f}"},
        {"id": "bk_ml_hi", "title": "മലയാളം + ഹിന്ദി",
         "description": f"അക്ഷരാമൃതം + Vidyamrut · ₹{p['malayalam'] + p['hindi']:.0f}"},
        {"id": "bk_ml_en", "title": "മലയാളം + ഇംഗ്ലീഷ്",
         "description": f"അക്ഷരാമൃതം + Easy English · ₹{p['malayalam'] + p['english']:.0f}"},
        {"id": "bk_hi_en", "title": "ഹിന്ദി + ഇംഗ്ലീഷ്",
         "description": f"Vidyamrut + Easy English · ₹{p['hindi'] + p['english']:.0f}"},
        {"id": "bk_all", "title": "മൂന്നും (Set)",
         "description": f"എല്ലാം · ₹{bc.SET_PRICE} (ഓരോന്നും 1 വീതം)"},
    ]
    if edit:
        body = ("പുസ്തകം വീണ്ടും തിരഞ്ഞെടുക്കുക (ഒന്ന്, ജോഡി, അല്ലെങ്കിൽ മൂന്നും).\n"
                "Pick your books again (single, a pair, or all three).")
    else:
        body = ("📚 *Books*\n"
                "മലയാളം, English & हिंदी ഭാഷകളുടെ അടിസ്ഥാനം ഉറപ്പാക്കുന്നതിനായി ഇതാ 3 പുസ്തകങ്ങൾ.\n\n"
                "വേണ്ട പുസ്തകം തിരഞ്ഞെടുക്കുക (ഒന്ന്, രണ്ട് അല്ലെങ്കിൽ മൂന്നും).\n"
                "Pick the books you want (one, two, or all three).\n\n"
                "_+ ഭാരം അനുസരിച്ച് കൊറിയർ ചാർജ് (₹75 മുതൽ)._")
    _send_list(phone, body, "📚 തിരഞ്ഞെടുക്കൂ", rows, header="Books")


def _send_qty_buttons(phone: str, label: str) -> None:
    _send_buttons(
        phone,
        f"*{label}* എത്ര എണ്ണം വേണം?\n"
        f"How many copies of *{label}*?\n\n"
        "_നമ്പർ ടാപ്പ് ചെയ്യൂ, അല്ലെങ്കിൽ ടൈപ്പ് ചെയ്യൂ (ഉദാ: 5)._",
        [("qty_1", "1"), ("qty_2", "2"), ("qty_3", "3")],
    )


def _send_phone_buttons(phone: str) -> None:
    _send_buttons(
        phone,
        f"ഡെലിവറി അപ്ഡേറ്റുകൾക്ക് *{_format_phone(phone)}* ശരിയായ നമ്പറാണോ?\n"
        f"Is *{_format_phone(phone)}* the right number for delivery updates?",
        [("ph_yes", "✅ ശരി"), ("ph_edit", "✏️ വേറെ നമ്പർ")],
    )


def _order_totals(order: dict) -> dict:
    """Money terms for `order`, honouring the Divya self-order exemption.

    Single source of truth: book_catalog.divya_order_terms(). Every place that
    quotes a total, asks for payment, or decides whether an order is fully
    paid must derive it from here so the customer total, the payment ask and
    the settlement ledger can never drift apart (previously this exemption
    was only applied retroactively at confirm_book_order, after the customer
    may have already been asked to pay the wrong amount).
    """
    return bc.divya_order_terms(order.get("phone"), order.get("items") or {},
                                order.get("delivery_method") or "courier")


def _summary_text(order: dict) -> str:
    items = order.get("items") or {}
    totals = _order_totals(order)
    lines = "\n".join(
        f"• {ln['label'].split(' (')[0]} × {ln['qty']} = ₹{ln['line_total']:.0f}"
        for ln in bc.line_items(items)
    )
    name = order.get("name") or "—"
    dtdc = order.get("dtdc_center")
    dtdc_line = f"\n🏢 DTDC: {dtdc}" if dtdc else ""
    return (
        "🧾 *ഓർഡർ വിശദാംശം / Order summary*\n\n"
        f"{lines}\n\n"
        f"പുസ്തകങ്ങൾ / Books: ₹{totals['books_total']:.0f}\n"
        f"കൊറിയർ / Courier: ₹{totals['courier']:.0f}\n"
        f"*ആകെ / Total: ₹{totals['grand_total']:.0f}*\n\n"
        f"👤 {name}\n"
        f"📍 {order.get('address', '')}"
        f"{dtdc_line}\n"
        f"📞 {_format_phone(order.get('contact_phone', ''))}"
    )


def _send_summary(phone: str, order: dict) -> None:
    _send_buttons(phone, _summary_text(order),
                  [("ord_yes", "✅ ഉറപ്പിക്കൂ"), ("ord_edit", "✏️ മാറ്റം"), ("ord_no", "❌ റദ്ദാക്കൂ")])


def _send_edit_menu(phone: str) -> None:
    _send_list(
        phone, "എന്ത് മാറ്റണം? / What would you like to edit?", "✏️ മാറ്റം",
        [
            {"id": "ed_name",  "title": "👤 പേര് / Name"},
            {"id": "ed_books", "title": "📚 പുസ്തകം & എണ്ണം"},
            {"id": "ed_addr",  "title": "📍 വിലാസം / Address"},
            {"id": "ed_dtdc",  "title": "🏢 DTDC center"},
            {"id": "ed_phone", "title": "📞 ഫോൺ നമ്പർ"},
        ],
        header="Edit order",
        section_title="എന്ത് മാറ്റണം",
    )


def _send_pay_buttons(phone: str) -> None:
    _send_buttons(
        phone,
        "പണമടച്ച ശേഷം *സ്ക്രീൻഷോട്ട്* ഇവിടെ അയക്കൂ.\n"
        "After paying, *send a screenshot* here.\n\n"
        "പണമടയ്ക്കും മുമ്പ് എന്തെങ്കിലും മാറ്റണോ?",
        [("pay_edit", "✏️ മാറ്റം"), ("pay_cancel", "❌ റദ്ദാക്കൂ")],
    )


def _payment_caption(order: dict) -> str:
    totals = _order_totals(order)
    return (
        f"💳 *₹{totals['grand_total']:.0f} അടയ്ക്കൂ* — ഈ UPI QR സ്കാൻ ചെയ്യൂ.\n"
        f"Pay *₹{totals['grand_total']:.0f}* by scanning this UPI QR.\n\n"
        f"ഓർഡർ / Order: {order.get('order_code')}\n\n"
        "പണമടച്ച ശേഷം പേയ്മെന്റ് സ്ക്രീൻഷോട്ട് ഇവിടെ അയക്കൂ. ഞങ്ങൾ വെരിഫൈ ചെയ്ത് ഓർഡർ ഉറപ്പിക്കും. 🙏\n"
        "After paying, send a screenshot of the confirmation here.\n\n"
        "💳 QR സ്കാൻ ചെയ്യാൻ പറ്റുന്നില്ലേ? *9072034907* എന്ന നമ്പറിലേക്ക് UPI അയക്കൂ.\n"
        "Can't scan? Pay by UPI to *9072034907* (GPay / PhonePe)."
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
    totals  = _order_totals(order)
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
    grand   = _order_totals(order)["grand_total"]
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
        "📍 *പൂർണ്ണ ഡെലിവറി വിലാസം* ടൈപ്പ് ചെയ്യൂ — വീട്/ഫ്ലാറ്റ് നമ്പർ, സ്ഥലം, "
        "ജില്ല, *PIN കോഡ്* സഹിതം (അവസാനം PIN).\n"
        "Type the *full delivery address* incl. *PIN code* — put the *PIN "
        "last*.\n"
        "_ഉദാ / e.g.: House name, Place, District - 680001_"
    )


def _name_prompt() -> str:
    return (
        "👤 പാർസൽ ലഭിക്കുന്ന ആളുടെ *പൂർണ്ണ പേര്* ടൈപ്പ് ചെയ്യൂ "
        "(ഉദാ: Priya Krishnan).\n"
        "Type the *full name* of the person receiving the parcel."
    )


def _is_valid_name(text: str) -> bool:
    """Return True if text looks like a real recipient name (not a placeholder)."""
    t = (text or "").strip()
    if len(t) < 3:
        return False
    if not re.search(r"[A-Za-zഀ-ൿ]", t):
        return False
    # Reject internal button IDs (e.g. qty_1, bk_ml, ph_yes, dtdc_skip)
    if re.fullmatch(r"[a-z][a-z0-9]*_[a-z0-9]+", t):
        return False
    # Reject address blocks: multiple newlines, PIN code, mobile number, or address keywords
    if t.count("\n") > 1:
        return False
    if re.search(r"\b\d{6}\b", t):
        return False
    if re.search(r"\b\d{10}\b", t):
        return False
    if re.search(r"\b(mob|p\.o|dist|district|pin)\b", t, re.IGNORECASE):
        return False
    return True


def _send_dtdc_prompt(phone: str) -> None:
    _send_buttons(
        phone,
        "📦 പുസ്തകങ്ങൾ *DTDC കൊറിയർ* വഴി അയക്കും.\n"
        "⚠️ ഹോം ഡെലിവറി ഉറപ്പില്ല — നിങ്ങളുടെ DTDC ബ്രാഞ്ച് അനുസരിച്ചാണ്.\n\n"
        "*ഇഷ്ടമുള്ള DTDC സെന്റർ* ഉണ്ടോ (ഉദാ: *DTDC Kozhikode City*)? "
        "ഉണ്ടെങ്കിൽ ടൈപ്പ് ചെയ്യൂ. ഇല്ലെങ്കിൽ *Skip* ടാപ്പ് ചെയ്യൂ.\n"
        "Type a preferred DTDC center, or tap Skip.",
        [("dtdc_skip", "⏭️ വേണ്ട")],
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
            f"നിങ്ങളുടെ ഓർഡർ *{active['order_code']}* പേയ്മെന്റ് സ്ഥിരീകരണത്തിനായി "
            "കാത്തിരിക്കുന്നു. പേയ്മെന്റ് സ്ക്രീൻഷോട്ട് (അല്ലെങ്കിൽ പേയ്മെന്റ് വിവരം) "
            "ഇവിടെ അയക്കൂ, അല്ലെങ്കിൽ പുതിയ ഓർഡറിന് *NEW* എന്ന് റിപ്ലൈ ചെയ്യൂ. 🙏\n"
            f"Order *{active['order_code']}* is awaiting payment — send the screenshot, "
            "or reply *NEW* for a fresh order."
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


def start_catalog(phone: str, name: str | None = None) -> list[str]:
    """Open the books catalog unconditionally.

    Public seam for the router's books-only default: when an idle customer's
    message isn't claimed by a more specific handler (vendor, help, notes,
    credits, tracking, website order, or an active book step) it lands here and
    the catalog is opened instead of the old generic welcome/print menu. The
    select list is sent inside `_start`, so the return is the usual [] ("already
    sent").
    """
    return _start(phone, name)


def start_order(phone: str, name: str | None = None) -> list[str]:
    """Public entry to begin/reset the book order flow for `phone`.

    Used by the typed trigger path. Sends the opening book list as a side
    effect; returns any text the caller must relay to the customer (e.g. the
    awaiting-payment guard message). NOTE: on a `collecting` cart this RESETS
    progress — use `resume_order` for the admin take-over button so a dropped
    cart is continued, not wiped.
    """
    return _start(phone, name)


# Step → no-arg prompt sender, for re-issuing the question a dropped cart
# stalled on. Steps needing the order/cursor (book_qty, book_summary, book_pay)
# are handled inline in `_resume`.
_RESUME_SENDERS = {
    "book_name":    lambda phone: _send_text(phone, _name_prompt()),
    "book_address": lambda phone: _send_text(phone, _address_prompt()),
    "book_dtdc":    _send_dtdc_prompt,
    "book_phone":   _send_phone_buttons,
}


def _llm_parse_books(text: str) -> dict | None:
    """Haiku fallback: extract {book_key: qty} from a book-ish message the
    deterministic parser missed. Reuses anu_parser (forced tool-use, never
    raises). Returns None on no book / any failure."""
    try:
        from anu_parser import parse_order_message
        parsed = parse_order_message(text) or {}
        items: dict[str, int] = {}
        for b in parsed.get("books") or []:
            k = b.get("title")
            q = int(b.get("qty") or 1)
            if k in bc.BOOKS and q > 0:
                items[k] = items.get(k, 0) + q
        return items or None
    except Exception as exc:
        logger.error("book_bot._llm_parse_books failed: %s", exc)
        return None


def _maybe_book_enquiry(phone: str, text: str, name: str | None) -> list[str] | None:
    """Triage a fresh (not mid-flow) book-ish message:
      1. a parseable typed order   -> stage + confirm
      2. a price/delivery question -> FAQ + open catalog
      3. otherwise                 -> None (caller falls through)
    """
    items = bc.parse_customer_order(text)
    if not items and is_book_trigger(text):
        items = _llm_parse_books(text)

    if items:
        active = _dbc.get_active_book_order(phone)
        # Don't hijack a customer who already owes payment — let _start remind them.
        if active and active.get("status") in ("awaiting_payment", "payment_review", "partially_paid"):
            return None
        code = active["order_code"] if active else _new_order_code()
        if not active:
            _dbc.create_book_order(code, phone, name)
        totals = bc.divya_order_terms(phone, items, "courier")
        _dbc.update_book_order(code, items=items, flow_cursor={},
                               books_total=totals["books_total"],
                               courier=totals["courier"],
                               grand_total=totals["grand_total"])
        _dbc.save_session(DB, phone, step="book_confirm_parsed")
        _send_parsed_confirm(phone, items, totals)
        return []

    if is_book_trigger(text) and bc.is_book_faq(text):
        _send_text(phone, bc.book_faq_text())
        _send_select_list(phone)
        return []

    return None


def _resume(phone: str) -> list[str]:
    """Re-issue the prompt for the step a dropped cart stalled at — WITHOUT
    wiping items/name/address. Lifts any `staff_hold`/SOS so the bot listens
    again. Falls back to a fresh start when there is nothing to resume.
    """
    order = _dbc.get_active_book_order(phone)
    if not order or order.get("status") != "collecting":
        # No cart in progress (or already past collecting) → start fresh.
        return _start(phone, None)

    session = _dbc.get_session(DB, phone) or {}
    step = session.get("step") or ""
    if step == "staff_hold":
        step = session.get("prev_step") or ""

    # Still on (or before) the catalog → (re)send the book list. Note book_qty
    # is NOT here: items can be empty mid-counting, but the flow_cursor holds
    # their place, so we re-ask the quantity rather than restart selection.
    if step in ("", "book_select"):
        _dbc.save_session(DB, phone, step="book_select", needs_human=False)
        _send_select_list(phone)
        return []

    _dbc.save_session(DB, phone, step=step, needs_human=False)
    if step == "book_qty":
        cur = (order.get("flow_cursor") or {}).get("current")
        if cur and cur in bc.BOOKS:
            _send_qty_buttons(phone, bc.BOOKS[cur]["label"])
        else:                                   # corrupt cursor → re-pick books
            _dbc.save_session(DB, phone, step="book_select", needs_human=False)
            _send_select_list(phone)
    elif step == "book_pay":
        refreshed = _dbc.get_book_order(order["order_code"])
        if not _send_qr(phone, refreshed):
            _send_pay_buttons(phone)
    elif step in _RESUME_SENDERS:
        _RESUME_SENDERS[step](phone)
    else:
        # book_summary / book_edit* / anything else with real progress → show
        # the order summary (Confirm / Edit / Cancel) rather than re-asking.
        _send_summary(phone, _dbc.get_book_order(order["order_code"]))
    return []


def resume_order(phone: str) -> list[str]:
    """Public entry for the admin take-over button — continue a customer's
    dropped cart in place (see `_resume`)."""
    return _resume(phone)


def _send_parsed_confirm(phone: str, items: dict, totals: dict) -> None:
    lines = ", ".join(
        f"{bc.BOOKS[k]['label'].split(' (')[0]} × {q}"
        for k, q in items.items() if q and k in bc.BOOKS
    )
    _send_buttons(
        phone,
        f"🛒 {lines}\n"
        f"പുസ്തകം/Books ₹{totals['books_total']:.0f} + കൊറിയർ/Courier ₹{totals['courier']:.0f} "
        f"= *₹{totals['grand_total']:.0f}*\n\n"
        "ഈ ഓർഡർ ഉറപ്പിക്കണോ? / Confirm this order?",
        [("ord_yes", "✅ Yes / ശരി"), ("bk_change", "✏️ Change / മാറ്റം")],
    )


def _handle_parsed_confirm(phone: str, text: str, order: dict) -> list[str]:
    t = (text or "").strip().lower()
    if t == "ord_yes" or t in _AFFIRM:
        items = order.get("items") or {}
        if not items:
            _dbc.save_session(DB, phone, step="book_select")
            _send_select_list(phone)
            return []
        totals = _order_totals(order)
        _dbc.save_session(DB, phone, step="book_name")
        _send_text(
            phone,
            f"നിങ്ങളുടെ ഓർഡർ ആകെ *₹{totals['grand_total']:.0f}* "
            f"(₹{totals['courier']:.0f} കൊറിയർ ഉൾപ്പെടെ).\n"
            f"Your order comes to *₹{totals['grand_total']:.0f}* (incl. ₹{totals['courier']:.0f} courier).\n\n"
            "👤 പാർസൽ ലഭിക്കുന്ന ആളുടെ *പൂർണ്ണ പേര്* ടൈപ്പ് ചെയ്യൂ.\n"
            "Type the *full name* of the person receiving the parcel.",
        )
        return []
    # Re-typed a book order at the confirm step — update items and re-confirm,
    # rather than dumping the customer into the catalog.
    reparsed = bc.parse_customer_order(text)
    if reparsed:
        code = order["order_code"]
        totals = bc.divya_order_terms(order.get("phone"), reparsed,
                                      order.get("delivery_method") or "courier")
        _dbc.update_book_order(code, items=reparsed, flow_cursor={},
                               books_total=totals["books_total"],
                               courier=totals["courier"],
                               grand_total=totals["grand_total"])
        _send_parsed_confirm(phone, reparsed, totals)
        return []
    # Explicit change / no / a tapped book id — reopen the catalog to pick manually.
    if (t == "bk_change" or t in _NEGATE or "change" in t or "മാറ്റ" in t
            or t.startswith("bk_") or t.startswith("qty_")):
        _dbc.save_session(DB, phone, step="book_select")
        _send_select_list(phone)
        return []
    # Anything else — re-ask Yes / Change without losing the staged order.
    _send_parsed_confirm(phone, order.get("items") or {}, _order_totals(order))
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
    totals = bc.divya_order_terms(order.get("phone"), items,
                                  order.get("delivery_method") or "courier")
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
        f"നിങ്ങളുടെ ഓർഡർ ആകെ *₹{totals['grand_total']:.0f}* "
        f"(₹{totals['courier']:.0f} കൊറിയർ ഉൾപ്പെടെ).\n"
        f"Your order comes to *₹{totals['grand_total']:.0f}* (incl. ₹{totals['courier']:.0f} courier).\n\n"
        "👤 പാർസൽ ലഭിക്കുന്ന ആളുടെ *പൂർണ്ണ പേര്* ടൈപ്പ് ചെയ്യൂ.\n"
        "Type the *full name* of the person receiving the parcel.",
    )
    return []


def _handle_name(phone: str, text: str, order: dict) -> list[str]:
    name = (text or "").strip()
    if not _is_valid_name(name):
        _send_text(phone, _name_prompt())
        return []
    _dbc.update_book_order(order["order_code"], name=name)
    _dbc.save_session(DB, phone, step="book_address")
    _send_text(phone, _address_prompt())
    return []


def _handle_address(phone: str, text: str, order: dict) -> list[str]:
    address = (text or "").strip()
    if len(address) < 10:
        _send_text(phone, "വിലാസം വളരെ ചെറുതാണ്. സ്ഥലവും *PIN കോഡും* സഹിതം *പൂർണ്ണ വിലാസം* "
                          "ടൈപ്പ് ചെയ്യൂ. 📍\n"
                          "That address looks too short — include place and PIN code.")
        return []
    if not _has_pincode(address):
        _send_text(phone, "വിലാസത്തിൽ *6-അക്ക PIN കോഡ്* അവസാനം ഉൾപ്പെടുത്തൂ "
                          "(അതില്ലാതെ അയക്കാനാവില്ല). 📍\n"
                          "Please include your *6-digit PIN code*, *at the "
                          "end* of the address.\n"
                          "_ഉദാ / e.g.: House name, Place, District - 680001_")
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
        _send_text(phone, "*ശരിയായ ഫോൺ നമ്പർ* (10 അക്കം) ടൈപ്പ് ചെയ്യൂ. 📞\n"
                          "Please type the correct phone number (10 digits).")
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
        _send_text(phone, "കുഴപ്പമില്ല — വീണ്ടും തുടങ്ങാം.\nNo problem — let's start over.")
        _send_select_list(phone)
        return []
    if t == "ord_yes" or t in _AFFIRM:
        _dbc.update_book_order(code, status="awaiting_payment")
        _dbc.save_session(DB, phone, step="book_pay")
        refreshed = _dbc.get_book_order(code)
        if not _send_qr(phone, refreshed):
            totals = _order_totals(refreshed)
            _send_text(phone, f"*₹{totals['grand_total']:.0f}* ഞങ്ങളുടെ UPI യിലേക്ക് അടച്ച് "
                              "സ്ക്രീൻഷോട്ട് അയക്കൂ. (ഇമേജ് വീണ്ടും വേണമെങ്കിൽ *QR* റിപ്ലൈ ചെയ്യൂ.)\n"
                              "Pay to our UPI and send a screenshot. (Reply *QR* to retry the image.)")
        _send_pay_buttons(phone)
        return []
    _send_summary(phone, order)
    return []


def _handle_edit(phone: str, text: str, order: dict) -> list[str]:
    code = order["order_code"]
    t = (text or "").strip().lower()
    if t == "ed_name" or t in {"name", "recipient"}:
        _dbc.save_session(DB, phone, step="book_edit_name")
        _send_text(phone, "👤 *പുതിയ പേര്* ടൈപ്പ് ചെയ്യൂ.\nType the *new recipient name*.")
        return []
    if t == "ed_books" or "book" in t:
        _dbc.update_book_order(code, flow_cursor={"editing": True})
        _dbc.save_session(DB, phone, step="book_select")
        _send_select_list(phone, edit=True)
        return []
    if t == "ed_addr" or "address" in t:
        _dbc.save_session(DB, phone, step="book_edit_address")
        _send_text(phone, "📍 *പുതിയ വിലാസം* ടൈപ്പ് ചെയ്യൂ (PIN കോഡ് സഹിതം).\n"
                          "Type the new delivery address (incl. PIN).")
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
        _send_text(phone, "പാർസൽ ലഭിക്കുന്ന ആളുടെ *പൂർണ്ണ പേര്* ടൈപ്പ് ചെയ്യൂ. 👤\n"
                          "Please type the recipient's *full name*.")
        return []
    _dbc.update_book_order(order["order_code"], name=name)
    _dbc.save_session(DB, phone, step="book_summary")
    _send_summary(phone, _dbc.get_book_order(order["order_code"]))
    return []


def _handle_edit_address(phone: str, text: str, order: dict) -> list[str]:
    address = (text or "").strip()
    if len(address) < 10:
        _send_text(phone, "വിലാസം വളരെ ചെറുതാണ്. സ്ഥലവും *PIN കോഡും* സഹിതം *പൂർണ്ണ വിലാസം* "
                          "ടൈപ്പ് ചെയ്യൂ. 📍\n"
                          "That address looks too short — include place and PIN code.")
        return []
    if not _has_pincode(address):
        _send_text(phone, "വിലാസത്തിൽ *6-അക്ക PIN കോഡ്* അവസാനം ഉൾപ്പെടുത്തൂ "
                          "(അതില്ലാതെ അയക്കാനാവില്ല). 📍\n"
                          "Please include your *6-digit PIN code*, *at the "
                          "end* of the address.\n"
                          "_ഉദാ / e.g.: House name, Place, District - 680001_")
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
        _send_text(phone, "*ശരിയായ ഫോൺ നമ്പർ* (10 അക്കം) ടൈപ്പ് ചെയ്യൂ. 📞\n"
                          "Please type the correct phone number (10 digits).")
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
    _send_buttons(phone, "മറ്റെന്തെങ്കിലും സഹായം വേണോ?\nAnything else we can help with?",
                  [("po_yes", "✅ വേണം"), ("po_no", "❌ വേണ്ട")])
    return []


def _handle_post_order_ask(phone: str, text: str, name: str | None) -> list[str]:
    t = (text or "").strip().lower()
    if t == "po_no" or t in _NEGATE:
        try:
            _dbc.clear_session(DB, phone)
        except Exception:
            pass
        _send_text(phone, "*Printosky* തിരഞ്ഞെടുത്തതിന് നന്ദി! 🙏 നല്ലൊരു ദിവസം. 📚\n"
                          "Thank you for shopping with Printosky!")
        return []
    if t == "po_yes" or t in _AFFIRM:
        # Start from the top: clear state and show the general help menu.
        try:
            _dbc.clear_session(DB, phone)
        except Exception:
            pass
        _send_text(phone, "👍 *എങ്ങനെ സഹായിക്കാം? / How can we help?*\n\n"
                          "📄 *പ്രിന്റൗട്ട് / Printouts* — PDF/ഡോക്യുമെന്റ് ഇവിടെ അയക്കൂ.\n"
                          "📚 *പുസ്തകങ്ങൾ / books* — *books* എന്ന് റിപ്ലൈ ചെയ്യൂ.\n"
                          "🧑‍💼 *സ്റ്റാഫുമായി സംസാരിക്കാൻ / staff* — *agent* എന്ന് റിപ്ലൈ ചെയ്യൂ.")
        return []
    _send_buttons(phone, "മറ്റെന്തെങ്കിലും? *വേണം* അല്ലെങ്കിൽ *വേണ്ട* ടാപ്പ് ചെയ്യൂ.\n"
                         "Anything else? Tap Yes or No.",
                  [("po_yes", "✅ വേണം"), ("po_no", "❌ വേണ്ട")])
    return []


# ── Mid-flow intent resolver ──────────────────────────────────────────────────
# When a customer in a stateful step (esp. book_pay) sends something the rigid
# handlers don't recognise, try to UNDERSTAND it before falling back to a canned
# reply: deterministic parse → Haiku → human. Only tag a human when both machine
# layers fail. Design:
# docs/specs/2026-07-13-mid-flow-intent-and-media-forwarding-design.md
# Word-boundary matched so "of course" doesn't match "rs", "address" doesn't
# match "add", "totally" doesn't match "total". Malayalam cues stay substring.
_PRICE_RE = re.compile(
    r"\b(amount|price|how much|cost|rate|total|balance|charge|rs|rupees)\b", re.I)
_PRICE_ML = ("എത്ര", "വില", "രൂപ", "₹")
_ADD_BOOK_RE = re.compile(r"\b(one more|more book|another|book|add)\b", re.I)
_ADD_BOOK_ML = ("പുസ്തകം", "കൂടി", "ഒന്ന് കൂടി")


def _is_price_question(text: str) -> bool:
    t = text or ""
    return bool(_PRICE_RE.search(t)) or any(w in t for w in _PRICE_ML)


def _wants_more_books(text: str) -> bool:
    t = text or ""
    return bool(_ADD_BOOK_RE.search(t)) or any(h in t for h in _ADD_BOOK_ML)


def _balance_reply(order: dict) -> str:
    code = order.get("order_code") or "—"
    grand = float(order.get("grand_total") or 0)
    paid = float(order.get("amount_paid") or 0)
    bal = grand - paid
    return (f"🧾 *{code}* — ആകെ ₹{grand:.0f} · അടച്ചത് ₹{paid:.0f} · ബാക്കി ₹{bal:.0f}.\n"
            f"Total ₹{grand:.0f} · paid ₹{paid:.0f} · balance ₹{bal:.0f}. 🙏")


def _escalate_to_human(phone: str, order: dict, customer_msg: str) -> list[str]:
    """Both machine layers failed — hold the bot and ping Anu. No customer reply."""
    code = order.get("order_code") or "—"
    _dbc.save_session(DB, phone, step="book_pay", needs_human=True)
    try:
        from routing.intent import decide_intent
        guess = decide_intent(customer_msg or "")
    except Exception:
        guess = "unknown"
    try:
        _send_text(
            VERIFIER_PHONE,
            "🙋 *Needs a human*\n"
            f"{order.get('name') or phone} +{re.sub(r'[^0-9]', '', phone or '')}\n"
            f"Order {code}\n"
            f"They said: \"{(customer_msg or '').strip()[:300]}\"\n"
            f"(bot guess: {guess}) — please reply to them directly.")
    except Exception as exc:
        logger.error("escalation notify failed for %s: %s", code, exc)
    return []


def _add_books_to_order(phone: str, order: dict, add_items: dict[str, int]) -> list[str]:
    """Add book(s) to an in-progress order, recompute totals, charge only the delta.

    Guard: never silently re-charge an order that is already confirmed or fully
    paid — escalate to a human instead.
    """
    code = order.get("order_code")
    grand_now = float(order.get("grand_total") or 0)
    paid = float(order.get("amount_paid") or 0)
    if order.get("status") == "confirmed" or (grand_now and paid >= grand_now):
        return _escalate_to_human(
            phone, order, f"wants to add {add_items} to an already-completed order")

    new_items = {k: int(v) for k, v in (order.get("items") or {}).items()}
    for k, q in add_items.items():
        if k in bc.BOOKS:
            new_items[k] = new_items.get(k, 0) + int(q)

    terms = bc.divya_order_terms(phone, new_items, order.get("delivery_method") or "courier")
    _dbc.update_book_order(code, items=new_items,
                           books_total=terms["books_total"],
                           courier=terms["courier"],
                           grand_total=terms["grand_total"])
    _dbc.save_session(DB, phone, step="book_pay", needs_human=False)

    grand = float(terms["grand_total"])
    bal = grand - paid
    added = ", ".join(f"{bc.BOOKS[k]['label'].split(' (')[0]} ×{q}"
                      for k, q in add_items.items() if k in bc.BOOKS)
    return [
        f"➕ ചേർത്തു: {added}.\n"
        f"പുതിയ ആകെ ₹{grand:.0f} · അടച്ചത് ₹{paid:.0f} · ബാക്കി ₹{bal:.0f}.\n"
        f"ബാക്കി തുക അടച്ച് സ്ക്രീൻഷോട്ട് അയക്കൂ. 🙏\n\n"
        f"➕ Added {added}. New total ₹{grand:.0f} · paid ₹{paid:.0f} · balance ₹{bal:.0f}. "
        f"Pay the balance and send a screenshot."
    ]


def resolve_stuck_message(phone: str, text: str, order: dict) -> list[str] | None:
    """Understand a mid-flow message before the caller falls back to its canned
    reply. Ladder: deterministic book parse → Haiku book parse → price question
    → vague add-a-book (ask which) → escalate. Returns replies when handled, or
    None to let the caller run its default (safe fall-through)."""
    if not order or not order.get("order_code"):
        return None

    # 1. Cheap deterministic book parse (no LLM).
    items = bc.parse_customer_order(text)
    if items:
        return _add_books_to_order(phone, order, items)

    # 2. Cheap keyword intents (no LLM).
    if _is_price_question(text):
        return [_balance_reply(order)]

    if _wants_more_books(text):
        return [
            "📚 ഏത് പുസ്തകം ചേർക്കണം? / Which book to add?\n"
            "• Aksharamrutham (Malayalam) ₹200\n"
            "• Vidyamrut (Hindi) ₹150\n"
            "• Easy English ₹200\n"
            "പേര് ടൈപ്പ് ചെയ്യൂ / just type the name."
        ]

    # 3. Trivial ack / very short noise → let the default reply stand. Do this
    #    BEFORE the Haiku call so "ok"/"👍" never spend a model request.
    t = (text or "").strip()
    if len(t) < 6 or t.lower() in _AFFIRM:
        return None

    # 4. Haiku book extraction — last machine attempt before a human.
    items = _llm_parse_books(text)
    if items:
        return _add_books_to_order(phone, order, items)

    # 5. Both machine layers failed → escalate.
    return _escalate_to_human(phone, order, text)


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
        _send_text(phone, "❌ നിങ്ങളുടെ ഓർഡർ *റദ്ദാക്കി*. പുതിയ ഓർഡറിന് എപ്പോൾ വേണമെങ്കിലും "
                          "*books* എന്ന് റിപ്ലൈ ചെയ്യൂ. 🙏\n"
                          "Your order has been cancelled. Reply *books* anytime to start a new order.")
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
                   f"✅ *{code}* ന്റെ പേയ്മെന്റ് വിവരം ലഭിച്ചു.\n\n"
                   "ഇപ്പോൾ സ്ഥിരീകരിക്കുന്നു — ഉടൻ അപ്ഡേറ്റ് ലഭിക്കും. നന്ദി! 🙏\n"
                   f"We're confirming it now for *{code}* — you'll get an update shortly.")
        return []
    # Before looping the screenshot prompt, try to understand what they said
    # (add a book, ask the balance, or escalate to a human).
    resolved = resolve_stuck_message(phone, text, order)
    if resolved is not None:
        for r in resolved:
            _send_text(phone, r)
        return resolved
    _send_text(phone, "UPI പേയ്മെന്റ് പൂർത്തിയാക്കി, സ്ഥിരീകരണത്തിന്റെ *സ്ക്രീൻഷോട്ട്* "
                      "ഇവിടെ അയക്കൂ. 🙏\n"
                      "Please complete the UPI payment and send a screenshot here.")
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
        if not _dbc.create_book_order(code, phone, parsed["name"] or name, source="website"):
            code = _new_order_code()
            _dbc.create_book_order(code, phone, parsed["name"] or name, source="website")

    totals = bc.divya_order_terms(phone, parsed["items"], "courier")
    _dbc.update_book_order(
        code,
        items=parsed["items"],
        name=parsed["name"] or name,
        address=parsed["address"],
        contact_phone=parsed["phone"],
        books_total=totals["books_total"],
        courier=totals["courier"],
        grand_total=totals["grand_total"],
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


def _get_courier_slug(courier_name: str) -> str:
    name = (courier_name or "").strip().lower()
    if "speed" in name:
        return "speedpost"
    if "india" in name:
        return "indiapost"
    if "dtdc" in name:
        return "dtdc"
    if "delhivery" in name:
        return "delhivery"
    return name


def _fetch_live_tracking(courier_name: str, tracking_no: str) -> dict | None:
    api_key = os.environ.get("TRACKCOURIER_API_KEY")
    if not api_key:
        return None
    slug = _get_courier_slug(courier_name)
    try:
        import requests
        url = "https://api.trackcourier.io/v1/track"
        headers = {"X-API-Key": api_key}
        params = {"courier": slug, "tracking_number": tracking_no}
        res = requests.get(url, headers=headers, params=params, timeout=3.0)
        if res.status_code == 200:
            body = res.json()
            if body.get("success") and body.get("data"):
                return body["data"]
    except Exception as e:
        logger.error("TrackCourier fetch error: %s", e)
    return None


def compose_tracking_reply(order: dict) -> str:
    """Build the tracking message for a dispatched order."""
    code = order.get("order_code", "")
    courier = order.get("courier_name") or "DTDC"
    tn = order.get("tracking_no")
    if not tn:
        return (f"📦 Your order *{code}* has been *dispatched* via {courier}. "
                "Your tracking number will be shared shortly — reply here if you "
                "need help.")

    slug = _get_courier_slug(courier)
    if slug == "speedpost" or slug == "indiapost":
        track_url = "https://trackcourier.io/speed-post-tracking/"
    elif slug == "dtdc":
        track_url = DTDC_TRACK_URL
    elif slug == "delhivery":
        track_url = "https://trackcourier.io/delhivery-tracking/"
    else:
        track_url = "https://trackcourier.io"

    live_data = _fetch_live_tracking(courier, tn)
    if live_data:
        status = (live_data.get("MostRecentStatus") or live_data.get("ShipmentState") or live_data.get("status") or "").replace("_", " ").title()
        checkpoints = live_data.get("Checkpoints") or live_data.get("checkpoints") or []
        latest_msg = ""
        if checkpoints:
            latest = checkpoints[0]
            latest_msg = f"📍 Update: _{latest.get('Activity') or latest.get('message') or ''}_"
            location = latest.get("Location") or latest.get("location")
            if location:
                latest_msg += f" ({location})"

        return (
            f"📦 *Order Status Update:* {code}\n"
            f"🚚 Carrier: *{courier}*\n"
            f"🔖 Tracking No: *{tn}*\n"
            f"⚡ Current Status: *{status}*\n"
            f"{latest_msg}\n\n"
            f"🔗 Track here: {track_url}"
        )

    return (
        f"📦 Your order *{code}* shipped via *{courier}*.\n"
        f"🔖 Tracking / Reference no: *{tn}*\n"
        f"🔗 Track here: {track_url}\n"
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


def maybe_handle_location(phone: str) -> list[str] | None:
    """Handle a WhatsApp location share (a pin, not typed text).

    We have no reverse-geocoding — a lat/long pin can't be turned into the
    house-name/PO/district/PIN address DTDC needs — so mid-address-capture we
    ask the customer to type it instead rather than leaving them with no
    reply at all (the message previously matched no branch in the webhook
    dispatch and was silently dropped). Returns [] when handled (already
    sent), or None when the customer wasn't mid-address-entry (not ours to
    handle — the caller may still want to log/ignore the location).
    """
    session = _dbc.get_session(DB, phone) or {}
    step = session.get("step") or ""
    if step not in ("book_address", "book_edit_address"):
        return None
    _send_text(
        phone,
        "📍 ക്ഷമിക്കണം, ലൊക്കേഷൻ പിൻ വായിക്കാൻ കഴിയില്ല — ദയവായി നിങ്ങളുടെ "
        "*പൂർണ്ണ വിലാസം* ടൈപ്പ് ചെയ്യൂ (സ്ഥലം + PIN കോഡ് സഹിതം, അവസാനം PIN).\n"
        "Sorry, we can't read location pins yet — please *type* your full "
        "address instead (place name + PIN code, PIN last).\n"
        "_ഉദാ / e.g.: House name, Place, District - 680001_",
    )
    return []


def maybe_handle_book(phone: str, text: str, name: str | None = None) -> list[str] | None:
    session = _dbc.get_session(DB, phone) or {}
    step = session.get("step") or ""

    # Post-delivery review: customer replied to the feedback template.
    if step == "book_feedback":
        return _handle_feedback_reply(phone, text)

    if step not in _BOOK_STEPS:
        # A complete ORDER template is unambiguous — ingest it even when a
        # staff_hold is parked on the chat, so a stale hold never black-holes a
        # real order (e.g. customer re-sends after staff took over). A genuine
        # in-progress print job (job_id / non-book step) still takes precedence.
        if step == "staff_hold" or not _in_print_flow(session):
            web = _try_website_order(phone, text, name)
            if web is not None:
                return web
        if not _in_print_flow(session):
            # A customer asking about an already-shipped order → re-share tracking
            # (must come BEFORE is_book_trigger, since "how do I get the book" etc.
            # would otherwise re-open the catalog).
            track = _maybe_tracking_reply(phone, text)
            if track is not None:
                return track
        if not _in_print_flow(session):
            enquiry = _maybe_book_enquiry(phone, text, name)
            if enquiry is not None:
                return enquiry
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
        "book_confirm_parsed": _handle_parsed_confirm,
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

    # Segment 2: order confirmed, payment pending → ask them to finish paying.
    if order.get("status") == "awaiting_payment":
        total  = _order_totals(order)["grand_total"] if have else 0
        amt_ml = f" — ₹{total:.0f}" if total else ""
        amt_en = f" of ₹{total:.0f}" if total else ""
        return (
            "നമസ്കാരം 👋 നിങ്ങളുടെ പുസ്തക ഓർഡർ പേയ്മെന്റ് ബാക്കിയുണ്ട്" + amt_ml + ".\n"
            "പേയ്മെന്റ് QR വീണ്ടും വേണമെങ്കിൽ *PAY* എന്ന് റിപ്ലൈ ചെയ്യൂ. 🙏\n\n"
            "Your order has a pending payment" + amt_en + ". "
            "Reply *PAY* and we'll resend the payment QR to finish. 🙏"
        )

    # Segment 1 (default): cart started, never confirmed → invite them back.
    line = ""
    if have:
        cart = ", ".join(f"{bc.BOOKS[k]['label'].split(' (')[0]} × {q}" for k, q in have)
        line = f"\n\n🛒 In your cart: {cart}"
    return (
        "👋 *Did you still want the books?*\n"
        "നിങ്ങൾ ഓർഡർ തുടങ്ങിയിരുന്നു, പക്ഷേ പൂർത്തിയാക്കിയില്ല.\n"
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


# Both templates are Meta-approved (ml) → default ON so the full stuck-cart
# backlog is nudged on deploy with no extra Vercel config (matches the
# hardcoded-template pattern of book_feedback). An env var still overrides
# either — set to "" to disable a segment.
#   xtraa_cart_continue   (collecting)       body {{1}}=name
#   xtraa_payment_pending (awaiting_payment) body {{1}}=name {{2}}=order {{3}}=amount
CART_CONTINUE_TEMPLATE = os.environ.get("CART_CONTINUE_TEMPLATE", "xtraa_cart_continue")
CART_PAYMENT_TEMPLATE  = os.environ.get("CART_PAYMENT_TEMPLATE", "xtraa_payment_pending")


def _cart_template_for(order: dict):
    """(template_name, body_params) for a stuck cart's segment, or (None, None)
    when that segment's template env var isn't configured.
      collecting       → CART_CONTINUE_TEMPLATE  [name]
      awaiting_payment → CART_PAYMENT_TEMPLATE   [name, order_code, amount]
    """
    name = order.get("name") or "Customer"
    if order.get("status") == "awaiting_payment":
        if not CART_PAYMENT_TEMPLATE:
            return None, None
        items = order.get("items") or {}
        total = _order_totals(order)["grand_total"] if items else 0
        return CART_PAYMENT_TEMPLATE, [name, order.get("order_code") or "", f"{total:.0f}"]
    if not CART_CONTINUE_TEMPLATE:
        return None, None
    return CART_CONTINUE_TEMPLATE, [name]


def send_template_reminders(idle_hours: int = 24, window_hours: int = 168) -> dict:
    """Nudge the >24h backlog (≤7 days) that a free-form message can't reach, via
    the Meta-approved templates. Shares ``abandoned_reminder_at`` with the
    free-form sweep, so each cart is nudged at most once overall. A segment whose
    template is not configured is skipped (left for a later sweep).
    Returns {carts, reminded}.
    """
    if not CART_CONTINUE_TEMPLATE and not CART_PAYMENT_TEMPLATE:
        return {"carts": 0, "reminded": 0}          # nothing approved yet
    carts = _dbc.find_abandoned_book_carts(idle_hours=idle_hours, window_hours=window_hours)
    import whatsapp_notify as _wn
    sender = getattr(_wn, "_send_meta_template", None)
    if sender is None:
        return {"carts": len(carts), "reminded": 0}
    reminded = 0
    for o in carts:
        template, params = _cart_template_for(o)
        if not template:
            continue                                 # segment template not set → skip
        try:
            if sender(o["phone"], template, params, "ml"):
                _dbc.mark_abandoned_reminded(o["order_code"])
                reminded += 1
        except Exception as exc:
            logger.error("template reminder failed for %s: %s", o.get("order_code"), exc)
    return {"carts": len(carts), "reminded": reminded}


def run_cart_reminders() -> dict:
    """Full stuck-cart sweep: free-form for carts still in the 24h window +
    templates for the >24h backlog. Returns combined counts plus each breakdown.
    """
    freeform = send_abandoned_reminders()
    template = send_template_reminders()
    return {
        "carts":    freeform["carts"] + template["carts"],
        "reminded": freeform["reminded"] + template["reminded"],
        "freeform": freeform,
        "template": template,
    }


def _remind_verifier(order: dict) -> bool:
    """Re-send ONE payment_review order's verification prompt to Anu.

    Mirrors _forward_to_verifier but without the screenshot bytes (the sweep only
    has the stored proof URL). Re-uses the SAME button ids (pf_/pp_/pr_<payid>)
    so a tap still routes through handle_verifier_reply unchanged. Returns False
    only when there is nothing actionable to send.
    """
    code = order.get("order_code")
    pays = _dbc.get_book_payments(code) or []
    pending = [p for p in pays if p.get("status") == "pending"]
    pay = pending[-1] if pending else (pays[-1] if pays else None)
    totals  = _order_totals(order)
    grand   = totals["grand_total"]
    paid    = _dbc.book_amount_paid(code)
    balance = grand - paid
    proof   = order.get("payment_proof_url") or ""
    caption = (
        "⏰ *Reminder — payment still awaiting your check*\n"
        f"Order: {code}\n"
        f"Total ₹{grand:.0f} · Paid ₹{paid:.0f} · *Balance ₹{balance:.0f}*\n"
        f"Customer: {order.get('name') or '—'} "
        f"+{re.sub(r'[^0-9]', '', order.get('phone', '') or '')}\n"
        f"Items: {_cart_line(order.get('items') or {})}\n"
        f"📍 {order.get('address', '') or ''}"
        + (f"\n🧾 {proof}" if proof else "")
    )
    _send_text(VERIFIER_PHONE, caption)
    if pay:
        payid = pay.get("id")
        _send_buttons(
            VERIFIER_PHONE, f"Full or part payment for *{code}*?",
            [(f"pf_{payid}", f"✅ Full ₹{balance:.0f}"),
             (f"pp_{payid}", "➗ Part payment"),
             (f"pr_{payid}", "❌ Not received")],
        )
    else:
        _send_buttons(
            VERIFIER_PHONE, f"Payment received for *{code}*?",
            [(f"vconf_{code}", "✅ Confirm"), (f"vrej_{code}", "❌ Reject")],
        )
    return True


def send_verifier_reminders(idle_minutes: int = 30, cooldown_hours: int = 3) -> dict:
    """Re-surface payment_review orders Anu hasn't actioned yet.

    The original "Payment to verify" prompt is fire-and-forget: if Anu misses it
    (it lands at night, or stacks behind another prompt) the order strands in
    payment_review forever — the customer was told "we're verifying" and nothing
    ever closes the loop. This sweep re-pings Anu and stamps verifier_reminder_at
    so it nudges at most once per `cooldown_hours`. Returns {found, reminded}.
    """
    stale = _dbc.find_stale_payment_reviews(idle_minutes=idle_minutes,
                                            cooldown_hours=cooldown_hours)
    reminded = 0
    for o in stale:
        try:
            if _remind_verifier(o):
                _dbc.mark_verifier_reminded(o["order_code"])
                reminded += 1
        except Exception as exc:
            logger.error("verifier reminder failed for %s: %s", o.get("order_code"), exc)
    return {"found": len(stale), "reminded": reminded}


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
    totals  = bc.divya_order_terms(parsed["phone"], items, deliv)
    courier = totals["courier"]
    grand   = totals["grand_total"]
    commission = totals["commission"]
    pradeep_commission = totals["pradeep_commission"]

    from datetime import datetime
    code = f"XTR-{datetime.now().strftime('%Y%m%d')}-{os.urandom(4).hex().upper()}"
    row = _dbc.create_walk_in_order(
        code, parsed["name"], parsed["phone"], parsed["address"], items,
        totals["books_total"], courier, grand,
        payment_mode="", status="confirmed",
        commission=commission, pradeep_commission=pradeep_commission,
        payment_collected_by=parsed["payment_collected_by"],
        delivery_method=deliv, via_divya=True, source="divya",
    )
    if not row:
        _send_text(VERIFIER_PHONE, "⚠️ Something went wrong saving the order — please try again.")
        return

    pay_label = {"divya":   "Divya collected",
                 "oxygen":  "Oxygen collected",
                 "pending": "Pending (we collect)"}.get(parsed["payment_collected_by"],
                                                        parsed["payment_collected_by"])
    deliv_label = "Office pickup" if deliv == "xtraa_office" else "Courier"
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
    deliv_label = "Office pickup" if delivery_method == "xtraa_office" else "Courier"
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
    totals  = bc.divya_order_terms(phone, items, delivery_method)
    courier = totals["courier"]
    grand   = totals["grand_total"]
    commission = totals["commission"]
    pradeep_commission = totals["pradeep_commission"]
    row = _dbc.create_walk_in_order(
        code, name, phone, address, items, totals["books_total"], courier, grand,
        payment_mode="", status="confirmed", commission=commission,
        pradeep_commission=pradeep_commission,
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
    grand = _order_totals(order)["grand_total"]
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
    grand = _order_totals(order)["grand_total"]
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


def _send_staged_confirm(o: dict) -> None:
    """Show ONE 'Confirm this order?' prompt for the staged order `o`."""
    totals = bc.divya_order_terms(o["phone"], o["items"], "courier")
    _send_buttons(
        VERIFIER_PHONE,
        "📋 *Confirm this order?*\n"
        f"{o['name']} · +{o['phone']}\n"
        f"{o.get('address') or '—'}\n"
        f"{_cart_line(o['items'])}\n"
        f"Books ₹{totals['books_total']:.0f} + Courier ₹{totals['courier']:.0f} = "
        f"*₹{totals['grand_total']:.0f}*\n"
        f"Divya commission (ML): ₹{totals['commission']:.0f} | "
        f"Pradeep (HI+EN): ₹{totals['pradeep_commission']:.0f}",
        [("aok", "✅ Confirm & print"), ("axx", "❌ Cancel")],
    )


def _anu_add_book_to_staged(key: str) -> None:
    """A 2nd+ book tap on an already-staged order: add the book to that same
    order and re-show one confirm prompt — never stage a parallel order."""
    import json
    sess = _anu_session()
    try:
        blob = json.loads(sess.get("saved_json") or "{}")
    except Exception:
        blob = {}
    o = blob.get("order") if isinstance(blob, dict) else None
    if not o:
        return
    items = dict(o.get("items") or {})
    items[key] = items.get(key, 0) + 1
    o["items"] = items
    o["book_explicit"] = True
    _anu_save_buffer(blob.get("raw", ""), "anu_staged", order=o)
    _send_staged_confirm(o)


def _handle_anu_freeform(text: str) -> None:
    """Auto-combine Anu's messages into one order; ask for gaps; confirm before save."""
    msg = (text or "").strip()
    # A book-button tap: if an order is already staged, ADD this book to it (one
    # order, one confirm) instead of re-staging a parallel single-book order.
    # Otherwise turn it into plain text the parser understands (the first book).
    bm = re.match(r"^abook_(malayalam|hindi|english)$", msg)
    if bm:
        if _anu_session().get("step") == "anu_staged":
            _anu_add_book_to_staged(bm.group(1))
            return
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
    _send_staged_confirm(o)


def _confirm_staged() -> None:
    """Anu tapped Confirm — create the staged order. Idempotent: a rapid second
    tap finds no staged order (buffer already cleared) and is a no-op, so a
    double-tap never creates two orders."""
    import json
    sess = _anu_session()
    if sess.get("step") != "anu_staged":
        _send_text(VERIFIER_PHONE, "✅ That order was already saved.")
        return
    try:
        o = json.loads(sess.get("saved_json") or "{}").get("order")
    except Exception:
        o = None
    if not o or not o.get("items"):
        _anu_clear_buffer()
        _send_text(VERIFIER_PHONE, "⚠️ That order expired — please forward it again.")
        return
    # Clear BEFORE creating so a second Confirm tap sees no staged order.
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


BOOK_FEEDBACK_TEMPLATE = os.environ.get("BOOK_FEEDBACK_TEMPLATE", "book_feedback_request")


def _request_book_feedback(order: dict) -> bool:
    """Ask the customer to review the book — via the Meta-approved template (the
    only way to reach them after delivery, when their 24h window is closed).

    Sent only when BOOK_FEEDBACK_TEMPLATE is set (template approved in Meta).
    On success, arm reply capture by parking the session at step 'book_feedback'.
    Returns True only if the request was actually sent.
    """
    phone = order.get("phone")
    if not BOOK_FEEDBACK_TEMPLATE or not phone:
        return False
    try:
        import whatsapp_notify as _wn
        sender = getattr(_wn, "_send_meta_template", None)
        if sender is None:
            return False
        ok = sender(phone, BOOK_FEEDBACK_TEMPLATE, [order.get("name") or "Customer"], "ml")
    except Exception as exc:
        logger.error("book feedback template send failed for %s: %s",
                     order.get("order_code"), exc)
        return False
    if ok:
        try:
            _dbc.save_session(DB, phone, step="book_feedback", needs_human=False)
        except Exception:
            pass
    return bool(ok)


def _handle_dtdc_delivered(t: str) -> bool:
    """Anu forwards a DTDC 'delivered' SMS → mark the matching order delivered and
    request a book review. Returns True when the message IS a DTDC delivery note
    (so it is consumed here, never falling through to the LLM order parser).

    Format: 'R5001087357 is delivered on 20/6/2026 to NAME Share feedback ...'.
    """
    m = re.search(r"\b(R\d{6,})\b", t or "")
    if not m or "deliver" not in (t or "").lower():
        return False
    ref = m.group(1)
    order = _dbc.find_dispatched_by_tracking(ref)
    if not order:
        _send_text(VERIFIER_PHONE,
                   f"⚠️ DTDC {ref}: no dispatched order found with that tracking number. "
                   "If it's ours, mark it delivered manually.")
        return True
    code = order["order_code"]
    _dbc.mark_book_delivered(code)
    requested = False
    try:
        requested = _request_book_feedback(order)
    except Exception as exc:
        logger.error("feedback request failed for %s: %s", code, exc)
    tail = (" Review request sent to the customer."
            if requested else
            " (Customer review request pending — feedback template not live yet.)")
    _send_text(VERIFIER_PHONE, f"✅ {code} marked *delivered* ({ref}).{tail}")
    return True


def _handle_feedback_reply(phone: str, text: str) -> list[str]:
    """A delivered customer replied to the review request: parse a 1-5 rating +
    optional comment, save it, forward to Anu, thank them. Their reply reopened
    the 24h window, so the thank-you sends fine."""
    t = (text or "").strip()
    order = _dbc.latest_delivered_order(phone) or {}
    code = order.get("order_code")
    m = re.match(r"^\s*([1-5])\b[\s.\-:)]*(.*)$", t, re.S)
    rating = int(m.group(1)) if m else None
    comment = ((m.group(2).strip() if m else t) or None)
    if code:
        _dbc.save_book_feedback(code, phone, rating=rating, comment=comment)
        stars = ("⭐" * rating) if rating else "—"
        _send_text(
            VERIFIER_PHONE,
            f"📣 *Book feedback* — {order.get('name') or phone} ({code})\n"
            f"Rating: {rating if rating else '—'}/5 {stars}\n"
            f"Comment: {comment or '—'}")
    _dbc.save_session(DB, phone, step="post_order", needs_human=False)
    return ["നിങ്ങളുടെ വിലയേറിയ അഭിപ്രായത്തിന് നന്ദി! 📚✨"]


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

    # 3.5 DTDC delivery confirmation forwarded by Anu → mark delivered + ask review.
    if _handle_dtdc_delivered(t):
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
    items = order.get("items") or {}
    confirm_fields = {
        "status":              "confirmed",
        "commission":          bc.commission_for(items),
        "pradeep_commission":  bc.pradeep_commission_for(items),
        "confirmed_at":        datetime.now(timezone.utc).isoformat(),
    }
    # Hard rule: Divya's own order is courier-free + commission-free.
    if bc.is_divya_phone(order.get("phone")):
        confirm_fields.update(commission=0.0, pradeep_commission=0.0,
                              via_divya=False, courier=0.0,
                              grand_total=order.get("books_total") or 0.0)
    ok = _dbc.update_book_order(order_code, **confirm_fields)

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

    totals = _order_totals(fresh)
    _send_text(
        phone,
        f"🎉 *Order confirmed!*\n\n"
        f"Order: *{order_code}*\n"
        f"Amount: ₹{totals['grand_total']:.0f}\n\n"
        "Your books will be couriered to the address you shared. "
        "Thank you for ordering from Printosky! 📚",
    )
    return {"ok": True, "order": fresh}

# ════════════════════════════════════════════════════════════════════════════════
# MA SOCIOLOGY FLOW (SNGU Sem 1)
# Mirrors the main book flow but uses the SOC_BOOKS catalog with no commission.
# Steps: soc_select → soc_qty → soc_address → soc_delivery → soc_summary → soc_pay
# ════════════════════════════════════════════════════════════════════════════════

_SOC_TRIGGER_WORDS = {
    "sociology", "sociological", "sngu", "soc",
    "m21so001dc", "m21so002dc", "m21so003dc", "m21so004dc", "m21so001ac",
}
_SOC_TRIGGER_PHRASES = [
    "ma sociology", "ma socio", "sociology book", "sociology books",
    "sngu book", "sngu books", "ma book", "semester 1", "sem 1",
    "social research", "sociological theory", "indian sociology",
    "economy polity", "project planning",
]
_SOC_STEPS = {
    "soc_select", "soc_qty", "soc_address", "soc_delivery",
    "soc_summary", "soc_pay", "post_soc_order",
}


def is_soc_trigger(text: str) -> bool:
    """True when the message should start the MA Sociology order flow."""
    if not text:
        return False
    t = text.strip().lower()
    words = set(re.split(r"[^\w]+", t))
    if words & _SOC_TRIGGER_WORDS:
        return True
    return any(phrase in t for phrase in _SOC_TRIGGER_PHRASES)


def _soc_send_catalog(phone: str) -> None:
    rows = []
    for i, k in enumerate(bc.SOC_BOOK_KEYS, 1):
        b = bc.SOC_BOOKS[k]
        rows.append((f"soc_{i}", f"{i}. {b['label']}", f"₹{b['price']} · {b['code']}"))
    body = (
        "📚 *MA Sociology — SNGU Sem 1*\n"
        "5 books available at ₹300 each.\n\n"
        "Reply the number(s) of the books you want\n"
        "(e.g. *1*, *1,3*, or *all* for all five).\n\n"
        "1. Foundations of Sociological Theory\n"
        "2. Fundamentals of Social Research\n"
        "3. Indian Sociology\n"
        "4. Economy, Polity and Society\n"
        "5. Project Planning and Management"
    )
    _send_text(phone, body)
    _dbc.save_session(DB, phone, step="soc_select", needs_human=False)


def _soc_cart(phone: str) -> dict:
    """Load the current sociology cart from session extra, or {}."""
    sess = _dbc.get_session(DB, phone) or {}
    try:
        import json
        return json.loads(sess.get("saved_json") or "{}").get("soc_cart") or {}
    except Exception:
        return {}


def _soc_save(phone: str, cart: dict, selected: list[str],
              cursor: int, **fields) -> None:
    import json
    sess = _dbc.get_session(DB, phone) or {}
    try:
        payload = json.loads(sess.get("saved_json") or "{}")
    except Exception:
        payload = {}
    payload["soc_cart"] = cart
    payload["soc_selected"] = selected
    payload["soc_cursor"] = cursor
    payload.update(fields)
    _dbc.save_session(DB, phone, saved_json=json.dumps(payload, ensure_ascii=False))


def _soc_data(phone: str) -> dict:
    import json
    sess = _dbc.get_session(DB, phone) or {}
    try:
        return json.loads(sess.get("saved_json") or "{}")
    except Exception:
        return {}


def _soc_summary_text(phone: str, data: dict) -> str:
    cart = data.get("soc_cart") or {}
    totals = bc.compute_soc_totals(cart)
    lines = bc.soc_line_items(cart)
    delivery = data.get("soc_delivery", "courier")
    courier = 0.0 if delivery == "xtraa_office" else totals["courier"]
    grand = totals["books_total"] + courier
    items_text = "\n".join(
        f"  {l['label']}: {l['qty']} × ₹{l['unit_price']:.0f} = ₹{l['line_total']:.0f}"
        for l in lines
    )
    deliv_label = "Office pickup" if delivery == "xtraa_office" else f"Courier ₹{courier:.0f}"
    return (
        f"📋 *Order Summary — MA Sociology*\n\n"
        f"{items_text}\n\n"
        f"Books: ₹{totals['books_total']:.0f}\n"
        f"Delivery: {deliv_label}\n"
        f"*Total: ₹{grand:.0f}*\n\n"
        f"Address: {data.get('soc_address') or '—'}"
    )


def maybe_handle_soc(phone: str, text: str, name: str | None = None) -> list[str] | None:
    """Handle MA Sociology order flow. Returns [] (already sent), a list of
    reply strings, or None (not our message to handle)."""
    sess = _dbc.get_session(DB, phone) or {}
    step = sess.get("step") or ""
    t = (text or "").strip()

    # Route into flow if triggered or already in soc steps
    if step not in _SOC_STEPS and not is_soc_trigger(t):
        return None

    # Fresh trigger — show catalog
    if step not in _SOC_STEPS or is_soc_trigger(t):
        _soc_send_catalog(phone)
        return []

    # ── soc_select: parse which books they want ────────────────────────────
    if step == "soc_select":
        # handle list-row tap: soc_1 ... soc_5
        m = re.match(r"^soc_(\d)$", t)
        if m:
            t = m.group(1)
        selected = bc.parse_soc_selection(t)
        if not selected:
            return ["Please reply with numbers like *1*, *1,3* or *all* to pick your books."]
        _soc_save(phone, {}, selected, 0)
        # ask qty for first selected book
        first = selected[0]
        label = bc.SOC_BOOKS[first]["label"]
        _dbc.save_session(DB, phone, step="soc_qty", needs_human=False)
        _send_buttons(phone,
                      f"How many copies of *{label}*?",
                      [("sqty_1", "1"), ("sqty_2", "2"), ("sqty_3", "3")])
        return []

    # ── soc_qty: collect quantity for each selected book one at a time ─────
    if step == "soc_qty":
        data = _soc_data(phone)
        selected = data.get("soc_selected") or []
        cursor = int(data.get("soc_cursor") or 0)
        cart = data.get("soc_cart") or {}
        if not selected:
            _soc_send_catalog(phone)
            return []
        # parse qty
        raw = re.sub(r"^sqty_", "", t)
        qty = bc.parse_qty(raw)
        if qty is None:
            label = bc.SOC_BOOKS[selected[cursor]]["label"]
            return [f"Please reply with a number (1, 2, 3 …) for *{label}*."]
        cart[selected[cursor]] = qty
        cursor += 1
        if cursor < len(selected):
            # next book
            label = bc.SOC_BOOKS[selected[cursor]]["label"]
            _soc_save(phone, cart, selected, cursor)
            _send_buttons(phone,
                          f"How many copies of *{label}*?",
                          [("sqty_1", "1"), ("sqty_2", "2"), ("sqty_3", "3")])
            return []
        # all qtys collected — ask address
        _soc_save(phone, cart, selected, cursor)
        _dbc.save_session(DB, phone, step="soc_address", needs_human=False)
        return ["Please share your *full delivery address* (with PIN code):"]

    # ── soc_address: collect delivery address ─────────────────────────────
    if step == "soc_address":
        if len(t) < 10:
            return ["Please share your full address including PIN code."]
        data = _soc_data(phone)
        _soc_save(phone, data.get("soc_cart") or {},
                  data.get("soc_selected") or [], int(data.get("soc_cursor") or 0),
                  soc_address=t)
        _dbc.save_session(DB, phone, step="soc_delivery", needs_human=False)
        _send_buttons(phone,
                      "How would you like to receive the books?",
                      [("sdel_courier", "📦 Courier"), ("sdel_office", "🏫 Office pickup")])
        return []

    # ── soc_delivery: courier or office ───────────────────────────────────
    if step == "soc_delivery":
        if "office" in t or t == "sdel_office":
            delivery = "xtraa_office"
        else:
            delivery = "courier"
        data = _soc_data(phone)
        _soc_save(phone, data.get("soc_cart") or {},
                  data.get("soc_selected") or [], int(data.get("soc_cursor") or 0),
                  soc_address=data.get("soc_address"), soc_delivery=delivery)
        _dbc.save_session(DB, phone, step="soc_summary", needs_human=False)
        data["soc_delivery"] = delivery
        summary = _soc_summary_text(phone, data)
        _send_buttons(phone, summary,
                      [("sconf", "✅ Confirm"), ("scanc", "❌ Cancel")])
        return []

    # ── soc_summary: confirm or cancel ────────────────────────────────────
    if step == "soc_summary":
        if t in {"scanc", "cancel"} | _NEGATE:
            _dbc.save_session(DB, phone, step=None, needs_human=False)
            return ["Order cancelled. Reply *sociology* anytime to start again. 🙏"]
        if t not in {"sconf"} | _AFFIRM:
            data = _soc_data(phone)
            summary = _soc_summary_text(phone, data)
            _send_buttons(phone, summary,
                          [("sconf", "✅ Confirm"), ("scanc", "❌ Cancel")])
            return []
        # Confirmed — create order in DB
        data = _soc_data(phone)
        cart = data.get("soc_cart") or {}
        delivery = data.get("soc_delivery", "courier")
        totals = bc.compute_soc_totals(cart)
        courier = 0.0 if delivery == "xtraa_office" else totals["courier"]
        grand = totals["books_total"] + courier
        code = _new_order_code()
        try:
            _dbc.create_book_order(
                order_code=code,
                name=name or phone,
                phone=phone,
                address=data.get("soc_address") or "",
                items=cart,
                status="awaiting_payment",
                books_total=totals["books_total"],
                courier=courier,
                grand_total=grand,
                commission=0.0,
                delivery_method=delivery,
                source="whatsapp",
            )
        except Exception as exc:
            logger.error("create soc order failed for %s: %s", phone, exc)
            return ["Sorry, there was an error saving your order. Please try again. 🙏"]
        _dbc.save_session(DB, phone, step="soc_pay", needs_human=False)
        # Send payment QR
        try:
            import whatsapp_notify as _wn
            _wn._send_meta_media(phone, _QR_PATH, "image",
                                 caption=(
                                     f"📚 *MA Sociology Books — ₹{grand:.0f}*\n\n"
                                     f"Order *{code}*\n"
                                     "Please pay to *Oxygen Students Paradise* and send a *screenshot*."
                                 ))
        except Exception:
            _send_text(phone,
                       f"📚 *MA Sociology Books — ₹{grand:.0f}*\n\n"
                       f"Order: *{code}*\n"
                       "Please pay and send a *screenshot* of the payment. 🙏")
        return []

    # ── soc_pay: waiting for payment screenshot ────────────────────────────
    if step == "soc_pay":
        return ["Thanks! Please send a *screenshot* of your payment to confirm. 🙏"]

    return None
