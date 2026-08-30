"""
/order/scale-rect — the customer preview's geometry, from the printer's code.

The customer's file is in their browser, not on the store PC, so order-v2 draws
the page itself with pdf.js. What it must NOT do is work out where the page
goes: that comes from here, which calls the same pdf_scaler.scale_rect() the
print path bakes with. One implementation of the geometry, no JavaScript copy
of it to drift.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import pdf_scaler
from api import handlers_order
from nup_imposer import portrait_sheet

A4_W, A4_H = portrait_sheet("A4")
A5_W, A5_H = portrait_sheet("A5")


@pytest.fixture
def call(monkeypatch):
    out = {}
    monkeypatch.setattr(handlers_order, "_json_response",
                        lambda h, status, data: out.update(status=status, data=data))

    def _call(**params):
        out.clear()
        qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        handlers_order._handle_order_scale_rect(object(), f"/order/scale-rect?{qs}")
        return out["status"], out["data"]

    return _call


class TestValidation:
    @pytest.mark.parametrize("params", [
        {},
        {"page_w": 100},
        {"page_h": 100},
        {"page_w": 0, "page_h": 100},
        {"page_w": -5, "page_h": 100},
        {"page_w": "wide", "page_h": 100},
    ])
    def test_a_page_size_is_required(self, params, call):
        status, data = call(mode="fit", **params)
        assert status == 400 and "page_w" in data["error"]

    def test_an_unknown_sheet_is_refused(self, call):
        status, data = call(page_w=A4_W, page_h=A4_H, sheet="B5", mode="fit")
        assert status == 400 and "B5" in data["error"]

    def test_the_sheet_list_matches_what_the_order_page_offers(self):
        assert handlers_order._VALID_SIZE == {"A4", "A3", "A5", "Legal", "Letter"}


class TestGeometry:
    def test_it_returns_what_scale_rect_returns(self, call):
        status, data = call(page_w=A5_W, page_h=A5_H, sheet="A4", mode="fit")
        assert status == 200
        assert data["scale"] == pdf_scaler.scale_rect(A5_W, A5_H, "A4", "fit")

    def test_a_no_op_is_a_success_with_a_null_scale(self, call):
        """Actual on a page already the sheet size changes nothing, and the
        caller needs to know that means "draw it unchanged", not "error"."""
        status, data = call(page_w=A4_W, page_h=A4_H, sheet="A4", mode="actual")
        assert status == 200 and data["scale"] is None

    def test_an_absent_mode_is_a_no_op_not_an_error(self, call):
        status, data = call(page_w=A5_W, page_h=A5_H, sheet="A4")
        assert status == 200 and data["scale"] is None

    def test_percent_is_of_the_page_not_the_sheet(self, call):
        """The customer-facing consequence of the owner's 2026-08-30 decision:
        an A5 page at 100 % stays A5 on the A4 sheet."""
        _, data = call(page_w=A5_W, page_h=A5_H, sheet="A4", mode="custom", percent=100)
        assert data["scale"] is None or data["scale"]["width"] == pytest.approx(A5_W)

    def test_the_crop_flag_is_reported(self, call):
        _, data = call(page_w=A4_W, page_h=A4_H, sheet="A4", mode="custom", percent=150)
        assert data["scale"]["crops"] is True

    def test_out_of_range_clamps_rather_than_failing(self, call):
        status, data = call(page_w=A4_W, page_h=A4_H, sheet="A4", mode="custom", percent=9999)
        assert status == 200
        assert data["scale"]["scale"] == pytest.approx(pdf_scaler.MAX_PERCENT / 100)

    def test_no_file_is_needed(self, call):
        """Pure numbers — nothing is uploaded and nothing is stored, so this
        works before the customer's file has ever left their browser."""
        status, _ = call(page_w=123.4, page_h=567.8, sheet="A4", mode="fit")
        assert status == 200
