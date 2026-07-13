# Mid-flow Intent + Never-skip Media Forwarding — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the book bot understand mid-flow messages (deterministic → Haiku → human) instead of looping a canned reply, auto-add a book with delta charging, and never drop a payment screenshot from a customer who owes money.

**Architecture:** A single reusable `resolve_stuck_message()` in `book_bot.py` runs a cheap→expensive→human ladder and is called by dead-end handlers (starting with `_handle_pay`) before their default reply. Media handling in `api/index.py` gates payment forwarding on the *order balance* (not the session step) and records `media_url` for every image so the admin transcript (which already renders images) can show them.

**Tech Stack:** Python 3, pytest (`tests/test_book_bot.py` `FakeDB` + `fake` fixture), existing `routing.intent.classify_intent`/`decide_intent` (claude-haiku-4-5), `book_catalog` pricing, Supabase (`conversation_log`, `book_orders`, `book_payments`).

**Spec:** [docs/specs/2026-07-13-mid-flow-intent-and-media-forwarding-design.md](../specs/2026-07-13-mid-flow-intent-and-media-forwarding-design.md)

**Note on scope:** During planning we confirmed (a) `handle_payment_proof` already accepts `partially_paid`; (b) `admin.html` already renders inbound images inline via `convRenderBubble`. So Feature 2c needs only a verification step, and the media work reduces to the `api/index.py` gate + return-value fix (Task 6).

---

## File Structure

- **Modify** `book_bot.py` — add helpers `_is_price_question`, `_wants_more_books`, `_balance_reply`, `_add_books_to_order`, `_escalate_to_human`, and orchestrator `resolve_stuck_message`; wire the orchestrator into `_handle_pay`.
- **Modify** `api/index.py` — in `_handle_media`, add `"partially_paid"` to the payment gate and return the stored `book-payments/…` path (so `media_url` is logged) instead of `None`.
- **Modify** `tests/test_book_bot.py` — unit tests for the new helpers/orchestrator + a `_handle_pay` integration test.
- **Create** `tests/test_media_forwarding.py` — tests for the `_handle_media` gate + return path.

All new module-level book helpers live next to the existing `_handle_pay` block. `re` and `logger` are already imported in `book_bot.py`; `book_catalog` is imported as `bc`, db as `_dbc`, and `VERIFIER_PHONE`/`DB` are module globals.

---

## Task 1: `_add_books_to_order` — merge a book, recompute, charge the delta

**Files:**
- Modify: `book_bot.py` (add helper near `_handle_pay`, ~line 1054)
- Test: `tests/test_book_bot.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_book_bot.py`:

```python
def test_add_books_to_order_charges_delta(fake):
    db = fake
    db.orders["C1"] = {"order_code": "C1", "phone": "919", "name": "Ajeesh",
                       "items": {"malayalam": 1}, "status": "partially_paid",
                       "grand_total": 275.0, "amount_paid": 200.0,
                       "_seq": 1}
    order = db.get_book_order("C1")
    replies = book_bot._add_books_to_order("919", order, {"english": 1})
    # order now has both books, recomputed to 475
    assert db.orders["C1"]["items"] == {"malayalam": 1, "english": 1}
    assert float(db.orders["C1"]["grand_total"]) == 475.0
    # reply mentions the added book and the ₹200 balance (475 - 200 paid)
    assert replies and "Added" in replies[0]
    assert "200" in replies[0]
    # not escalated
    assert db.sessions.get("919", {}).get("needs_human") is not True


def test_add_books_guard_blocks_completed_order(fake):
    db = fake
    db.orders["C2"] = {"order_code": "C2", "phone": "919", "name": "X",
                       "items": {"malayalam": 1}, "status": "confirmed",
                       "grand_total": 275.0, "amount_paid": 275.0, "_seq": 1}
    sent = []
    import book_bot as bb
    bb._send_text = lambda p, m: sent.append((p, m))          # capture Anu ping
    order = db.get_book_order("C2")
    replies = bb._add_books_to_order("919", order, {"english": 1})
    # did NOT re-charge: items unchanged, escalated to human instead
    assert db.orders["C2"]["items"] == {"malayalam": 1}
    assert db.sessions["919"]["needs_human"] is True
    assert replies == []
    assert any(p == bb.VERIFIER_PHONE for p, _ in sent)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_bot.py::test_add_books_to_order_charges_delta tests/test_book_bot.py::test_add_books_guard_blocks_completed_order -v`
Expected: FAIL — `AttributeError: module 'book_bot' has no attribute '_add_books_to_order'`.

- [ ] **Step 3: Implement `_add_books_to_order`**

Add to `book_bot.py` immediately above `def _handle_pay`:

```python
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
```

(`_escalate_to_human` is added in Task 2; the guard test imports it via the same module, and Task 2 runs before the full suite is green. Implement Task 2's helper now if running strictly in order — see Task 2 Step 3.)

- [ ] **Step 4: Implement `_escalate_to_human` now (dependency)**

Add to `book_bot.py` above `_add_books_to_order` (full body repeated in Task 2 Step 3 — identical):

```python
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
```

- [ ] **Step 5: Run to verify pass**

Run: `pytest tests/test_book_bot.py::test_add_books_to_order_charges_delta tests/test_book_bot.py::test_add_books_guard_blocks_completed_order -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add book_bot.py tests/test_book_bot.py
git commit -m "feat(book): add-a-book delta helper + human-escalation for mid-flow"
```

---

## Task 2: `_escalate_to_human` — unit-test the escalation contract

**Files:**
- Modify: `book_bot.py` (helper already added in Task 1 Step 4)
- Test: `tests/test_book_bot.py`

- [ ] **Step 1: Write the failing test**

```python
def test_escalate_to_human_holds_bot_and_pings_anu(fake):
    import book_bot as bb
    db = fake
    db.orders["C3"] = {"order_code": "C3", "phone": "919", "name": "Ajeesh",
                       "items": {"malayalam": 1}, "status": "partially_paid",
                       "grand_total": 275.0, "amount_paid": 200.0, "_seq": 1}
    sent = []
    bb._send_text = lambda p, m: sent.append((p, m))
    order = db.get_book_order("C3")
    replies = bb._escalate_to_human("919", order, "Is cash on delivery available")
    assert replies == []                                   # bot silent to customer
    assert db.sessions["919"]["needs_human"] is True       # bot held
    anu = [m for p, m in sent if p == bb.VERIFIER_PHONE]
    assert anu and "Needs a human" in anu[0]
    assert "cash on delivery" in anu[0].lower()
```

- [ ] **Step 2: Run to verify it fails, then passes**

Run: `pytest tests/test_book_bot.py::test_escalate_to_human_holds_bot_and_pings_anu -v`
Expected: PASS (helper already exists from Task 1 Step 4). If it fails with `AttributeError`, the helper wasn't added — add it per Task 1 Step 4.

- [ ] **Step 3: Confirm helper body**

The `_escalate_to_human` body is exactly as in Task 1 Step 4. No further code.

- [ ] **Step 4: Commit**

```bash
git add tests/test_book_bot.py
git commit -m "test(book): escalation holds bot + pings Anu"
```

---

## Task 3: `_is_price_question`, `_wants_more_books`, `_balance_reply`

**Files:**
- Modify: `book_bot.py`
- Test: `tests/test_book_bot.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

@pytest.mark.parametrize("text,expected", [
    ("how much do I owe", True),
    ("Amount", True),
    ("എത്ര രൂപ", True),
    ("Easy English", False),
    ("send it fast", False),
])
def test_is_price_question(text, expected):
    assert book_bot._is_price_question(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("Need one more book", True),
    ("add another", True),
    ("ഒരു പുസ്തകം കൂടി", True),
    ("Easy English", False),
    ("ok", False),
])
def test_wants_more_books(text, expected):
    assert book_bot._wants_more_books(text) is expected


def test_balance_reply_shows_numbers(fake):
    order = {"order_code": "C9", "grand_total": 475.0, "amount_paid": 200.0}
    r = book_bot._balance_reply(order)
    assert "475" in r and "200" in r and "275" in r
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_bot.py -k "price_question or wants_more_books or balance_reply" -v`
Expected: FAIL — attributes not defined.

- [ ] **Step 3: Implement the three helpers**

Add to `book_bot.py` above `_add_books_to_order`:

```python
_PRICE_WORDS = ("amount", "price", "how much", "cost", "rate", "total", "balance",
                "charge", "rs", "rupees", "എത്ര", "വില", "രൂപ", "₹")
_ADD_BOOK_HINTS = ("book", "one more", "another", "add", "more book",
                   "പുസ്തകം", "കൂടി", "ഒന്ന് കൂടി")


def _is_price_question(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in _PRICE_WORDS)


def _wants_more_books(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _ADD_BOOK_HINTS)


def _balance_reply(order: dict) -> str:
    code = order.get("order_code") or "—"
    grand = float(order.get("grand_total") or 0)
    paid = float(order.get("amount_paid") or 0)
    bal = grand - paid
    return (f"🧾 *{code}* — ആകെ ₹{grand:.0f} · അടച്ചത് ₹{paid:.0f} · ബാക്കി ₹{bal:.0f}.\n"
            f"Total ₹{grand:.0f} · paid ₹{paid:.0f} · balance ₹{bal:.0f}. 🙏")
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_book_bot.py -k "price_question or wants_more_books or balance_reply" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add book_bot.py tests/test_book_bot.py
git commit -m "feat(book): price-question, add-book-hint, and balance helpers"
```

---

## Task 4: `resolve_stuck_message` — the ladder orchestrator

**Files:**
- Modify: `book_bot.py`
- Test: `tests/test_book_bot.py`

- [ ] **Step 1: Write the failing tests**

```python
def _mk_pay_order(db, items=None):
    db.orders["R1"] = {"order_code": "R1", "phone": "919", "name": "Ajeesh",
                       "items": items or {"malayalam": 1}, "status": "partially_paid",
                       "grand_total": 275.0, "amount_paid": 200.0, "_seq": 1}
    return db.get_book_order("R1")


def test_resolve_adds_named_book(fake):
    db = fake
    order = _mk_pay_order(db)
    replies = book_bot.resolve_stuck_message("919", "Easy English", order)
    assert db.orders["R1"]["items"] == {"malayalam": 1, "english": 1}
    assert "Added" in replies[0]


def test_resolve_asks_which_book_on_vague_add(fake):
    db = fake
    order = _mk_pay_order(db)
    replies = book_bot.resolve_stuck_message("919", "Need one more book", order)
    # asked which book, did NOT escalate, did NOT change items
    assert replies and "Which book" in replies[0]
    assert db.orders["R1"]["items"] == {"malayalam": 1}
    assert db.sessions.get("919", {}).get("needs_human") is not True


def test_resolve_answers_price_question(fake):
    db = fake
    order = _mk_pay_order(db)
    replies = book_bot.resolve_stuck_message("919", "how much balance left", order)
    assert replies and "balance" in replies[0].lower()


def test_resolve_escalates_unknown(fake):
    import book_bot as bb
    db = fake
    order = _mk_pay_order(db)
    sent = []
    bb._send_text = lambda p, m: sent.append((p, m))
    replies = bb.resolve_stuck_message("919", "Is cash on delivery available", order)
    assert replies == []
    assert db.sessions["919"]["needs_human"] is True
    assert any(p == bb.VERIFIER_PHONE for p, _ in sent)


def test_resolve_ignores_trivial_ack(fake):
    db = fake
    order = _mk_pay_order(db)
    # "ok" is a trivial ack → return None so the caller's default reply stands
    assert book_bot.resolve_stuck_message("919", "ok", order) is None
    assert db.sessions.get("919", {}).get("needs_human") is not True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_bot.py -k resolve_ -v`
Expected: FAIL — `resolve_stuck_message` not defined.

- [ ] **Step 3: Implement `resolve_stuck_message`**

Add to `book_bot.py` above `_add_books_to_order`:

```python
def resolve_stuck_message(phone: str, text: str, order: dict) -> list[str] | None:
    """Try to understand a mid-flow message before the caller falls back to its
    canned reply. Ladder: deterministic book parse → Haiku book parse → price
    question → vague add-a-book (ask which) → escalate. Returns replies when
    handled, or None to let the caller run its default (safe fall-through)."""
    if not order or not order.get("order_code"):
        return None

    items = bc.parse_customer_order(text) or _llm_parse_books(text)
    if items:
        return _add_books_to_order(phone, order, items)

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

    t = (text or "").strip()
    if len(t) < 6 or t.lower() in _AFFIRM:
        return None                                     # trivial ack → default reply

    return _escalate_to_human(phone, order, text)
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_book_bot.py -k resolve_ -v`
Expected: PASS. (`_AFFIRM` is an existing module global used by `_handle_pay`.)

- [ ] **Step 5: Commit**

```bash
git add book_bot.py tests/test_book_bot.py
git commit -m "feat(book): resolve_stuck_message intent ladder"
```

---

## Task 5: Wire `resolve_stuck_message` into `_handle_pay`

**Files:**
- Modify: `book_bot.py` (`_handle_pay`, the final default branch)
- Test: `tests/test_book_bot.py`

- [ ] **Step 1: Write the failing integration test**

```python
def test_handle_pay_adds_book_before_screenshot_loop(fake):
    db = fake
    db.orders["P1"] = {"order_code": "P1", "phone": "919", "name": "Ajeesh",
                       "items": {"malayalam": 1}, "status": "partially_paid",
                       "grand_total": 275.0, "amount_paid": 200.0, "_seq": 1}
    order = db.get_book_order("P1")
    # Free text that is NOT qr/edit/cancel/payment — used to hit the screenshot loop.
    book_bot._handle_pay("919", "Easy English", order)
    assert db.orders["P1"]["items"] == {"malayalam": 1, "english": 1}
    assert float(db.orders["P1"]["grand_total"]) == 475.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_book_bot.py::test_handle_pay_adds_book_before_screenshot_loop -v`
Expected: FAIL — items stay `{"malayalam": 1}` (message fell into the screenshot-prompt default).

- [ ] **Step 3: Wire the resolver in**

In `book_bot.py` `_handle_pay`, locate the final default block (the last `return []` after the `if pay:` payment-text branch):

```python
    _send_text(phone, "UPI പേയ്മെന്റ് പൂർത്തിയാക്കി, സ്ഥിരീകരണത്തിന്റെ *സ്ക്രീൻഷോട്ട്* "
                      "ഇവിടെ അയക്കൂ. 🙏\n"
                      "Please complete the UPI payment and send a screenshot here.")
    _send_pay_buttons(phone)
    return []
```

Insert, immediately **before** that `_send_text(...)` default:

```python
    resolved = resolve_stuck_message(phone, text, order)
    if resolved is not None:
        for r in resolved:
            _send_text(phone, r)
        return resolved
```

(The resolver already sends Anu/escalation messages itself; here we relay any customer-facing replies, matching how other branches use `_send_text`.)

- [ ] **Step 4: Run to verify pass, then the full book suite**

Run: `pytest tests/test_book_bot.py::test_handle_pay_adds_book_before_screenshot_loop -v`
Expected: PASS.
Run: `pytest tests/test_book_bot.py -q`
Expected: all PASS (no regressions in existing pay-stage tests).

- [ ] **Step 5: Commit**

```bash
git add book_bot.py tests/test_book_bot.py
git commit -m "feat(book): consult intent resolver in book_pay before screenshot loop"
```

---

## Task 6: `api/index.py` media — forward on balance + log `media_url`

**Files:**
- Modify: `api/index.py` (`_handle_media`, the `if msg_type == "image"` payment branch, ~line 1172-1200)
- Create: `tests/test_media_forwarding.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_media_forwarding.py`:

```python
import importlib


def test_handle_media_forwards_partially_paid_and_returns_path(monkeypatch):
    import db_cloud
    import book_bot
    import whatsapp_notify
    api = importlib.import_module("api.index")

    # customer owes money on a partially-paid order
    monkeypatch.setattr(db_cloud, "get_active_book_order",
                        lambda phone: {"order_code": "C", "status": "partially_paid"})
    monkeypatch.setattr(db_cloud, "get_book_payments",
                        lambda code: [{"id": 7,
                                       "proof_url": "https://x.supabase.co/storage/v1/"
                                                    "object/public/incoming-files/"
                                                    "book-payments/C_1.jpg?"}])
    called = {}
    monkeypatch.setattr(book_bot, "handle_payment_proof",
                        lambda phone, content, mime: called.setdefault("proof", True) or ["ok"])
    monkeypatch.setattr(api, "_download_meta_media", lambda mid: b"imgbytes")
    monkeypatch.setattr(whatsapp_notify, "_send", lambda phone, msg: None)

    path = api._handle_media("919", "image", "MID", "image/jpeg", "")
    assert called.get("proof") is True                       # forwarded to Anu
    assert path == "book-payments/C_1.jpg"                    # logged as media_url
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_media_forwarding.py -v`
Expected: FAIL — the gate omits `partially_paid`, so the branch is skipped and the function returns `None` (or falls through), asserting `path == "book-payments/C_1.jpg"` fails.

- [ ] **Step 3: Fix the gate and return value**

In `api/index.py` `_handle_media`, the payment branch currently reads:

```python
            _bo = get_active_book_order(sender) or {}
            if _bo.get("status") in ("awaiting_payment", "payment_review"):
                from whatsapp_notify import _send
                content = _download_meta_media(media_id)
                replies = None
                if content is not None:
                    replies = handle_payment_proof(sender, content, mime_type or "image/jpeg")
                if replies is None:
                    replies = ["We couldn't read that image. Please resend a clear "
                               "screenshot of your payment confirmation. 🙏"]
                for reply in replies:
                    _send(sender, reply)
                return None
```

Replace it with (add `"partially_paid"`; return the stored `book-payments/…` path so `media_url` is logged):

```python
            _bo = get_active_book_order(sender) or {}
            if _bo.get("status") in ("awaiting_payment", "payment_review", "partially_paid"):
                from whatsapp_notify import _send
                content = _download_meta_media(media_id)
                replies = None
                if content is not None:
                    replies = handle_payment_proof(sender, content, mime_type or "image/jpeg")
                if replies is None:
                    replies = ["We couldn't read that image. Please resend a clear "
                               "screenshot of your payment confirmation. 🙏"]
                for reply in replies:
                    _send(sender, reply)
                # Return the stored proof path so the inbound image logs a media_url
                # (was None → payment screenshots were invisible in the transcript).
                try:
                    from db_cloud import get_book_payments
                    code = _bo.get("order_code")
                    pays = sorted(get_book_payments(code) or [],
                                  key=lambda p: p.get("id") or 0)
                    if pays:
                        import re as _re
                        u = pays[-1].get("proof_url") or ""
                        mm = _re.search(r"/incoming-files/(.+?)(?:\?|$)", u)
                        if mm:
                            return mm.group(1)
                except Exception as e:
                    logger.error("payment media_url path lookup failed: %s", e)
                return None
```

- [ ] **Step 4: Run to verify pass**

Run: `pytest tests/test_media_forwarding.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add api/index.py tests/test_media_forwarding.py
git commit -m "fix(book): forward payment screenshots on partial balance + log media_url"
```

---

## Task 7: Verify admin transcript shows payment screenshots (no code)

**Files:** none (verification only). `website/admin.html` `convRenderBubble` already renders `message_type == image` with a `media_url` as an inline thumbnail + lightbox; the transcript endpoint (`api/handlers_admin.py`) already resolves `media_url` to a public URL.

- [ ] **Step 1: Confirm the render path exists**

Run: `grep -n "conv-img" website/admin.html`
Expected: matches showing the `<img class="conv-img">` render branch for images.

- [ ] **Step 2: Confirm the endpoint resolves media_url**

Run: `grep -n "get_media_url" api/handlers_admin.py`
Expected: a line converting `media_url` storage paths to public URLs in the transcript response.

- [ ] **Step 3: Manual check (after deploy)**

Open the admin transcript for a customer who sent a payment screenshot *after* Task 6 ships; the screenshot renders inline with a click-to-open lightbox. No code change; if it does not render, the gap is a null `media_url` (Task 6) — verify that row now has a `book-payments/…` path.

---

## Full-suite gate

- [ ] **Run the whole book suite**

Run: `pytest tests/test_book_bot.py tests/test_media_forwarding.py -q`
Expected: all PASS.

- [ ] **Run the broader suite for regressions** (skip live/browser tests)

Run: `pytest -q -k "not live and not e2e"`
Expected: PASS (or unchanged from baseline).

---

## Self-Review notes

- **Spec coverage:** Feature 1 (understand → Haiku → human) = Tasks 1–5; add-a-book delta + guard = Task 1; escalation contract = Task 2; price + vague-add = Tasks 3–4; wiring = Task 5. Feature 2a/2b (log media_url + balance-gated forward) = Task 6. Feature 2c (admin visibility) = already implemented, verified in Task 7.
- **Type consistency:** `resolve_stuck_message`, `_add_books_to_order`, `_escalate_to_human`, `_is_price_question`, `_wants_more_books`, `_balance_reply` use identical signatures wherever referenced. `divya_order_terms` keys used: `books_total`, `courier`, `grand_total` (confirmed in `book_catalog.py`).
- **Non-goals honored:** no front-door router reuse, no per-image vision, no happy-path changes, no admin UI rebuild.
