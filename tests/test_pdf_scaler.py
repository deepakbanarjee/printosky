"""
Tests for pdf_scaler.py — Fit / Actual size / Custom %.

The scaler exists to be baked into the PDF rather than asked of the driver, so
these tests check two things: the geometry is what the words mean, and a job
that does not ask for scaling is left completely alone.

That second one is the safety property the whole feature rests on — see
docs/plans/2026-08-30-scaling-and-post-press-services.md, rule 1.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fitz
import pytest

import pdf_scaler as ps
from nup_imposer import portrait_sheet

A4_W, A4_H = portrait_sheet("A4")
A5_W, A5_H = portrait_sheet("A5")
A3_W, A3_H = portrait_sheet("A3")


def make_pdf(sizes) -> bytes:
    """A PDF whose pages are the given (width, height) points, each with a mark
    so we can tell placement worked."""
    doc = fitz.open()
    for w, h in sizes:
        page = doc.new_page(width=w, height=h)
        page.draw_rect(fitz.Rect(10, 10, w - 10, h - 10))
    stream = io.BytesIO()
    doc.save(stream)
    doc.close()
    return stream.getvalue()


# ─────────────────────────────────────────────────────────────────────────────
# scale_rect — the geometry every caller shares
# ─────────────────────────────────────────────────────────────────────────────

class TestNoOps:
    """None means "print the original file, untouched"."""

    @pytest.mark.parametrize("mode", [None, "", "  ", "shrink", "noscale", "FITT"])
    def test_absent_or_unknown_mode(self, mode):
        assert ps.scale_rect(A4_W, A4_H, "A4", mode) is None

    @pytest.mark.parametrize("percent", [None, "", "abc", object()])
    def test_custom_without_a_usable_percent(self, percent):
        assert ps.scale_rect(A4_W, A4_H, "A4", "custom", percent) is None

    def test_actual_when_the_page_is_already_the_sheet(self):
        assert ps.scale_rect(A4_W, A4_H, "A4", "actual") is None

    def test_actual_still_acts_when_the_page_is_a_different_size(self):
        assert ps.scale_rect(A5_W, A5_H, "A4", "actual") is not None
        assert ps.scale_rect(A3_W, A3_H, "A4", "actual") is not None

    def test_a_degenerate_page(self):
        assert ps.scale_rect(0, 100, "A4", "fit") is None
        assert ps.scale_rect(100, -5, "A4", "fit") is None


class TestFit:
    def test_fills_the_printable_area_aspect_kept(self):
        r = ps.scale_rect(A4_W, A4_H, "A4", "fit")
        assert r["width"] == pytest.approx(A4_W - 2 * ps.FIT_MARGIN_PT, abs=0.5)
        assert r["width"] / r["height"] == pytest.approx(A4_W / A4_H, rel=1e-6)

    def test_centred(self):
        r = ps.scale_rect(A5_W, A5_H, "A4", "fit")
        assert r["x0"] == pytest.approx(r["sheet_w"] - r["x1"], abs=0.01)
        assert r["y0"] == pytest.approx(r["sheet_h"] - r["y1"], abs=0.01)

    def test_enlarges_a_small_page(self):
        assert ps.scale_rect(A5_W, A5_H, "A4", "fit")["scale"] > 1

    def test_shrinks_a_large_page(self):
        assert ps.scale_rect(A3_W, A3_H, "A4", "fit")["scale"] < 1

    def test_never_crops(self):
        for w, h in [(A5_W, A5_H), (A3_W, A3_H), (A4_W, A4_H), (1000, 200)]:
            assert ps.scale_rect(w, h, "A4", "fit")["crops"] is False


class TestActual:
    def test_is_true_size(self):
        r = ps.scale_rect(A5_W, A5_H, "A4", "actual")
        assert r["scale"] == 1.0
        assert r["width"] == pytest.approx(A5_W)
        assert r["height"] == pytest.approx(A5_H)

    def test_an_a5_page_stays_a5_on_an_a4_sheet(self):
        """The whole point of Actual size — and what Fit would not do."""
        r = ps.scale_rect(A5_W, A5_H, "A4", "actual")
        assert r["sheet_w"] == pytest.approx(A4_W)
        assert r["width"] == pytest.approx(A5_W)
        assert ps.scale_rect(A5_W, A5_H, "A4", "fit")["width"] > r["width"]

    def test_an_oversize_page_crops(self):
        r = ps.scale_rect(A3_W, A3_H, "A4", "actual")
        assert r["crops"] is True
        assert r["x0"] < 0 and r["y0"] < 0      # overhangs evenly on both sides


class TestCustom:
    def test_percent_is_of_the_page_not_the_sheet(self):
        """Owner, 2026-08-30. An A5 page at 100 % stays A5 — it is not blown up
        to fill the A4 sheet, which is what "percent of the sheet" would mean."""
        r = ps.scale_rect(A5_W, A5_H, "A4", "custom", 100)
        assert r["width"] == pytest.approx(A5_W)

    def test_custom_100_is_actual(self):
        for w, h in [(A5_W, A5_H), (A3_W, A3_H), (300, 900)]:
            custom = ps.scale_rect(w, h, "A4", "custom", 100)
            actual = ps.scale_rect(w, h, "A4", "actual")
            assert custom == actual

    def test_custom_100_on_a_same_size_page_is_a_no_op(self):
        assert ps.scale_rect(A4_W, A4_H, "A4", "custom", 100) is None

    @pytest.mark.parametrize("percent,expected", [(50, 0.5), (75, 0.75), (150, 1.5), (200, 2.0)])
    def test_scale_follows_the_percentage(self, percent, expected):
        assert ps.scale_rect(A4_W, A4_H, "A4", "custom", percent)["scale"] == pytest.approx(expected)

    def test_out_of_range_is_clamped_not_rejected(self):
        assert ps.scale_rect(A4_W, A4_H, "A4", "custom", 5)["scale"] == pytest.approx(ps.MIN_PERCENT / 100)
        assert ps.scale_rect(A4_W, A4_H, "A4", "custom", 9000)["scale"] == pytest.approx(ps.MAX_PERCENT / 100)

    def test_over_100_crops_and_is_flagged(self):
        r = ps.scale_rect(A4_W, A4_H, "A4", "custom", 150)
        assert r["crops"] is True

    def test_under_100_does_not_crop(self):
        assert ps.scale_rect(A4_W, A4_H, "A4", "custom", 75)["crops"] is False

    def test_percent_accepts_a_string(self):
        assert ps.scale_rect(A4_W, A4_H, "A4", "custom", "75")["scale"] == pytest.approx(0.75)


class TestOtherSheets:
    def test_the_sheet_is_always_portrait(self):
        for size in ("A4", "A3", "A5", "Legal"):
            r = ps.scale_rect(A5_W, A5_H, size, "fit")
            assert r["sheet_h"] > r["sheet_w"], size

    def test_an_a4_page_fits_an_a3_sheet_with_room(self):
        assert ps.scale_rect(A4_W, A4_H, "A3", "fit")["scale"] > 1


# ─────────────────────────────────────────────────────────────────────────────
# apply_scale — the baked artifact
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyScale:
    def test_no_mode_is_a_no_op(self):
        pdf = make_pdf([(A4_W, A4_H)])
        for mode in (None, "", "nonsense"):
            assert ps.apply_scale(pdf, mode) is None

    def test_actual_on_a_matching_page_is_a_no_op(self):
        pdf = make_pdf([(A4_W, A4_H), (A4_W, A4_H)])
        assert ps.apply_scale(pdf, "actual", paper_size="A4") is None

    def test_output_pages_are_sheet_sized(self):
        pdf = make_pdf([(A5_W, A5_H), (A3_W, A3_H)])
        out = ps.apply_scale(pdf, "fit", paper_size="A4")
        doc = fitz.open("pdf", out)
        try:
            assert len(doc) == 2
            for page in doc:
                assert page.rect.width == pytest.approx(A4_W, abs=0.5)
                assert page.rect.height == pytest.approx(A4_H, abs=0.5)
        finally:
            doc.close()

    def test_page_count_is_preserved(self):
        pdf = make_pdf([(A5_W, A5_H)] * 7)
        doc = fitz.open("pdf", ps.apply_scale(pdf, "custom", 80, "A4"))
        try:
            assert len(doc) == 7
        finally:
            doc.close()

    def test_mixed_page_sizes_all_land_on_one_sheet_size(self):
        """A document that mixes A4 and A3 still prints as uniform A4 sheets."""
        pdf = make_pdf([(A4_W, A4_H), (A3_W, A3_H), (A5_W, A5_H)])
        out = ps.apply_scale(pdf, "actual", paper_size="A4")
        assert out is not None          # the A3 and A5 pages need placing
        doc = fitz.open("pdf", out)
        try:
            assert {(round(p.rect.width), round(p.rect.height)) for p in doc} == {
                (round(A4_W), round(A4_H))
            }
        finally:
            doc.close()

    def test_it_never_rotates(self):
        """Rotation belongs to nup_imposer. A landscape page shrinks to fit; it
        does not turn."""
        pdf = make_pdf([(A4_H, A4_W)])          # landscape
        doc = fitz.open("pdf", ps.apply_scale(pdf, "fit", paper_size="A4"))
        try:
            assert doc[0].rotation == 0
        finally:
            doc.close()

    def test_an_empty_pdf_is_a_no_op(self):
        # Hand-built: PyMuPDF refuses to *save* a zero-page document, but it
        # will happily open one, and a stray one must not crash a print.
        empty = (b"%PDF-1.4\n"
                 b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
                 b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
                 b"trailer<</Root 1 0 R>>\n%%EOF")
        assert ps.apply_scale(empty, "fit") is None

    def test_corrupt_input_raises_for_the_caller_to_guard(self):
        # print_planner wraps this in ops_watchdog.guard(reraise=False) and
        # prints unscaled — an alert, not a silently wrong sheet.
        with pytest.raises(Exception):
            ps.apply_scale(b"this is not a pdf", "fit")

    def test_the_result_still_opens_and_has_content(self):
        pdf = make_pdf([(A5_W, A5_H)])
        doc = fitz.open("pdf", ps.apply_scale(pdf, "fit", paper_size="A4"))
        try:
            assert doc[0].get_drawings(), "the drawn rect should survive placement"
        finally:
            doc.close()


class TestCountCroppedPages:
    def test_counts_only_the_pages_that_lose_content(self):
        pdf = make_pdf([(A4_W, A4_H), (A3_W, A3_H), (A5_W, A5_H)])
        # Actual size on A4: only the A3 page overflows.
        assert ps.count_cropped_pages(pdf, "actual", paper_size="A4") == 1

    def test_fit_never_crops_anything(self):
        pdf = make_pdf([(A4_W, A4_H), (A3_W, A3_H), (A5_W, A5_H)])
        assert ps.count_cropped_pages(pdf, "fit", paper_size="A4") == 0

    def test_every_page_crops_when_blown_up(self):
        pdf = make_pdf([(A4_W, A4_H)] * 4)
        assert ps.count_cropped_pages(pdf, "custom", 200, "A4") == 4
