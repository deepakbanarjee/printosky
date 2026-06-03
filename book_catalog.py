"""Xtraa book campaign — catalog data and pure order logic.

This module holds NO I/O. Everything here is pure and unit-tested so the
conversational flow in book_bot.py can stay thin. Edit BOOKS / SET_PRICE /
COURIER below to change pricing — nothing else needs to change.
"""

from __future__ import annotations

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

# Flat courier charge added to every order (owner decision, 2026-06-02).
COURIER = 75

# Divya teacher (Xtraa coordinator) earns a flat commission per physical book
# sold. Applies to every book order; courier is excluded. CRITICAL: this figure
# drives the settlement ledger — change it here and nowhere else.
COMMISSION_PER_BOOK = 50

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
    for ln in lines[1:]:
        if ":" not in ln:
            continue
        key, _, val = ln.partition(":")
        key, val = key.strip().lower(), val.strip()
        if not val:
            continue
        if key in aliases:
            qty = parse_qty(val)
            if qty:
                items[aliases[key]] = items.get(aliases[key], 0) + qty
        else:
            fields[key] = val

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


def _is_complete_uniform_set(items: dict[str, int]) -> int:
    """If items are all three books at the same qty N (N>=1), return N; else 0."""
    present = {k: q for k, q in items.items() if q and q > 0}
    if set(present.keys()) != set(BOOK_KEYS):
        return 0
    qtys = set(present.values())
    if len(qtys) == 1:
        return qtys.pop()
    return 0


def compute_totals(items: dict[str, int]) -> dict:
    """Compute books_total, courier and grand_total for a cart.

    items maps book key → qty. Zero/absent qty is ignored. If the cart is
    exactly N complete sets (all three, same qty), set pricing applies;
    otherwise each book is charged at its individual offer price.
    """
    clean = {k: int(q) for k, q in items.items() if q and int(q) > 0}

    sets = _is_complete_uniform_set(clean)
    if sets:
        books_total = SET_PRICE * sets
    else:
        books_total = sum(BOOKS[k]["price"] * q for k, q in clean.items() if k in BOOKS)

    courier = COURIER if clean else 0
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
    """Divya teacher's commission for a cart: ₹50 per physical book sold.

    Courier is excluded. This is the single source of truth for the figure that
    feeds the settlement ledger — never recompute it inline elsewhere.
    """
    return float(COMMISSION_PER_BOOK * total_book_count(items))


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
