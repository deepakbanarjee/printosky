"""Unit tests for book_catalog pure logic (selection/qty parsing, totals)."""

import pytest

import book_catalog as bc


# ── parse_selection ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text,expected", [
    ("1", ["malayalam"]),
    ("2", ["hindi"]),
    ("3", ["english"]),
    ("1,3", ["malayalam", "english"]),
    ("1 3", ["malayalam", "english"]),
    ("3, 1", ["malayalam", "english"]),      # canonical order regardless of input order
    ("1,2,3", ["malayalam", "hindi", "english"]),
    ("4", ["malayalam", "hindi", "english"]),
    ("all", ["malayalam", "hindi", "english"]),
    ("ALL", ["malayalam", "hindi", "english"]),
    ("set", ["malayalam", "hindi", "english"]),
    ("1,1", ["malayalam"]),                  # de-duplicated
])
def test_parse_selection_valid(text, expected):
    assert bc.parse_selection(text) == expected


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "  ", "9", "0", "abc", "5", "1,9", "banana"])
def test_parse_selection_invalid(text):
    assert bc.parse_selection(text) is None


# ── parse_qty ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text,expected", [
    ("1", 1),
    ("2", 2),
    ("10", 10),
    ("2 books", 2),
    (" 3 ", 3),
    ("99", 99),
])
def test_parse_qty_valid(text, expected):
    assert bc.parse_qty(text) == expected


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "0", "-1", "abc", "100", "1000"])
def test_parse_qty_invalid(text):
    assert bc.parse_qty(text) is None


# ── compute_totals ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_totals_single_book():
    t = bc.compute_totals({"malayalam": 1})
    assert t["books_total"] == 200.0
    assert t["courier"] == 75.0
    assert t["grand_total"] == 275.0
    assert t["is_set"] is False


@pytest.mark.unit
def test_totals_two_books_no_set_price():
    # Any 2 books → individual offer prices, no set discount.
    t = bc.compute_totals({"malayalam": 1, "english": 1})
    assert t["books_total"] == 400.0      # 200 + 200
    assert t["grand_total"] == 475.0
    assert t["is_set"] is False


@pytest.mark.unit
def test_totals_all_three_uses_set_price():
    t = bc.compute_totals({"malayalam": 1, "hindi": 1, "english": 1})
    assert t["books_total"] == 549.0      # set price, not 550
    assert t["grand_total"] == 624.0
    assert t["is_set"] is True


@pytest.mark.unit
def test_totals_two_complete_sets():
    t = bc.compute_totals({"malayalam": 2, "hindi": 2, "english": 2})
    assert t["books_total"] == 1098.0     # 549 * 2
    assert t["grand_total"] == 1173.0
    assert t["is_set"] is True


@pytest.mark.unit
def test_totals_all_three_uneven_qty_is_individual():
    # All three present but not uniform → individual pricing, no set.
    t = bc.compute_totals({"malayalam": 2, "hindi": 1, "english": 1})
    assert t["books_total"] == 750.0      # 400 + 150 + 200
    assert t["is_set"] is False


@pytest.mark.unit
def test_totals_empty_cart_has_no_courier():
    t = bc.compute_totals({})
    assert t["books_total"] == 0.0
    assert t["courier"] == 0.0
    assert t["grand_total"] == 0.0


@pytest.mark.unit
def test_totals_ignores_zero_qty():
    t = bc.compute_totals({"malayalam": 2, "hindi": 0, "english": 0})
    assert t["books_total"] == 400.0
    assert t["is_set"] is False


# ── line_items ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_line_items_ordered_and_priced():
    lines = bc.line_items({"english": 2, "malayalam": 1})
    # Canonical order: malayalam before english.
    assert [l["key"] for l in lines] == ["malayalam", "english"]
    assert lines[0]["line_total"] == 200.0
    assert lines[1]["qty"] == 2
    assert lines[1]["line_total"] == 400.0


@pytest.mark.unit
def test_line_items_skips_zero():
    lines = bc.line_items({"malayalam": 1, "hindi": 0})
    assert len(lines) == 1
    assert lines[0]["key"] == "malayalam"
