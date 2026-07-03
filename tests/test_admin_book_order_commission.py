"""Unit tests for Pradeep sir's commission on the admin book-order endpoints.

commission_for() pays Divya per Malayalam book; pradeep_commission_for() pays
Pradeep sir per Hindi/English book. Both admin endpoints below previously only
computed the Divya figure, silently leaving pradeep_commission at its 0.0
default whenever staff created/edited a walk-in order containing Hindi or
English copies.
"""
import json

import pytest

import api.index  # noqa: F401  (import first: handlers_admin <-> api.index is circular)
import api.handlers_admin as ha


class _FakeHandler:
    def __init__(self):
        self.status = None
        self.payload = None
        self.headers = {"X-Admin-Password": "secret123"}
        self.path = "/admin/book-orders"


@pytest.fixture
def patched(monkeypatch):
    # _handle_admin_book_order_create/edit authenticate via _auth_admin_pw
    # (header/query based) — a different mechanism from the body-embedded
    # admin_password field other admin handlers use, so it needs its own stub.
    monkeypatch.setattr(ha, "_auth_admin_pw", lambda pw: True)

    def fake_json(h, status, payload):
        h.status = status
        h.payload = payload
    monkeypatch.setattr(ha, "_json_response", fake_json)
    return monkeypatch


def test_create_walk_in_order_computes_pradeep_commission(patched):
    monkeypatch = patched
    captured = {}

    def fake_create_walk_in_order(code, name, phone, address, items,
                                  books_total, courier, grand, payment_mode, status,
                                  **kw):
        captured.update(kw)
        return {"order_code": code, "phone": phone}

    import db_cloud
    monkeypatch.setattr(db_cloud, "create_walk_in_order", fake_create_walk_in_order)

    h = _FakeHandler()
    body = json.dumps({
        "items": {"hindi": 1, "english": 1},
        "name": "Walk-in Customer",
        "phone": "9947184088",
        "address": "Thrissur - 680001",
        "payment_mode": "cash",
        "handed_over": True,
    }).encode()
    ha._handle_admin_book_order_create(h, body)

    assert h.status == 200
    assert captured["commission"] == 0.0            # no Malayalam books
    assert captured["pradeep_commission"] == 100.0   # 1 hindi + 1 english @ ₹50 each


def test_edit_recomputes_pradeep_commission_when_items_change(patched):
    monkeypatch = patched
    updated = {}

    import db_cloud
    monkeypatch.setattr(db_cloud, "get_book_order",
                        lambda code: {"order_code": code, "phone": "9947184088",
                                     "items": {"malayalam": 1}, "books_total": 200.0,
                                     "commission": 50.0, "pradeep_commission": 0.0})

    def fake_update_book_order(code, **fields):
        updated.update(fields)
        return True
    monkeypatch.setattr(db_cloud, "update_book_order", fake_update_book_order)

    h = _FakeHandler()
    body = json.dumps({"items": {"hindi": 2}}).encode()
    ha._handle_admin_book_order_edit(h, body, "XTR-20260703-TEST0001")

    assert h.status == 200
    assert updated["commission"] == 0.0             # malayalam removed from cart
    assert updated["pradeep_commission"] == 100.0    # 2 hindi books @ ₹50 each


def test_divya_self_order_edit_zeroes_pradeep_commission_too(patched):
    monkeypatch = patched
    updated = {}

    import db_cloud
    monkeypatch.setattr(db_cloud, "get_book_order",
                        lambda code: {"order_code": code, "phone": "919526738641",
                                     "items": {"hindi": 1}, "books_total": 150.0,
                                     "commission": 0.0, "pradeep_commission": 50.0})
    monkeypatch.setattr(db_cloud, "update_book_order",
                        lambda code, **f: updated.update(f) or True)

    h = _FakeHandler()
    body = json.dumps({"items": {"hindi": 1, "english": 1}}).encode()
    ha._handle_admin_book_order_edit(h, body, "XTR-20260703-TEST0002")

    assert h.status == 200
    assert updated["commission"] == 0.0
    assert updated["pradeep_commission"] == 0.0      # Divya's own order: no one earns commission
    assert updated["courier"] == 0.0
