"""Flow tests for book_bot — combo multi-select + per-book count + edit.

db_cloud is faked in-memory; the interactive senders are stubbed. The flow sends
messages as side effects and returns [] (or None when not a book message).
"""

import re as _re

import pytest

import book_bot
import book_catalog as bc
import db_cloud as _dbc

_ML = _re.compile(r"[ഀ-ൿ]")   # Malayalam block
_HI = _re.compile(r"[ऀ-ॿ]")   # Devanagari block


class FakeDB:
    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.orders: dict[str, dict] = {}
        self.payments: dict[int, dict] = {}
        self._seq = 0
        self._payseq = 0
        self.feedback: dict[str, dict] = {}

    def get_session(self, db, phone):
        return dict(self.sessions.get(phone, {}))

    def save_session(self, db, phone, **kw):
        self.sessions.setdefault(phone, {}).update(kw)

    def clear_session(self, db, phone):
        self.sessions.pop(phone, None)

    def get_active_book_order(self, phone):
        active = [o for o in self.orders.values()
                  if o["phone"] == phone
                  and o["status"] in ("collecting", "awaiting_payment", "payment_review", "partially_paid")]
        active.sort(key=lambda o: o["_seq"], reverse=True)
        return dict(active[0]) if active else {}

    def create_book_order(self, code, phone, name=None, source="whatsapp"):
        self._seq += 1
        o = {"order_code": code, "phone": phone, "name": name, "items": {},
             "status": "collecting", "flow_cursor": {}, "source": source,
             "_seq": self._seq}
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

    # part-payment ledger
    def add_book_payment(self, order_code, proof_url):
        self._payseq += 1
        row = {"id": self._payseq, "order_code": order_code, "proof_url": proof_url,
               "amount": None, "status": "pending"}
        self.payments[self._payseq] = row
        return dict(row)

    def get_book_payment(self, payment_id):
        return dict(self.payments.get(int(payment_id), {}))

    def get_book_payments(self, order_code):
        return [dict(p) for p in self.payments.values() if p["order_code"] == order_code]

    def verify_book_payment(self, payment_id, amount):
        p = self.payments.get(int(payment_id))
        if p:
            p.update(status="verified", amount=amount)
        return dict(p or {})

    def reject_book_payment(self, payment_id):
        p = self.payments.get(int(payment_id))
        if p:
            p["status"] = "rejected"
        return True

    def book_amount_paid(self, order_code):
        return float(sum((p.get("amount") or 0) for p in self.payments.values()
                         if p["order_code"] == order_code and p["status"] == "verified"))

    # stale payment_review reconciliation (verifier reminder sweep)
    def find_stale_payment_reviews(self, idle_minutes=30, cooldown_hours=3, limit=100):
        rows = [dict(o) for o in self.orders.values()
                if o.get("status") == "payment_review"
                and not o.get("verifier_reminder_at")]
        return rows[:limit]

    def mark_verifier_reminded(self, order_code):
        o = self.orders.get(order_code)
        if o:
            o["verifier_reminder_at"] = "2026-06-19T00:00:00+00:00"

    # delivery confirmation + book feedback
    def find_dispatched_by_tracking(self, ref):
        for o in self.orders.values():
            if o.get("status") == "dispatched" and (o.get("tracking_no") or "") == ref:
                return dict(o)
        return {}

    def mark_book_delivered(self, code):
        o = self.orders.get(code)
        if o:
            o["status"] = "delivered"
            o["delivered_at"] = "2026-06-21T00:00:00+00:00"
        return bool(o)

    def latest_delivered_order(self, phone):
        ds = [o for o in self.orders.values()
              if o.get("phone") == phone and o.get("status") == "delivered"]
        ds.sort(key=lambda o: o.get("_seq", 0), reverse=True)
        return dict(ds[0]) if ds else {}

    def save_book_feedback(self, code, phone, rating=None, comment=None):
        row = self.feedback.setdefault(
            code, {"order_code": code, "phone": phone, "rating": None, "comment": None})
        if rating is not None:
            row["rating"] = rating
        if comment is not None:
            row["comment"] = comment
        return dict(row)


@pytest.fixture
def fake(monkeypatch):
    db = FakeDB()
    for fn in ("get_session", "save_session", "clear_session",
               "get_active_book_order", "create_book_order", "update_book_order",
               "get_book_order", "upload_book_payment_proof", "log_message",
               "add_book_payment", "get_book_payment", "get_book_payments",
               "verify_book_payment", "reject_book_payment", "book_amount_paid"):
        monkeypatch.setattr(_dbc, fn, getattr(db, fn))

    sent = {"list": [], "buttons": [], "text": [], "qr": [], "forward": []}
    monkeypatch.setattr(book_bot, "_send_list",
                        lambda phone, body, btn, rows, header=None, section_title="": sent["list"].append([r["id"] for r in rows]))
    monkeypatch.setattr(book_bot, "_send_buttons",
                        lambda phone, body, buttons, header=None: sent["buttons"].append([b[0] for b in buttons]))
    monkeypatch.setattr(book_bot, "_send_text",
                        lambda phone, msg: sent["text"].append(msg))
    monkeypatch.setattr(book_bot, "_send_qr",
                        lambda phone, order: (sent["qr"].append(order["order_code"]) or True))
    monkeypatch.setattr(book_bot, "_forward_to_verifier",
                        lambda order, payment, content, mime: sent["forward"].append((order["order_code"], payment.get("id"))))
    return db, sent


PHONE = "919000000001"
ADDR = "12 MG Road, Thrissur, 680001"


def _code(db):
    return next(iter(db.orders))


# ── website checkout hand-off (ORDER template) ────────────────────────────────

WEB_ORDER = (
    "ORDER\n"
    "Name: Priya Krishnan\n"
    "Phone: 9876543210\n"
    "Address: 12 MG Road, Thrissur 680001\n"
    "Aksharamrutham: 2\n"
    "Vidyamrut: 1\n"
    "Delivery: courier\n"
)


@pytest.mark.unit
def test_conversational_order_stamped_whatsapp(fake):
    # An order started from the chat catalog is tagged source='whatsapp', so it
    # can be told apart from website / walk-in / divya orders later.
    db, _ = fake
    book_bot.maybe_handle_book(PHONE, "book")
    o = db.get_active_book_order(PHONE)
    assert o and o["source"] == "whatsapp"


@pytest.mark.integration
def test_website_order_ingested_without_reasking(fake):
    db, sent = fake
    res = book_bot.maybe_handle_book(PHONE, WEB_ORDER)
    assert res == []                                    # handled in one shot
    code = _code(db)
    o = db.orders[code]
    assert o["items"] == {"malayalam": 2, "hindi": 1}   # parsed + alias-mapped
    assert o["source"] == "website"                     # tagged distinct from chat orders
    assert o["name"] == "Priya Krishnan"
    assert o["address"] == "12 MG Road, Thrissur 680001"
    assert o["contact_phone"] == "9876543210"
    # Stored totals must include courier — bug: website orders stored grand_total=0.
    import book_catalog as _bc
    _t = _bc.compute_totals(o["items"])
    assert o["courier"] == _t["courier"] and o["courier"] > 0
    assert o["books_total"] == _t["books_total"] > 0
    assert o["grand_total"] == _t["grand_total"] == o["books_total"] + o["courier"]
    assert db.sessions[PHONE]["step"] == "book_summary"
    # Jumped straight to the summary (Confirm / Edit / Cancel) — never re-asked.
    assert sent["buttons"][-1] == ["ord_yes", "ord_edit", "ord_no"]
    assert sent["list"] == []                           # selection list never shown

    # Confirm -> payment QR, status awaiting_payment.
    book_bot.maybe_handle_book(PHONE, "ord_yes")
    assert db.orders[code]["status"] == "awaiting_payment"
    assert sent["qr"] == [code]


@pytest.mark.unit
def test_website_order_ignored_mid_print(fake):
    db, _ = fake
    db.sessions[PHONE] = {"step": "size", "job_id": "OSP-x"}
    # An ORDER message must NOT hijack an in-progress print job.
    assert book_bot.maybe_handle_book(PHONE, WEB_ORDER) is None


@pytest.mark.integration
def test_website_order_ingested_during_staff_hold(fake):
    # Bug (Pinky): a customer in staff_hold who sends a valid ORDER had it
    # black-holed — _in_print_flow(staff_hold) was True so _try_website_order was
    # skipped. A complete ORDER template must still be ingested in staff_hold.
    db, sent = fake
    db.sessions[PHONE] = {"step": "staff_hold", "needs_human": True}
    res = book_bot.maybe_handle_book(PHONE, WEB_ORDER)
    assert res == []                                     # ingested, not dropped
    code = _code(db)
    assert db.orders[code]["items"] == {"malayalam": 2, "hindi": 1}
    assert db.orders[code]["courier"] > 0                # totals computed
    assert db.sessions[PHONE]["step"] == "book_summary"  # hold released into order flow


@pytest.mark.unit
def test_incomplete_order_template_not_ingested(fake):
    db, _ = fake
    bad = "ORDER\nName: Priya\n"   # no phone, no books -> parse ok=False
    assert book_bot.maybe_handle_book(PHONE, bad) is None


# ── part-payments: multiple screenshots, amount validated by Anu ───────────────

V = book_bot.VERIFIER_PHONE


def _make_awaiting_order(db, items, code="XTR-TEST-0001"):
    db._seq += 1
    db.orders[code] = {
        "order_code": code, "phone": PHONE, "name": "Priya",
        "address": "12 MG Road 680001", "contact_phone": PHONE,
        "items": items, "status": "awaiting_payment", "_seq": db._seq,
    }
    return code


# ── pasted payment text → routed to Anu, never the screenshot loop ────────────

RASMI_SMS = ("Rs.275.00 paid thru A/C XX7606 on 13-6-26 13:21:36 to OXYGEN "
             "STUDENTS, UPI Ref 616443327414. If not done, SMS BLOCKUPI to "
             "9901771222.-Canara Bank")


@pytest.mark.integration
def test_pasted_payment_text_routes_to_anu(fake):
    db, sent = fake
    code = _make_awaiting_order(db, {"malayalam": 1})
    db.sessions[PHONE] = {"step": "book_pay"}

    res = book_bot.maybe_handle_book(PHONE, RASMI_SMS)
    assert res == []

    # Order moves to payment_review with the reference captured.
    assert db.orders[code]["status"] == "payment_review"
    assert db.orders[code]["payment_ref"] == "616443327414"

    # A pending payment row exists and Anu got Full/Part/Not-received buttons.
    assert any(p["order_code"] == code for p in db.payments.values())
    assert sent["buttons"][-1][0].startswith("pf_")

    # Customer is acknowledged — NOT looped with "send a screenshot".
    assert any("confirming it now" in m for m in sent["text"])
    assert not any("send a screenshot" in m.lower() for m in sent["text"])


@pytest.mark.unit
def test_start_does_not_wipe_awaiting_payment_order(fake):
    # Re-entering the flow (typing "books") on a confirmed-but-unpaid order used
    # to reset items/address to empty — the bug that lost Rasmi's order.
    db, sent = fake
    code = _make_awaiting_order(db, {"malayalam": 1})
    db.orders[code].update(grand_total=275, books_total=200)

    res = book_bot._start(PHONE, "Priya")

    assert db.orders[code]["items"] == {"malayalam": 1}      # preserved, not wiped
    assert db.orders[code]["status"] == "awaiting_payment"
    assert res and any("awaiting payment" in m.lower() for m in res)
    assert sent["list"] == []                                # no fresh selection list


@pytest.mark.integration
def test_part_then_full_two_screenshots_confirms(fake):
    db, sent = fake
    code = _make_awaiting_order(db, {"malayalam": 3, "hindi": 2})   # grand 1044

    # Screenshot 1 → ledger row, forwarded to Anu.
    book_bot.handle_payment_proof(PHONE, b"img1", "image/jpeg")
    p1 = max(db.payments)
    # Anu: part payment, types the amount.
    book_bot.handle_verifier_reply(V, f"pp_{p1}")
    book_bot.handle_verifier_reply(V, "500")
    assert db.book_amount_paid(code) == 500
    assert db.orders[code]["status"] == "partially_paid"
    assert db.orders[code]["amount_paid"] == 500
    assert any("Balance" in m for m in sent["text"])      # customer told the balance

    # Screenshot 2 → second ledger row (first NOT overwritten).
    book_bot.handle_payment_proof(PHONE, b"img2", "image/jpeg")
    p2 = max(db.payments)
    assert p2 != p1
    # Anu: full clears the remaining 544 → order confirmed.
    book_bot.handle_verifier_reply(V, f"pf_{p2}")
    assert db.book_amount_paid(code) == 1044
    assert db.orders[code]["status"] == "confirmed"
    assert len(db.get_book_payments(code)) == 2           # every screenshot captured


@pytest.mark.integration
def test_anu_can_act_on_any_screenshot_not_just_last(fake):
    # The old bug: only the last screenshot was confirmable. Now each is addressable.
    db, sent = fake
    code = _make_awaiting_order(db, {"malayalam": 1})      # grand 275
    book_bot.handle_payment_proof(PHONE, b"a", "image/jpeg")
    first = max(db.payments)
    book_bot.handle_payment_proof(PHONE, b"b", "image/jpeg")
    second = max(db.payments)
    assert first != second
    # Reject the SECOND (a dup) and act on the FIRST — both independently addressable.
    book_bot.handle_verifier_reply(V, f"pr_{second}")
    assert db.payments[second]["status"] == "rejected"
    book_bot.handle_verifier_reply(V, f"pf_{first}")
    assert db.payments[first]["status"] == "verified"
    assert db.orders[code]["status"] == "confirmed"


@pytest.mark.integration
def test_reject_after_partial_keeps_balance_and_asks_remaining(fake):
    db, sent = fake
    code = _make_awaiting_order(db, {"malayalam": 3, "hindi": 2})   # grand 1044
    book_bot.handle_payment_proof(PHONE, b"1", "image/jpeg")
    p1 = max(db.payments)
    book_bot.handle_verifier_reply(V, f"pp_{p1}")
    book_bot.handle_verifier_reply(V, "500")
    assert db.orders[code]["status"] == "partially_paid"

    book_bot.handle_payment_proof(PHONE, b"2", "image/jpeg")
    p2 = max(db.payments)
    sent["text"].clear()
    book_bot.handle_verifier_reply(V, f"pr_{p2}")          # Anu: not received
    assert db.payments[p2]["status"] == "rejected"
    assert db.book_amount_paid(code) == 500               # balance unchanged
    assert db.orders[code]["status"] == "partially_paid"
    assert any("remaining" in m for m in sent["text"])    # customer asked for the rest


@pytest.mark.integration
def test_overpayment_is_flagged(fake):
    db, sent = fake
    code = _make_awaiting_order(db, {"malayalam": 1})      # grand 275
    book_bot.handle_payment_proof(PHONE, b"1", "image/jpeg")
    p1 = max(db.payments)
    book_bot.handle_verifier_reply(V, f"pp_{p1}")
    book_bot.handle_verifier_reply(V, "300")              # > 275
    assert db.orders[code]["status"] == "confirmed"
    assert any("Overpaid" in m for m in sent["text"])


# ── trigger ───────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text", ["book", "BOOKS", "xtraa", "Aksharamrutham"])
def test_trigger_words(text):
    assert book_bot.is_book_trigger(text) is True


# Bare greeting / language words must NOT hijack the shared line into the
# book flow — only specific brand/title words or qualified phrases do.
@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "hi", "Hi there", "hello", "en", "ml",
    "english", "malayalam", "hindi", "easy",
    "I need english printout",
])
def test_non_trigger_words(text):
    assert book_bot.is_book_trigger(text) is False


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "easy english", "malayalam book", "ml book",
    "I want to order books", "need book",
])
def test_trigger_phrases(text):
    assert book_bot.is_book_trigger(text) is True


@pytest.mark.unit
def test_no_trigger_when_mid_print(fake):
    db, _ = fake
    db.sessions[PHONE] = {"step": "size", "job_id": "OSP-x"}
    assert book_bot.maybe_handle_book(PHONE, "book") is None


# ── selection list offers combos ──────────────────────────────────────────────

@pytest.mark.unit
def test_typed_malayalam_title_all_advances(fake):
    # Confused customer SENDS the Malayalam option as text (typed/echoed the
    # visible title) instead of tapping the list row. Must still advance.
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "മൂന്നും (Set)")
    assert db.sessions[PHONE]["step"] == "book_qty"


@pytest.mark.unit
def test_typed_malayalam_title_single_advances(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "അക്ഷരാമൃതം")
    assert db.sessions[PHONE]["step"] == "book_qty"


@pytest.mark.unit
def test_typed_malayalam_title_combo_advances(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "മലയാളം + ഹിന്ദി")
    assert db.sessions[PHONE]["step"] == "book_qty"
    order = db.orders[_code(db)]
    assert set((order.get("items") or {}).keys()) == {"malayalam", "hindi"}


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
    assert db.sessions[PHONE]["step"] == "book_name"
    assert db.orders[code]["items"] == {"malayalam": 1, "hindi": 1, "english": 1}
    assert db.orders[code]["grand_total"] == 664.0       # 549 set + 115 courier (1250g)

    book_bot.maybe_handle_book(PHONE, "Priya Krishnan")  # book_name step
    assert db.sessions[PHONE]["step"] == "book_address"

    book_bot.maybe_handle_book(PHONE, ADDR)              # book_address step
    assert db.sessions[PHONE]["step"] == "book_dtdc"

    book_bot.maybe_handle_book(PHONE, "dtdc_skip")       # book_dtdc step
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
    assert sent["forward"] == [(code, 1)]                  # (order, payment id) forwarded to verifier
    res = book_bot.confirm_book_order(code)
    assert res["ok"] and db.orders[code]["status"] == "confirmed"
    assert db.sessions[PHONE]["step"] == "post_order"       # follow-up armed


# ── Divya's own self-order exemption must apply during checkout itself ────────
# (not just retroactively at confirm_book_order) — she pays book cost alone,
# no courier, no commission, on any channel including her own chat session.

@pytest.mark.integration
def test_divya_self_order_via_chat_is_courier_free_at_qty_step(fake):
    db, sent = fake
    book_bot.maybe_handle_book(bc.DIVYA_PHONE, "book")
    code = _code(db)
    book_bot.maybe_handle_book(bc.DIVYA_PHONE, "bk_all")
    book_bot.maybe_handle_book(bc.DIVYA_PHONE, "qty_1")
    book_bot.maybe_handle_book(bc.DIVYA_PHONE, "qty_1")
    book_bot.maybe_handle_book(bc.DIVYA_PHONE, "qty_1")   # done counting
    order = db.orders[code]
    assert order["courier"] == 0.0
    assert order["grand_total"] == order["books_total"] == 549.0

    # The summary/payment screens shown to her must reflect the same exemption,
    # not just the stored row (this is what she actually sees and is asked to pay).
    summary = book_bot._summary_text(db.orders[code])
    caption = book_bot._payment_caption(db.orders[code])
    assert "₹549" in summary and "₹0" in summary
    assert "₹549" in caption


@pytest.mark.integration
def test_divya_self_order_via_website_template_is_courier_free(fake):
    db, sent = fake
    web_order = (
        "ORDER\n"
        "Name: Divya M\n"
        "Phone: 9947184088\n"      # delivery contact can differ from her own number
        "Address: 12 MG Road, Thrissur 680001\n"
        "Aksharamrutham: 1\n"
        "Vidyamrut: 1\n"
        "Easy English: 1\n"
        "Delivery: courier\n"
    )
    res = book_bot.maybe_handle_book(bc.DIVYA_PHONE, web_order)
    assert res == []
    code = _code(db)
    order = db.orders[code]
    assert order["courier"] == 0.0
    assert order["grand_total"] == order["books_total"] == 549.0


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
    assert db.orders[code]["grand_total"] == 715.0       # 400+200 + 115 courier (1500g)


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
    book_bot.maybe_handle_book(PHONE, "Priya Krishnan")   # book_name step
    book_bot.maybe_handle_book(PHONE, ADDR)               # book_address step
    book_bot.maybe_handle_book(PHONE, "dtdc_skip")        # book_dtdc step (no preference)
    book_bot.maybe_handle_book(PHONE, "ph_yes")           # book_phone step
    assert db.sessions[PHONE]["step"] == "book_summary"


@pytest.mark.integration
def test_edit_address(fake):
    db, sent = fake
    _drive_to_summary(db)
    code = _code(db)
    book_bot.maybe_handle_book(PHONE, "ord_edit")
    assert db.sessions[PHONE]["step"] == "book_edit"
    # edit menu is now a list (5 options: name, books, address, dtdc, phone)
    assert sent["list"][-1] == ["ed_name", "ed_books", "ed_addr", "ed_dtdc", "ed_phone"]

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
    assert db.sessions[PHONE]["step"] == "book_name"
    book_bot.maybe_handle_book(PHONE, "Priya Krishnan")               # book_name step
    assert db.sessions[PHONE]["step"] == "book_address"
    book_bot.maybe_handle_book(PHONE, "My house, MG Road, Thrissur")   # no PIN
    assert db.sessions[PHONE]["step"] == "book_address"                # stayed
    assert any("PIN" in m for m in sent["text"])
    book_bot.maybe_handle_book(PHONE, "My house, MG Road, Thrissur 680001")  # with PIN
    assert db.sessions[PHONE]["step"] == "book_dtdc"                  # advanced to dtdc


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
    # edit menu is now a list (5 options)
    assert sent["list"][-1] == ["ed_name", "ed_books", "ed_addr", "ed_dtdc", "ed_phone"]


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


@pytest.mark.unit
def test_abandoned_message_payment_segment(fake):
    # Confirmed-but-unpaid cart → "complete your payment ₹X", not the continue copy.
    import book_catalog as _bc
    total = _bc.compute_totals({"malayalam": 2})["grand_total"]
    msg = book_bot._abandoned_message(
        {"order_code": "XTR-9", "status": "awaiting_payment", "items": {"malayalam": 2}})
    assert "PAY" in msg
    assert f"₹{total:.0f}" in msg
    assert _ML.search(msg)                       # bilingual
    assert "to continue" not in msg.lower()      # not the collecting copy


@pytest.mark.unit
def test_abandoned_message_continue_segment_is_bilingual(fake):
    # Not-yet-confirmed cart → keeps the existing continue copy, now bilingual.
    msg = book_bot._abandoned_message(
        {"order_code": "XTR-8", "status": "collecting", "items": {"malayalam": 1}})
    assert _ML.search(msg)                       # Malayalam line added
    assert "didn't finish" in msg.lower() or "didn’t finish" in msg.lower()
    assert "Aksharamrutham × 1" in msg           # cart contents preserved


# ── template reminders for the >24h backlog (Fix C2) ──────────────────────────

@pytest.mark.unit
def test_cart_template_for_segments(fake, monkeypatch):
    monkeypatch.setattr(book_bot, "CART_CONTINUE_TEMPLATE", "xtraa_cart_continue")
    monkeypatch.setattr(book_bot, "CART_PAYMENT_TEMPLATE", "xtraa_payment_pending")
    import book_catalog as _bc
    t, p = book_bot._cart_template_for(
        {"order_code": "XTR-1", "status": "collecting", "name": "Priya", "items": {"malayalam": 1}})
    assert t == "xtraa_cart_continue" and p == ["Priya"]
    total = _bc.compute_totals({"malayalam": 2})["grand_total"]
    t2, p2 = book_bot._cart_template_for(
        {"order_code": "XTR-2", "status": "awaiting_payment", "name": "Anu", "items": {"malayalam": 2}})
    assert t2 == "xtraa_payment_pending"
    assert p2 == ["Anu", "XTR-2", f"{total:.0f}"]


@pytest.mark.unit
def test_cart_template_for_skips_when_unset(fake, monkeypatch):
    monkeypatch.setattr(book_bot, "CART_CONTINUE_TEMPLATE", "")
    monkeypatch.setattr(book_bot, "CART_PAYMENT_TEMPLATE", "")
    t, p = book_bot._cart_template_for({"order_code": "X", "status": "collecting", "items": {}})
    assert t is None and p is None


@pytest.mark.unit
def test_send_template_reminders_sends_and_stamps(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "CART_CONTINUE_TEMPLATE", "xtraa_cart_continue")
    monkeypatch.setattr(book_bot, "CART_PAYMENT_TEMPLATE", "xtraa_payment_pending")
    carts = [
        {"order_code": "XTR-A", "phone": "9111", "status": "collecting",
         "name": "P", "items": {"malayalam": 1}},
        {"order_code": "XTR-B", "phone": "9222", "status": "awaiting_payment",
         "name": "Q", "items": {"malayalam": 1}},
    ]
    monkeypatch.setattr(_dbc, "find_abandoned_book_carts", lambda **kw: carts)
    marked = []
    monkeypatch.setattr(_dbc, "mark_abandoned_reminded", lambda code: marked.append(code))
    calls = []
    import whatsapp_notify as _wn
    monkeypatch.setattr(_wn, "_send_meta_template",
                        lambda phone, name, params, lang: calls.append((phone, name, params, lang)) or True)

    res = book_bot.send_template_reminders()
    assert res == {"carts": 2, "reminded": 2}
    assert marked == ["XTR-A", "XTR-B"]
    assert calls[0] == ("9111", "xtraa_cart_continue", ["P"], "ml")
    assert calls[1][1] == "xtraa_payment_pending"


@pytest.mark.unit
def test_send_template_reminders_skips_unconfigured_segment(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "CART_CONTINUE_TEMPLATE", "")            # not approved yet
    monkeypatch.setattr(book_bot, "CART_PAYMENT_TEMPLATE", "xtraa_payment_pending")
    carts = [
        {"order_code": "XTR-A", "phone": "9111", "status": "collecting",
         "name": "P", "items": {"malayalam": 1}},
        {"order_code": "XTR-B", "phone": "9222", "status": "awaiting_payment",
         "name": "Q", "items": {"malayalam": 1}},
    ]
    monkeypatch.setattr(_dbc, "find_abandoned_book_carts", lambda **kw: carts)
    marked = []
    monkeypatch.setattr(_dbc, "mark_abandoned_reminded", lambda code: marked.append(code))
    import whatsapp_notify as _wn
    monkeypatch.setattr(_wn, "_send_meta_template", lambda *a, **k: True)

    res = book_bot.send_template_reminders()
    assert res == {"carts": 2, "reminded": 1}     # only the payment cart sent
    assert marked == ["XTR-B"]


@pytest.mark.unit
def test_run_cart_reminders_combines_both(fake, monkeypatch):
    monkeypatch.setattr(book_bot, "send_abandoned_reminders", lambda: {"carts": 2, "reminded": 2})
    monkeypatch.setattr(book_bot, "send_template_reminders", lambda: {"carts": 5, "reminded": 3})
    res = book_bot.run_cart_reminders()
    assert res["carts"] == 7 and res["reminded"] == 5
    assert res["freeform"] == {"carts": 2, "reminded": 2}
    assert res["template"] == {"carts": 5, "reminded": 3}


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
    assert sent["forward"] == [("XTR-OOB", 1)]               # (order, payment id) forwarded to Anu


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


# ── verifier reminder sweep: don't let payment_review orders strand ───────────
# Root cause (Sudhin/Mayaja): the verification prompt to Anu is fire-and-forget.
# When she misses it (sent at night, or stacked behind another prompt) the order
# sits in payment_review forever — nothing re-surfaces it. This sweep re-pings.

@pytest.mark.integration
def test_stale_payment_review_is_re_sent_to_anu_then_idempotent(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(_dbc, "find_stale_payment_reviews",
                        db.find_stale_payment_reviews, raising=False)
    monkeypatch.setattr(_dbc, "mark_verifier_reminded",
                        db.mark_verifier_reminded, raising=False)

    code = _make_awaiting_order(db, {"malayalam": 1})            # grand 275
    book_bot.handle_payment_proof(PHONE, b"img", "image/jpeg")  # -> payment_review + pending pay
    assert db.orders[code]["status"] == "payment_review"
    payid = max(db.payments)
    sent["buttons"].clear(); sent["text"].clear()

    # First sweep: Anu re-pinged with the SAME actionable buttons; order stamped.
    res1 = book_bot.send_verifier_reminders()
    assert res1 == {"found": 1, "reminded": 1}
    assert any(f"pf_{payid}" in ids for ids in sent["buttons"])  # Full/Part/Not-received re-sent
    assert any(code in m for m in sent["text"])                  # reminder names the order
    assert db.orders[code].get("verifier_reminder_at")          # stamped → no re-spam

    # Second sweep within cooldown: nothing re-sent.
    sent["buttons"].clear()
    res2 = book_bot.send_verifier_reminders()
    assert res2 == {"found": 0, "reminded": 0}
    assert sent["buttons"] == []


# ── delivery confirmation (Anu forwards DTDC) + book feedback ─────────────────

DTDC_DELIVERED = ("R5001087357 is delivered on 20/6/2026 to SREEYA P G "
                  "Share feedback https://1jx.in/DTDCCR/e2bbd040 DTDC won't ask for any payment OTP")


@pytest.mark.integration
def test_dtdc_delivered_marks_order_delivered_and_acks_anu(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(_dbc, "find_dispatched_by_tracking", db.find_dispatched_by_tracking, raising=False)
    monkeypatch.setattr(_dbc, "mark_book_delivered", db.mark_book_delivered, raising=False)
    monkeypatch.setattr(_dbc, "save_book_feedback", db.save_book_feedback, raising=False)
    monkeypatch.setattr(book_bot, "_request_book_feedback", lambda order: True)  # no live Meta call
    db._seq += 1
    db.orders["XTR-DLV1"] = {"order_code": "XTR-DLV1", "phone": PHONE, "name": "Sreeya P G",
                             "items": {"malayalam": 1}, "status": "dispatched",
                             "tracking_no": "R5001087357", "_seq": db._seq}
    handled = book_bot.handle_verifier_reply(V, DTDC_DELIVERED)
    assert handled is True
    assert db.orders["XTR-DLV1"]["status"] == "delivered"
    assert any("XTR-DLV1" in m for m in sent["text"])      # Anu acked, naming the order


@pytest.mark.unit
def test_dtdc_delivered_unknown_ref_warns_anu(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(_dbc, "find_dispatched_by_tracking", db.find_dispatched_by_tracking, raising=False)
    handled = book_bot.handle_verifier_reply(
        V, "R9999999999 is delivered on 20/6/2026 to NOBODY Share feedback https://x")
    assert handled is True      # consumed here, NOT passed to the LLM order parser
    assert any("R9999999999" in m for m in sent["text"])


@pytest.mark.integration
def test_customer_feedback_reply_saved_and_forwarded(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(_dbc, "save_book_feedback", db.save_book_feedback, raising=False)
    monkeypatch.setattr(_dbc, "latest_delivered_order", db.latest_delivered_order, raising=False)
    db._seq += 1
    db.orders["XTR-DLV2"] = {"order_code": "XTR-DLV2", "phone": PHONE, "name": "Asha",
                             "items": {"malayalam": 1}, "status": "delivered", "_seq": db._seq}
    db.sessions[PHONE] = {"step": "book_feedback"}
    book_bot.maybe_handle_book(PHONE, "5 nalla pusthakam adipoli")
    assert db.feedback["XTR-DLV2"]["rating"] == 5
    assert "nalla" in (db.feedback["XTR-DLV2"]["comment"] or "")
    assert any("XTR-DLV2" in m or "5/5" in m for m in sent["text"])   # forwarded to Anu


@pytest.mark.unit
def test_start_order_public_wrapper_sends_select_list(fake):
    db, sent = fake
    res = book_bot.start_order(PHONE)
    assert res == []                              # nothing to relay on a fresh start
    assert db.sessions[PHONE]["step"] == "book_select"
    assert sent["list"][-1] == ["bk_ml", "bk_hi", "bk_en",
                                "bk_ml_hi", "bk_ml_en", "bk_hi_en", "bk_all"]


@pytest.mark.unit
def test_start_order_relays_awaiting_payment_guard(fake):
    db, sent = fake
    # Seed an order already awaiting payment.
    db.create_book_order("XTR-TEST-1", PHONE, None)
    db.update_book_order("XTR-TEST-1", status="awaiting_payment")
    res = book_bot.start_order(PHONE)
    assert len(res) == 1
    assert "XTR-TEST-1" in res[0]                 # guard text returned to caller


# ── admin take-over of a dropped cart (resume, never wipe) ────────────────────

def _seed_collecting(db, *, step, items=None, name=None, address=None,
                     flow_cursor=None, contact_phone=None, session=None):
    """Seed a collecting cart + its bot session, as a dropped cart looks live."""
    code = "XTR-RESUME-1"
    db.create_book_order(code, PHONE, name)
    db.update_book_order(code, items=items or {}, name=name, address=address,
                         flow_cursor=flow_cursor or {}, contact_phone=contact_phone,
                         grand_total=250, books_total=180, courier=70)
    db.sessions[PHONE] = {"step": step, **(session or {})}
    return code


@pytest.mark.unit
def test_resume_empty_cart_resends_catalog(fake):
    db, sent = fake
    code = _seed_collecting(db, step="book_select", items={})
    res = book_bot.resume_order(PHONE)
    assert res == []
    assert sent["list"][-1] == ["bk_ml", "bk_hi", "bk_en",
                                "bk_ml_hi", "bk_ml_en", "bk_hi_en", "bk_all"]
    assert db.orders[code]["items"] == {}          # nothing to wipe, nothing lost


@pytest.mark.unit
def test_resume_midflow_does_not_wipe_and_reissues_prompt(fake):
    # CORE of Fix A: a cart that stalled at the address step must keep its
    # books + name and simply be re-asked for the address — not reset to step 1.
    db, sent = fake
    code = _seed_collecting(db, step="book_address",
                            items={"malayalam": 2}, name="Priya Krishnan")
    res = book_bot.resume_order(PHONE)
    assert res == []
    assert db.orders[code]["items"] == {"malayalam": 2}   # NOT wiped
    assert db.orders[code]["name"] == "Priya Krishnan"    # NOT wiped
    assert db.sessions[PHONE]["step"] == "book_address"   # stays where they were
    assert sent["text"][-1] == book_bot._address_prompt()
    assert sent["list"] == []                              # catalog never re-shown


@pytest.mark.unit
def test_resume_qty_step_reissues_quantity_buttons(fake):
    # Mid-counting: items is still empty but the flow_cursor holds their place.
    db, sent = fake
    _seed_collecting(db, step="book_qty", items={},
                     flow_cursor={"current": "hindi", "queue": []})
    book_bot.resume_order(PHONE)
    assert sent["buttons"][-1] == ["qty_1", "qty_2", "qty_3"]


@pytest.mark.unit
def test_resume_from_staff_hold_uses_prev_step_and_clears_sos(fake):
    # Staff had taken the chat (bot silent). Take-over resumes the real step
    # and lifts the SOS so the bot answers again.
    db, sent = fake
    _seed_collecting(db, step="staff_hold", items={"malayalam": 1},
                     name="A", address=ADDR,
                     session={"prev_step": "book_dtdc", "needs_human": True})
    book_bot.resume_order(PHONE)
    assert sent["buttons"][-1] == ["dtdc_skip"]
    assert db.sessions[PHONE]["step"] == "book_dtdc"
    assert db.sessions[PHONE]["needs_human"] is False


@pytest.mark.unit
def test_resume_summary_step_shows_summary(fake):
    db, sent = fake
    _seed_collecting(db, step="book_summary", items={"malayalam": 1},
                     name="A", address=ADDR, contact_phone="9876543210")
    book_bot.resume_order(PHONE)
    assert sent["buttons"][-1] == ["ord_yes", "ord_edit", "ord_no"]


@pytest.mark.unit
def test_resume_no_cart_starts_fresh(fake):
    db, sent = fake
    res = book_bot.resume_order(PHONE)
    assert res == []
    assert sent["list"][-1][0] == "bk_ml"          # opening catalog sent
    assert db.sessions[PHONE]["step"] == "book_select"


@pytest.mark.unit
def test_opening_list_is_trilingual(monkeypatch):
    bodies = []
    monkeypatch.setattr(book_bot, "_send_list",
                        lambda phone, body, btn, rows, header=None, section_title="": bodies.append(body))
    book_bot._send_select_list(PHONE)
    body = bodies[-1]
    assert _ML.search(body), "opening list must contain Malayalam"
    assert "हिंदी" in body, "opening list must name Hindi in Devanagari"
    assert _HI.search(body)
    assert "English" in body


@pytest.mark.unit
def test_select_rows_have_malayalam(monkeypatch):
    rows_seen = []
    monkeypatch.setattr(book_bot, "_send_list",
                        lambda phone, body, btn, rows, header=None, section_title="": rows_seen.append(rows))
    book_bot._send_select_list(PHONE)
    rows = rows_seen[-1]
    blob = " ".join(r["title"] + " " + r.get("description", "") for r in rows)
    assert _ML.search(blob), "selection rows must contain Malayalam"


@pytest.mark.unit
def test_address_prompt_is_bilingual():
    p = book_bot._address_prompt()
    assert _ML.search(p) and "PIN" in p


@pytest.mark.unit
def test_qty_prompt_after_full_set_is_malayalam(fake):
    db, sent = fake
    book_bot.maybe_handle_book(PHONE, "book")
    book_bot.maybe_handle_book(PHONE, "bk_all")          # choose all 3
    book_bot.maybe_handle_book(PHONE, "1")               # qty for book 1
    book_bot.maybe_handle_book(PHONE, "1")               # qty for book 2
    book_bot.maybe_handle_book(PHONE, "1")               # qty for book 3 -> name prompt
    assert any(_ML.search(m) for m in sent["text"]), "name prompt must be Malayalam"


# ── _is_valid_name validation ──────────────────────────────────────────────

def test_valid_name_accepts_simple_name():
    from book_bot import _is_valid_name
    assert _is_valid_name("Zeenath Priyaranjini")

def test_valid_name_rejects_multiline_address():
    from book_bot import _is_valid_name
    addr = "ZEENATH PRIYARANJINI \nKAYAMKULAM HOUSE \nP.O.KONATHUKUNNU \nTHRISSUR DIST \nPIN 680123"
    assert not _is_valid_name(addr)

def test_valid_name_rejects_pin_code():
    from book_bot import _is_valid_name
    assert not _is_valid_name("Suresh Kumar 680001")

def test_valid_name_rejects_mobile_number():
    from book_bot import _is_valid_name
    assert not _is_valid_name("Arun 9447123456")

def test_valid_name_rejects_mob_keyword():
    from book_bot import _is_valid_name
    assert not _is_valid_name("Ravi MOB 9447123456")

def test_valid_name_rejects_dist_keyword():
    from book_bot import _is_valid_name
    assert not _is_valid_name("Thrissur Dist PIN 680001")

def test_valid_name_accepts_name_with_one_newline():
    from book_bot import _is_valid_name
    # Some people type name on two lines (first + last) — allow 1 newline
    assert _is_valid_name("Margaret P.J\nമാർഗരറ്റ്")

def test_valid_name_rejects_short():
    from book_bot import _is_valid_name
    assert not _is_valid_name("Ab")

def test_valid_name_rejects_button_ids():
    from book_bot import _is_valid_name
    assert not _is_valid_name("qty_1")
    assert not _is_valid_name("qty_2")
    assert not _is_valid_name("bk_ml")
    assert not _is_valid_name("ph_yes")
    assert not _is_valid_name("dtdc_skip")


# ── _has_pincode: house/door numbers must not be mistaken for a real PIN ──────

@pytest.mark.unit
def test_has_pincode_rejects_bare_house_number():
    from book_bot import _has_pincode
    # No real PIN anywhere — only a 6-digit door number.
    assert not _has_pincode("Door No 204568, near temple, Thrissur")
    assert not _has_pincode("Flat No: 118392, Rose Apartments, Kochi")
    assert not _has_pincode("House no 556231 Ollur")


@pytest.mark.unit
def test_has_pincode_accepts_real_pin_after_house_number():
    from book_bot import _has_pincode
    # A real PIN later in the same address must still be recognised.
    assert _has_pincode("Door No 204568, near temple, Thrissur - 680001")


@pytest.mark.unit
def test_has_pincode_accepts_normal_addresses_unaffected():
    from book_bot import _has_pincode
    assert _has_pincode("12 MG Road, Thrissur 680001")
    assert _has_pincode("Nedumkunnam PO\nKottayam 686542")
    assert not _has_pincode("My house, MG Road, Thrissur")


@pytest.mark.unit
def test_address_prompt_shows_example_with_pin_last():
    p = book_bot._address_prompt()
    assert "680001" in p and "PIN" in p


# ── WhatsApp location shares mid-address-capture ──────────────────────────────

@pytest.mark.unit
def test_maybe_handle_location_ignored_outside_address_step(fake):
    db, sent = fake
    db.sessions[PHONE] = {"step": "book_summary"}
    assert book_bot.maybe_handle_location(PHONE) is None
    assert sent["text"] == []


@pytest.mark.unit
def test_maybe_handle_location_prompts_during_address_step(fake):
    db, sent = fake
    db.sessions[PHONE] = {"step": "book_address"}
    result = book_bot.maybe_handle_location(PHONE)
    assert result == []
    assert any("type" in m.lower() and "address" in m.lower() for m in sent["text"])


@pytest.mark.unit
def test_maybe_handle_location_prompts_during_edit_address_step(fake):
    db, sent = fake
    db.sessions[PHONE] = {"step": "book_edit_address"}
    result = book_bot.maybe_handle_location(PHONE)
    assert result == []
    assert sent["text"]


def test_payment_caption_includes_upi_number():
    order = {"order_code": "XTR-1", "phone": "919999999999",
             "items": {"malayalam": 1}, "delivery_method": "courier"}
    cap = book_bot._payment_caption(order)
    assert "9072034907" in cap


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


def test_typed_order_creates_confirm(fake):
    db, sent = fake
    out = book_bot.maybe_handle_book("91555", "Aksharamrutham 1 copy")
    assert out == []
    assert db.sessions["91555"]["step"] == "book_confirm_parsed"
    order = db.get_active_book_order("91555")
    assert order["items"] == {"malayalam": 1}
    assert sent["buttons"] and "ord_yes" in sent["buttons"][-1]


def test_price_question_sends_faq(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda text: None)
    # A price question that names NO specific book (a named book -> confirm, which
    # itself shows the price). "books" triggers the book flow; "how much" is FAQ.
    out = book_bot.maybe_handle_book("91556", "how much for the books")
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


def test_parsed_confirm_retyped_order_reconfirms(fake):
    db, sent = fake
    db.create_book_order("XTR-7", "91666", name=None)
    db.update_book_order("XTR-7", items={"malayalam": 1},
                         books_total=200, courier=75, grand_total=275)
    db.save_session("supabase", "91666", step="book_confirm_parsed")
    out = book_bot.maybe_handle_book("91666", "hindi book venam")
    assert out == []
    # Still confirming (not dumped to the catalog); items updated; re-confirmed.
    assert db.sessions["91666"]["step"] == "book_confirm_parsed"
    assert db.get_active_book_order("91666")["items"] == {"hindi": 1}
    assert sent["buttons"] and "ord_yes" in sent["buttons"][-1]


def test_parsed_confirm_unrecognized_reasks_without_losing_order(fake):
    db, sent = fake
    db.create_book_order("XTR-6", "91665", name=None)
    db.update_book_order("XTR-6", items={"malayalam": 1},
                         books_total=200, courier=75, grand_total=275)
    db.save_session("supabase", "91665", step="book_confirm_parsed")
    out = book_bot.maybe_handle_book("91665", "hmm")
    assert out == []
    assert db.sessions["91665"]["step"] == "book_confirm_parsed"      # preserved
    assert db.get_active_book_order("91665")["items"] == {"malayalam": 1}  # not lost
    assert sent["buttons"] and "ord_yes" in sent["buttons"][-1]       # re-asked


# ── Mid-flow intent resolver (resolve_stuck_message) ──────────────────────────

def _mk_pay_order(db, items=None, status="partially_paid",
                  grand=275.0, paid=200.0, code="R1"):
    db._seq += 1
    db.orders[code] = {
        "order_code": code, "phone": PHONE, "name": "Ajeesh",
        "items": items or {"malayalam": 1}, "status": status,
        "grand_total": grand, "amount_paid": paid, "_seq": db._seq,
    }
    return db.get_book_order(code)


@pytest.mark.unit
def test_add_books_to_order_charges_delta(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda t: None)
    order = _mk_pay_order(db, {"malayalam": 1})
    replies = book_bot._add_books_to_order(PHONE, order, {"english": 1})
    assert db.orders["R1"]["items"] == {"malayalam": 1, "english": 1}
    assert float(db.orders["R1"]["grand_total"]) == 475.0
    assert replies and "Added" in replies[0]
    assert "200" in replies[0]                       # balance line 475-200
    assert db.sessions.get(PHONE, {}).get("needs_human") is not True


@pytest.mark.unit
def test_add_books_guard_blocks_completed_order(fake, monkeypatch):
    db, sent = fake
    import routing.intent as _ri
    monkeypatch.setattr(_ri, "decide_intent", lambda t: "unknown")
    calls = []
    monkeypatch.setattr(book_bot, "_send_text", lambda p, m: calls.append((p, m)))
    order = _mk_pay_order(db, {"malayalam": 1}, status="confirmed",
                          grand=275.0, paid=275.0, code="R2")
    replies = book_bot._add_books_to_order(PHONE, order, {"english": 1})
    assert db.orders["R2"]["items"] == {"malayalam": 1}      # NOT re-charged
    assert db.sessions[PHONE]["needs_human"] is True
    assert replies == []
    assert any(p == book_bot.VERIFIER_PHONE for p, _ in calls)


@pytest.mark.unit
def test_escalate_to_human_holds_bot_and_pings_anu(fake, monkeypatch):
    db, sent = fake
    import routing.intent as _ri
    monkeypatch.setattr(_ri, "decide_intent", lambda t: "unknown")
    calls = []
    monkeypatch.setattr(book_bot, "_send_text", lambda p, m: calls.append((p, m)))
    order = _mk_pay_order(db, code="R3")
    replies = book_bot._escalate_to_human(PHONE, order, "Is cash on delivery available")
    assert replies == []
    assert db.sessions[PHONE]["needs_human"] is True
    anu = [m for p, m in calls if p == book_bot.VERIFIER_PHONE]
    assert anu and "Needs a human" in anu[0]
    assert "cash on delivery" in anu[0].lower()


@pytest.mark.parametrize("text,expected", [
    ("how much do I owe", True), ("Amount", True), ("എത്ര രൂപ", True),
    ("Easy English", False), ("send it fast", False),
    ("of course", False),      # must not match bare "rs" substring
    ("totally agree", False),  # must not match "total" substring
])
def test_is_price_question(text, expected):
    assert book_bot._is_price_question(text) is expected


@pytest.mark.parametrize("text,expected", [
    ("Need one more book", True), ("add another", True),
    ("ഒരു പുസ്തകം കൂടി", True),
    ("Easy English", False), ("ok", False),
    ("what is your address", False),   # must not match "add" inside "address"
])
def test_wants_more_books(text, expected):
    assert book_bot._wants_more_books(text) is expected


def test_balance_reply_shows_numbers():
    order = {"order_code": "C9", "grand_total": 475.0, "amount_paid": 200.0}
    r = book_bot._balance_reply(order)
    assert "475" in r and "200" in r and "275" in r


@pytest.mark.unit
def test_resolve_adds_named_book(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda t: None)
    order = _mk_pay_order(db, {"malayalam": 1})
    replies = book_bot.resolve_stuck_message(PHONE, "Easy English", order)
    assert db.orders["R1"]["items"] == {"malayalam": 1, "english": 1}
    assert "Added" in replies[0]


@pytest.mark.unit
def test_resolve_asks_which_book_on_vague_add(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda t: None)
    order = _mk_pay_order(db, {"malayalam": 1})
    replies = book_bot.resolve_stuck_message(PHONE, "Need one more book", order)
    assert replies and "Which book" in replies[0]
    assert db.orders["R1"]["items"] == {"malayalam": 1}
    assert db.sessions.get(PHONE, {}).get("needs_human") is not True


@pytest.mark.unit
def test_resolve_answers_price_question(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda t: None)
    order = _mk_pay_order(db, {"malayalam": 1})
    replies = book_bot.resolve_stuck_message(PHONE, "how much balance left", order)
    assert replies and "balance" in replies[0].lower()


@pytest.mark.unit
def test_resolve_escalates_unknown(fake, monkeypatch):
    db, sent = fake
    import routing.intent as _ri
    monkeypatch.setattr(_ri, "decide_intent", lambda t: "unknown")
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda t: None)
    calls = []
    monkeypatch.setattr(book_bot, "_send_text", lambda p, m: calls.append((p, m)))
    order = _mk_pay_order(db)
    replies = book_bot.resolve_stuck_message(PHONE, "Is cash on delivery available", order)
    assert replies == []
    assert db.sessions[PHONE]["needs_human"] is True
    assert any(p == book_bot.VERIFIER_PHONE for p, _ in calls)


@pytest.mark.unit
def test_resolve_ignores_trivial_ack(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda t: None)
    order = _mk_pay_order(db)
    assert book_bot.resolve_stuck_message(PHONE, "ok", order) is None
    assert db.sessions.get(PHONE, {}).get("needs_human") is not True


@pytest.mark.integration
def test_handle_pay_adds_book_before_screenshot_loop(fake, monkeypatch):
    db, sent = fake
    monkeypatch.setattr(book_bot, "_llm_parse_books", lambda t: None)
    _mk_pay_order(db, {"malayalam": 1}, code="P1")
    order = db.get_book_order("P1")
    book_bot._handle_pay(PHONE, "Easy English", order)
    assert db.orders["P1"]["items"] == {"malayalam": 1, "english": 1}
    assert float(db.orders["P1"]["grand_total"]) == 475.0


# ── Anu intake: multi-book accumulation + idempotent confirm (Bug 1) ──────────

def _stage_anu_order(items=None, name="Sheeja R S", phone="9446708675",
                     address="Anchal 691306"):
    o = {"name": name, "phone": phone, "address": address, "copies": 1,
         "items": items or {"malayalam": 1}, "book_explicit": True}
    book_bot._anu_save_buffer("raw text", "anu_staged", order=o)
    return o


@pytest.mark.integration
def test_second_book_tap_accumulates_into_one_order(fake, monkeypatch):
    import json
    db, sent = fake
    created = []
    monkeypatch.setattr(book_bot, "_create_divya_confirmed",
                        lambda *a, **k: created.append(a))
    _stage_anu_order(items={"malayalam": 1})
    # Anu taps a second book button while an order is already staged.
    book_bot._handle_anu_freeform("abook_english")
    blob = json.loads(db.sessions[book_bot.VERIFIER_PHONE]["saved_json"])
    assert blob["order"]["items"] == {"malayalam": 1, "english": 1}   # accumulated
    assert db.sessions[book_bot.VERIFIER_PHONE]["step"] == "anu_staged"  # still one
    assert created == []                                             # nothing saved yet
    assert sent["buttons"] and sent["buttons"][-1][0] == "aok"       # one confirm re-shown


@pytest.mark.integration
def test_confirm_staged_is_idempotent_on_double_tap(fake, monkeypatch):
    db, sent = fake
    created = []
    monkeypatch.setattr(book_bot, "_create_divya_confirmed",
                        lambda *a, **k: created.append(a))
    _stage_anu_order(items={"malayalam": 1, "english": 1})
    book_bot._confirm_staged()      # first Confirm & print tap
    book_bot._confirm_staged()      # rapid second tap
    assert len(created) == 1                                         # exactly ONE order
    assert db.sessions[book_bot.VERIFIER_PHONE].get("step") in ("", None)
    assert any("already saved" in m.lower() for m in sent["text"])


@pytest.mark.integration
def test_confirm_staged_creates_with_all_books(fake, monkeypatch):
    db, sent = fake
    saved_items = []
    monkeypatch.setattr(book_bot, "_create_divya_confirmed",
                        lambda code, name, phone, address, items, **k: saved_items.append(items))
    _stage_anu_order(items={"malayalam": 1, "english": 1})
    book_bot._confirm_staged()
    assert saved_items == [{"malayalam": 1, "english": 1}]           # both books saved


# ── Anu intake: deterministic name recovery when LLM misses it (Bug 2) ────────

_KUMARI = "Kumari Deepthy \nOmkaram\nThanneersala\nAYIROOPPARA\nPothencode po\n695584"


def test_recover_order_from_raw_extracts_name_and_address():
    rec = book_bot._recover_order_from_raw(_KUMARI)
    assert rec["name"] == "Kumari Deepthy"
    assert "Omkaram" in rec["address"] and "695584" in rec["address"]


def test_recover_order_from_raw_needs_a_pin():
    # No 6-digit PIN → don't guess a name (could be anything).
    assert book_bot._recover_order_from_raw("Kumari Deepthy Omkaram") == {}


def test_recover_order_from_raw_ignores_book_only():
    assert book_bot._recover_order_from_raw("Easy English") == {}


@pytest.mark.integration
def test_anu_intake_recovers_name_instead_of_dead_ending(fake, monkeypatch):
    db, sent = fake
    # Reproduce the live failure: the LLM says "not an order" for a clear
    # name+address forward.
    monkeypatch.setattr(book_bot.anu_parser, "parse_order_message",
                        lambda t: {"is_order": False})
    book_bot._handle_anu_freeform(_KUMARI)
    # The bot now progresses to asking for the phone (name recovered), NOT the
    # generic "send the rest" dead-end.
    assert any("phone number" in m.lower() and "Kumari Deepthy" in m for m in sent["text"])
    assert not any("send the rest" in m.lower() for m in sent["text"])
    assert db.sessions[book_bot.VERIFIER_PHONE]["step"] == "anu_intake"



# ── front-loaded address in the NAME step (the "keeps asking for PIN" bug) ─────
# Real incident: a customer typed their whole address — house, PO, PIN, district —
# in one line at the name prompt. The bot discarded it and then nagged for the
# PIN four times (a PIN already in that first line). The name step must instead
# capture name + address in one shot and move on to the courier choice.

_FRONTLOAD = "Manoj Nadakkavu Cheramanglam pin 678703 palakkad"


def _seed_at_name(db, code="XTR-FL-1"):
    db.create_book_order(code, PHONE, name=None)
    db.orders[code]["items"] = {"malayalam": 1}
    db.save_session(None, PHONE, step="book_name")
    return code


@pytest.fixture
def no_llm(monkeypatch):
    """Opt-in: stub the Haiku front-load splitter to 'no result' so a capture
    routed through maybe_handle_book exercises the deterministic fallback without
    any real network call. (Only affects `_llm_split_name_address`.)"""
    monkeypatch.setattr(book_bot, "_llm_split_name_address", lambda t: {})


@pytest.mark.integration
def test_frontloaded_address_in_name_step_is_captured(fake, no_llm):
    db, sent = fake
    code = _seed_at_name(db)

    book_bot.maybe_handle_book(PHONE, _FRONTLOAD)

    order = db.orders[code]
    assert order["name"] == "Manoj Nadakkavu Cheramanglam"
    assert "678703" in (order["address"] or "")        # PIN retained
    assert db.sessions[PHONE]["step"] == "book_dtdc"     # skipped the address nag
    assert not any("include your" in m.lower() and "pin" in m.lower()
                   for m in sent["text"])


@pytest.mark.integration
def test_frontloaded_echoes_both_name_and_address(fake, no_llm):
    """Confirm-back: both parsed fields are shown so a mis-parse is visible."""
    db, sent = fake
    _seed_at_name(db, code="XTR-FL-2")

    book_bot.maybe_handle_book(PHONE, _FRONTLOAD)

    echoed = "\n".join(sent["text"])
    assert "678703" in echoed and "Manoj" in echoed


@pytest.mark.integration
def test_plain_name_still_advances_to_address(fake):
    """Regression: an ordinary name (no PIN) keeps the normal 2-step flow."""
    db, sent = fake
    code = _seed_at_name(db, code="XTR-FL-3")

    book_bot.maybe_handle_book(PHONE, "Priya Krishnan")

    assert db.sessions[PHONE]["step"] == "book_address"
    assert db.orders[code]["name"] == "Priya Krishnan"


@pytest.mark.integration
def test_stray_six_digit_number_is_not_treated_as_address(fake, no_llm):
    """A bare 6-digit amount/reference at the name prompt must NOT be silently
    committed as an address — it falls back to the normal name re-prompt."""
    db, sent = fake
    code = _seed_at_name(db, code="XTR-FL-4")

    book_bot.maybe_handle_book(PHONE, "Cost 145000 is fine")

    assert db.sessions[PHONE]["step"] == "book_name"     # did NOT skip ahead
    assert not db.orders[code].get("address")


@pytest.mark.integration
def test_too_short_frontload_is_not_captured(fake):
    """"A 678684" has a PIN but no address shape → not a fast-path capture."""
    db, sent = fake
    code = _seed_at_name(db, code="XTR-FL-5")

    book_bot.maybe_handle_book(PHONE, "A 678684")

    assert db.sessions[PHONE]["step"] == "book_name"
    assert not db.orders[code].get("address")


@pytest.mark.integration
def test_frontload_prefers_haiku_split_when_available(fake, monkeypatch):
    """When the Haiku parser returns a clean split, it wins over the deterministic
    one — and the model's separated address + pincode are recombined into one
    line with the PIN retained."""
    db, sent = fake
    code = _seed_at_name(db, code="XTR-FL-7")
    monkeypatch.setattr(book_bot, "_llm_split_name_address",
                        lambda t: {"name": "Manoj",
                                   "address": "Nadakkavu, Cheramanglam, Palakkad, 678703"})

    book_bot.maybe_handle_book(PHONE, _FRONTLOAD)

    order = db.orders[code]
    assert order["name"] == "Manoj"                    # cleaner than deterministic
    assert order["address"] == "Nadakkavu, Cheramanglam, Palakkad, 678703"
    assert db.sessions[PHONE]["step"] == "book_dtdc"


def test_llm_split_recombines_address_and_pincode(monkeypatch):
    """_llm_split_name_address stitches the model's separate address + pincode
    into one PIN-bearing line, and rejects output where the PIN didn't survive."""
    import anu_parser
    monkeypatch.setattr(anu_parser, "parse_order_message",
                        lambda t: {"is_order": True, "name": "Manoj",
                                   "address": "Nadakkavu, Cheramanglam", "pincode": "678703"})
    rec = book_bot._llm_split_name_address(_FRONTLOAD)
    assert rec["name"] == "Manoj"
    assert "678703" in rec["address"] and "Cheramanglam" in rec["address"]

    # No PIN anywhere → reject (don't store a PIN-less address).
    monkeypatch.setattr(anu_parser, "parse_order_message",
                        lambda t: {"is_order": True, "name": "Manoj",
                                   "address": "Nadakkavu, Cheramanglam", "pincode": ""})
    assert book_bot._llm_split_name_address(_FRONTLOAD) == {}


def test_split_keeps_full_multiword_name_no_truncation():
    """No fixed word cap: a 4-word name before the PIN is kept whole."""
    rec = book_bot._split_name_address("Priya Lakshmi Krishnan Nair pin 678684")
    assert rec["name"] == "Priya Lakshmi Krishnan Nair"
    assert "678684" in rec["address"]


def test_split_stops_name_at_comma():
    rec = book_bot._split_name_address(
        "Anjana, Kaniyampal House, Punnayurkulam, Thrissur 680568")
    assert rec["name"] == "Anjana"
    assert "680568" in rec["address"]


def test_split_stops_name_at_house_number_word():
    """'Flat' (a building term now in _ADDR_KEYWORDS) ends the name."""
    rec = book_bot._split_name_address("Manoj Flat 3B Green Villa, Kochi 682016")
    assert rec["name"] == "Manoj"
    assert "682016" in rec["address"]


def test_split_needs_address_signal_not_just_a_pin():
    assert book_bot._split_name_address("Cost 145000 is fine") == {}
    assert book_bot._split_name_address("A 678684") == {}


def test_split_needs_a_pin():
    assert book_bot._split_name_address("Manoj Nadakkavu Cheramanglam") == {}


def test_split_handles_malayalam_name():
    rec = book_bot._split_name_address("മനോജ്, ചെറമംഗലം, പാലക്കാട് 678703")
    assert "മനോജ്" in rec["name"]
    assert "678703" in rec["address"]


@pytest.mark.integration
def test_edit_name_is_unaffected_by_frontload_capture(fake):
    """The fast path lives only in _handle_name; editing the name must not
    trigger address capture even if the new text carries a PIN-like blob."""
    db, sent = fake
    code = _seed_at_name(db, code="XTR-FL-6")
    db.orders[code]["name"] = "Old Name"
    db.orders[code]["address"] = "Old addr, Thrissur 680001"
    db.save_session(None, PHONE, step="book_edit_name")

    # Even a PIN-bearing blob at the edit-name step must NOT hit the fast path:
    # it should not overwrite the address or jump to the courier step.
    book_bot.maybe_handle_book(PHONE, _FRONTLOAD)

    assert db.sessions[PHONE]["step"] != "book_dtdc"          # not the fast path
    assert db.orders[code]["address"] == "Old addr, Thrissur 680001"  # untouched
