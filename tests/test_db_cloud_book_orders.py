"""Regression guards for db_cloud book-order helpers.

The unit tests for book_bot mock db_cloud entirely, so they never exercised the
real update_book_order — which is how a NameError ('timezone' not imported) shipped
and silently broke book replies for any customer with an existing order.
These tests call the real functions with a mocked Supabase client.
"""

from unittest.mock import MagicMock

import pytest

import db_cloud


@pytest.mark.unit
def test_module_imports_timezone_and_timedelta():
    # update_book_order / mark_sla_alerted / find_sla_breaches all use these.
    assert hasattr(db_cloud, "timezone")
    assert hasattr(db_cloud, "timedelta")


@pytest.mark.unit
def test_update_book_order_does_not_raise(monkeypatch):
    # The updated_at stamp (datetime.now(timezone.utc)) runs BEFORE the try block,
    # so a missing timezone import propagates out — exactly what broke books.
    chain = MagicMock()
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock(data=[{}])
    monkeypatch.setattr(db_cloud, "_client", lambda: chain)

    db_cloud.update_book_order("XTR-TEST-0001", status="collecting", items={})  # must not raise
    chain.table.assert_called_with("book_orders")


@pytest.mark.unit
def test_update_book_order_noop_on_empty_fields(monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(db_cloud, "_client", lambda: called.__setitem__("n", called["n"] + 1))
    db_cloud.update_book_order("XTR-TEST-0002")  # no fields → early return, no client call
    assert called["n"] == 0


@pytest.mark.unit
def test_mark_sla_alerted_does_not_raise(monkeypatch):
    chain = MagicMock()
    chain.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[{}])
    monkeypatch.setattr(db_cloud, "_client", lambda: chain)
    db_cloud.mark_sla_alerted("918000000001")  # must not raise


@pytest.mark.unit
def test_find_abandoned_book_carts_does_not_raise(monkeypatch):
    chain = MagicMock()
    (chain.table.return_value.select.return_value.in_.return_value.is_.return_value
        .lt.return_value.gt.return_value.order.return_value.limit.return_value
        .execute.return_value.data) = []
    monkeypatch.setattr(db_cloud, "_client", lambda: chain)
    assert db_cloud.find_abandoned_book_carts() == []


@pytest.mark.unit
def test_mark_abandoned_reminded_does_not_raise(monkeypatch):
    chain = MagicMock()
    chain.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    monkeypatch.setattr(db_cloud, "_client", lambda: chain)
    db_cloud.mark_abandoned_reminded("XTR-TEST")  # must not raise


@pytest.mark.unit
def test_create_walk_in_order_does_not_raise(monkeypatch):
    chain = MagicMock()
    chain.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"order_code": "XTR-W"}])
    monkeypatch.setattr(db_cloud, "_client", lambda: chain)
    row = db_cloud.create_walk_in_order(
        "XTR-W", "Walk In", "919000000009", None,
        {"malayalam": 1}, 200.0, 0.0, 200.0, "cash", "delivered")
    assert row == {"order_code": "XTR-W"}
    chain.table.assert_called_with("book_orders")
