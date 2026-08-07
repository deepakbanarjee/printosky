"""Unit tests for db_cloud.mark_job_paid_manual — the in-store cash/UPI mark-paid."""
from __future__ import annotations

import pytest
import db_cloud


class _Resp:
    def __init__(self, data):
        self.data = data


class _Tbl:
    """Minimal stand-in for the supabase-py chained table builder."""

    def __init__(self, state):
        self.state = state
        self._op = None
        self._payload = None

    def select(self, _cols):
        self._op = "select"
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, _c, _v):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        if self._op == "select":
            return _Resp([dict(r) for r in self.state["rows"]])
        if self._op == "update":
            self.state["updates"].append(self._payload)
            for r in self.state["rows"]:
                r.update(self._payload)
            return _Resp([])
        return _Resp([])


class _FakeClient:
    def __init__(self, rows):
        self.state = {"rows": rows, "updates": []}

    def table(self, _name):
        return _Tbl(self.state)


def _use(monkeypatch, rows):
    fc = _FakeClient(rows)
    monkeypatch.setattr(db_cloud, "_client", lambda: fc)
    return fc


def test_pending_job_marked_paid_cash(monkeypatch):
    fc = _use(monkeypatch, [{"job_id": "J1", "status": "Pending", "pickup_code": "0042"}])
    res = db_cloud.mark_job_paid_manual("J1", 46.0, "cash")
    assert res["ok"] and res["status"] == "Paid" and res["payment_mode"] == "cash"
    upd = fc.state["updates"][-1]
    assert upd["status"] == "Paid"
    assert upd["amount_collected"] == 46.0
    assert upd["payment_mode"] == "cash"
    # No gateway/routing side effects on a manual payment.
    assert "razorpay_payment_id" not in upd
    assert "assigned_store_id" not in upd


def test_claims_pickup_code_when_missing(monkeypatch):
    fc = _use(monkeypatch, [{"job_id": "J2", "status": "Pending", "pickup_code": None}])
    import pickup_code
    monkeypatch.setattr(pickup_code, "claim_unique_pickup_code", lambda client: "7777")
    res = db_cloud.mark_job_paid_manual("J2", 10, "upi")
    assert res["pickup_code"] == "7777"
    assert fc.state["updates"][-1]["pickup_code"] == "7777"


def test_already_paid_not_overwritten(monkeypatch):
    fc = _use(monkeypatch, [{"job_id": "J3", "status": "Paid", "pickup_code": "0099"}])
    res = db_cloud.mark_job_paid_manual("J3", 999, "cash")
    assert res.get("already") == "Paid"
    assert fc.state["updates"] == []  # nothing written — real payment preserved


def test_printed_job_not_overwritten(monkeypatch):
    fc = _use(monkeypatch, [{"job_id": "J3b", "status": "Printed", "pickup_code": "0100"}])
    res = db_cloud.mark_job_paid_manual("J3b", 5, "upi")
    assert res.get("already") == "Printed"
    assert fc.state["updates"] == []


def test_missing_job_raises(monkeypatch):
    _use(monkeypatch, [])
    with pytest.raises(ValueError):
        db_cloud.mark_job_paid_manual("NOPE", 5, "cash")


def test_bad_method_raises(monkeypatch):
    _use(monkeypatch, [{"job_id": "J4", "status": "Pending"}])
    with pytest.raises(ValueError):
        db_cloud.mark_job_paid_manual("J4", 5, "card")
