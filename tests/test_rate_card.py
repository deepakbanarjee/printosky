"""
Tests for rate_card.py — pricing engine for Printosky.

Covers:
- calc_sheets(): sheet counting for all sides/layout combinations
- get_print_rate(): per-sheet rates, student discount, colour tiers
- get_spiral_rate(), get_soft_binding_rate(), get_thermal_binding_rate()
- calculate_item_cost(): single print item cost
- calculate_finishing_cost(): all finishing types, urgent surcharge
- calculate_quote(): full job quotes, multi-item jobs
- Legacy calculate_sheets() and calculate_print_cost()
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import rate_card as rc


# ─────────────────────────────────────────────────────────────────────────────
# calc_sheets
# ─────────────────────────────────────────────────────────────────────────────

class TestCalcSheets:
    """Sheet count calculation — single-side, double-side, layout."""

    def test_ss_1up_basic(self):
        assert rc.calc_sheets(10, "ss", "1-up") == 10

    def test_ss_1up_one_page(self):
        assert rc.calc_sheets(1, "ss", "1-up") == 1

    def test_ds_1up_even_pages(self):
        # 6 pages DS → ceil(6/2)=3 → next even=4
        assert rc.calc_sheets(6, "ds", "1-up") == 4

    def test_ds_1up_odd_pages(self):
        # 5 pages DS → ceil(5/2)=3 → next even=4
        assert rc.calc_sheets(5, "ds", "1-up") == 4

    def test_ds_1up_34_pages(self):
        # 34 pages DS → ceil(34/2)=17 → next even=18
        assert rc.calc_sheets(34, "ds", "1-up") == 18

    def test_ds_1up_1_page(self):
        # 1 page DS → ceil(1/2)=1 → next even=2
        assert rc.calc_sheets(1, "ds", "1-up") == 2

    def test_ds_1up_already_even(self):
        # 4 pages DS → ceil(4/2)=2 → already even → 2
        assert rc.calc_sheets(4, "ds", "1-up") == 2

    def test_ss_2up(self):
        # 50 pages 2-up SS → ceil(50/2)=25 sheets
        assert rc.calc_sheets(50, "ss", "2-up") == 25

    def test_ss_4up(self):
        # 40 pages 4-up SS → ceil(40/4)=10 sheets
        assert rc.calc_sheets(40, "ss", "4-up") == 10

    def test_ds_2up(self):
        # 50 pages 2-up DS → after layout: ceil(50/2)=25 pages → DS: ceil(25/2)=13 → next even=14
        assert rc.calc_sheets(50, "ds", "2-up") == 14

    def test_unknown_layout_defaults_to_1up(self):
        # Unknown layout treated as 1-up
        assert rc.calc_sheets(10, "ss", "badlayout") == 10

    def test_minimum_one_sheet(self):
        # Can never return 0
        assert rc.calc_sheets(0, "ss", "1-up") >= 1


# ─────────────────────────────────────────────────────────────────────────────
# get_print_rate
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPrintRate:
    """Per-sheet rate lookup."""

    def test_a4_bw_ss(self):
        assert rc.get_print_rate("A4_BW", "ss", 10) == 3.0

    def test_a4_bw_ds(self):
        assert rc.get_print_rate("A4_BW", "ds", 10) == 3.0

    def test_a4_col_ss_tier1(self):
        # ≤30 sheets → Rs.10
        assert rc.get_print_rate("A4_col", "ss", 20) == 10.0

    def test_a4_col_ss_tier1_boundary(self):
        assert rc.get_print_rate("A4_col", "ss", 30) == 10.0

    def test_a4_col_ss_tier2(self):
        # 31–50 sheets → Rs.9
        assert rc.get_print_rate("A4_col", "ss", 31) == 9.0
        assert rc.get_print_rate("A4_col", "ss", 50) == 9.0

    def test_a4_col_ss_tier3(self):
        # >50 sheets → Rs.8
        assert rc.get_print_rate("A4_col", "ss", 51) == 8.0

    def test_a4_col_ds_tier1(self):
        # ≤30 DS → Rs.20
        assert rc.get_print_rate("A4_col", "ds", 10) == 20.0

    def test_student_bw_under_100(self):
        # Student ≤100 sheets → Rs.2
        assert rc.get_print_rate("A4_BW", "ss", 50, is_student=True) == 2.0

    def test_student_bw_over_100(self):
        # Student >100 sheets → Rs.1.5
        assert rc.get_print_rate("A4_BW", "ss", 101, is_student=True) == 1.5

    def test_student_bw_boundary_100(self):
        assert rc.get_print_rate("A4_BW", "ss", 100, is_student=True) == 2.0

    def test_student_flag_not_applied_to_colour(self):
        # is_student should not change colour rates
        rate_normal = rc.get_print_rate("A4_col", "ss", 10, is_student=False)
        rate_student = rc.get_print_rate("A4_col", "ss", 10, is_student=True)
        assert rate_normal == rate_student

    def test_legal_bw_ss(self):
        assert rc.get_print_rate("Legal_BW", "ss", 5) == 4.0

    def test_legal_bw_ds(self):
        assert rc.get_print_rate("Legal_BW", "ds", 5) == 5.0

    def test_a3_bw_ss(self):
        assert rc.get_print_rate("A3_BW", "ss", 5) == 5.0

    def test_unknown_paper_falls_back_to_a4_bw(self):
        # Unknown paper type → A4_BW default (Rs.3)
        assert rc.get_print_rate("NONEXISTENT", "ss", 5) == 3.0

    def test_invalid_sides_defaults_to_ss(self):
        # Invalid sides → treated as ss
        assert rc.get_print_rate("A4_BW", "invalid", 10) == 3.0


# ─────────────────────────────────────────────────────────────────────────────
# Binding rate lookups
# ─────────────────────────────────────────────────────────────────────────────

class TestSpiralRate:
    def test_tier1(self):
        assert rc.get_spiral_rate(20) == 30
        assert rc.get_spiral_rate(30) == 30

    def test_tier2(self):
        assert rc.get_spiral_rate(31) == 40
        assert rc.get_spiral_rate(70) == 40

    def test_tier3(self):
        assert rc.get_spiral_rate(71) == 50
        assert rc.get_spiral_rate(100) == 50

    def test_max_tier(self):
        assert rc.get_spiral_rate(260) == 150  # capped at last tier

    def test_a3(self):
        assert rc.get_spiral_rate(10, "A3") == 80

    def test_a3_case_insensitive(self):
        assert rc.get_spiral_rate(10, "a3") == 80


class TestSoftBindingRate:
    def test_tier1(self):
        assert rc.get_soft_binding_rate(50) == 80
        assert rc.get_soft_binding_rate(70) == 80

    def test_tier2(self):
        assert rc.get_soft_binding_rate(71) == 110
        assert rc.get_soft_binding_rate(100) == 110

    def test_without_print(self):
        assert rc.get_soft_binding_rate(50, with_print=False) == 100

    def test_max_tier(self):
        assert rc.get_soft_binding_rate(300) == 180


class TestThermalBindingRate:
    """S7-5: Thermal binding rate lookup."""

    def test_tier1(self):
        # ≤50 sheets → Rs.60
        assert rc.get_thermal_binding_rate(10) == 60
        assert rc.get_thermal_binding_rate(50) == 60

    def test_tier2(self):
        # 51–100 sheets → Rs.80
        assert rc.get_thermal_binding_rate(51) == 80
        assert rc.get_thermal_binding_rate(100) == 80

    def test_over_max(self):
        # >100 → capped at last tier Rs.80
        assert rc.get_thermal_binding_rate(200) == 80


# ─────────────────────────────────────────────────────────────────────────────
# calculate_item_cost
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateItemCost:
    def test_simple_a4_bw_ss(self):
        r = rc.calculate_item_cost(10, "A4_BW", "ss", "1-up", 1)
        assert r["sheets"] == 10
        assert r["rate"] == 3.0
        assert r["print_cost"] == 30.0

    def test_copies_multiplies_cost(self):
        r = rc.calculate_item_cost(10, "A4_BW", "ss", "1-up", 3)
        assert r["print_cost"] == 90.0

    def test_ds_rounding_in_cost(self):
        # 34 pages DS → 18 sheets × Rs.3 = Rs.54
        r = rc.calculate_item_cost(34, "A4_BW", "ds", "1-up", 1)
        assert r["sheets"] == 18
        assert r["print_cost"] == 54.0

    def test_breakdown_line_present(self):
        r = rc.calculate_item_cost(10, "A4_BW", "ss", "1-up", 1)
        assert "Rs." in r["breakdown_line"]

    def test_colour_label_in_breakdown(self):
        r = rc.calculate_item_cost(5, "A4_col", "ss", "1-up", 1)
        assert "Colour" in r["breakdown_line"]


# ─────────────────────────────────────────────────────────────────────────────
# calculate_finishing_cost
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateFinishingCost:
    def test_none(self):
        r = rc.calculate_finishing_cost("none", 30)
        assert r["finishing_cost"] == 0
        assert r["outsourced"] is False

    def test_staple(self):
        r = rc.calculate_finishing_cost("staple", 30)
        assert r["finishing_cost"] == 0

    def test_spiral(self):
        r = rc.calculate_finishing_cost("spiral", 20)
        assert r["finishing_cost"] == 30

    def test_wiro_same_as_spiral(self):
        r = rc.calculate_finishing_cost("wiro", 20)
        assert r["finishing_cost"] == 30

    def test_soft(self):
        r = rc.calculate_finishing_cost("soft", 50)
        assert r["finishing_cost"] == 80

    def test_project_white(self):
        r = rc.calculate_finishing_cost("project", 100, project_cover="white")
        assert r["finishing_cost"] == 220
        assert r["outsourced"] is True

    def test_project_gold(self):
        r = rc.calculate_finishing_cost("project", 100, project_cover="gold")
        assert r["finishing_cost"] == 250

    def test_record(self):
        r = rc.calculate_finishing_cost("record", 100)
        assert r["finishing_cost"] == 400
        assert r["outsourced"] is True

    def test_lam_sheet(self):
        r = rc.calculate_finishing_cost("lam_sheet", 1)
        assert r["finishing_cost"] == 60

    def test_thermal(self):
        """S7-5: thermal binding cost."""
        r = rc.calculate_finishing_cost("thermal", 30)
        assert r["finishing_cost"] == 60

    def test_thermal_upper_tier(self):
        r = rc.calculate_finishing_cost("thermal", 80)
        assert r["finishing_cost"] == 80

    def test_urgent_surcharge_on_soft(self):
        r = rc.calculate_finishing_cost("soft", 50, urgent=True)
        assert r["finishing_cost"] == 80 + 20  # soft + Rs.20 surcharge

    def test_urgent_surcharge_on_project(self):
        r = rc.calculate_finishing_cost("project", 100, urgent=True)
        assert r["finishing_cost"] == 220 + 20

    def test_urgent_not_applied_to_spiral(self):
        # spiral is NOT in URGENT_ELIGIBLE
        r_normal = rc.calculate_finishing_cost("spiral", 20, urgent=False)
        r_urgent = rc.calculate_finishing_cost("spiral", 20, urgent=True)
        assert r_normal["finishing_cost"] == r_urgent["finishing_cost"]

    def test_breakdown_contains_label(self):
        r = rc.calculate_finishing_cost("spiral", 20)
        assert "Spiral" in r["breakdown_line"]

    def test_outsourced_note_in_breakdown(self):
        r = rc.calculate_finishing_cost("project", 50)
        assert "outsourced" in r["breakdown_line"].lower()

    def test_finishing_case_insensitive(self):
        r = rc.calculate_finishing_cost("SPIRAL", 20)
        assert r["finishing_cost"] == 30


# ─────────────────────────────────────────────────────────────────────────────
# calculate_quote — full job
# ─────────────────────────────────────────────────────────────────────────────

class TestCalculateQuote:
    def test_simple_bw_ss_no_finishing(self):
        q = rc.calculate_quote(
            [{"pages": 10, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}]
        )
        assert q["total_sheets"] == 10
        assert q["print_cost"] == 30.0
        assert q["finishing_cost"] == 0
        assert q["total"] == 30.0

    def test_quote_with_spiral(self):
        # 34p DS A4 BW + spiral → 18 sheets × Rs.3 = Rs.54 + Rs.30 = Rs.84
        q = rc.calculate_quote(
            [{"pages": 34, "paper_type": "A4_BW", "sides": "ds", "layout": "1-up", "copies": 1}],
            finishing="spiral"
        )
        assert q["total_sheets"] == 18
        assert q["print_cost"] == 54.0
        assert q["finishing_cost"] == 30
        assert q["total"] == 84.0

    def test_quote_colour_tiered(self):
        # 20p A4 col SS → 20 sheets ≤30 → Rs.10/sheet = Rs.200
        q = rc.calculate_quote(
            [{"pages": 20, "paper_type": "A4_col", "sides": "ss", "layout": "1-up", "copies": 1}]
        )
        assert q["print_cost"] == 200.0

    def test_quote_multi_item(self):
        q = rc.calculate_quote([
            {"pages": 5,  "paper_type": "A4_col", "sides": "ss", "layout": "1-up", "copies": 1},
            {"pages": 10, "paper_type": "A4_BW",  "sides": "ss", "layout": "1-up", "copies": 1},
        ])
        # 5 col × Rs.10 + 10 BW × Rs.3 = Rs.50 + Rs.30 = Rs.80
        assert q["print_cost"] == 80.0
        assert q["total_sheets"] == 15

    def test_quote_multi_item_breakdown_prefix(self):
        q = rc.calculate_quote([
            {"pages": 5, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1},
            {"pages": 5, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1},
        ])
        assert q["breakdown"][0].startswith("Item 1:")
        assert q["breakdown"][1].startswith("Item 2:")

    def test_quote_single_item_no_prefix(self):
        q = rc.calculate_quote(
            [{"pages": 5, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}]
        )
        assert not q["breakdown"][0].startswith("Item")

    def test_quote_total_line_in_breakdown(self):
        q = rc.calculate_quote(
            [{"pages": 5, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}]
        )
        assert any("Total" in line for line in q["breakdown"])

    def test_quote_outsourced_finishing_flag(self):
        q = rc.calculate_quote(
            [{"pages": 10, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}],
            finishing="project"
        )
        assert q["outsourced_finishing"] is True

    def test_quote_inhouse_finishing_flag(self):
        q = rc.calculate_quote(
            [{"pages": 10, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}],
            finishing="spiral"
        )
        assert q["outsourced_finishing"] is False

    def test_quote_student_discount(self):
        q_normal  = rc.calculate_quote(
            [{"pages": 50, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}],
            is_student=False
        )
        q_student = rc.calculate_quote(
            [{"pages": 50, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}],
            is_student=True
        )
        assert q_student["total"] < q_normal["total"]

    def test_quote_copies(self):
        q1 = rc.calculate_quote(
            [{"pages": 10, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}]
        )
        q3 = rc.calculate_quote(
            [{"pages": 10, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 3}]
        )
        assert q3["print_cost"] == q1["print_cost"] * 3

    def test_quote_thermal_finishing(self):
        """S7-5: full quote with thermal binding."""
        q = rc.calculate_quote(
            [{"pages": 20, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}],
            finishing="thermal"
        )
        # 20 sheets BW × Rs.3 = Rs.60 print + Rs.60 thermal = Rs.120
        assert q["finishing_cost"] == 60
        assert q["total"] == 120.0


# ─────────────────────────────────────────────────────────────────────────────
# Legacy functions (backward compat)
# ─────────────────────────────────────────────────────────────────────────────

class TestLegacyCalculateSheets:
    def test_single_side(self):
        assert rc.calculate_sheets(10, "single", "single") == 10

    def test_double_side(self):
        # 10 pages double → DS: ceil(10/2)=5 → already even → 5... wait, 5 is odd → 6
        result = rc.calculate_sheets(10, "double", "double")
        assert result == rc.calc_sheets(10, "ds", "1-up")

    def test_2up_single(self):
        result = rc.calculate_sheets(10, "2up", "single")
        assert result == rc.calc_sheets(10, "ss", "2-up")


class TestLegacyCalculatePrintCost:
    def test_basic_bw(self):
        r = rc.calculate_print_cost(10, "A4", "bw", "single", "single", 1, "none", False)
        assert r["sheets"] == 10
        assert r["print_cost"] == 30.0
        assert r["total"] == 30.0

    def test_with_delivery(self):
        r = rc.calculate_print_cost(10, "A4", "bw", "single", "single", 1, "none", True)
        assert r["delivery_cost"] == 30
        assert r["total"] == 60.0

    def test_colour(self):
        r = rc.calculate_print_cost(10, "A4", "col", "single", "single", 1, "none", False)
        # 10 sheets col ≤30 → Rs.10/sheet = Rs.100
        assert r["print_cost"] == 100.0

    def test_return_shape(self):
        r = rc.calculate_print_cost(10, "A4", "bw", "single", "single", 1, "none", False)
        for key in ("sheets", "print_cost", "finishing_cost", "delivery_cost",
                    "total", "finishing_label", "breakdown"):
            assert key in r
