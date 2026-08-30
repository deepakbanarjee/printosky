"""
Tests for rate_card.py — pricing engine for Printosky.

Covers:
- calc_sheets(): sheet counting for all sides/layout combinations
- get_print_rate(): per-sheet rates, student discount, colour tiers
- get_spiral_rate(), get_soft_binding_rate(), get_wiro_rate()
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
        # 6 pages DS → ceil(6/2)=3 sheets
        assert rc.calc_sheets(6, "ds", "1-up") == 3

    def test_ds_1up_odd_pages(self):
        # 5 pages DS → ceil(5/2)=3 sheets
        assert rc.calc_sheets(5, "ds", "1-up") == 3

    def test_ds_1up_34_pages(self):
        # 34 pages DS → ceil(34/2)=17 sheets
        assert rc.calc_sheets(34, "ds", "1-up") == 17

    def test_ds_1up_1_page(self):
        # 1 page DS → ceil(1/2)=1 sheet
        assert rc.calc_sheets(1, "ds", "1-up") == 1

    def test_ds_1up_already_even(self):
        # 4 pages DS → ceil(4/2)=2 sheets
        assert rc.calc_sheets(4, "ds", "1-up") == 2

    def test_ss_2up(self):
        # 50 pages 2-up SS → ceil(50/2)=25 sheets
        assert rc.calc_sheets(50, "ss", "2-up") == 25

    def test_ss_4up(self):
        # 40 pages 4-up SS → ceil(40/4)=10 sheets
        assert rc.calc_sheets(40, "ss", "4-up") == 10

    def test_ds_2up(self):
        # 50 pages 2-up DS → after layout: ceil(50/2)=25 pages → DS: ceil(25/2)=13 sheets
        assert rc.calc_sheets(50, "ds", "2-up") == 13

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


class TestWiroRate:
    """Wiro got its own tiers on 2026-08-30 and a machine ceiling."""

    def test_tiers(self):
        assert rc.get_wiro_rate(30) == 50
        assert rc.get_wiro_rate(70) == 100
        assert rc.get_wiro_rate(100) == 150
        assert rc.get_wiro_rate(130) == 200
        assert rc.get_wiro_rate(150) == 250

    def test_above_the_limit_returns_none_not_a_price(self):
        assert rc.get_wiro_rate(151) is None
        assert rc.get_wiro_rate(500) is None


class TestSpiralA3Tiers:
    """A3 spiral was a flat Rs.80 at every thickness until 2026-08-30, which
    made a 250-sheet A3 spiral the cheapest binding in the shop."""

    def test_a3_is_tiered(self):
        assert rc.get_spiral_rate(10, "A3") == 80
        assert rc.get_spiral_rate(250, "A3") == 400

    def test_a3_is_dearer_than_a4_at_every_tier(self):
        for sheets in (30, 70, 100, 130, 150, 170, 200, 250):
            assert rc.get_spiral_rate(sheets, "A3") > rc.get_spiral_rate(sheets, "A4")


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
        # 34 pages DS → 17 sheets × Rs.3 = Rs.51.00
        r = rc.calculate_item_cost(34, "A4_BW", "ds", "1-up", 1)
        assert r["sheets"] == 17
        assert r["print_cost"] == 51.0

    def test_breakdown_line_present(self):
        r = rc.calculate_item_cost(10, "A4_BW", "ss", "1-up", 1)
        assert "Rs." in r["breakdown_line"]

    def test_colour_label_in_breakdown(self):
        r = rc.calculate_item_cost(5, "A4_col", "ss", "1-up", 1)
        assert "Colour" in r["breakdown_line"]

    def test_colour_billed_per_page_duplex_equals_simplex(self):
        # Owner rule: colour is charged per page — duplex must not change the
        # price, and there is no doubled DS rate.
        ss = rc.calculate_item_cost(10, "A4_col", "ss", "1-up", 1)
        ds = rc.calculate_item_cost(10, "A4_col", "ds", "1-up", 1)
        assert ss["print_cost"] == 100.0
        assert ds["print_cost"] == 100.0   # was 120.0 before the per-page fix
        assert ds["sheets"] == 10          # billed by page, not halved

    def test_colour_odd_duplex_not_rounded_up(self):
        # 3 colour pages DS → 3 pages × Rs.10 = Rs.30 (no even round-up, no doubling)
        r = rc.calculate_item_cost(3, "A4_col", "ds", "1-up", 1)
        assert r["print_cost"] == 30.0

    def test_bw_duplex_discount_preserved(self):
        # B&W keeps the per-sheet model: DS still ~halves vs SS.
        ss = rc.calculate_item_cost(10, "A4_BW", "ss", "1-up", 1)
        ds = rc.calculate_item_cost(10, "A4_BW", "ds", "1-up", 1)
        assert ss["print_cost"] == 30.0
        assert ds["print_cost"] < ss["print_cost"]


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

    def test_wiro_has_its_own_tiers(self):
        # Wiro used to borrow spiral's tiers behind a "for now" comment. Since
        # 2026-08-30 it starts at Rs.50 and steps Rs.50 per tier.
        assert rc.calculate_finishing_cost("wiro", 20)["finishing_cost"] == 50
        assert rc.calculate_finishing_cost("wiro", 90)["finishing_cost"] == 150
        assert rc.calculate_finishing_cost("wiro", 150)["finishing_cost"] == 250

    def test_wiro_is_refused_above_the_machine_limit(self):
        # Never a silent zero: over 150 sheets the counter must offer something
        # else rather than quote nothing.
        r = rc.calculate_finishing_cost("wiro", 200)
        assert r["refused"]
        assert r["finishing_cost"] == 0
        assert "spiral or soft" in r["breakdown_line"]

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

    def test_lam_sheet_a4(self):
        # Rs.60 + the owner's 2026-08-30 premium of Rs.10.
        r = rc.calculate_finishing_cost("lam_sheet", 1)
        assert r["finishing_cost"] == 70

    def test_lam_sheet_a3_is_not_billed_as_a4(self):
        # calculate_finishing_cost used to hardcode LAMINATION_RATES["a4"], so
        # an A3 pouch lamination billed as A4 and the A3 rates were dead code.
        assert rc.calculate_finishing_cost("lam_sheet", 1, paper_size="A3")["finishing_cost"] == 120
        assert rc.calculate_finishing_cost("lam_sheet", 1, paper_size="A3",
                                           is_colour=True)["finishing_cost"] == 140

    def test_lam_roll_is_per_sheet_with_a_floor(self):
        # Had no branch at all before 2026-08-30 — every roll lamination was
        # quoted at zero while being paid for as outsourced work.
        assert rc.calculate_finishing_cost("lam_roll", 3)["finishing_cost"] == 150   # min 10
        assert rc.calculate_finishing_cost("lam_roll", 14)["finishing_cost"] == 210
        assert rc.calculate_finishing_cost("lam_roll", 14, paper_size="A3")["finishing_cost"] == 420

    def test_lam_cover_reads_the_price_that_was_always_there(self):
        # BINDING_RATES carried lam_cover: 50 and nothing ever read it.
        assert rc.calculate_finishing_cost("lam_cover", 1)["finishing_cost"] == 50

    def test_id_card_is_per_card(self):
        # Rs.100 per card, printing included (owner, 2026-08-30).
        assert rc.calculate_finishing_cost("id_card", 1)["finishing_cost"] == 100
        assert rc.calculate_finishing_cost("id_card", 4)["finishing_cost"] == 400

    def test_perfect_is_priced_as_soft(self):
        for sheets in (50, 90, 140):
            assert (rc.calculate_finishing_cost("perfect", sheets)["finishing_cost"]
                    == rc.calculate_finishing_cost("soft", sheets)["finishing_cost"])

    def test_thesis_flat_with_print_project_plus_premium_without(self):
        assert rc.calculate_finishing_cost("thesis", 60)["finishing_cost"] == 500
        assert rc.calculate_finishing_cost("thesis", 60,
                                           with_print=False)["finishing_cost"] == 320
        assert rc.calculate_finishing_cost("thesis", 60, with_print=False,
                                           project_cover="gold")["finishing_cost"] == 350

    def test_bind_only_premium_is_twenty_across_the_board(self):
        for key in ("spiral", "wiro", "soft", "perfect"):
            with_print = rc.calculate_finishing_cost(key, 50)["finishing_cost"]
            bind_only = rc.calculate_finishing_cost(key, 50, with_print=False)["finishing_cost"]
            assert bind_only - with_print == rc.BIND_ONLY_PREMIUM, key

    def test_thermal_is_withdrawn(self):
        """Withdrawn 2026-08-30 (was backlog S7-5, "rate never tested").
        An order for it must be flagged, never quoted at zero."""
        r = rc.calculate_finishing_cost("thermal", 30)
        assert r["unpriced"] is True
        assert r["finishing_cost"] == 0

    def test_urgent_surcharge_on_soft(self):
        r = rc.calculate_finishing_cost("soft", 50, urgent=True)
        assert r["finishing_cost"] == 80 + 20  # soft + Rs.20 surcharge

    def test_urgent_surcharge_on_project(self):
        r = rc.calculate_finishing_cost("project", 100, urgent=True)
        assert r["finishing_cost"] == 220 + 20

    def test_urgent_now_applies_to_every_priced_finishing(self):
        # Was soft + project only. Since 2026-08-30 anything can be rushed, so
        # an operator has no exceptions to remember.
        for key in sorted(rc.BINDING_RATES):
            normal = rc.calculate_finishing_cost(key, 20, urgent=False)["finishing_cost"]
            urgent = rc.calculate_finishing_cost(key, 20, urgent=True)["finishing_cost"]
            expected = rc.URGENT_SURCHARGE if key in rc.URGENT_ELIGIBLE else 0
            assert urgent - normal == expected, key

    def test_urgent_never_applies_to_the_free_ones(self):
        for key in sorted(rc.ZERO_PRICED_FINISHINGS):
            assert rc.calculate_finishing_cost(key, 20, urgent=True)["finishing_cost"] == 0

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
        # 34p DS A4 BW + spiral → 17 sheets × Rs.3 = Rs.51 + Rs.30 = Rs.81
        q = rc.calculate_quote(
            [{"pages": 34, "paper_type": "A4_BW", "sides": "ds", "layout": "1-up", "copies": 1}],
            finishing="spiral"
        )
        assert q["total_sheets"] == 17
        assert q["print_cost"] == 51.0
        assert q["finishing_cost"] == 30
        assert q["total"] == 81.0

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

    def test_quote_flags_a_withdrawn_finishing_instead_of_charging_zero(self):
        q = rc.calculate_quote(
            [{"pages": 20, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}],
            finishing="thermal"
        )
        assert q["unpriced_finishing"] is True
        assert q["finishing_cost"] == 0
        assert q["total"] == 60          # the printing only — and flagged.0


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

    def test_2up_layout(self):
        # 2up branch in calculate_print_cost
        r = rc.calculate_print_cost(20, "A4", "bw", "2up", "single", 1, "none", False)
        assert r["sheets"] == rc.calc_sheets(20, "ss", "2-up")

    def test_4up_layout(self):
        # 4up branch in calculate_print_cost
        r = rc.calculate_print_cost(40, "A4", "bw", "4up", "single", 1, "none", False)
        assert r["sheets"] == rc.calc_sheets(40, "ss", "4-up")


class TestLegacyCalculateSheets4up:
    def test_4up_single(self):
        # 4up branch in calculate_sheets (lines 446-448)
        result = rc.calculate_sheets(40, "4up", "single")
        assert result == rc.calc_sheets(40, "ss", "4-up")

    def test_4up_double(self):
        result = rc.calculate_sheets(40, "4up", "double")
        assert result == rc.calc_sheets(40, "ds", "4-up")


# ─────────────────────────────────────────────────────────────────────────────
# load_rates_from_supabase — mocked HTTP
# ─────────────────────────────────────────────────────────────────────────────

class TestLoadRatesFromSupabase:
    def _make_mock_urlopen(self, payload: list):
        """Return a context-manager mock that yields payload as JSON bytes."""
        import io
        import json
        from unittest.mock import MagicMock

        cm = MagicMock()
        cm.__enter__ = lambda s: MagicMock(read=lambda: json.dumps(payload).encode())
        cm.__exit__ = MagicMock(return_value=False)
        return cm

    def test_returns_true_on_success(self, monkeypatch):
        import urllib.request
        payload = [{"key": "a4_bw_single", "price": 3.0, "staff_quote": False}]
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=5: self._make_mock_urlopen(payload)
        )
        result = rc.load_rates_from_supabase("https://example.supabase.co", "fake-key")
        assert result is True

    def test_updates_rates_dict(self, monkeypatch):
        import urllib.request
        payload = [{"key": "a4_bw_single", "price": 5.0, "staff_quote": False}]
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=5: self._make_mock_urlopen(payload)
        )
        rc.load_rates_from_supabase("https://example.supabase.co", "fake-key")
        assert rc.RATES["A4"]["bw"]["single"] == 5.0
        # Restore default
        rc.RATES["A4"]["bw"]["single"] = 3.0

    def test_updates_finishing_rate(self, monkeypatch):
        import urllib.request
        payload = [{"key": "finishing_spiral", "price": 99.0, "staff_quote": False}]
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=5: self._make_mock_urlopen(payload)
        )
        rc.load_rates_from_supabase("https://example.supabase.co", "fake-key")
        assert rc.FINISHING_RATES["spiral"]["price"] == 99.0
        rc.FINISHING_RATES["spiral"]["price"] = 30  # restore

    def test_updates_delivery_charge(self, monkeypatch):
        import urllib.request
        payload = [{"key": "delivery", "price": 50.0, "staff_quote": False}]
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=5: self._make_mock_urlopen(payload)
        )
        rc.load_rates_from_supabase("https://example.supabase.co", "fake-key")
        assert rc.DELIVERY_CHARGE == 50.0
        rc.DELIVERY_CHARGE = 30  # restore

    def test_returns_false_on_empty_response(self, monkeypatch):
        import urllib.request
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=5: self._make_mock_urlopen([])
        )
        result = rc.load_rates_from_supabase("https://example.supabase.co", "fake-key")
        assert result is False

    def test_returns_false_on_network_error(self, monkeypatch):
        import urllib.request
        def _raise(*a, **kw):
            raise OSError("no network")
        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        result = rc.load_rates_from_supabase("https://example.supabase.co", "fake-key")
        assert result is False

    def test_unknown_key_ignored(self, monkeypatch):
        import urllib.request
        payload = [{"key": "nonexistent_key", "price": 999.0, "staff_quote": False}]
        monkeypatch.setattr(
            urllib.request, "urlopen",
            lambda req, timeout=5: self._make_mock_urlopen(payload)
        )
        # Should not raise, just skip unknown key
        result = rc.load_rates_from_supabase("https://example.supabase.co", "fake-key")
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# get_pdf_page_count — mocked imports
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPdfPageCount:
    def test_returns_zero_when_all_libs_unavailable(self, monkeypatch):
        # Simulate all PDF libs absent
        import builtins
        real_import = builtins.__import__

        def _block_pdf(name, *args, **kwargs):
            if name in ("pikepdf", "pypdf", "PyPDF2"):
                raise ImportError(f"blocked: {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _block_pdf)
        result = rc.get_pdf_page_count("/nonexistent/file.pdf")
        assert result == 0

    def test_returns_zero_for_bad_path(self):
        # All libs present but file doesn't exist → all raise → returns 0
        result = rc.get_pdf_page_count("/this/path/does/not/exist.pdf")
        assert result == 0
