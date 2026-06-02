"""Flow tests for book_bot — the book-order state machine.

db_cloud is faked in-memory; the network senders (_send_qr/_send_text) are
stubbed so no Meta/Supabase calls happen.
"""

import pytest

import book_bot
import db_cloud as _dbc


class FakeDB:
    """Minimal in-memory stand-in for the db_cloud functions book_bot uses."""

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self._seq = 0

    # sessions
    def get_session(self, db, phone):
        return dict(self.sessions.get(phone, {}))

    def save_session(self, db, phone, **kw):
        self.sessions.setdefault(phone, {}).update(kw)

    def clear_session(self, db, phone):
        self.sessions.pop(phone, None)

    # book orders
    def get_active_book_order(self, phone):
        active = [o for o in self.orders.values()
                  if o["phone"] == phone
                  and o["status"] in ("collecting", "awaiting_payment", "payment_review")]
        active.sort(key=lambda o: o["_seq"], reverse=True)
        return dict(active[0]) if active else {}

    def create_book_order(self, code, phone, name=None):
        self._seq += 1
        o = {"order_code": code, "phone": phone, "name": name, "items": {},
             "status": "collecting", "flow_cursor": {}, "_seq": self._seq}
        self.orders[code] = o
        return dict(o)

    def update_book_order(self, code, **fields):
        self.orders[code].update(fields)

    def get_book_order(self, code):
        return dict(self.orders.get(code, {}))

    def upload_book_payment_proof(self, code, content, mime):
        return "https://example.test/proof.jpg"

    def log_message(self, *a, **k):
        pass


@pytest.fixture
def fake(monkeypatch):
    db = FakeDB()
    for fn in ("get_session", "save_session", "clear_session",
               "get_active_book_order", "create_book_order", "update_book_order",
               "get_book_order", "upload_book_payment_proof", "log_message"):
        monkeypatch.setattr(_dbc, fn, getattr(db, fn))

    sent = {"qr": [], "text": []}
    monkeypatch.setattr(book_bot, "_send_qr",
                        lambda phone, order: (sent["qr"].append(order["order_code"]) or True))
    monkeypatch.setattr(book_bot, "_send_text",
                        lambda phone, msg: sent["text"].append((phone, msg)))
    return db, sent


PHONE = "919000000001"


# ── trigger detection ─────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text", ["book", "BOOK", "books please", "xtraa", "Adithara"])
def test_trigger_words(text):
    assert book_bot.is_book_trigger(text) is True


@pytest.mark.unit
@pytest.mark.parametrize("text", ["facebook", "i want a print", "hello", ""])
def test_non_trigger_words(text):
    assert book_bot.is_book_trigger(text) is False


@pytest.mark.unit
def test_no_trigger_when_mid_print_flow(fake):
    db, _ = fake
    db.sessions[PHONE] = {"step": "size", "job_id": "OSP-x"}
    # Even with "book" in the text, an active print job is not hijacked.
    assert book_bot.maybe_handle_book(PHONE, "book") is None


@pytest.mark.unit
def test_non_book_message_returns_none(fake):
    assert book_bot.maybe_handle_book(PHONE, "hello there") is None


# ── happy path: all three (set) ───────────────────────────────────────────────

@pytest.mark.integration
def test_full_happy_path_set(fake):
    db, sent = fake

    r = book_bot.maybe_handle_book(PHONE, "book", name="Asha")
    assert r and "Adithara" in r[0]
    assert db.sessions[PHONE]["step"] == "book_select"
    assert len(db.orders) == 1
    code = next(iter(db.orders))

    # Select all three
    r = book_bot.maybe_handle_book(PHONE, "4")
    assert db.sessions[PHONE]["step"] == "book_qty"
    assert "Malayalam" in r[0]

    # Quantities: 1 each
    book_bot.maybe_handle_book(PHONE, "1")   # malayalam
    book_bot.maybe_handle_book(PHONE, "1")   # hindi
    r = book_bot.maybe_handle_book(PHONE, "1")  # english → done
    assert db.sessions[PHONE]["step"] == "book_address"
    assert db.orders[code]["grand_total"] == 624.0   # 549 set + 75 courier
    assert "624" in r[0]

    # Address
    r = book_bot.maybe_handle_book(PHONE, "12 MG Road, Thrissur, 680001")
    assert db.sessions[PHONE]["step"] == "book_phone"
    assert db.orders[code]["address"].startswith("12 MG Road")

    # Confirm phone (use WhatsApp number)
    r = book_bot.maybe_handle_book(PHONE, "yes")
    assert db.sessions[PHONE]["step"] == "book_summary"
    assert db.orders[code]["contact_phone"] == PHONE
    assert "Order summary" in r[0]
    assert "Total: ₹624" in r[0]

    # Confirm order → QR sent, awaiting payment
    r = book_bot.maybe_handle_book(PHONE, "yes")
    assert db.sessions[PHONE]["step"] == "book_pay"
    assert db.orders[code]["status"] == "awaiting_payment"
    assert sent["qr"] == [code]
    assert r == []   # QR caption carried the instructions

    # Payment screenshot
    r = book_bot.handle_payment_proof(PHONE, b"fakejpg", "image/jpeg")
    assert db.orders[code]["status"] == "payment_review"
    assert db.orders[code]["payment_proof_url"]
    assert db.sessions[PHONE]["needs_human"] is True
    assert "verifying" in r[0].lower()

    # Owner confirms
    res = book_bot.confirm_book_order(code)
    assert res["ok"] is True
    assert db.orders[code]["status"] == "confirmed"
    assert PHONE not in db.sessions          # session cleared
    assert sent["text"] and "confirmed" in sent["text"][-1][1].lower()


# ── any-2 path uses individual pricing ────────────────────────────────────────

@pytest.mark.integration
def test_any_two_books_individual_pricing(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    code = next(iter(db.orders))

    book_bot.maybe_handle_book(PHONE, "1,3")    # malayalam + english
    book_bot.maybe_handle_book(PHONE, "1")      # ml qty
    r = book_bot.maybe_handle_book(PHONE, "1")  # en qty → done
    # 200 + 200 + 75 courier = 475, NOT set price
    assert db.orders[code]["grand_total"] == 475.0
    assert "475" in r[0]


# ── re-prompts on bad input ───────────────────────────────────────────────────

@pytest.mark.unit
def test_invalid_selection_reprompts(fake):
    db, _ = fake
    book_bot.maybe_handle_book(PHONE, "book")
    r = book_bot.maybe_handle_book(PHONE, "banana")
    assert db.sessions[PHONE]["step"] == "book_select"   # stayed put
    assert "didn't catch" in r[0]


@pytest.mark.unit
def test_invalid_qty_reprompts(fake):
    db, _ = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "1")
    r = book_bot.maybe_handle_book(PHONE, "lots")
    assert db.sessions[PHONE]["step"] == "book_qty"
    assert "How many" in r[0]


@pytest.mark.unit
def test_short_address_reprompts(fake):
    db, _ = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "1")
    book_bot.maybe_handle_book(PHONE, "1")
    r = book_bot.maybe_handle_book(PHONE, "x")
    assert db.sessions[PHONE]["step"] == "book_address"
    assert "too short" in r[0]


@pytest.mark.unit
def test_summary_no_restarts(fake):
    db, _ = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "1")
    book_bot.maybe_handle_book(PHONE, "1")
    book_bot.maybe_handle_book(PHONE, "12 MG Road, Thrissur 680001")
    book_bot.maybe_handle_book(PHONE, "yes")
    r = book_bot.maybe_handle_book(PHONE, "no")
    assert db.sessions[PHONE]["step"] == "book_select"
    assert "start over" in r[0].lower()


@pytest.mark.unit
def test_alternate_phone_number_accepted(fake):
    db, _ = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "1")
    book_bot.maybe_handle_book(PHONE, "1")
    book_bot.maybe_handle_book(PHONE, "12 MG Road, Thrissur 680001")
    book_bot.maybe_handle_book(PHONE, "9876543210")
    code = next(iter(db.orders))
    assert db.orders[code]["contact_phone"] == "9876543210"


# ── payment proof only when awaiting payment ──────────────────────────────────

@pytest.mark.unit
def test_payment_proof_ignored_when_not_in_pay_step(fake):
    db, _ = fake
    db.sessions[PHONE] = {"step": "book_select"}
    assert book_bot.handle_payment_proof(PHONE, b"x", "image/jpeg") is None


@pytest.mark.unit
def test_confirm_unknown_order(fake):
    res = book_bot.confirm_book_order("XTR-NOPE")
    assert res["ok"] is False


@pytest.mark.integration
def test_new_starts_fresh_order_during_payment_review(fake):
    db, sent = fake
    # Drive one order to payment_review.
    book_bot.maybe_handle_book(PHONE, "book")
    code1 = next(iter(db.orders))
    book_bot.maybe_handle_book(PHONE, "1")   # select malayalam
    book_bot.maybe_handle_book(PHONE, "1")   # qty → address
    book_bot.maybe_handle_book(PHONE, "12 MG Road, Thrissur 680001")
    book_bot.maybe_handle_book(PHONE, "yes")  # phone → summary
    book_bot.maybe_handle_book(PHONE, "yes")  # confirm → book_pay
    book_bot.handle_payment_proof(PHONE, b"x", "image/jpeg")
    assert db.orders[code1]["status"] == "payment_review"
    assert db.sessions[PHONE]["needs_human"] is True

    # NEW must open a fresh order, not dead-end, and clear the needs_human flag.
    r = book_bot.maybe_handle_book(PHONE, "new")
    assert "Adithara" in r[0]
    assert db.sessions[PHONE]["step"] == "book_select"
    assert db.sessions[PHONE]["needs_human"] is False
    assert len(db.orders) == 2          # a second order was created
    # The old order is untouched, still awaiting the owner.
    assert db.orders[code1]["status"] == "payment_review"
