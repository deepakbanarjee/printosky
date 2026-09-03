"""
Guard tests for scaling in print_planner — A-2 of the scaling plan.

The load-bearing test in this file is TestUnchangedWithoutScale: a print_spec
that does not ask for scaling must plan EXACTLY as it did before the feature
existed. Every job in production today is such a spec, including the twelve
A4 combinations verified on paper at OSP (docs/PRINT_ROTATION_MATRIX.md), so
if that class holds, this feature cannot have changed anything that works.

The rest check that a spec which does ask gets what it asked for, and that the
combinations we do not support yet are dropped loudly rather than half-applied.
"""

import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fitz
import pytest

import print_planner
import pdf_scaler
from nup_imposer import portrait_sheet

A4_W, A4_H = portrait_sheet("A4")
A5_W, A5_H = portrait_sheet("A5")


@pytest.fixture
def pdf(tmp_path):
    def _make(sizes=((A4_W, A4_H),) * 4):
        doc = fitz.open()
        for w, h in sizes:
            doc.new_page(width=w, height=h).draw_rect(fitz.Rect(10, 10, w - 10, h - 10))
        path = str(tmp_path / "in.pdf")
        doc.save(path)
        doc.close()
        return path
    return _make


@pytest.fixture
def reports(monkeypatch):
    """Capture ops_watchdog alerts — a dropped scale must never be silent."""
    seen = []
    monkeypatch.setattr(print_planner, "_report",
                        lambda check, ok, detail: seen.append((check, ok, detail)))
    return seen


def plan(pdf_path, spec, tmp_path):
    return print_planner.plan_print_job("JOB-1", pdf_path, spec, str(tmp_path))


def sheets(pdf_path):
    """What actually comes off the printer: every page's size, rotation and
    rendered pixels. Independent of the non-deterministic bits of a PDF file."""
    import hashlib
    doc = fitz.open(pdf_path)
    try:
        return [(round(p.rect.width, 2), round(p.rect.height, 2), p.rotation,
                 hashlib.sha256(p.get_pixmap(dpi=36).samples).hexdigest())
                for p in doc]
    finally:
        doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# The safety property
# ─────────────────────────────────────────────────────────────────────────────

class TestUnchangedWithoutScale:

    BASE_SPECS = [
        {"sides": "simplex", "colour_mode": "bw", "nup": 1},
        {"sides": "duplex", "colour_mode": "bw", "nup": 1},
        {"sides": "duplex", "colour_mode": "col", "nup": 2},
        {"sides": "duplex", "colour_mode": "bw", "nup": 4, "orientation": "landscape"},
        {"sides": "simplex", "colour_mode": "bw", "nup": 1, "orientation": "landscape"},
        {"sides": "duplex", "colour_mode": "mixed", "colour_pages": [2], "nup": 1},
        {"sides": "duplex", "colour_mode": "bw", "nup": 9, "nup_direction": "vertical"},
    ]

    @pytest.mark.parametrize("spec", BASE_SPECS)
    def test_no_scale_means_no_scaling_and_no_noscale(self, spec, pdf, tmp_path, reports):
        actions, temp = plan(pdf(), dict(spec), tmp_path)
        try:
            assert all(a["scale_applied"] is False for a in actions)
            assert reports == []
        finally:
            print_planner.cleanup_temp_dir(temp)

    @pytest.mark.parametrize("spec", BASE_SPECS)
    def test_no_scaled_file_is_ever_written(self, spec, pdf, tmp_path):
        actions, temp = plan(pdf(), dict(spec), tmp_path)
        try:
            if temp:
                assert not os.path.exists(os.path.join(temp, "scaled.pdf"))
        finally:
            print_planner.cleanup_temp_dir(temp)

    @pytest.mark.parametrize("spec", BASE_SPECS)
    def test_the_sheets_are_identical_with_an_empty_scale_block(self, spec, pdf, tmp_path):
        """An empty or absent scale block must be indistinguishable.

        Compared by what comes off the printer — page sizes, rotation and the
        rendered image of every sheet — rather than by PDF bytes, which carry
        non-deterministic ids and timestamps from PyMuPDF's writer.
        """
        src = pdf()
        a1, t1 = plan(src, dict(spec), tmp_path / "a")
        a2, t2 = plan(src, {**spec, "scale": {}}, tmp_path / "b")
        try:
            strip = lambda acts: [{k: v for k, v in a.items() if k != "pdf_path"} for a in acts]
            assert strip(a1) == strip(a2)
            assert [sheets(a["pdf_path"]) for a in a1] == [sheets(a["pdf_path"]) for a in a2]
        finally:
            print_planner.cleanup_temp_dir(t1)
            print_planner.cleanup_temp_dir(t2)

    def test_a_spec_with_no_scale_prints_the_original_file_itself(self, pdf, tmp_path):
        """1-up portrait, no page selection: the planner must hand the printer
        the very file it was given, not a rewritten copy."""
        src = pdf()
        actions, temp = plan(src, {"sides": "simplex", "colour_mode": "bw", "nup": 1}, tmp_path)
        try:
            assert actions[0]["pdf_path"] == src
        finally:
            print_planner.cleanup_temp_dir(temp)


# ─────────────────────────────────────────────────────────────────────────────
# When a job does ask
# ─────────────────────────────────────────────────────────────────────────────

class TestScaleIsBaked:

    @pytest.mark.parametrize("scale", [
        {"mode": "fit"},
        {"mode": "actual"},
        {"mode": "custom", "percent": 75},
        {"mode": "custom", "percent": 150},
    ])
    def test_portrait_1up_bakes_and_flags(self, scale, pdf, tmp_path, reports):
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1, "scale": scale}
        actions, temp = plan(pdf(((A5_W, A5_H),) * 2), spec, tmp_path)
        try:
            assert actions[0]["scale_applied"] is True
            assert os.path.exists(os.path.join(temp, "scaled.pdf"))
            assert reports == []
            doc = fitz.open(actions[0]["pdf_path"])
            try:
                assert doc[0].rect.width == pytest.approx(A4_W, abs=0.5)
            finally:
                doc.close()
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_actual_on_a_matching_page_stays_a_no_op(self, pdf, tmp_path):
        """Nothing to do — so nothing is done, and the flag stays down."""
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "scale": {"mode": "actual"}, "paper_size": "A4"}
        actions, temp = plan(pdf(), spec, tmp_path)
        try:
            assert actions[0]["scale_applied"] is False
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_the_scaled_file_feeds_the_mixed_colour_split(self, pdf, tmp_path):
        """Sub-jobs must be cut from the scaled PDF, not the original."""
        spec = {"sides": "simplex", "colour_mode": "mixed", "colour_pages": [2],
                "nup": 1, "scale": {"mode": "custom", "percent": 50}}
        actions, temp = plan(pdf(((A5_W, A5_H),) * 4), spec, tmp_path)
        try:
            assert len(actions) > 1
            assert all(a["scale_applied"] for a in actions)
            doc = fitz.open(actions[0]["pdf_path"])
            try:
                assert doc[0].rect.width == pytest.approx(A4_W, abs=0.5)
            finally:
                doc.close()
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_landscape_1up_goes_through_the_imposer(self, pdf, tmp_path, reports):
        """A small page fits a landscape A4 slot, so Actual is honoured whole
        and silently. Note A5 does NOT fit: turned sideways it is 210mm wide
        against a 196mm slot. See TestActualThatCannotFit."""
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "orientation": "landscape", "scale": {"mode": "actual"}}
        actions, temp = plan(pdf(sizes=((300, 400),) * 4), spec, tmp_path)
        try:
            assert actions[0]["scale_applied"] is True
            assert os.path.exists(os.path.join(temp, "imposed.pdf"))
            assert not os.path.exists(os.path.join(temp, "scaled.pdf"))
            assert reports == []
        finally:
            print_planner.cleanup_temp_dir(temp)


# ─────────────────────────────────────────────────────────────────────────────
# Actual size on a sheet that cannot hold it
# ─────────────────────────────────────────────────────────────────────────────
#
# Found in the 2026-09-03 acceptance run, Phase 2 (S7). "Actual" on a 1-up
# LANDSCAPE job placed the page at true size in a slot too small for it: an A4
# source is 297mm wide once turned and the slot is 210mm, so 43mm ran off each
# edge and the title and footer left the paper. Nothing was said.
#
# It was also a Rule 1 break. The same job with NO scale key fits correctly,
# and "actual" is supposed to MEAN no scaling — so adding the key changed a
# job that worked.

class TestActualThatCannotFit:

    def _ink_within_page(self, path):
        doc = fitz.open(path)
        try:
            for page in doc:
                boxes = [fitz.Rect(d["rect"]) for d in page.get_drawings()]
                boxes += [fitz.Rect(b[:4]) for b in page.get_text("blocks")]
                for b in boxes:
                    if (b.x0 < -1 or b.y0 < -1
                            or b.x1 > page.rect.width + 1
                            or b.y1 > page.rect.height + 1):
                        return False
            return True
        finally:
            doc.close()

    def test_no_content_leaves_the_paper(self, pdf, tmp_path, reports):
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "orientation": "landscape", "scale": {"mode": "actual"}}
        actions, temp = plan(pdf(), spec, tmp_path)
        try:
            assert self._ink_within_page(actions[0]["pdf_path"]), (
                "content is being drawn outside the sheet")
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_it_says_so_rather_than_quietly_shrinking(self, pdf, tmp_path, reports):
        """Printing smaller than "Actual size" promised is a wrong answer. It
        is the better wrong answer than losing the edges, but it is not one to
        give silently."""
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "orientation": "landscape", "scale": {"mode": "actual"}}
        actions, temp = plan(pdf(), spec, tmp_path)
        try:
            fired = [d for c, ok, d in reports
                     if c == "print_planner.scale_actual_landscape" and ok is False]
            assert fired, "the page was shrunk with nothing said"
            assert "Use Fit" in fired[0]
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_a_page_that_does_fit_is_left_at_true_size(self, pdf, tmp_path, reports):
        """The fix must not turn every landscape Actual into a Fit. A page
        small enough to sit in the turned slot is exactly what Actual size is
        for, and it must still come out at true size."""
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "orientation": "landscape", "scale": {"mode": "actual"}}
        actions, temp = plan(pdf(sizes=((300, 400),) * 2), spec, tmp_path)
        try:
            assert [c for c, _, _ in reports
                    if c == "print_planner.scale_actual_landscape"] == []
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_absent_and_actual_now_agree_again(self, pdf, tmp_path, reports):
        """Rule 1. "actual" means no scaling, so it must land where a job with
        no scale key lands — which is what broke."""
        base = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "orientation": "landscape"}
        src = pdf()
        a, t1 = plan(src, base, tmp_path)
        b, t2 = plan(src, {**base, "scale": {"mode": "actual"}}, tmp_path)
        try:
            assert sheets(a[0]["pdf_path"]) == sheets(b[0]["pdf_path"])
        finally:
            print_planner.cleanup_temp_dir(t1)
            print_planner.cleanup_temp_dir(t2)


# ─────────────────────────────────────────────────────────────────────────────
# Dropped loudly, never half-applied
# ─────────────────────────────────────────────────────────────────────────────

class TestDroppedCombinationsAlert:

    @pytest.mark.parametrize("nup", [2, 4, 6, 9])
    def test_scale_on_nup_is_ignored_and_alerts(self, nup, pdf, tmp_path, reports):
        spec = {"sides": "duplex", "colour_mode": "bw", "nup": nup,
                "scale": {"mode": "custom", "percent": 50}}
        actions, temp = plan(pdf(), spec, tmp_path)
        try:
            assert all(a["scale_applied"] is False for a in actions)
            assert not os.path.exists(os.path.join(temp, "scaled.pdf"))
            assert [c for c, ok, _ in reports if c == "print_planner.scale_on_nup"]
            assert all(ok is False for _, ok, _ in reports)
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_custom_on_landscape_is_ignored_and_alerts(self, pdf, tmp_path, reports):
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "orientation": "landscape", "scale": {"mode": "custom", "percent": 60}}
        actions, temp = plan(pdf(), spec, tmp_path)
        try:
            assert actions[0]["scale_applied"] is False
            assert [c for c, _, _ in reports if c == "print_planner.scale_custom_landscape"]
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_an_unknown_mode_alerts(self, pdf, tmp_path, reports):
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "scale": {"mode": "shrink-to-fit"}}
        actions, temp = plan(pdf(), spec, tmp_path)
        try:
            assert actions[0]["scale_applied"] is False
            assert [c for c, _, _ in reports if c == "print_planner.scale_unknown_mode"]
        finally:
            print_planner.cleanup_temp_dir(temp)

    def test_a_junk_percent_alerts(self, pdf, tmp_path, reports):
        spec = {"sides": "simplex", "colour_mode": "bw", "nup": 1,
                "scale": {"mode": "custom", "percent": "big"}}
        actions, temp = plan(pdf(), spec, tmp_path)
        try:
            assert actions[0]["scale_applied"] is False
            assert [c for c, _, _ in reports if c == "print_planner.scale_bad_percent"]
        finally:
            print_planner.cleanup_temp_dir(temp)


class TestResolveScale:
    """The resolver on its own — the previews will read it too."""

    def test_absent_scale(self):
        assert print_planner.resolve_scale({}, 1, "portrait") == (None, None)

    def test_percent_is_clamped_not_rejected(self, reports):
        assert print_planner.resolve_scale(
            {"scale": {"mode": "custom", "percent": 900}}, 1, "portrait"
        ) == ("custom", pdf_scaler.MAX_PERCENT)

    def test_fit_and_actual_carry_no_percent(self):
        for mode in ("fit", "actual"):
            assert print_planner.resolve_scale(
                {"scale": {"mode": mode, "percent": 50}}, 1, "portrait") == (mode, None)
