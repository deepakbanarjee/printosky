"""Integration test for db_cloud.update_job_paid + pickup_code.

Verifies the idempotency contract: paying for the same job twice (Razorpay
webhook is at-least-once) must not generate a second pickup_code.
"""
from __future__ import annotations

import pytest

import db_cloud
from pickup_code import is_valid_pickup_code


# --- a minimal in-memory supabase double --------------------------------


class _FakeQuery:
    """Captures select/update/eq state and serves rows from the parent's store."""

    def __init__(self, parent: "_FakeClient", table: str):
        self.parent = parent
        self.table_name = table
        self.mode = "select"          # 'select' | 'update'
        self.payload: dict | None = None
        self.filters: dict[str, object] = {}
        self.select_columns: tuple[str, ...] = ()

    def select(self, *cols: str):
        self.mode = "select"
        self.select_columns = cols
        return self

    def update(self, payload: dict):
        self.mode = "update"
        self.payload = payload
        return self

    def eq(self, col: str, value: object):
        self.filters[col] = value
        return self

    def limit(self, _n: int):
        return self

    def execute(self):
        rows = list(self.parent.tables.get(self.table_name, []))
        for col, val in self.filters.items():
            rows = [r for r in rows if r.get(col) == val]

        class R:
            pass
        r = R()

        if self.mode == "select":
            if self.select_columns and "*" not in self.select_columns:
                projected = [{c: row.get(c) for c in self.select_columns} for row in rows]
                r.data = projected
            else:
                r.data = rows
            return r

        # update mode: mutate the original rows from the parent store
        for original in self.parent.tables.get(self.table_name, []):
            if all(original.get(col) == val for col, val in self.filters.items()):
                original.update(self.payload or {})
        r.data = []
        return r


class _FakeClient:
    def __init__(self, jobs_rows):
        self.tables = {"jobs": list(jobs_rows)}

    def table(self, name: str) -> _FakeQuery:
        return _FakeQuery(self, name)


# --- tests --------------------------------------------------------------


@pytest.fixture
def fake_jobs_table():
    return [
        {"job_id": "OSP-J1", "pickup_code": None, "status": "Pending"},
        {"job_id": "OSP-J2", "pickup_code": "P-7K2N", "status": "Paid"},
    ]


def test_first_payment_generates_pickup_code(monkeypatch, fake_jobs_table):
    fake = _FakeClient(fake_jobs_table)
    monkeypatch.setattr(db_cloud, "_client", lambda: fake)

    db_cloud.update_job_paid("OSP-J1", amount=84.0, method="upi", pay_id="pay_TEST1")

    j1 = next(r for r in fake.tables["jobs"] if r["job_id"] == "OSP-J1")
    assert j1["status"] == "Paid"
    assert j1["amount_collected"] == 84.0
    assert j1["payment_mode"] == "upi"
    assert j1["razorpay_payment_id"] == "pay_TEST1"
    assert is_valid_pickup_code(j1["pickup_code"])


def test_repeat_webhook_does_not_overwrite_existing_code(monkeypatch, fake_jobs_table):
    """Razorpay's at-least-once delivery must not regenerate the pickup code."""
    fake = _FakeClient(fake_jobs_table)
    monkeypatch.setattr(db_cloud, "_client", lambda: fake)

    db_cloud.update_job_paid("OSP-J2", amount=84.0, method="upi", pay_id="pay_TEST2")

    j2 = next(r for r in fake.tables["jobs"] if r["job_id"] == "OSP-J2")
    assert j2["pickup_code"] == "P-7K2N", "pickup_code must be preserved on repeat webhook"
    assert j2["razorpay_payment_id"] == "pay_TEST2"


def test_pickup_code_failure_does_not_block_payment_status(monkeypatch, fake_jobs_table):
    """If pickup-code claiming raises, payment status must still be recorded.

    Patches ``pickup_code.claim_unique_pickup_code`` directly because
    db_cloud now lazy-imports it inside ``update_job_paid``; patching on
    db_cloud (where it used to live module-top) would no longer take effect.
    """
    import pickup_code as pc
    fake = _FakeClient(fake_jobs_table)
    monkeypatch.setattr(db_cloud, "_client", lambda: fake)
    monkeypatch.setattr(
        pc,
        "claim_unique_pickup_code",
        lambda _client: (_ for _ in ()).throw(RuntimeError("simulated")),
    )

    db_cloud.update_job_paid("OSP-J1", amount=84.0, method="upi", pay_id="pay_TEST3")

    j1 = next(r for r in fake.tables["jobs"] if r["job_id"] == "OSP-J1")
    assert j1["status"] == "Paid"  # payment status still recorded
    assert j1["razorpay_payment_id"] == "pay_TEST3"
    assert j1.get("pickup_code") is None  # left unset
