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


# ── 3-day dispatch SLA (find_book_dispatch_sla_breaches) ──────────────────────

def _book_confirmed_row(code, hours_ago):
    from datetime import datetime, timezone, timedelta
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"order_code": code, "name": code, "phone": "919000000000",
            "contact_phone": None, "confirmed_at": ts}


def _sla_chain(monkeypatch, rows):
    chain = MagicMock()
    (chain.table.return_value.select.return_value.eq.return_value.is_.return_value
        .order.return_value.limit.return_value.execute.return_value.data) = rows
    monkeypatch.setattr(db_cloud, "_client", lambda: chain)
    return chain


@pytest.mark.unit
def test_sla_breaches_filters_past_window_and_sorts_oldest_first(monkeypatch):
    # 100h and 80h are past the 72h SLA; 10h is within it.
    _sla_chain(monkeypatch, [_book_confirmed_row("XTR-A", 100),
                             _book_confirmed_row("XTR-FRESH", 10),
                             _book_confirmed_row("XTR-B", 80)])
    out = db_cloud.find_book_dispatch_sla_breaches(72)
    assert [o["order_code"] for o in out] == ["XTR-A", "XTR-B"]  # oldest breach first
    assert out[0]["age_hours"] >= out[1]["age_hours"] >= 72


@pytest.mark.unit
def test_sla_breaches_empty_when_all_within_window(monkeypatch):
    _sla_chain(monkeypatch, [_book_confirmed_row("XTR-NEW", 5),
                             _book_confirmed_row("XTR-NEW2", 1)])
    assert db_cloud.find_book_dispatch_sla_breaches(72) == []


@pytest.mark.unit
def test_sla_breaches_skips_rows_missing_confirmed_at(monkeypatch):
    _sla_chain(monkeypatch, [{"order_code": "XTR-NOTS", "confirmed_at": None}])
    assert db_cloud.find_book_dispatch_sla_breaches(72) == []


# ── Acquisition breakdown (book_acq_breakdown) ────────────────────────────────

@pytest.mark.unit
def test_acq_breakdown_counts_sold_by_channel(monkeypatch):
    rows = [
        {"acq_source": "instagram", "source": "whatsapp", "status": "delivered"},
        {"acq_source": "instagram", "source": "whatsapp", "status": "confirmed"},
        {"acq_source": "facebook",  "source": "whatsapp", "status": "dispatched"},
        {"acq_source": None,        "source": "divya",    "status": "confirmed"},    # divya fallback
        {"acq_source": None,        "source": "whatsapp", "status": "confirmed"},    # unknown
        {"acq_source": "instagram", "source": "whatsapp", "status": "collecting"},   # not sold → excluded
    ]
    chain = MagicMock()
    chain.table.return_value.select.return_value.limit.return_value.execute.return_value.data = rows
    monkeypatch.setattr(db_cloud, "_client", lambda: chain)
    assert db_cloud.book_acq_breakdown() == {"instagram": 2, "facebook": 1, "divya": 1, "unknown": 1}
