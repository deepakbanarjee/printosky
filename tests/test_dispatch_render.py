"""Tests for dispatch_render — branded Xtraa courier-slip HTML.

Pure rendering, no network. Mirrors the proven tools/gen_despatch_slips.py
output but is driven by live book_orders dicts and references assets by URL.
"""
import pytest

import dispatch_render as dr


SAMPLE = {
    "order_code": "XTR-20260620-ABCD1234",
    "name": "Priya Krishnan",
    "address": "12 MG Road\nThrissur, Kerala\n680001",
    "phone": "9876543210",
    "items": {"malayalam": 1, "english": 1},
    "grand_total": 475,
    "amount_paid": 475,
}


@pytest.mark.unit
def test_courier_slip_has_core_order_fields():
    html = dr.build_courier_slips([SAMPLE])
    assert "XTR-20260620-ABCD1234" in html          # order code
    assert "Priya Krishnan" in html                  # customer name
    assert "Thrissur, Kerala" in html                # address line preserved
    assert "Malayalam" in html and "English" in html # book contents, human names
    assert "+91 98765 43210" in html                 # phone formatted
    assert dr.INSERT_URL in html                     # thank-you insert referenced
    assert dr.LOGO_URL in html                       # brand logo referenced
    assert "A4 landscape" in html                    # @page size A4 landscape


@pytest.mark.unit
def test_paid_flag_shown_when_fully_paid():
    assert "PAID" in dr.build_courier_slips([SAMPLE])
    unpaid = {**SAMPLE, "amount_paid": 0}
    assert "PAID" not in dr.build_courier_slips([unpaid])


@pytest.mark.unit
def test_html_is_escaped():
    nasty = {**SAMPLE, "name": "A & B <script>"}
    html = dr.build_courier_slips([nasty])
    assert "<script>" not in html
    assert "&amp;" in html or "&lt;" in html


@pytest.mark.unit
def test_empty_orders_is_safe():
    html = dr.build_courier_slips([])
    assert "<html" in html.lower()
    assert "No confirmed orders" in html


@pytest.mark.unit
def test_one_page_per_order():
    html = dr.build_courier_slips([SAMPLE, {**SAMPLE, "order_code": "XTR-2"}])
    assert html.count('class="slip"') == 2
