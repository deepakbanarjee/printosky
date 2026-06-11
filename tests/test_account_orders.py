"""Unit tests for order history in the /account/summary payload.

The handler (api.index._handle_account_summary) is exercised in isolation:
_json_response, _resolve_account, and the db_cloud seams are monkeypatched so no
Supabase or network is touched. Locks the `orders` contract the account hub
renders (job_id, amount, spec fields, pickup_code, status).
"""
from unittest.mock import MagicMock


_ACCOUNT = {
    "ok": True, "kind": "phone", "phone": "919495706405",
    "name": "Divya", "needs_phone_link": False,
}


def _stub_db(monkeypatch, jobs):
    import db_cloud
    monkeypatch.setattr(db_cloud, "wallet_balance", lambda p: 0)
    monkeypatch.setattr(db_cloud, "list_notes_by_uploader", lambda p: [])
    monkeypatch.setattr(db_cloud, "note_subscription_status", lambda p: {})
    monkeypatch.setattr(db_cloud, "list_jobs_by_sender", lambda p, limit=20: jobs)


def test_account_summary_includes_orders(monkeypatch):
    import api.index as idx
    captured = {}
    monkeypatch.setattr(idx, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(idx, "_resolve_account", lambda h: _ACCOUNT)
    _stub_db(monkeypatch, [{
        "job_id": "OSP-20260611-0042", "received_at": "2026-06-11T09:00:00",
        "filename": "report.pdf", "status": "Paid", "page_count": 5, "copies": 2,
        "colour": "bw", "finishing": "spiral", "amount_quoted": 91.5,
        "amount_collected": 91.5, "pickup_code": "ABC1234", "delivery": 0,
    }])

    idx._handle_account_summary(MagicMock(), b"{}")

    assert captured["status"] == 200
    orders = captured["data"]["orders"]
    assert len(orders) == 1
    o = orders[0]
    assert o["job_id"] == "OSP-20260611-0042"
    assert o["placed_at"] == "2026-06-11T09:00:00"
    assert o["pages"] == 5 and o["copies"] == 2
    assert o["colour"] == "bw" and o["finishing"] == "spiral"
    assert o["amount_rs"] == 91.5
    assert o["pickup_code"] == "ABC1234"
    assert o["status"] == "Paid"


def test_orders_amount_falls_back_to_quoted_when_not_collected(monkeypatch):
    import api.index as idx
    captured = {}
    monkeypatch.setattr(idx, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(idx, "_resolve_account", lambda h: _ACCOUNT)
    _stub_db(monkeypatch, [{
        "job_id": "OSP-20260611-0043", "received_at": "2026-06-11T10:00:00",
        "filename": "a.pdf", "status": "Pending", "page_count": 1, "copies": 1,
        "amount_quoted": 10.0, "amount_collected": None,  # unpaid order
    }])

    idx._handle_account_summary(MagicMock(), b"{}")

    assert captured["data"]["orders"][0]["amount_rs"] == 10.0
    assert captured["data"]["orders"][0]["status"] == "Pending"


def test_account_summary_no_orders_returns_empty_list(monkeypatch):
    import api.index as idx
    captured = {}
    monkeypatch.setattr(idx, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(idx, "_resolve_account", lambda h: _ACCOUNT)
    _stub_db(monkeypatch, [])

    idx._handle_account_summary(MagicMock(), b"{}")

    assert captured["status"] == 200
    assert captured["data"]["orders"] == []
