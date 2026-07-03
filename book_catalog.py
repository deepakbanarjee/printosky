"""Book campaign — catalog data and pure order logic.

This module holds NO I/O. Everything here is pure and unit-tested so the
conversational flow in book_bot.py can stay thin. Edit BOOKS / SET_PRICE / courier constants below to change pricing — nothing else needs to change.
"""

from __future__ import annotations

import math
import re

# ── Catalog ───────────────────────────────────────────────────────────────────
# Prices locked from the campaign poster (offer price vs MRP).
# Labels are editable — correct the titles here if needed.
BOOKS: dict[str, dict] = {
    "malayalam": {"label": "Aksharamrutham (Malayalam)", "mrp": 250, "price": 200},
    "hindi":     {"label": "Vidyamrut (Hindi)",          "mrp": 200, "price": 150},
    "english":   {"label": "Easy English",               "mrp": 250, "price": 200},
}

# Stable display / selection order.
BOOK_KEYS: list[str] = ["malayalam", "hindi", "english"]

# Price for a complete set of all three (one of each). Honours the poster offer.
SET_PRICE = 549

# Book weights in grams (used for courier calculation).
_WEIGHT_G: dict[str, int] = {"malayalam": 500, "hindi": 250, "english": 500}

# Courier pricing: ₹75 for first 1 kg, +₹40 per additional 500 g slab (ceiling).
_COURIER_BASE      = 75
_COURIER_THRESHOLD = 1000   # grams included in base rate
_COURIER_SLAB_G    = 500    # grams per extra slab
_COURIER_SLAB_RATE = 40     # ₹ per extra slab


# Bulk incentive: orders of 5+ physical books earn a discount on courier charge.
_BULK_QTY_THRESHOLD    = 5
_BULK_COURIER_DISCOUNT = 0.07   # 7% off courier for 5+ books


def courier_charge(items: dict[str, int]) -> float:
    """Weight-based courier charge for an order.

    ₹75 for the first kg, +₹40 per additional 500 g slab (ceiling division).
    Returns 0 for an empty cart.
    """
    clean = {k: int(q) for k, q in items.items() if q and int(q) > 0}
    if not clean:
        return 0.0
    weight = sum(_WEIGHT_G.get(k, 0) * q for k, q in clean.items())
    extra_slabs = math.ceil(max(0, weight - _COURIER_THRESHOLD) / _COURIER_SLAB_G)
    base = _COURIER_BASE + extra_slabs * _COURIER_SLAB_RATE
    if sum(clean.values()) >= _BULK_QTY_THRESHOLD:
        base *= (1 - _BULK_COURIER_DISCOUNT)
    return float(round(base))

# Divya teacher (coordinator) earns a flat commission per physical book
# sold. Applies to every book order; courier is excluded. CRITICAL: this figure
# drives the settlement ledger — change it here and nowhere else.
COMMISSION_PER_BOOK = 50

# Divya teacher's own WhatsApp number (digits only, with country code). The rule
# is hard-coded: EVERY book order — any channel — earns Divya her per-book
# commission. The ONLY exception is when Divya orders for HERSELF from this
# number: she pays the book cost alone — no courier, no commission. Matched on
# the last 10 digits so +91 / 0 / spacing variants all resolve to the same person.
DIVYA_PHONE = "919526738641"


def is_divya_phone(phone: str | None) -> bool:
    """True when `phone` is Divya's own number (the no-courier / no-commission case)."""
    digits = re.sub(r"\D", "", phone or "")
    return len(digits) >= 10 and digits[-10:] == DIVYA_PHONE[-10:]


MAX_QTY = 99

# Numbered selection map shown to the customer.
_SELECTION_MAP = {"1": "malayalam", "2": "hindi", "3": "english"}
_ALL_TOKENS = {"4", "all", "all three", "all 3", "set", "set of 3", "everything", "3 books"}


def parse_selection(text: str) -> list[str] | None:
    """Parse a book selection reply into an ordered, de-duplicated list of keys.

    Accepts:
      "1" / "2" / "3"            → single book
      "1,3" / "1 3" / "1, 3"     → multiple books
      "4" / "all" / "set"        → all three
    Returns keys in canonical BOOK_KEYS order, or None if nothing valid.
    """
    if not text:
        return None
    t = text.strip().lower()

    if t in _ALL_TOKENS:
        return list(BOOK_KEYS)

    tokens = [tok for tok in re.split(r"[\s,]+", t) if tok]
    if not tokens:
        return None

    chosen: set[str] = set()
    for tok in tokens:
        if tok in _ALL_TOKENS:
            return list(BOOK_KEYS)
        key = _SELECTION_MAP.get(tok)
        if key:
            chosen.add(key)
        else:
            # Any unrecognised token invalidates the whole reply — re-prompt.
            return None

    if not chosen:
        return None
    return [k for k in BOOK_KEYS if k in chosen]


def parse_qty(text: str) -> int | None:
    """Parse a quantity reply. Returns an int in 1..MAX_QTY, or None if invalid."""
    if not text:
        return None
    t = text.strip()
    # Reject explicit negatives ("-1") — \d+ alone would silently match the "1".
    if t.startswith("-"):
        return None
    m = re.search(r"\d+", t)
    if not m:
        return None
    try:
        n = int(m.group())
    except ValueError:
        return None
    if n < 1 or n > MAX_QTY:
        return None
    return n


def parse_anu_order(text: str) -> dict | None:
    """Parse an order message forwarded by Anu using the fixed ORDER template.

    Returns None when the message is not an order at all (first line is not
    'ORDER'). Otherwise returns a dict describing the parsed order:

        {
          "ok": bool,                    # False if validation failed
          "errors": list[str],           # human-readable problems, if any
          "name": str | None,
          "phone": str | None,           # digits only
          "address": str | None,
          "items": {book_key: qty},
          "payment_collected_by": "oxygen" | "divya" | "pending",
          "delivery_method": "courier" | "xtraa_office",
        }

    Template (case-insensitive labels, one field per line):

        ORDER
        Name: <customer name>
        Phone: <10-digit number>
        Address: <full address + PIN>
        Aksharamrutham: <qty>
        Vidyamrut: <qty>
        Easy English: <qty>
        Payment: pending | divya | oxygen
        Delivery: courier | office
    """
    if not text:
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines or not lines[0].lower().startswith("order"):
        return None

    aliases = {
        "malayalam": "malayalam", "aksharamrutham": "malayalam", "ml": "malayalam",
        "hindi": "hindi", "vidyamrut": "hindi", "hi": "hindi",
        "english": "english", "easy english": "english", "en": "english",
    }

    fields: dict[str, str] = {}
    items: dict[str, int] = {}
    last_field: str | None = None
    for ln in lines[1:]:
        if ":" not in ln:
            # Continuation of the previous free-text field — real addresses are
            # often multi-line (house name / PO / district / PIN each on their
            # own line) and must not be silently dropped just because they lack
            # a "key:" prefix.
            if last_field:
                fields[last_field] = (fields[last_field] + "\n" + ln).strip()
            continue
        key, _, val = ln.partition(":")
        key, val = key.strip().lower(), val.strip()
        if not val:
            last_field = None
            continue
        if key in aliases:
            qty = parse_qty(val)
            if qty:
                items[aliases[key]] = items.get(aliases[key], 0) + qty
            last_field = None
        else:
            fields[key] = val
            last_field = key

    name    = fields.get("name")
    phone   = re.sub(r"\D", "", fields.get("phone", "")) or None
    address = fields.get("address")

    pay = (fields.get("payment", "") or "").lower()
    if "divya" in pay:
        payment_collected_by = "divya"
    elif "oxygen" in pay:
        payment_collected_by = "oxygen"
    else:
        payment_collected_by = "pending"

    deliv = (fields.get("delivery", "") or "").lower()
    delivery_method = "xtraa_office" if "office" in deliv else "courier"

    errors: list[str] = []
    if not name:
        errors.append("Name is missing")
    if not phone or len(phone) < 10:
        errors.append("A valid 10-digit phone number is missing")
    if not items:
        errors.append("No book quantities found")
    if delivery_method == "courier" and not address:
        errors.append("Address is required for courier delivery")

    return {
        "ok":                   not errors,
        "errors":               errors,
        "name":                 name,
        "phone":                phone,
        "address":              address,
        "items":                items,
        "payment_collected_by": payment_collected_by,
        "delivery_method":      delivery_method,
    }


def _complete_set_count(items: dict[str, int]) -> int:
    """Return how many complete sets (one of each book) can be formed from items."""
    present = {k: q for k, q in items.items() if q and q > 0}
    if set(present.keys()) != set(BOOK_KEYS):
        return 0
    return min(present.values())


def compute_totals(items: dict[str, int]) -> dict:
    """Compute books_total, courier and grand_total for a cart.

    items maps book key → qty. Zero/absent qty is ignored. If the cart is
    exactly N complete sets (all three, same qty), set pricing applies;
    otherwise each book is charged at its individual offer price.
    """
    clean = {k: int(q) for k, q in items.items() if q and int(q) > 0}

    sets = _complete_set_count(clean)
    if sets:
        books_total = SET_PRICE * sets
        remainder = {k: q - sets for k, q in clean.items() if k in BOOKS and q - sets > 0}
        books_total += sum(BOOKS[k]["price"] * q for k, q in remainder.items())
    else:
        books_total = sum(BOOKS[k]["price"] * q for k, q in clean.items() if k in BOOKS)

    courier = courier_charge(clean)
    return {
        "books_total": float(books_total),
        "courier":     float(courier),
        "grand_total": float(books_total + courier),
        "is_set":      bool(sets),
    }


def total_book_count(items: dict[str, int]) -> int:
    """Total physical books in a cart (sum of quantities for known titles)."""
    return sum(int(q) for k, q in (items or {}).items()
               if k in BOOKS and q and int(q) > 0)


def commission_for(items: dict[str, int]) -> float:
    """Divya teacher's commission: ₹50 per Malayalam book sold.

    Hindi and English commission goes to Pradeep sir — see pradeep_commission_for.
    Courier excluded. Single source of truth for the Divya settlement ledger.
    """
    return float(COMMISSION_PER_BOOK * int(items.get("malayalam") or 0))


def pradeep_commission_for(items: dict[str, int]) -> float:
    """Pradeep sir's commission: ₹50 per Hindi or English book sold."""
    return float(COMMISSION_PER_BOOK * (int(items.get("hindi") or 0) + int(items.get("english") or 0)))


def divya_order_terms(phone: str | None, items: dict[str, int],
                      delivery_method: str = "courier") -> dict:
    """Final money terms for a book order, applying the hard-coded Divya rule.

    Every order earns Divya ₹50/book and is billed courier as usual — EXCEPT
    Divya's own direct order (her number), which is courier-free and
    commission-free (she pays the book cost alone). This is the single source of
    truth: every create / confirm / edit path must derive these fields from here
    so the customer total and the settlement ledger can never drift apart.

    Returns: {books_total, courier, grand_total, commission, via_divya}.
    """
    totals = compute_totals(items)
    books_total = totals["books_total"]
    if is_divya_phone(phone):
        return {"books_total": books_total, "courier": 0.0,
                "grand_total": books_total, "commission": 0.0,
                "pradeep_commission": 0.0, "via_divya": False}
    courier = 0.0 if delivery_method == "xtraa_office" else totals["courier"]
    return {"books_total": books_total, "courier": courier,
            "grand_total": books_total + courier,
            "commission": commission_for(items),
            "pradeep_commission": pradeep_commission_for(items), "via_divya": True}


def line_items(items: dict[str, int]) -> list[dict]:
    """Return ordered line items for the order summary.

    Each entry: {key, label, qty, unit_price, line_total}.
    """
    clean = {k: int(q) for k, q in items.items() if q and int(q) > 0}
    out = []
    for k in BOOK_KEYS:
        q = clean.get(k, 0)
        if q <= 0:
            continue
        unit = BOOKS[k]["price"]
        out.append({
            "key":        k,
            "label":      BOOKS[k]["label"],
            "qty":        q,
            "unit_price": float(unit),
            "line_total": float(unit * q),
        })
    return out


# ── MA Sociology catalog (SNGU Semester 1) ───────────────────────────────────
# Sreenarayanaguru Open University — MA Sociology, Sem 1 SLM books.
# No commission applies; sold directly by Printosky.
SOC_BOOKS: dict[str, dict] = {
    "soc1": {"label": "Foundations of Sociological Theory", "code": "M21SO001DC", "price": 300},
    "soc2": {"label": "Fundamentals of Social Research",    "code": "M21SO002DC", "price": 300},
    "soc3": {"label": "Indian Sociology",                   "code": "M21SO003DC", "price": 300},
    "soc4": {"label": "Economy, Polity and Society",        "code": "M21SO004DC", "price": 300},
    "soc5": {"label": "Project Planning and Management",    "code": "M21SO001AC", "price": 300},
}
SOC_BOOK_KEYS: list[str] = ["soc1", "soc2", "soc3", "soc4", "soc5"]

_SOC_WEIGHT_G: dict[str, int] = {k: 350 for k in SOC_BOOK_KEYS}


def soc_courier_charge(items: dict[str, int]) -> float:
    """Courier charge for a sociology order (same weight/rate logic as main catalog)."""
    clean = {k: int(q) for k, q in items.items() if k in SOC_BOOKS and q and int(q) > 0}
    if not clean:
        return 0.0
    weight = sum(_SOC_WEIGHT_G.get(k, 350) * q for k, q in clean.items())
    extra_slabs = math.ceil(max(0, weight - _COURIER_THRESHOLD) / _COURIER_SLAB_G)
    base = _COURIER_BASE + extra_slabs * _COURIER_SLAB_RATE
    if sum(clean.values()) >= _BULK_QTY_THRESHOLD:
        base *= (1 - _BULK_COURIER_DISCOUNT)
    return float(round(base))


def compute_soc_totals(items: dict[str, int]) -> dict:
    """Totals for a sociology order. No commission, no set-price discount."""
    clean = {k: int(q) for k, q in items.items() if k in SOC_BOOKS and q and int(q) > 0}
    books_total = sum(SOC_BOOKS[k]["price"] * q for k, q in clean.items())
    courier = soc_courier_charge(clean)
    return {
        "books_total": float(books_total),
        "courier":     float(courier),
        "grand_total": float(books_total + courier),
    }


def soc_line_items(items: dict[str, int]) -> list[dict]:
    """Ordered line items for a sociology order summary."""
    clean = {k: int(q) for k, q in items.items() if k in SOC_BOOKS and q and int(q) > 0}
    out = []
    for k in SOC_BOOK_KEYS:
        q = clean.get(k, 0)
        if q <= 0:
            continue
        unit = SOC_BOOKS[k]["price"]
        out.append({"key": k, "label": SOC_BOOKS[k]["label"],
                    "qty": q, "unit_price": float(unit), "line_total": float(unit * q)})
    return out


def parse_soc_selection(text: str) -> list[str] | None:
    """Parse a sociology book selection reply (1-5, comma/space separated, or 'all').

    Returns canonical-order list of soc keys, or None if invalid.
    """
    if not text:
        return None
    t = text.strip().lower()
    if t in {"all", "all five", "all 5", "5 books", "everything"}:
        return list(SOC_BOOK_KEYS)
    tokens = [tok for tok in re.split(r"[\s,]+", t) if tok]
    chosen: set[str] = set()
    for tok in tokens:
        if tok in {"all", "all five", "all 5"}:
            return list(SOC_BOOK_KEYS)
        if tok.isdigit() and 1 <= int(tok) <= 5:
            chosen.add(SOC_BOOK_KEYS[int(tok) - 1])
        else:
            return None
    if not chosen:
        return None
    return [k for k in SOC_BOOK_KEYS if k in chosen]


# ── payment-confirmation text detection ───────────────────────────────────────
# Customers sometimes paste their bank/UPI confirmation text instead of sending a
# screenshot, e.g. "Rs.275.00 paid ... to OXYGEN STUDENTS, UPI Ref 616443327414
# ... -Canara Bank". parse_payment_text recognises such a message so the bot can
# route it to staff (Anu) for verification instead of looping "send a screenshot".

_PAY_REF_RE = re.compile(
    r"\b(?:upi\s*ref(?:erence)?|ref(?:erence)?(?:\s*(?:no|number|id))?|utr|"
    r"txn(?:\s*(?:no|id))?|transaction\s*(?:id|no|number|ref))"
    r"\s*[:#.\-]?\s*((?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{6,})",
    re.IGNORECASE,
)
_PAY_AMOUNT_RE = re.compile(
    r"(?:rs\.?|inr|₹)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", re.IGNORECASE)
_PAY_WORD_RE = re.compile(
    r"\b(paid|credited|debited|received|sent|success(?:ful)?|transferred)\b",
    re.IGNORECASE,
)
_PAY_LONGNUM_RE = re.compile(r"\b(\d{10,})\b")


def parse_payment_text(text: str) -> dict | None:
    """Detect a pasted payment/UPI confirmation. Returns {"ref", "amount"} or None.

    Heuristic — a message is treated as a payment confirmation when it has an
    explicit transaction-reference label (UPI Ref / UTR / Txn ID / …), OR a
    'paid/credited/…' verb together with a money amount or a long (>=10 digit)
    number. Conservative enough that ordinary chat ("ok", "how many copies", a
    bare phone number, a single digit) returns None.
    """
    if not text:
        return None
    t = text.strip()
    if len(t) < 8:
        return None

    ref = None
    m = _PAY_REF_RE.search(t)
    if m:
        ref = m.group(1)

    amount = None
    am = _PAY_AMOUNT_RE.search(t)
    if am:
        try:
            amount = float(am.group(1).replace(",", ""))
        except ValueError:
            amount = None

    has_word = bool(_PAY_WORD_RE.search(t))
    longnum = _PAY_LONGNUM_RE.search(t)

    is_payment = bool(ref) or (has_word and (amount is not None or longnum is not None))
    if not is_payment:
        return None

    if ref is None and longnum is not None:
        ref = longnum.group(1)
    return {"ref": ref, "amount": amount}
