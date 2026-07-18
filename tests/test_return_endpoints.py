"""Unit tests for the admin book-returns / replacements endpoints.

A return records a physically-returned book against an existing order. Resolution
is a replacement reship, a recorded refund, or both. A replacement is a NEW
book_orders row (is_replacement=true) that rides the normal dispatch pipeline;
refunds are recorded, never auto-processed.
"""
import json

import pytest

import api.index  # noqa: F401  (import first: handlers_admin <-> api.index is circular)
import api.handlers_admin as ha


class _FakeHandler:
    def __init__(self, path="/admin/returns"):
        self.status = None
        self.payload = None
        self.headers = {"X-Admin-Password": "secret123"}
        self.path = path


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(ha, "_auth_admin_pw", lambda pw: True)
    monkeypatch.setattr(ha, "_admin_pw_from_request", lambda h: "secret123")

    def fake_json(h, status, payload):
        h.status = status
        h.payload = payload
    monkeypatch.setattr(ha, "_json_response", fake_json)
    return monkeypatch


ORDER = {
    "order_code": "XTR-20260701-AAAA0001", "name": "Sajitha K",
    "phone": "919400012345", "address": "Nattika, Thrissur - 680566",
    "items": {"hindi": 1}, "delivery_method": "courier", "status": "delivered",
}


def _stub_db(monkeypatch, order=ORDER):
    """Patch the db_cloud calls the return handlers make; capture their args."""
    import db_cloud
    cap = {"return": None, "replacement": None, "updates": []}

    monkeypatch.setattr(db_cloud, "get_book_order", lambda code: dict(order) if order else {})

    def fake_create_replacement_order(order_code, parent_order_code, return_code,
                                      name, phone, address, items, **kw):
        cap["replacement"] = dict(order_code=order_code, parent=parent_order_code,
                                  return_code=return_code, items=items, **kw)
        return {"order_code": order_code}
    monkeypatch.setattr(db_cloud, "create_replacement_order", fake_create_replacement_order)

    def fake_create_book_return(return_code, order_code, phone, name, returned_items,
                                reason, **kw):
        cap["return"] = dict(return_code=return_code, order_code=order_code,
                             returned_items=returned_items, reason=reason, **kw)
        return {"return_code": return_code}
    monkeypatch.setattr(db_cloud, "create_book_return", fake_create_book_return)

    import book_catalog
    monkeypatch.setattr(book_catalog, "compute_totals",
                        lambda items: {"books_total": 200.0, "courier": 60.0})
    return cap


def test_create_replacement_makes_linked_reship(patched):
    cap = _stub_db(patched)
    h = _FakeHandler("/admin/returns/create")
    body = json.dumps({
        "order_code": ORDER["order_code"],
        "returned_items": {"hindi": 1},
        "replacement_items": {"malayalam": 1},
        "reason": "wrong_language",
        "resolution": "replacement",
        "courier_borne_by": "store",
    }).encode()
    ha._handle_admin_return_create(h, body)

    assert h.status == 200
    assert h.payload["return_code"].startswith("RET-")
    rep_code = h.payload["replacement_order_code"]
    assert rep_code and rep_code.startswith("XTR-")
    # Replacement links back to the original order + the return.
    assert cap["replacement"]["parent"] == ORDER["order_code"]
    assert cap["replacement"]["items"] == {"malayalam": 1}
    assert cap["replacement"]["courier_borne_by"] == "store"
    # store-borne courier → the reship carries the courier figure but no revenue.
    assert cap["replacement"]["courier"] == 60.0
    # The return row records the swap + points at the reship.
    assert cap["return"]["returned_items"] == {"hindi": 1}
    assert cap["return"]["replacement_order_code"] == rep_code
    assert cap["return"]["resolution"] == "replacement"


def test_create_refund_only_makes_no_reship(patched):
    cap = _stub_db(patched)
    h = _FakeHandler("/admin/returns/create")
    body = json.dumps({
        "order_code": ORDER["order_code"],
        "returned_items": {"hindi": 1},
        "resolution": "refund",
        "settlement_direction": "refund",
        "settlement_amount": 150,
        "settlement_mode": "upi",
    }).encode()
    ha._handle_admin_return_create(h, body)

    assert h.status == 200
    assert h.payload["replacement_order_code"] is None
    assert cap["replacement"] is None
    assert cap["return"]["resolution"] == "refund"
    assert cap["return"]["settlement_direction"] == "refund"
    assert cap["return"]["settlement_amount"] == 150.0
    assert cap["return"]["settlement_mode"] == "upi"


def test_create_replacement_collects_price_diff_and_couriers(patched):
    # Sajitha case: pricier book + both couriers → customer owes the store.
    cap = _stub_db(patched)
    h = _FakeHandler("/admin/returns/create")
    body = json.dumps({
        "order_code": ORDER["order_code"],
        "returned_items": {"hindi": 1},
        "replacement_items": {"malayalam": 1},
        "resolution": "replacement",
        "courier_borne_by": "customer",
        "price_delta": 50,           # ML 200 − HI 150
        "inward_courier": 75,
        "outward_courier": 75,
        "settlement_direction": "collect",
        "settlement_amount": 200,    # 50 + 75 + 75
        "settlement_mode": "upi",
    }).encode()
    ha._handle_admin_return_create(h, body)

    assert h.status == 200
    assert cap["return"]["settlement_direction"] == "collect"
    assert cap["return"]["settlement_amount"] == 200.0
    assert cap["return"]["price_delta"] == 50.0
    assert cap["return"]["inward_courier"] == 75.0
    assert cap["return"]["outward_courier"] == 75.0
    # The reship itself carries no money — settlement lives on the return.
    assert cap["replacement"]["courier_borne_by"] == "customer"


def test_create_requires_order_code(patched):
    _stub_db(patched)
    h = _FakeHandler("/admin/returns/create")
    ha._handle_admin_return_create(h, json.dumps({"returned_items": {"hindi": 1}}).encode())
    assert h.status == 400


def test_create_replacement_requires_replacement_items(patched):
    _stub_db(patched)
    h = _FakeHandler("/admin/returns/create")
    body = json.dumps({
        "order_code": ORDER["order_code"],
        "returned_items": {"hindi": 1},
        "resolution": "replacement",
    }).encode()
    ha._handle_admin_return_create(h, body)
    assert h.status == 400


def test_create_unknown_order_404(patched):
    _stub_db(patched, order=None)
    h = _FakeHandler("/admin/returns/create")
    body = json.dumps({
        "order_code": "XTR-nope", "returned_items": {"hindi": 1},
        "replacement_items": {"malayalam": 1},
    }).encode()
    ha._handle_admin_return_create(h, body)
    assert h.status == 404


def test_create_customer_courier_charges_reship(patched):
    cap = _stub_db(patched)
    h = _FakeHandler("/admin/returns/create")
    body = json.dumps({
        "order_code": ORDER["order_code"],
        "returned_items": {"hindi": 1},
        "replacement_items": {"malayalam": 1},
        "courier_borne_by": "customer",
    }).encode()
    ha._handle_admin_return_create(h, body)
    assert h.status == 200
    assert cap["replacement"]["courier_borne_by"] == "customer"
    assert cap["replacement"]["courier"] == 60.0


def test_list_returns(patched):
    import db_cloud
    patched.setattr(db_cloud, "list_book_returns",
                    lambda status=None, limit=200: [{"return_code": "RET-x", "status": status}])
    h = _FakeHandler("/admin/returns?status=requested")
    ha._handle_admin_returns_list(h)
    assert h.status == 200
    assert h.payload["returns"][0]["status"] == "requested"


def test_received_marks_item_received(patched):
    import db_cloud
    updates = {}
    patched.setattr(db_cloud, "get_book_return", lambda code: {"return_code": code})
    patched.setattr(db_cloud, "update_book_return",
                    lambda code, **f: updates.update(f) or True)
    h = _FakeHandler("/admin/returns/RET-1/received")
    ha._handle_admin_return_received(h, json.dumps({"condition": "resellable"}).encode(), "RET-1")
    assert h.status == 200
    assert updates["status"] == "item_received"
    assert updates["condition"] == "resellable"


def test_settle_records_and_resolves_refund_only(patched):
    import db_cloud
    updates = {}
    patched.setattr(db_cloud, "get_book_return",
                    lambda code: {"return_code": code, "resolution": "refund", "status": "item_received"})
    patched.setattr(db_cloud, "update_book_return",
                    lambda code, **f: updates.update(f) or True)
    h = _FakeHandler("/admin/returns/RET-1/settle")
    ha._handle_admin_return_settle(h, json.dumps(
        {"settlement_direction": "refund", "settlement_amount": 150, "settlement_mode": "upi"}).encode(), "RET-1")
    assert h.status == 200
    assert updates["settlement_status"] == "done"
    assert updates["settlement_direction"] == "refund"
    assert updates["settlement_amount"] == 150.0
    assert updates["status"] == "resolved"      # refund-only return now fully resolved


def test_settle_collect_records_money_in(patched):
    import db_cloud
    updates = {}
    patched.setattr(db_cloud, "get_book_return",
                    lambda code: {"return_code": code, "resolution": "replacement", "status": "item_received"})
    patched.setattr(db_cloud, "update_book_return",
                    lambda code, **f: updates.update(f) or True)
    h = _FakeHandler("/admin/returns/RET-1/settle")
    ha._handle_admin_return_settle(h, json.dumps(
        {"settlement_direction": "collect", "settlement_amount": 200, "settlement_mode": "qr"}).encode(), "RET-1")
    assert h.status == 200
    assert updates["settlement_direction"] == "collect"
    assert updates["settlement_status"] == "done"
    assert updates["settlement_mode"] == "qr"


def test_settle_requires_direction_and_positive_amount(patched):
    import db_cloud
    patched.setattr(db_cloud, "get_book_return", lambda code: {"return_code": code})
    h = _FakeHandler("/admin/returns/RET-1/settle")
    # Missing direction → 400
    ha._handle_admin_return_settle(h, json.dumps({"settlement_amount": 100}).encode(), "RET-1")
    assert h.status == 400
    # Zero amount → 400
    h2 = _FakeHandler("/admin/returns/RET-1/settle")
    ha._handle_admin_return_settle(h2, json.dumps(
        {"settlement_direction": "collect", "settlement_amount": 0}).encode(), "RET-1")
    assert h2.status == 400


def test_close_marks_closed(patched):
    import db_cloud
    updates = {}
    patched.setattr(db_cloud, "get_book_return", lambda code: {"return_code": code})
    patched.setattr(db_cloud, "update_book_return",
                    lambda code, **f: updates.update(f) or True)
    h = _FakeHandler("/admin/returns/RET-1/close")
    ha._handle_admin_return_close(h, "RET-1")
    assert h.status == 200
    assert updates["status"] == "closed"


def test_auth_required(patched):
    patched.setattr(ha, "_auth_admin_pw", lambda pw: False)
    h = _FakeHandler("/admin/returns/create")
    ha._handle_admin_return_create(h, b"{}")
    assert h.status == 403
