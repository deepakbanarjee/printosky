"""Flow tests for book_bot — combo multi-select + per-book count + edit.

db_cloud is faked in-memory; the interactive senders are stubbed. The flow sends
messages as side effects and returns [] (or None when not a book message).
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
        return True

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

    sent = {"list": [], "buttons": [], "text": [], "qr": [], "forward": []}
    monkeypatch.setattr(book_bot, "_send_list",
                        lambda phone, body, btn, rows, header=None: sent["list"].append([r["id"] for r in rows]))
    monkeypatch.setattr(book_bot, "_send_buttons",
                        lambda phone, body, buttons, header=None: sent["buttons"].append([b[0] for b in buttons]))
    monkeypatch.setattr(book_bot, "_send_text",
                        lambda phone, msg: sent["text"].append(msg))
    monkeypatch.setattr(book_bot, "_send_qr",
                        lambda phone, order: (sent["qr"].append(order["order_code"]) or True))
    monkeypatch.setattr(book_bot, "_forward_to_verifier",
                        lambda order, content, mime: sent["forward"].append(order["order_code"]))
    return db, sent


PHONE = "919000000001"
ADDR = "12 MG Road, Thrissur, 680001"


def _code(db):
    return next(iter(db.orders))


# ── trigger ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text", ["book", "BOOKS", "xtraa", "Aksharamrutham"])
def test_trigger_words(text):
    assert book_bot.is_book_trigger(text) is True


@pytest.mark.unit
def test_no_trigger_when_mid_print(fake):
    db, _ = fake
    db.sessions[PHONE] = {"step": "size", "job_id": "OSP-x"}
    assert book_bot.maybe_handle_book(PHONE, "book") is None


# ── selection list offers combos ──────────────────────────────────────────────

@pytest.mark.unit
def test_select_list_has_combo_rows(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    assert db.sessions[PHONE]["step"] == "book_select"
    assert sent["list"][-1] == ["bk_ml", "bk_hi", "bk_en",
                                "bk_ml_hi", "bk_ml_en", "bk_hi_en", "bk_all"]


# ── happy path: all 3 in one tap, count each ──────────────────────────────────

@pytest.mark.integration
def test_all_three_one_tap_then_count_each(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    code = _code(db)

    book_bot.maybe_handle_book(PHONE, "bk_all")          # one tap → all 3
    assert db.sessions[PHONE]["step"] == "book_qty"
    assert db.orders[code]["flow_cursor"]["current"] == "malayalam"
    assert db.orders[code]["flow_cursor"]["queue"] == ["hindi", "english"]

    book_bot.maybe_handle_book(PHONE, "qty_1")           # malayalam
    assert db.orders[code]["flow_cursor"]["current"] == "hindi"
    book_bot.maybe_handle_book(PHONE, "qty_1")           # hindi
    assert db.orders[code]["flow_cursor"]["current"] == "english"
    book_bot.maybe_handle_book(PHONE, "qty_1")           # english → done
    assert db.sessions[PHONE]["step"] == "book_address"
    assert db.orders[code]["items"] == {"malayalam": 1, "hindi": 1, "english": 1}
    assert db.orders[code]["grand_total"] == 624.0       # 549 set + 75

    book_bot.maybe_handle_book(PHONE, ADDR)
    assert db.sessions[PHONE]["step"] == "book_phone"
    assert sent["buttons"][-1] == ["ph_yes", "ph_edit"]

    book_bot.maybe_handle_book(PHONE, "ph_yes")
    assert db.sessions[PHONE]["step"] == "book_summary"
    assert sent["buttons"][-1] == ["ord_yes", "ord_edit", "ord_no"]

    book_bot.maybe_handle_book(PHONE, "ord_yes")
    assert db.sessions[PHONE]["step"] == "book_pay"
    assert sent["qr"] == [code]

    book_bot.handle_payment_proof(PHONE, b"x", "image/jpeg")
    assert db.orders[code]["status"] == "payment_review"
    assert sent["forward"] == [code]                       # screenshot forwarded to verifier
    res = book_bot.confirm_book_order(code)
    assert res["ok"] and db.orders[code]["status"] == "confirmed"
    assert db.sessions[PHONE]["step"] == "post_order"       # follow-up armed


# ── multi-select pair in one tap ──────────────────────────────────────────────

@pytest.mark.integration
def test_pair_one_tap(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    code = _code(db)
    book_bot.maybe_handle_book(PHONE, "bk_ml_en")        # Malayalam + English
    assert db.orders[code]["flow_cursor"]["current"] == "malayalam"
    assert db.orders[code]["flow_cursor"]["queue"] == ["english"]
    book_bot.maybe_handle_book(PHONE, "qty_2")           # ml ×2
    book_bot.maybe_handle_book(PHONE, "qty_1")           # en ×1 → done
    assert db.orders[code]["items"] == {"malayalam": 2, "english": 1}
    assert db.orders[code]["grand_total"] == 675.0       # 400+200+75


@pytest.mark.unit
def test_typed_multi_select_fallback(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    code = _code(db)
    book_bot.maybe_handle_book(PHONE, "1,3")             # typed → ml + en
    assert db.orders[code]["flow_cursor"]["current"] == "malayalam"
    assert db.orders[code]["flow_cursor"]["queue"] == ["english"]


# ── edit at summary ───────────────────────────────────────────────────────────

def _drive_to_summary(db):
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "bk_ml")
    book_bot.maybe_handle_book(PHONE, "qty_1")
    book_bot.maybe_handle_book(PHONE, ADDR)
    book_bot.maybe_handle_book(PHONE, "ph_yes")
    assert db.sessions[PHONE]["step"] == "book_summary"


@pytest.mark.integration
def test_edit_address(fake):
    db, sent = fake
    _drive_to_summary(db)
    code = _code(db)
    book_bot.maybe_handle_book(PHONE, "ord_edit")
    assert db.sessions[PHONE]["step"] == "book_edit"
    assert sent["buttons"][-1] == ["ed_books", "ed_addr", "ed_phone"]

    book_bot.maybe_handle_book(PHONE, "ed_addr")
    assert db.sessions[PHONE]["step"] == "book_edit_address"
    book_bot.maybe_handle_book(PHONE, "New House, Ollur, Thrissur 680306")
    assert db.orders[code]["address"].startswith("New House")
    assert db.sessions[PHONE]["step"] == "book_summary"   # back to summary


@pytest.mark.integration
def test_edit_phone(fake):
    db, sent = fake
    _drive_to_summary(db)
    code = _code(db)
    book_bot.maybe_handle_book(PHONE, "ord_edit")
    book_bot.maybe_handle_book(PHONE, "ed_phone")
    assert db.sessions[PHONE]["step"] == "book_edit_phone"
    book_bot.maybe_handle_book(PHONE, "9876543210")
    assert db.orders[code]["contact_phone"] == "9876543210"
    assert db.sessions[PHONE]["step"] == "book_summary"


@pytest.mark.integration
def test_edit_books_returns_to_summary(fake):
    db, sent = fake
    _drive_to_summary(db)
    code = _code(db)
    assert db.orders[code]["address"] == ADDR

    book_bot.maybe_handle_book(PHONE, "ord_edit")
    book_bot.maybe_handle_book(PHONE, "ed_books")
    assert db.sessions[PHONE]["step"] == "book_select"
    assert db.orders[code]["flow_cursor"].get("editing") is True

    book_bot.maybe_handle_book(PHONE, "bk_hi")            # re-pick a different book
    book_bot.maybe_handle_book(PHONE, "qty_2")           # done counting → editing → summary
    assert db.sessions[PHONE]["step"] == "book_summary"  # NOT back through address/phone
    assert db.orders[code]["items"] == {"hindi": 2}
    assert db.orders[code]["address"] == ADDR            # address preserved


# ── address must include a PIN code ───────────────────────────────────────────

@pytest.mark.unit
def test_address_without_pincode_rejected(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "bk_ml")
    book_bot.maybe_handle_book(PHONE, "qty_1")
    assert db.sessions[PHONE]["step"] == "book_address"
    book_bot.maybe_handle_book(PHONE, "My house, MG Road, Thrissur")   # no PIN
    assert db.sessions[PHONE]["step"] == "book_address"                # stayed
    assert any("PIN" in m for m in sent["text"])
    book_bot.maybe_handle_book(PHONE, "My house, MG Road, Thrissur 680001")  # with PIN
    assert db.sessions[PHONE]["step"] == "book_phone"                 # advanced


@pytest.mark.unit
def test_edit_address_requires_pincode(fake):
    db, sent = fake
    _drive_to_summary(db)
    book_bot.maybe_handle_book(PHONE, "ord_edit")
    book_bot.maybe_handle_book(PHONE, "ed_addr")
    book_bot.maybe_handle_book(PHONE, "New place, Ollur")             # no PIN
    assert db.sessions[PHONE]["step"] == "book_edit_address"          # stayed
    book_bot.maybe_handle_book(PHONE, "New place, Ollur 680306")      # with PIN
    assert db.sessions[PHONE]["step"] == "book_summary"


# ── cancel / edit at the payment stage ────────────────────────────────────────

def _drive_to_pay(db):
    _drive_to_summary(db)
    book_bot.maybe_handle_book(PHONE, "ord_yes")
    assert db.sessions[PHONE]["step"] == "book_pay"


@pytest.mark.integration
def test_pay_stage_offers_edit_cancel(fake):
    db, sent = fake
    _drive_to_pay(db)
    assert sent["buttons"][-1] == ["pay_edit", "pay_cancel"]


@pytest.mark.integration
def test_pay_stage_cancel(fake):
    db, sent = fake
    _drive_to_pay(db)
    code = _code(db)
    book_bot.maybe_handle_book(PHONE, "pay_cancel")
    assert db.orders[code]["status"] == "cancelled"
    assert PHONE not in db.sessions                                  # session cleared
    assert any("cancelled" in m.lower() for m in sent["text"])


@pytest.mark.integration
def test_pay_stage_edit_reopens_order(fake):
    db, sent = fake
    _drive_to_pay(db)
    code = _code(db)
    book_bot.maybe_handle_book(PHONE, "pay_edit")
    assert db.sessions[PHONE]["step"] == "book_edit"
    assert db.orders[code]["status"] == "collecting"                 # reopened
    assert sent["buttons"][-1] == ["ed_books", "ed_addr", "ed_phone"]


# ── cancel / re-prompt / misc ─────────────────────────────────────────────────

@pytest.mark.unit
def test_cancel_starts_over(fake):
    db, sent = fake
    _drive_to_summary(db)
    book_bot.maybe_handle_book(PHONE, "ord_no")
    assert db.sessions[PHONE]["step"] == "book_select"


@pytest.mark.unit
def test_invalid_selection_reprompts(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    n = len(sent["list"])
    book_bot.maybe_handle_book(PHONE, "banana")
    assert db.sessions[PHONE]["step"] == "book_select"
    assert len(sent["list"]) == n + 1


@pytest.mark.unit
def test_non_book_message_returns_none(fake):
    assert book_bot.maybe_handle_book(PHONE, "hello there") is None


@pytest.mark.unit
def test_confirm_unknown_order(fake):
    assert book_bot.confirm_book_order("XTR-NOPE")["ok"] is False


# ── abandoned-cart reminders ──────────────────────────────────────────────────

@pytest.mark.unit
def test_send_abandoned_reminders(fake, monkeypatch):
    db, sent = fake
    carts = [
        {"order_code": "XTR-1", "phone": "919111111111", "items": {"malayalam": 2}},
        {"order_code": "XTR-2", "phone": "919222222222", "items": {}},
    ]
    monkeypatch.setattr(_dbc, "find_abandoned_book_carts", lambda **kw: carts)
    marked = []
    monkeypatch.setattr(_dbc, "mark_abandoned_reminded", lambda code: marked.append(code))

    res = book_bot.send_abandoned_reminders()
    assert res == {"carts": 2, "reminded": 2}
    assert marked == ["XTR-1", "XTR-2"]                 # each reminded exactly once
    assert len(sent["text"]) == 2
    assert "Aksharamrutham × 2" in sent["text"][0]       # cart contents shown
    assert "didn't finish" in sent["text"][1].lower() or "didn’t finish" in sent["text"][1].lower()


@pytest.mark.unit
def test_no_abandoned_carts_no_messages(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(_dbc, "find_abandoned_book_carts", lambda **kw: [])
    res = book_bot.send_abandoned_reminders()
    assert res == {"carts": 0, "reminded": 0}
    assert sent["text"] == []


# ── verifier (Anu) confirm / reject ───────────────────────────────────────────

VERIFIER = "919072034907"


@pytest.mark.integration
def test_verifier_confirm(fake):
    db, sent = fake
    db.create_book_order("XTR-V1", "919888888888", "Cust")
    db.update_book_order("XTR-V1", status="payment_review", items={"malayalam": 1})
    handled = book_bot.handle_verifier_reply(VERIFIER, "vconf_XTR-V1")
    assert handled is True
    assert db.orders["XTR-V1"]["status"] == "confirmed"
    assert db.sessions["919888888888"]["step"] == "post_order"     # customer follow-up armed
    assert any("confirmed" in m.lower() for m in sent["text"])


@pytest.mark.integration
def test_verifier_reject_asks_resend(fake):
    db, sent = fake
    db.create_book_order("XTR-V2", "919888888888", "Cust")
    db.update_book_order("XTR-V2", status="payment_review")
    handled = book_bot.handle_verifier_reply(VERIFIER, "vrej_XTR-V2")
    assert handled is True
    assert db.orders["XTR-V2"]["status"] == "awaiting_payment"     # reopened
    assert db.sessions["919888888888"]["step"] == "book_pay"       # can resend screenshot
    assert any("resend" in m.lower() for m in sent["text"])


@pytest.mark.unit
def test_verifier_typed_confirm(fake):
    db, sent = fake
    db.create_book_order("XTR-V3", "919888888888")
    db.update_book_order("XTR-V3", status="payment_review", items={"hindi": 1})
    assert book_bot.handle_verifier_reply(VERIFIER, "confirm XTR-V3") is True
    assert db.orders["XTR-V3"]["status"] == "confirmed"


@pytest.mark.unit
def test_non_verifier_not_handled(fake):
    db, _ = fake
    assert book_bot.handle_verifier_reply("919999999999", "vconf_XTR-X") is False


@pytest.mark.unit
def test_verifier_chitchat_consumed_silently(fake):
    # Anu's number is now multi-purpose (verifier + Divya order forwarder), so her
    # messages are ALWAYS consumed (never fall through to the customer flow).
    # Short chit-chat trips the cheap pre-gate: consumed, but nothing is created.
    db, sent = fake
    assert book_bot.handle_verifier_reply(VERIFIER, "hello there") is True
    assert sent["text"] == [] and sent["buttons"] == []


@pytest.mark.unit
def test_assemble_normalises_parse():
    o = book_bot._assemble({
        "name": " Deepa ps ", "phone": "+91 98472-20820",
        "address": "Kausthubham, Malappuram", "pincode": "673634",
        "copies": 20, "books": [{"title": "malayalam", "qty": 20}],
        "book_explicit": True,
    })
    assert o["name"] == "Deepa ps"
    assert o["phone"] == "919847220820"
    assert o["address"] == "Kausthubham, Malappuram, 673634"
    assert o["items"] == {"malayalam": 20}
    assert o["book_explicit"] is True
    # No book named → items stay EMPTY (never guessed), book_explicit False.
    o2 = book_bot._assemble({"name": "X", "phone": "9495706405", "copies": 1, "books": []})
    assert o2["items"] == {} and o2["book_explicit"] is False


# ── post-order follow-up ──────────────────────────────────────────────────────

@pytest.mark.integration
def test_post_order_yes_shows_menu(fake):
    db, sent = fake
    db.sessions[PHONE] = {"step": "post_order"}
    book_bot.maybe_handle_book(PHONE, "thanks!")              # any message → ask
    assert db.sessions[PHONE]["step"] == "post_order_ask"
    assert sent["buttons"][-1] == ["po_yes", "po_no"]
    book_bot.maybe_handle_book(PHONE, "po_yes")
    assert PHONE not in db.sessions                          # cleared → start from top
    assert any("How can we help" in m for m in sent["text"])


@pytest.mark.integration
def test_post_order_no_thanks_and_stops(fake):
    db, sent = fake
    db.sessions[PHONE] = {"step": "post_order"}
    book_bot.maybe_handle_book(PHONE, "ok")
    book_bot.maybe_handle_book(PHONE, "po_no")
    assert PHONE not in db.sessions
    assert any("thank you" in m.lower() for m in sent["text"])


# ── regression: payment-proof routing + confirm verification (2026-06-04) ─────

@pytest.mark.unit
def test_payment_proof_routes_on_order_state_without_book_pay_session(fake):
    """Bug A: a payment screenshot must route to the verifier based on the ORDER
    state, even when the session step is NOT 'book_pay' (QR sent out-of-band, or
    session drift). Otherwise it falls through to print-job intake (the
    'considered it as a print' bug) and Anu never sees it."""
    db, sent = fake
    db.create_book_order("XTR-OOB", "918888800000", "Cust")
    db.update_book_order("XTR-OOB", status="awaiting_payment", items={"malayalam": 1})
    db.sessions.pop("918888800000", None)                    # no book_pay session
    replies = book_bot.handle_payment_proof("918888800000", b"img", "image/jpeg")
    assert replies is not None                                # not ignored → not print
    assert db.orders["XTR-OOB"]["status"] == "payment_review"
    assert sent["forward"] == ["XTR-OOB"]                     # forwarded to Anu


@pytest.mark.unit
def test_confirm_failure_does_not_send_false_confirmation(fake, monkeypatch):
    """Bug B: if the status write does not persist, confirm_book_order must
    report failure and must NOT send a false '🎉 Order confirmed!' message."""
    db, sent = fake
    db.create_book_order("XTR-FAIL", "918888811111", "Cust")
    db.update_book_order("XTR-FAIL", status="payment_review", items={"malayalam": 1})
    monkeypatch.setattr(_dbc, "update_book_order", lambda code, **f: False)  # write fails
    res = book_bot.confirm_book_order("XTR-FAIL")
    assert res["ok"] is False
    assert res.get("error") == "status_not_persisted"
    assert db.orders["XTR-FAIL"]["status"] == "payment_review"     # unchanged
    assert not any("Order confirmed" in m for m in sent["text"])
