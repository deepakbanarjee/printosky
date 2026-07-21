"""Dispatch double-ship fix: a packed order must leave the pick list.

A box that's packed but not yet couriered stays status='confirmed'. Before this
fix the daily dispatch sheet reprinted its slip and it shipped twice. packed_at
now drops it off the pick list and surfaces it in an "awaiting courier" banner.
"""
import io
import json  # noqa: F401

import pytest

import api.index  # noqa: F401  (import first: handlers_admin <-> api.index is circular)
import api.handlers_admin as ha


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(ha, "_auth_admin_pw", lambda pw: True)
    monkeypatch.setattr(ha, "_admin_pw_from_request", lambda h: "secret")

    def fake_json(h, status, payload):
        h.status = status
        h.payload = payload
    monkeypatch.setattr(ha, "_json_response", fake_json)
    return monkeypatch


class _JsonHandler:
    def __init__(self, path="/x"):
        self.status = None
        self.payload = None
        self.headers = {"X-Admin-Password": "secret"}
        self.path = path


class _SheetHandler:
    """Minimal stand-in for the BaseHTTPRequestHandler the sheet writes to."""
    def __init__(self):
        self.headers = {"X-Admin-Password": "secret"}
        self.path = "/admin/book-orders/dispatch-sheet"
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, *a, **k):
        pass

    def end_headers(self):
        pass

    def html(self):
        return self.wfile.getvalue().decode("utf-8")


# ── pack handler ────────────────────────────────────────────────────────────
def test_pack_stamps_packed_at(patched):
    import db_cloud
    updates = {}
    patched.setattr(db_cloud, "get_book_order", lambda code: {"order_code": code})
    patched.setattr(db_cloud, "update_book_order",
                    lambda code, **f: updates.update(f) or True)
    h = _JsonHandler("/admin/book-orders/XTR-1/pack")
    ha._handle_admin_book_order_pack(h, "XTR-1")
    assert h.status == 200
    assert "packed_at" in updates and updates["packed_at"]
    # Status is NOT changed — stays confirmed so ledgers are unaffected.
    assert "status" not in updates


def test_pack_unknown_order_404(patched):
    import db_cloud
    patched.setattr(db_cloud, "get_book_order", lambda code: {})
    h = _JsonHandler("/admin/book-orders/nope/pack")
    ha._handle_admin_book_order_pack(h, "nope")
    assert h.status == 404


def test_pack_requires_auth(patched):
    patched.setattr(ha, "_auth_admin_pw", lambda pw: False)
    h = _JsonHandler("/admin/book-orders/XTR-1/pack")
    ha._handle_admin_book_order_pack(h, "XTR-1")
    assert h.status == 403


# ── dispatch sheet excludes packed orders ───────────────────────────────────
TO_PACK = {"order_code": "XTR-TOPACK-1", "name": "Asha", "items": {"hindi": 1},
           "address": "x", "phone": "919000000001", "grand_total": 150}
PACKED  = {"order_code": "XTR-PACKED-9", "name": "Bina", "items": {"malayalam": 1},
           "address": "y", "phone": "919000000002", "grand_total": 200,
           "packed_at": "2026-07-19T10:00:00+00:00"}


def test_dispatch_sheet_excludes_packed_and_reminds(patched):
    import db_cloud
    patched.setattr(db_cloud, "list_book_orders",
                    lambda status=None, limit=200: [dict(TO_PACK), dict(PACKED)])
    h = _SheetHandler()
    ha._handle_admin_dispatch_sheet(h)
    html = h.html()

    assert h.status == 200
    # Only the un-packed order drives the pick list.
    assert "Pick List &mdash; 1 order" in html
    assert "XTR-TOPACK-1" in html                       # slip present for to-pack
    # The packed order is off the pick list but shown in the reminder banner.
    assert "AWAITING COURIER (1)" in html
    assert "XTR-PACKED-9" in html
    # It must NOT get a packing slip (that's what caused the double ship).
    assert html.count("XTR-PACKED-9") == 1              # only in the banner
