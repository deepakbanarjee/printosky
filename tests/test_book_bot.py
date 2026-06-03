"""Flow tests for book_bot — the button-driven book-order state machine.

db_cloud is faked in-memory; the interactive senders (_send_list / _send_buttons
/ _send_text / _send_qr) are stubbed so no Meta/Supabase calls happen. The flow
sends messages as side effects and returns [] (or None when not a book message).
"""

import pytest

import book_bot
import db_cloud as _dbc


class FakeDB:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self._seq = 0

    def get_session(self, db, phone):
        return dict(self.sessions.get(phone, {}))

    def save_session(self, db, phone, **kw):
        self.sessions.setdefault(phone, {}).update(kw)

    def clear_session(self, db, phone):
        self.sessions.pop(phone, None)

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

    sent = {"list": [], "buttons": [], "text": [], "qr": []}
    monkeypatch.setattr(book_bot, "_send_list",
                        lambda phone, body, btn, rows, header=None: sent["list"].append([r["id"] for r in rows]))
    monkeypatch.setattr(book_bot, "_send_buttons",
                        lambda phone, body, buttons, header=None: sent["buttons"].append([b[0] for b in buttons]))
    monkeypatch.setattr(book_bot, "_send_text",
                        lambda phone, msg: sent["text"].append(msg))
    monkeypatch.setattr(book_bot, "_send_qr",
                        lambda phone, order: (sent["qr"].append(order["order_code"]) or True))
    return db, sent


PHONE = "919000000001"


# ── trigger detection ─────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text", ["book", "BOOKS", "books please", "xtraa", "Aksharamrutham"])
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
    assert book_bot.maybe_handle_book(PHONE, "book") is None


# ── happy path: All-3 set via buttons ─────────────────────────────────────────

@pytest.mark.integration
def test_full_happy_path_set_buttons(fake):
    db, sent = fake

    assert book_bot.maybe_handle_book(PHONE, "book", name="Asha") == []
    assert db.sessions[PHONE]["step"] == "book_select"
    assert sent["list"] and sent["list"][-1] == ["bk_ml", "bk_hi", "bk_en", "bk_set"]
    code = next(iter(db.orders))

    # Tap "All 3 — Set"
    book_bot.maybe_handle_book(PHONE, "bk_set")
    assert db.sessions[PHONE]["step"] == "book_qty"
    assert db.orders[code]["flow_cursor"]["current"] == "__set__"
    assert sent["buttons"][-1] == ["qty_1", "qty_2", "qty_3"]

    # Tap qty "1" → 1 set
    book_bot.maybe_handle_book(PHONE, "qty_1")
    assert db.sessions[PHONE]["step"] == "book_address"
    assert db.orders[code]["grand_total"] == 624.0   # 549 set + 75 courier
    assert any("624" in m for m in sent["text"])

    # Address (typed)
    book_bot.maybe_handle_book(PHONE, "12 MG Road, Thrissur, 680001")
    assert db.sessions[PHONE]["step"] == "book_phone"
    assert sent["buttons"][-1] == ["ph_yes", "ph_edit"]

    # Confirm phone via button
    book_bot.maybe_handle_book(PHONE, "ph_yes")
    assert db.sessions[PHONE]["step"] == "book_summary"
    assert db.orders[code]["contact_phone"] == PHONE
    assert sent["buttons"][-1] == ["ord_yes", "ord_no"]

    # Confirm order via button → QR sent
    book_bot.maybe_handle_book(PHONE, "ord_yes")
    assert db.sessions[PHONE]["step"] == "book_pay"
    assert db.orders[code]["status"] == "awaiting_payment"
    assert sent["qr"] == [code]

    # Payment screenshot
    book_bot.handle_payment_proof(PHONE, b"x", "image/jpeg")
    assert db.orders[code]["status"] == "payment_review"
    assert db.sessions[PHONE]["needs_human"] is True

    # Owner confirms
    res = book_bot.confirm_book_order(code)
    assert res["ok"] is True
    assert db.orders[code]["status"] == "confirmed"
    assert PHONE not in db.sessions


# ── single book + add another (cart) ──────────────────────────────────────────

@pytest.mark.integration
def test_single_then_add_another(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    code = next(iter(db.orders))

    book_bot.maybe_handle_book(PHONE, "bk_ml")      # Aksharamrutham
    assert db.orders[code]["flow_cursor"]["current"] == "malayalam"
    book_bot.maybe_handle_book(PHONE, "qty_2")      # 2 copies
    assert db.orders[code]["items"] == {"malayalam": 2}
    assert db.sessions[PHONE]["step"] == "book_addmore"
    assert sent["buttons"][-1] == ["bk_add", "bk_checkout"]

    # Add another book
    book_bot.maybe_handle_book(PHONE, "bk_add")
    assert db.sessions[PHONE]["step"] == "book_select"
    book_bot.maybe_handle_book(PHONE, "bk_en")      # Easy English
    book_bot.maybe_handle_book(PHONE, "qty_1")
    assert db.orders[code]["items"] == {"malayalam": 2, "english": 1}
    assert db.sessions[PHONE]["step"] == "book_addmore"

    # Checkout
    book_bot.maybe_handle_book(PHONE, "bk_checkout")
    assert db.sessions[PHONE]["step"] == "book_address"
    # 2×200 + 1×200 + 75 courier = 675
    assert db.orders[code]["grand_total"] == 675.0


# ── typed fallbacks still work ────────────────────────────────────────────────

@pytest.mark.unit
def test_typed_selection_fallback(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    code = next(iter(db.orders))
    book_bot.maybe_handle_book(PHONE, "2")          # typed → hindi
    assert db.orders[code]["flow_cursor"]["current"] == "hindi"
    book_bot.maybe_handle_book(PHONE, "3")          # typed qty 3
    assert db.orders[code]["items"] == {"hindi": 3}


@pytest.mark.unit
def test_invalid_selection_reprompts_list(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    n_before = len(sent["list"])
    book_bot.maybe_handle_book(PHONE, "banana")
    assert db.sessions[PHONE]["step"] == "book_select"
    assert len(sent["list"]) == n_before + 1        # re-sent the list


@pytest.mark.unit
def test_summary_start_over(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "bk_ml")
    book_bot.maybe_handle_book(PHONE, "qty_1")
    book_bot.maybe_handle_book(PHONE, "bk_checkout")
    book_bot.maybe_handle_book(PHONE, "12 MG Road, Thrissur 680001")
    book_bot.maybe_handle_book(PHONE, "ph_yes")
    book_bot.maybe_handle_book(PHONE, "ord_no")     # start over
    assert db.sessions[PHONE]["step"] == "book_select"


@pytest.mark.unit
def test_phone_edit_then_typed_number(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "bk_ml")
    book_bot.maybe_handle_book(PHONE, "qty_1")
    book_bot.maybe_handle_book(PHONE, "bk_checkout")
    book_bot.maybe_handle_book(PHONE, "12 MG Road, Thrissur 680001")
    book_bot.maybe_handle_book(PHONE, "ph_edit")    # wants different number
    assert db.sessions[PHONE]["step"] == "book_phone"
    book_bot.maybe_handle_book(PHONE, "9876543210")
    code = next(iter(db.orders))
    assert db.orders[code]["contact_phone"] == "9876543210"


@pytest.mark.unit
def test_non_book_message_returns_none(fake):
    assert book_bot.maybe_handle_book(PHONE, "hello there") is None


@pytest.mark.unit
def test_payment_proof_ignored_when_not_in_pay_step(fake):
    db, _ = fake
    db.sessions[PHONE] = {"step": "book_select"}
    assert book_bot.handle_payment_proof(PHONE, b"x", "image/jpeg") is None


@pytest.mark.unit
def test_confirm_unknown_order(fake):
    res = book_bot.confirm_book_order("XTR-NOPE")
    assert res["ok"] is False
