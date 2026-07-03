"""Regression tests for db_cloud.divya_ledger — S10-10.

divya_ledger previously only summed via_divya=True rows (normal commission
orders). It silently dropped Divya's own self-orders (via_divya=False, courier
and commission-free) even though she physically took those books and owes
Oxygen their cost — making the settlement `net` figure wrong. These tests
mock the Supabase client and exercise the real function.
"""
from unittest.mock import MagicMock

import pytest

import db_cloud


def _mock_rows(monkeypatch, rows):
    chain = MagicMock()
    (chain.table.return_value.select.return_value.in_.return_value
         .order.return_value.execute.return_value) = MagicMock(data=rows)
    monkeypatch.setattr(db_cloud, "_client", lambda: chain)


NORMAL_ROW = {
    "order_code": "XTR-20260701-AAAA0001", "name": "Rajeena",
    "phone": "919947184088", "items": {"malayalam": 1}, "grand_total": 275.0,
    "commission": 50.0, "payment_collected_by": "oxygen",
    "delivery_method": "courier", "divya_settled": False,
    "status": "confirmed", "created_at": "2026-07-01T00:00:00+00:00",
    "via_divya": True,
}

DIVYA_SELF_ROW = {
    "order_code": "XTR-20260701-BBBB0002", "name": "Divya M",
    "phone": "919526738641", "items": {"malayalam": 2}, "grand_total": 400.0,
    "commission": 0.0, "payment_collected_by": "oxygen",
    "delivery_method": "courier", "divya_settled": False,
    "status": "confirmed", "created_at": "2026-07-02T00:00:00+00:00",
    "via_divya": False,
}


@pytest.mark.unit
def test_normal_orders_unaffected_by_own_use_change(monkeypatch):
    _mock_rows(monkeypatch, [NORMAL_ROW])
    d = db_cloud.divya_ledger()
    assert d["total_orders"] == 1
    assert d["total_books"] == 1
    assert d["total_commission"] == 50.0
    assert d["oxygen_owes_divya"] == 50.0
    assert d["divya_owes_oxygen"] == 0.0
    assert d["books_taken"] == 0
    assert d["books_cost"] == 0.0


@pytest.mark.unit
def test_divya_self_order_counted_as_deduction_not_commission(monkeypatch):
    _mock_rows(monkeypatch, [DIVYA_SELF_ROW])
    d = db_cloud.divya_ledger()
    # Not counted in the commission totals — she earns nothing on herself.
    assert d["total_orders"] == 0
    assert d["total_books"] == 0
    assert d["total_commission"] == 0.0
    # Surfaced as a deduction she owes Oxygen for the books she took.
    assert d["books_taken"] == 2
    assert d["books_cost"] == 400.0
    assert d["divya_owes_oxygen"] == 400.0
    assert d["net"] == 400.0
    assert len(d["unsettled"]) == 1
    assert d["unsettled"][0]["collected_by"] == "divya_own_use"
    assert d["unsettled"][0]["direction"] == "divya_owes_oxygen"
    assert d["unsettled"][0]["amount"] == 400.0


@pytest.mark.unit
def test_net_combines_commission_and_own_use_deduction(monkeypatch):
    # Rajeena's order: Oxygen owes Divya ₹50 commission.
    # Divya's own order: she owes Oxygen ₹400 for her own copies.
    # Net = divya_owes_oxygen - oxygen_owes_divya = 400 - 50 = 350 (she pays).
    _mock_rows(monkeypatch, [NORMAL_ROW, DIVYA_SELF_ROW])
    d = db_cloud.divya_ledger()
    assert d["oxygen_owes_divya"] == 50.0
    assert d["divya_owes_oxygen"] == 400.0
    assert d["net"] == 350.0
    assert d["books_taken"] == 2
    assert d["books_cost"] == 400.0
    assert d["total_orders"] == 1        # only the normal commission order


@pytest.mark.unit
def test_settled_own_use_order_excluded_from_unsettled_but_kept_in_orders(monkeypatch):
    settled_row = dict(DIVYA_SELF_ROW, divya_settled=True)
    _mock_rows(monkeypatch, [settled_row])
    d = db_cloud.divya_ledger()
    assert d["orders"] == [dict(
        order_code="XTR-20260701-BBBB0002", name="Divya M", books=2,
        grand_total=400.0, commission=0.0, collected_by="divya_own_use",
        direction="divya_owes_oxygen", amount=400.0, settled=True,
        created_at="2026-07-02T00:00:00+00:00",
    )]
    assert d["unsettled"] == []
    assert d["divya_owes_oxygen"] == 0.0
    assert d["books_taken"] == 2          # still counted in the informational total
    assert d["books_cost"] == 400.0
