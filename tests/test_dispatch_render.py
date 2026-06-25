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


@pytest.mark.unit
def test_left_and_right_are_siblings_under_slip():
    """The courier slip (.left) and Thank-You insert (.right) must be direct
    children of .slip so the flex row lays them out side-by-side. A missing
    </div> nests .right inside .left, which stacks the insert below the slip
    instead of on the right — see the landscape layout bug.
    """
    from html.parser import HTMLParser

    class _Tree(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []          # (tag, classes)
            self.right_parents = []  # parent classes for each .right seen
            self.slip_child_classes = []

        def handle_starttag(self, tag, attrs):
            classes = dict(attrs).get("class", "").split()
            if "right" in classes:
                parent = self.stack[-1][1] if self.stack else []
                self.right_parents.append(parent)
            if self.stack and "slip" in self.stack[-1][1]:
                self.slip_child_classes.append(classes)
            self.stack.append((tag, classes))

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i][0] == tag:
                    del self.stack[i:]
                    break

    p = _Tree()
    p.feed(dr.build_courier_slips([SAMPLE]))

    # .right's parent must be .slip, never .left.
    assert p.right_parents, "no .right element rendered"
    for parent in p.right_parents:
        assert "slip" in parent, f".right nested wrong; parent={parent}"
        assert "left" not in parent, f".right is inside .left: {parent}"

    # .slip must have BOTH .left and .right as direct children.
    direct = [c for c in p.slip_child_classes]
    assert any("left" in c for c in direct), "no .left under .slip"
    assert any("right" in c for c in direct), "no .right under .slip"
