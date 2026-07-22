"""book_bot.verify_payment_from_admin — the dashboard payment-verdict entry point.

Same effect as Anu tapping Full / Part / Not-received on WhatsApp, but callable
from the admin panel with no dependency on her 24-hour window. The DB layer and
_after_payment_change are stubbed; we assert the routing (which ledger call fires,
with what amount) and the guards.
"""
import pytest

import book_bot
import api.index  # noqa: F401  (import first: handlers_admin <-> api.index is circular)
import api.handlers_admin as ha


class _H:
    def __init__(self):
        self.status = None
        self.payload = None
        self.headers = {}
        self.path = "/admin/book-orders/payments-to-verify"


def test_panel_keeps_partially_paid_as_reference(monkeypatch):
    monkeypatch.setattr(ha, "_auth_admin_pw", lambda pw: True)
    monkeypatch.setattr(ha, "_admin_pw_from_request", lambda h: "x")
    monkeypatch.setattr(ha, "_json_response",
                        lambda h, s, p: (setattr(h, "status", s), setattr(h, "payload", p)))
    import db_cloud
    orders = {
        "payment_review": [{"order_code": "XTR-PR", "name": "A", "phone": "911",
                            "items": {"malayalam": 1}, "grand_total": 275}],
        "partially_paid": [{"order_code": "XTR-PP", "name": "B", "phone": "912",
                            "items": {"malayalam": 1}, "grand_total": 275}],
    }
    pays = {
        "XTR-PR": [{"id": 10, "status": "pending", "proof_url": "u", "created_at": "t"}],
        "XTR-PP": [{"id": 11, "status": "verified", "amount": 200, "proof_url": "u2", "created_at": "t"}],
    }
    monkeypatch.setattr(db_cloud, "list_book_orders", lambda status=None, limit=200: orders.get(status, []))
    monkeypatch.setattr(db_cloud, "get_book_payments", lambda code: pays.get(code, []))
    monkeypatch.setattr(db_cloud, "book_amount_paid", lambda code: 200.0 if code == "XTR-PP" else 0.0)

    h = _H()
    ha._handle_admin_payments_to_verify(h)
    assert h.status == 200
    rows = {r["order_code"]: r for r in h.payload["payments"]}
    assert set(rows) == {"XTR-PR", "XTR-PP"}
    # payment_review → an actionable pending screenshot
    assert [p["payid"] for p in rows["XTR-PR"]["pending"]] == [10]
    # partially_paid → kept as a reference (no pending), balance still shown
    assert rows["XTR-PP"]["pending"] == []
    assert rows["XTR-PP"]["balance"] == 75.0


@pytest.fixture
def stub(monkeypatch):
    calls = {"verify": None, "reject": None, "after": None}
    monkeypatch.setattr(book_bot, "_order_totals", lambda o: {"grand_total": 500.0})
    monkeypatch.setattr(book_bot, "_after_payment_change",
                        lambda code, rejected=False: calls.__setitem__("after", (code, rejected)))
    monkeypatch.setattr(book_bot._dbc, "get_book_order",
                        lambda code: {"order_code": code, "status": "confirmed"})
    monkeypatch.setattr(book_bot._dbc, "book_amount_paid", lambda code: 200.0)
    monkeypatch.setattr(book_bot._dbc, "verify_book_payment",
                        lambda pid, amt: calls.__setitem__("verify", (pid, amt)))
    monkeypatch.setattr(book_bot._dbc, "reject_book_payment",
                        lambda pid: calls.__setitem__("reject", pid))
    return monkeypatch, calls


def _pending(monkeypatch, status="pending", code="XTR-1"):
    monkeypatch.setattr(book_bot._dbc, "get_book_payment",
                        lambda pid: {"id": pid, "order_code": code, "status": status})


def test_full_verifies_the_balance(stub):
    mp, calls = stub
    _pending(mp)
    res = book_bot.verify_payment_from_admin(7, "full")
    assert res["ok"]
    assert calls["verify"] == (7, 300.0)         # balance = 500 - 200
    assert calls["after"] == ("XTR-1", False)


def test_part_verifies_the_given_amount(stub):
    mp, calls = stub
    _pending(mp)
    res = book_bot.verify_payment_from_admin(7, "part", amount=150.0)
    assert res["ok"]
    assert calls["verify"] == (7, 150.0)
    assert calls["after"] == ("XTR-1", False)


def test_part_without_amount_is_rejected(stub):
    mp, calls = stub
    _pending(mp)
    res = book_bot.verify_payment_from_admin(7, "part")
    assert not res["ok"]
    assert calls["verify"] is None               # nothing written


def test_reject_marks_not_received(stub):
    mp, calls = stub
    _pending(mp)
    res = book_bot.verify_payment_from_admin(7, "reject")
    assert res["ok"]
    assert calls["reject"] == 7
    assert calls["after"] == ("XTR-1", True)      # rejected=True → customer re-asked


def test_already_handled_payment(stub):
    mp, calls = stub
    _pending(mp, status="verified")
    res = book_bot.verify_payment_from_admin(7, "full")
    assert not res["ok"] and "Already" in res["error"]
    assert calls["verify"] is None


def test_missing_payment(stub):
    mp, _ = stub
    mp.setattr(book_bot._dbc, "get_book_payment", lambda pid: {})
    res = book_bot.verify_payment_from_admin(7, "full")
    assert not res["ok"]


def test_bad_kind(stub):
    mp, calls = stub
    _pending(mp)
    res = book_bot.verify_payment_from_admin(7, "whatever")
    assert not res["ok"]
    assert calls["verify"] is None and calls["reject"] is None
