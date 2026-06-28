"""Unit tests for POST /admin/book-orders/start."""
import pytest

import api.index  # noqa: F401  (import first: handlers_admin <-> api.index is circular)
import api.handlers_admin as ha


class _FakeHandler:
    """Minimal stand-in capturing the JSON response the handler writes."""
    def __init__(self):
        self.status = None
        self.payload = None


@pytest.fixture
def patched(monkeypatch):
    # Deterministic admin auth without touching real env/hash.
    monkeypatch.setattr(ha, "ADMIN_PASSWORD_HASH", "GOODHASH")
    monkeypatch.setattr(ha, "_sha256",
                        lambda s: "GOODHASH" if s == "secret123" else "BAD")

    def fake_json(h, status, payload):
        h.status = status
        h.payload = payload
    monkeypatch.setattr(ha, "_json_response", fake_json)
    return monkeypatch


def test_start_requires_valid_password(patched):
    h = _FakeHandler()
    ha._handle_admin_start_book_order(h, b'{"admin_password":"wrong","phone":"91900"}')
    assert h.status == 403


def test_start_rejects_malformed_phone(patched):
    h = _FakeHandler()
    ha._handle_admin_start_book_order(h, b'{"admin_password":"secret123","phone":"not-a-number"}')
    assert h.status == 400


def test_start_requires_phone(patched):
    h = _FakeHandler()
    ha._handle_admin_start_book_order(h, b'{"admin_password":"secret123","phone":""}')
    assert h.status == 400


def test_start_calls_resume_order_and_relays_text(patched):
    monkeypatch = patched
    calls = {"start": [], "sent": []}
    import book_bot
    # The take-over button resumes a dropped cart (never wipes it).
    monkeypatch.setattr(book_bot, "resume_order",
                        lambda phone: (calls["start"].append(phone) or ["guard msg"]))
    import whatsapp_notify
    monkeypatch.setattr(whatsapp_notify, "_send",
                        lambda phone, msg: (calls["sent"].append((phone, msg)) or True))
    import db_cloud
    monkeypatch.setattr(db_cloud, "log_message", lambda *a, **k: None)
    monkeypatch.setattr(ha, "_clear_needs_human", lambda phone: None)

    h = _FakeHandler()
    ha._handle_admin_start_book_order(h, b'{"admin_password":"secret123","phone":"919000000001"}')

    assert h.status == 200 and h.payload == {"ok": True}
    assert calls["start"] == ["919000000001"]
    assert calls["sent"] == [("919000000001", "guard msg")]


# ── POST /admin/run-cart-nudge (manual abandoned-cart sweep) ──────────────────

def test_run_cart_nudge_requires_valid_password(patched):
    h = _FakeHandler()
    ha._handle_admin_run_cart_nudge(h, b'{"admin_password":"wrong"}')
    assert h.status == 403


def test_run_cart_nudge_runs_sweep_and_returns_counts(patched):
    monkeypatch = patched
    import book_bot
    # Manual button fires the full sweep (free-form + template backlog).
    monkeypatch.setattr(book_bot, "run_cart_reminders",
                        lambda: {"carts": 3, "reminded": 2,
                                 "freeform": {"carts": 1, "reminded": 1},
                                 "template": {"carts": 2, "reminded": 1}})
    h = _FakeHandler()
    ha._handle_admin_run_cart_nudge(h, b'{"admin_password":"secret123"}')
    assert h.status == 200
    assert h.payload["ok"] is True
    assert h.payload["carts"] == 3 and h.payload["reminded"] == 2
