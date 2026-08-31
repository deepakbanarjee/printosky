"""
Tests for the post-press service rate engine — B-1.

Nothing calls calculate_service_quote() yet; it is wired up in B-3. These tests
are what makes the rates checkable before any of it is reachable from a console.

Two behaviours matter more than the individual numbers:

  * **A minimum names itself.** An operator has to be able to explain "why is
    3 sheets Rs.300" before the customer asks, so the breakdown says so.
  * **An unpriced service is flagged, never billed at zero.** That is the whole
    lesson of the five finishings that quoted Rs.0 for months (see
    tests/test_finishing_and_size_coverage.py).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import rate_card as rc


def q(kind, **meta):
    return rc.calculate_service_quote(kind, meta)


# ─────────────────────────────────────────────────────────────────────────────
# Copy — the print rate card, because a copied sheet costs what a printed one does
# ─────────────────────────────────────────────────────────────────────────────

class TestCopy:
    def test_bw_is_the_print_rate(self):
        assert q("copy", sheets=10, colour="bw")["total"] == 30      # 10 x Rs.3

    def test_colour_is_the_colour_rate(self):
        assert q("copy", sheets=10, colour="col")["total"] == 100    # 10 x Rs.10

    def test_copies_multiply(self):
        assert q("copy", sheets=10, colour="bw", copies=3)["total"] == 90

    def test_the_student_rate_reaches_photocopying(self):
        """Owner, 2026-08-30: printing and photocopy, nothing else."""
        assert q("copy", sheets=10, colour="bw", is_student=True)["total"] == 20

    def test_a5_copies_at_half_the_a4_rate(self):
        assert q("copy", sheets=10, colour="bw", paper_size="A5")["total"] == 15


# ─────────────────────────────────────────────────────────────────────────────
# Scan
# ─────────────────────────────────────────────────────────────────────────────

class TestScan:
    @pytest.mark.parametrize("sheets,rate", [(1, 10), (50, 10), (51, 7), (100, 7), (101, 5), (500, 5)])
    def test_a4_tiers_and_their_boundaries(self, sheets, rate):
        assert q("scan", sheets=sheets)["total"] == sheets * rate

    @pytest.mark.parametrize("sheets", [1, 50, 51, 100, 101])
    def test_a3_is_exactly_double_a4(self, sheets):
        assert q("scan", sheets=sheets, paper_size="A3")["total"] == \
               q("scan", sheets=sheets)["total"] * 2

    def test_the_per_customer_special_rate_is_gone(self):
        """A per-customer override living in a shared table is one that gets
        applied by accident. Removed 2026-08-30."""
        assert "special" not in rc.SCANNING_RATES


# ─────────────────────────────────────────────────────────────────────────────
# Lamination — three different products, three different prices
# ─────────────────────────────────────────────────────────────────────────────

class TestLaminate:
    def test_pouch_by_size(self):
        assert q("laminate", sheets=1, lam_type="pouch")["total"] == 70
        assert q("laminate", sheets=1, lam_type="pouch", paper_size="A3")["total"] == 120
        assert q("laminate", sheets=1, lam_type="pouch", paper_size="A3",
                 is_colour=True)["total"] == 140

    def test_roll_is_per_sheet_not_the_pouch_rate(self):
        """Roll and pouch are different processes. Wiring roll to the pouch
        table would overcharge by about four times."""
        assert q("laminate", sheets=20, lam_type="roll")["total"] == 300   # 20 x Rs.15
        assert q("laminate", sheets=20, lam_type="pouch")["total"] == 1400

    def test_roll_bills_the_minimum_and_says_so(self):
        r = q("laminate", sheets=3, lam_type="roll")
        assert r["total"] == 150                       # 10 x Rs.15, not 3
        assert "minimum 10 sheets applied (3 brought)" in " ".join(r["breakdown"])

    def test_cover_reads_the_rate_that_was_always_there(self):
        assert q("laminate", sheets=2, lam_type="cover")["total"] == 100   # 2 x Rs.50

    def test_id_lamination_has_no_rate_and_says_so(self):
        """Different product from ID card PRINTING (Rs.100/card, printing
        included), and its own rate was never given."""
        r = q("laminate", sheets=1, lam_type="id")
        assert r["needs_manual_price"] is True
        assert r["total"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# Foiling — one formula covers A4, A3 and covers
# ─────────────────────────────────────────────────────────────────────────────

class TestFoil:
    def test_above_the_minimum_is_straight_piece_rate(self):
        assert q("foil", sheets=14)["total"] == 420          # 14 x Rs.30
        assert q("foil", sheets=20, paper_size="A3")["total"] == 1000

    def test_below_the_minimum_bills_the_minimum_and_says_so(self):
        r = q("foil", sheets=3)
        assert r["total"] == 300                             # 10 x Rs.30
        assert "minimum 10 sheets applied (3 brought)" in " ".join(r["breakdown"])

    def test_the_cover_floor_falls_out_of_the_same_rule(self):
        """The owner quoted 'minimum Rs.500 for up to 10 covers'. That is
        exactly 10 x Rs.50, so covers need no special case."""
        assert q("foil", sheets=1, paper_size="cover")["total"] == 500
        assert q("foil", sheets=10, paper_size="cover")["total"] == 500
        assert q("foil", sheets=11, paper_size="cover")["total"] == 550


# ─────────────────────────────────────────────────────────────────────────────
# Binding a customer's own sheets
# ─────────────────────────────────────────────────────────────────────────────

class TestBindOnly:
    def test_it_carries_the_bind_only_premium(self):
        with_print = rc.calculate_finishing_cost("spiral", 50)["finishing_cost"]
        assert q("bind", sheets=50, binding="spiral")["total"] == with_print + rc.BIND_ONLY_PREMIUM

    def test_thesis_uses_its_bind_only_price(self):
        assert q("bind", sheets=60, binding="thesis")["total"] == 320
        assert q("bind", sheets=60, binding="thesis", project_cover="gold")["total"] == 350

    def test_a_binding_the_machine_cannot_do_is_flagged(self):
        r = q("bind", sheets=200, binding="wiro")     # wiro stops at 150 sheets
        assert r["needs_manual_price"] is True
        assert r["total"] == 0

    def test_a_withdrawn_binding_is_flagged(self):
        assert q("bind", sheets=50, binding="thermal")["needs_manual_price"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Cutting and punching
# ─────────────────────────────────────────────────────────────────────────────

class TestHandwork:
    @pytest.mark.parametrize("kind", ["cut", "punch"])
    def test_per_pass_with_a_floor(self, kind):
        assert q(kind, passes=4)["total"] == 100        # 4 x Rs.20 = 80 -> floor
        assert q(kind, passes=10)["total"] == 200       # above the floor
        assert q(kind, passes=1)["total"] == 100

    @pytest.mark.parametrize("kind", ["cut", "punch"])
    def test_free_on_a_job_we_printed_or_bound(self, kind):
        r = q(kind, passes=6, with_our_job=True)
        assert r["total"] == 0
        assert "free" in " ".join(r["breakdown"])

    def test_the_floor_names_itself(self):
        assert "minimum Rs.100 applied" in " ".join(q("cut", passes=2)["breakdown"])


# ─────────────────────────────────────────────────────────────────────────────
# Photos and DTP
# ─────────────────────────────────────────────────────────────────────────────

class TestPhotoAndDtp:
    def test_photo_set_and_sheet(self):
        assert q("photo", unit="set5", qty=1)["total"] == 50
        assert q("photo", unit="sheet", qty=1)["total"] == 100
        assert q("photo", unit="set5", qty=3)["total"] == 150

    @pytest.mark.parametrize("unit", ["stamp", "postcard", "4x6"])
    def test_the_sizes_whose_rates_are_pending_are_flagged(self, unit):
        r = q("photo", unit=unit, qty=1)
        assert r["needs_manual_price"] is True
        assert r["total"] == 0

    def test_dtp_is_per_page_by_language(self):
        assert q("dtp", pages=3, language="malayalam")["total"] == 120
        assert q("dtp", pages=3, language="english")["total"] == 120
        assert q("dtp", pages=3, language="hindi")["total"] == 180

    def test_dtp_says_printing_is_extra(self):
        """Typing only — the printed pages bill at the ordinary print rates."""
        assert "printing charged separately" in " ".join(q("dtp", pages=1)["breakdown"])


# ─────────────────────────────────────────────────────────────────────────────
# Failing loud
# ─────────────────────────────────────────────────────────────────────────────

class TestNothingIsSilentlyFree:
    def test_an_unknown_kind_is_flagged_not_free(self):
        r = q("teleportation")
        assert r["unpriced"] is True and r["needs_manual_price"] is True
        assert "NO RATE" in " ".join(r["breakdown"])

    def test_other_always_needs_a_price(self):
        assert q("other", description="something unusual")["needs_manual_price"] is True

    def test_a_typed_price_settles_it(self):
        r = q("other", description="foil block making", manual_price=250)
        assert r["total"] == 250
        assert r["needs_manual_price"] is False

    def test_a_junk_typed_price_does_not_clear_the_flag(self):
        r = q("other", description="x", manual_price="two hundred")
        assert r["needs_manual_price"] is True
        assert r["total"] == 0

    @pytest.mark.parametrize("kind", sorted(rc.SERVICE_KINDS))
    def test_every_kind_is_priced_or_flagged_never_a_silent_zero(self, kind):
        r = rc.calculate_service_quote(kind, {"sheets": 12, "passes": 12, "pages": 12,
                                              "qty": 12, "binding": "spiral"})
        assert r["total"] > 0 or r["needs_manual_price"], f"{kind} quoted Rs.0 unflagged"

    def test_every_kind_has_a_label(self):
        for kind in rc.SERVICE_KINDS:
            assert rc.calculate_service_quote(kind, {})["label"]


class TestUrgent:
    def test_any_service_can_be_rushed(self):
        """Owner, 2026-08-30 — was soft and project binding only."""
        for kind, meta in [("scan", {"sheets": 10}), ("foil", {"sheets": 20}),
                           ("copy", {"sheets": 10}), ("dtp", {"pages": 2})]:
            plain = rc.calculate_service_quote(kind, meta)["total"]
            rush = rc.calculate_service_quote(kind, {**meta, "urgent": True})["total"]
            assert rush - plain == rc.URGENT_SURCHARGE, kind

    def test_it_is_named_in_the_breakdown(self):
        assert "Urgent: +Rs.20" in " ".join(q("scan", sheets=5, urgent=True)["breakdown"])


class TestBreakdownIsUsable:
    def test_every_quote_ends_with_its_total(self):
        for kind in rc.SERVICE_KINDS:
            last = rc.calculate_service_quote(kind, {"sheets": 5})["breakdown"][-1]
            assert last.startswith("--- Total: Rs.")
