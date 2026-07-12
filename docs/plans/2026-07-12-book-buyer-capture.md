# Book-Buyer Capture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop losing book buyers who *type* their order or *ask price/payment* — auto-answer price+delivery, and capture free-typed orders with a confirm step, reusing the existing address→payment flow.

**Architecture:** Pure parsing/FAQ helpers go in `book_catalog.py`; the conversational glue (a confirm step + an enquiry triage) goes in `book_bot.py` at the `maybe_handle_book` entry, which runs before the front-door router and already receives the message text. Deterministic-first parsing with a Haiku fallback (reusing `anu_parser`). No new tables; typed orders create the same `book_orders` row a tapped order would.

**Tech Stack:** Python 3.11, pytest, existing `book_catalog` / `book_bot` / `db_cloud` (`_dbc`) modules, `anu_parser` (Claude Haiku).

---

## Reference facts (verified in the codebase)

- `book_catalog.BOOKS` = `{"malayalam":{label:"Aksharamrutham (Malayalam)",price:200}, "hindi":{label:"Vidyamrut (Hindi)",price:150}, "english":{label:"Easy English",price:200}}`; `BOOK_KEYS=["malayalam","hindi","english"]`; `_COURIER_BASE=75`; `parse_qty(text)` returns 1..99 or None.
- `book_catalog.divya_order_terms(phone, items, delivery)` returns `{"books_total","courier","grand_total",...}`; `compute_totals` / `line_items` exist.
- `book_bot` uses `import db_cloud as _dbc`. Order lifecycle: `_dbc.create_book_order(code,phone,name)`, `_dbc.update_book_order(code, items=..., books_total=..., courier=..., grand_total=..., flow_cursor=...)`, `_dbc.get_active_book_order(phone)`, `_dbc.get_book_order(code)`, `_dbc.save_session(DB,phone,step=...)`. `_new_order_code()` mints a code.
- `book_bot._BOOK_STEPS` is the set of steps that keep a message inside the book flow; `maybe_handle_book` dispatches active steps via a `handlers` dict.
- The "all books counted" tail of `_handle_qty` (book_bot.py ~703-723) is the exact transition we mirror on confirm-YES: set items+totals → `step="book_name"` → send total + name prompt.
- `_send_text`, `_send_buttons`, `_send_list`, `_send_qr`, `_send_select_list`, `_payment_caption`, `_order_totals`, `is_book_trigger`, `_in_print_flow`, `_AFFIRM`, `_NEGATE` all exist in `book_bot`.
- Tests: `tests/test_book_bot.py` provides the `fake` fixture (in-memory `FakeDB` bound onto `_dbc`, and `book_bot._send_list/_send_buttons/_send_text/_send_qr` stubbed into a `sent` dict). `tests/test_book_catalog.py` tests pure catalog functions directly.

---

## File structure

| File | Change |
|------|--------|
| `book_catalog.py` | **Add** `parse_customer_order`, `is_book_faq`, `book_faq_text` (pure). |
| `book_bot.py` | **Add** `_llm_parse_books`, `_send_parsed_confirm`, `_handle_parsed_confirm`, `_maybe_book_enquiry`; register `book_confirm_parsed`; **modify** `_payment_caption` and the `maybe_handle_book` entry. |
| `tests/test_book_catalog.py` | Tests for the 3 pure helpers. |
| `tests/test_book_bot.py` | Tests for the confirm step, enquiry triage, payment caption. |

---

## Task 1: `parse_customer_order` (pure)

**Files:** Modify `book_catalog.py`; Test `tests/test_book_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_book_catalog.py  (append)
from book_catalog import parse_customer_order


class TestParseCustomerOrder:
    def test_single_title_default_qty(self):
        assert parse_customer_order("Aksharamrutham") == {"malayalam": 1}

    def test_title_with_qty(self):
        assert parse_customer_order("aksharamrutham 2 copy") == {"malayalam": 2}

    def test_qty_before_title(self):
        assert parse_customer_order("2 easy english") == {"english": 2}

    def test_language_words(self):
        assert parse_customer_order("malayalam book venam") == {"malayalam": 1}

    def test_multi_book_defaults_one_each(self):
        assert parse_customer_order("malayalam and hindi") == {"malayalam": 1, "hindi": 1}

    def test_vidyamrut_alias(self):
        assert parse_customer_order("vidyamrut 3") == {"hindi": 3}

    def test_no_book_returns_none(self):
        assert parse_customer_order("how much for delivery") is None

    def test_empty_returns_none(self):
        assert parse_customer_order("") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_catalog.py::TestParseCustomerOrder -v`
Expected: FAIL — cannot import `parse_customer_order`.

- [ ] **Step 3: Implement**

```python
# book_catalog.py  (add near parse_qty)
# Customer-typed order parsing. Deterministic title/keyword match; quantities via
# parse_qty. Bare language words are accepted because the book campaign is the
# shop's main WhatsApp push — the confirm step (book_bot) is the safety net.
_CUSTOMER_TITLE_TOKENS: dict[str, list[str]] = {
    "malayalam": ["aksharamrutham", "aksharamritham", "malayalam", "അക്ഷരാമൃതം"],
    "hindi":     ["vidyamrut", "vidyamrutham", "hindi", "വിദ്യാമൃത്"],
    "english":   ["easy english", "easyenglish", "english"],
}


def parse_customer_order(text: str) -> dict[str, int] | None:
    """Parse a customer's free-typed book order into {book_key: qty}, or None.

    Deterministic: matches known titles / language words. A single named book
    takes any quantity found in the text; multiple books default to 1 each
    (the confirm step lets the customer adjust).
    """
    if not text:
        return None
    t = text.strip().lower()
    found: list[str] = []
    for key, tokens in _CUSTOMER_TITLE_TOKENS.items():
        if any(tok in t for tok in tokens):
            found.append(key)
    if not found:
        return None
    qty = parse_qty(t) if len(found) == 1 else None
    return {k: (qty if (qty and len(found) == 1) else 1) for k in found}
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_book_catalog.py::TestParseCustomerOrder -v`
Expected: PASS (8).

- [ ] **Step 5: Commit**

```bash
git add book_catalog.py tests/test_book_catalog.py
git commit -m "feat(book): parse_customer_order for free-typed orders"
```

---

## Task 2: `is_book_faq` + `book_faq_text` (pure)

**Files:** Modify `book_catalog.py`; Test `tests/test_book_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_book_catalog.py  (append)
from book_catalog import is_book_faq, book_faq_text, BOOKS


class TestBookFaq:
    def test_price_words(self):
        for m in ("what is the price", "ethra rupees", "how much", "book cost"):
            assert is_book_faq(m) is True

    def test_delivery_words(self):
        for m in ("delivery how many days", "when will i get it", "courier charge"):
            assert is_book_faq(m) is True

    def test_payment_words(self):
        for m in ("gpay number", "how to pay", "upi"):
            assert is_book_faq(m) is True

    def test_non_faq(self):
        assert is_book_faq("aksharamrutham") is False
        assert is_book_faq("hi") is False

    def test_faq_text_lists_prices_from_catalog(self):
        txt = book_faq_text()
        assert str(int(BOOKS["malayalam"]["price"])) in txt   # 200
        assert str(int(BOOKS["hindi"]["price"])) in txt       # 150
        assert "3" in txt and "5 days" in txt                 # 3-5 days
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_catalog.py::TestBookFaq -v`
Expected: FAIL — cannot import `is_book_faq` / `book_faq_text`.

- [ ] **Step 3: Implement**

```python
# book_catalog.py  (add after parse_customer_order)
_FAQ_WORDS = {
    "price", "prices", "cost", "rate", "rates", "ethra", "വില",
    "delivery", "courier", "കൊറിയർ", "days",
    "gpay", "upi", "payment", "pay", "account",
}
_FAQ_PHRASES = [
    "how much", "how many rupees", "when will i get", "how many days",
    "which number", "g pay",
]


def is_book_faq(text: str) -> bool:
    """True if the message is a price / delivery / payment question."""
    if not text:
        return False
    t = text.strip().lower()
    words = set(re.split(r"[^\wഀ-ൿ]+", t))
    if words & _FAQ_WORDS:
        return True
    return any(p in t for p in _FAQ_PHRASES)


def book_faq_text() -> str:
    """Bilingual price + delivery answer, built from the live catalog."""
    lines = "\n".join(
        f"• {BOOKS[k]['label'].split(' (')[0]} — ₹{BOOKS[k]['price']:.0f}"
        for k in BOOK_KEYS
    )
    return (
        "📚 *പുസ്തകങ്ങൾ / Books — വില / Price*\n"
        f"{lines}\n"
        f"+ കൊറിയർ / courier from ₹{_COURIER_BASE}\n\n"
        "🚚 ഡെലിവറി / Delivery: *3–5 days* by courier.\n"
        "💳 ഓർഡർ ചെയ്‌താൽ ഉടനെ UPI QR അയക്കും / We'll send the UPI QR as soon as you order.\n\n"
        "ഓർഡർ ചെയ്യാൻ പുസ്തകത്തിന്റെ പേരും എണ്ണവും ടൈപ്പ് ചെയ്യൂ (ഉദാ: *Aksharamrutham 2*), അല്ലെങ്കിൽ താഴെ ടാപ്പ് ചെയ്യൂ 👇\n"
        "To order, reply with the book & quantity, or tap below 👇"
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_book_catalog.py::TestBookFaq -v`
Expected: PASS (5).

- [ ] **Step 5: Commit**

```bash
git add book_catalog.py tests/test_book_catalog.py
git commit -m "feat(book): is_book_faq + catalog-driven book_faq_text"
```

---

## Task 3: UPI-number fallback in the payment caption

**Files:** Modify `book_bot.py` (`_payment_caption`); Test `tests/test_book_bot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_book_bot.py  (append; module already imports book_bot, bc)
def test_payment_caption_includes_upi_number():
    order = {"order_code": "XTR-1", "phone": "919999999999",
             "items": {"malayalam": 1}, "delivery_method": "courier"}
    cap = book_bot._payment_caption(order)
    assert "9072034907" in cap
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_bot.py::test_payment_caption_includes_upi_number -v`
Expected: FAIL — "9072034907" not in caption.

- [ ] **Step 3: Implement** — append the fallback line to the returned string in `_payment_caption` (book_bot.py ~371-379). Change the final `return (...)` to include, before the closing paren:

```python
        "After paying, send a screenshot of the confirmation here.\n\n"
        "💳 QR സ്കാൻ ചെയ്യാൻ പറ്റുന്നില്ലേ? *9072034907* എന്ന നമ്പറിലേക്ക് UPI അയക്കൂ.\n"
        "Can't scan? Pay by UPI to *9072034907* (GPay / PhonePe)."
```

(Replace the existing final line `"After paying, send a screenshot of the confirmation here."` with the block above — same string concatenation, no trailing comma issues.)

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_book_bot.py::test_payment_caption_includes_upi_number -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add book_bot.py tests/test_book_bot.py
git commit -m "feat(book): offer UPI number 9072034907 when QR can't be scanned"
```

---

## Task 4: Confirm step (`book_confirm_parsed`)

**Files:** Modify `book_bot.py`; Test `tests/test_book_bot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_book_bot.py  (append)
def test_parsed_confirm_yes_advances_to_name(fake):
    db, sent = fake
    # Seed a confirm-pending order the way _maybe_book_enquiry will.
    db.create_book_order("XTR-9", "91888", name=None)
    db.update_book_order("XTR-9", items={"malayalam": 1},
                         books_total=200, courier=75, grand_total=275)
    db.save_session("supabase", "91888", step="book_confirm_parsed")
    out = book_bot.maybe_handle_book("91888", "yes")
    assert out == []
    assert db.sessions["91888"]["step"] == "book_name"
    assert any("full name" in m.lower() or "പേര" in m for m in sent["text"])

def test_parsed_confirm_change_reopens_catalog(fake):
    db, sent = fake
    db.create_book_order("XTR-8", "91777", name=None)
    db.update_book_order("XTR-8", items={"hindi": 1})
    db.save_session("supabase", "91777", step="book_confirm_parsed")
    out = book_bot.maybe_handle_book("91777", "bk_change")
    assert out == []
    assert db.sessions["91777"]["step"] == "book_select"
    assert sent["list"]        # catalog select list was sent
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_bot.py::test_parsed_confirm_yes_advances_to_name tests/test_book_bot.py::test_parsed_confirm_change_reopens_catalog -v`
Expected: FAIL — step `book_confirm_parsed` isn't handled (message ignored / no name step).

- [ ] **Step 3: Implement**

3a. Register the step. In `book_bot.py`, add `"book_confirm_parsed"` to the `_BOOK_STEPS` set, and add to the `handlers` dict in `maybe_handle_book`:
```python
        "book_confirm_parsed": _handle_parsed_confirm,
```

3b. Add the sender + handler (near the other `_handle_*` functions):
```python
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
    # Anything else — change / no / a tapped book id — reopen the catalog.
    _dbc.save_session(DB, phone, step="book_select")
    _send_select_list(phone)
    return []
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_book_bot.py -k "parsed_confirm" -v`
Expected: PASS (2).

- [ ] **Step 5: Commit**

```bash
git add book_bot.py tests/test_book_bot.py
git commit -m "feat(book): book_confirm_parsed step for typed-order confirmation"
```

---

## Task 5: Enquiry triage at the book entry (+ Haiku fallback)

**Files:** Modify `book_bot.py`; Test `tests/test_book_bot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_book_bot.py  (append)
def test_typed_order_creates_confirm(fake):
    db, sent = fake
    out = book_bot.maybe_handle_book("91555", "Aksharamrutham 1 copy")
    assert out == []
    assert db.sessions["91555"]["step"] == "book_confirm_parsed"
    order = db.get_active_book_order("91555")
    assert order["items"] == {"malayalam": 1}
    assert sent["buttons"] and "ord_yes" in sent["buttons"][-1]

def test_price_question_sends_faq(fake):
    db, sent = fake
    out = book_bot.maybe_handle_book("91556", "malayalam book price ethra")
    assert out == []
    assert any("3–5 days" in m for m in sent["text"])
    assert sent["list"]     # catalog opened after the FAQ

def test_llm_fallback_used_when_deterministic_misses(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda text: {"english": 2})
    # 'order books' is a book trigger but names no specific title deterministically.
    out = book_bot.maybe_handle_book("91557", "order books for me")
    assert out == []
    assert db.get_active_book_order("91557")["items"] == {"english": 2}

def test_non_book_message_returns_none(fake):
    db, sent = fake
    assert book_bot.maybe_handle_book("91558", "hello there") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_bot.py -k "typed_order or price_question or llm_fallback or non_book_message" -v`
Expected: FAIL — enquiry triage not wired.

- [ ] **Step 3: Implement**

3a. Add the helpers (near `_start`):
```python
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
      1. a parseable typed order  -> stage + confirm
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
```

3b. Wire it into `maybe_handle_book`, in the `step not in _BOOK_STEPS` branch, immediately **before** the existing `if is_book_trigger(text) and not _in_print_flow(session): return _start(...)` line:
```python
        if not _in_print_flow(session):
            enquiry = _maybe_book_enquiry(phone, text, name)
            if enquiry is not None:
                return enquiry
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_book_bot.py -k "typed_order or price_question or llm_fallback or non_book_message" -v`
Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add book_bot.py tests/test_book_bot.py
git commit -m "feat(book): triage typed orders + price/delivery FAQ at book entry"
```

---

## Task 6: Full verification

- [ ] **Step 1: Run the book test files**

Run: `pytest tests/test_book_bot.py tests/test_book_catalog.py -q`
Expected: all pass.

- [ ] **Step 2: Full non-browser suite stays green**

Run: `pytest -q -p no:cacheprovider --ignore=tests/test_browser_admin.py --ignore=tests/test_browser_mis.py -o addopts=""`
Expected: 0 failed. (Investigate any new failure; the change touches only the not-mid-flow book entry + the payment caption.)

- [ ] **Step 3: End-to-end dry-run of a typed order**

Run:
```bash
python -c "import book_bot; book_bot.maybe_handle_book('919999999999','Aksharamrutham 2 copy')"
```
Expected: no exception (a confirm prompt is 'sent' via the real senders if env allows; harmless locally).

---

## Self-review notes

- **Spec coverage:** §4 FAQ → Tasks 2 & 5; §5 typed-order + confirm → Tasks 1, 4, 5; §6 payment number → Task 3; D4 Haiku fallback → Task 5 (`_llm_parse_books`); D5 bilingual → strings in Tasks 2/4; D6 reuse name→address → Task 4 sets `book_name` and the existing `_handle_name`/`_handle_address` continue. D7 → Task 3.
- **Type consistency:** `parse_customer_order → dict[str,int]|None`; confirm order carries `items` + totals set by `divya_order_terms`; `_maybe_book_enquiry` returns `list|None` matching `maybe_handle_book`'s contract; step name `book_confirm_parsed` is identical in `_BOOK_STEPS`, the handlers dict, and every `save_session`.
- **No placeholders:** every step has runnable code + commands.
- **Known tradeoffs:** bare language words ("english") parse as a book — the confirm step (not auto-order) is the guard; the Haiku fallback only fires on `is_book_trigger` misses, bounding cost.

---

## Roadmap context

- Ships independently of Plan 1 (front-door) and Plan 2 (catalog split). After Plan 2 removes Malayalam from `BOOK_KEYS`, `book_faq_text` and `parse_customer_order` still resolve Malayalam via `BOOKS` / `_CUSTOMER_TITLE_TOKENS` (route it to the Malayalam flow there).
