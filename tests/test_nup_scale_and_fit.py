"""Scale modes (fit / actual / shrink / custom) and the print-area fit check."""

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

import nup_imposer  # noqa: E402
import print_planner  # noqa: E402


A4_W, A4_H = 595.28, 841.89
A3_W, A3_H = 841.89, 1190.55


# ── resolve_scale ─────────────────────────────────────────────────────────────

def test_fit_scales_up_a_small_page_to_the_slot():
    w, h, f = nup_imposer.resolve_scale("fit", 100, 100, 400, 400)
    assert (round(w), round(h)) == (400, 400)
    assert f == pytest.approx(4.0)


def test_fit_scales_down_an_oversized_page():
    _, _, f = nup_imposer.resolve_scale("fit", 800, 800, 400, 400)
    assert f == pytest.approx(0.5)


def test_actual_never_scales():
    w, h, f = nup_imposer.resolve_scale("actual", 800, 900, 400, 400)
    assert (w, h, f) == (800, 900, 1.0)


def test_shrink_scales_down_but_never_up():
    _, _, big = nup_imposer.resolve_scale("shrink", 800, 800, 400, 400)
    assert big == pytest.approx(0.5)
    _, _, small = nup_imposer.resolve_scale("shrink", 100, 100, 400, 400)
    assert small == 1.0, "shrink must not enlarge a page that already fits"


def test_custom_applies_the_percentage():
    w, h, f = nup_imposer.resolve_scale("custom", 200, 400, 999, 999, scale_percent=50)
    assert (w, h) == (100, 200)
    assert f == pytest.approx(0.5)


def test_custom_rejects_zero_and_negative():
    _, _, f = nup_imposer.resolve_scale("custom", 200, 200, 400, 400, scale_percent=0)
    assert f > 0


def test_unknown_mode_falls_back_to_fit():
    _, _, unknown = nup_imposer.resolve_scale("wibble", 800, 800, 400, 400)
    _, _, fit = nup_imposer.resolve_scale("fit", 800, 800, 400, 400)
    assert unknown == fit


def test_fit_without_aspect_stretches_to_the_slot():
    w, h, f = nup_imposer.resolve_scale("fit", 100, 400, 400, 400, maintain_aspect=False)
    assert (w, h) == (400, 400)
    assert f is None


# ── check_fit ─────────────────────────────────────────────────────────────────

def test_a4_page_on_a4_slot_actual_size_fits():
    r = nup_imposer.check_fit(A4_W, A4_H, A4_W, A4_H, scale_mode="actual")
    assert r["fits"]
    assert r["overflow_pct"] == 0.0


def test_a3_page_at_actual_size_on_a4_overflows():
    r = nup_imposer.check_fit(A3_W, A3_H, A4_W, A4_H, scale_mode="actual")
    assert not r["fits"]
    assert r["overflow_pct"] > 40  # A3 is ~141% of A4 on each axis
    assert r["factor"] == 1.0


def test_the_same_a3_page_fits_under_fit_and_shrink():
    for mode in ("fit", "shrink"):
        r = nup_imposer.check_fit(A3_W, A3_H, A4_W, A4_H, scale_mode=mode)
        assert r["fits"], f"{mode} should fit an A3 page onto an A4 slot"
        assert r["factor"] < 1.0


def test_custom_over_100_percent_overflows():
    r = nup_imposer.check_fit(A4_W, A4_H, A4_W, A4_H,
                              scale_mode="custom", scale_percent=150)
    assert not r["fits"]
    assert r["overflow_pct"] == pytest.approx(50.0, abs=0.5)


def test_fit_check_uses_the_same_no_rotate_portraits_rule():
    """A portrait page is never turned, so it is measured upright — and against
    a landscape slot that means it really does overflow on height. The check
    must agree with the imposer or the customer is warned about the wrong thing."""
    r = nup_imposer.check_fit(A4_W, A4_H, A4_H, A4_W, scale_mode="actual")
    assert not r["fits"], "portrait page measured as if it had been turned"

    # A landscape source into a portrait slot still turns, so it fits.
    r_land = nup_imposer.check_fit(A4_H, A4_W, A4_W, A4_H, scale_mode="actual")
    assert r_land["fits"]


# ── end to end through the planner ────────────────────────────────────────────

@pytest.fixture
def a3_pdf(tmp_path):
    path = str(tmp_path / "a3.pdf")
    doc = fitz.open()
    for i in range(2):
        pg = doc.new_page(width=A3_W, height=A3_H)
        pg.insert_text((100, 100), f"Page {i + 1}", fontsize=48)
    doc.save(path)
    doc.close()
    return path


def test_planner_warns_when_actual_size_overflows(a3_pdf, tmp_path):
    spec = {"nup": 1, "scale_mode": "actual", "paper_size": "A4",
            "colour_mode": "bw", "sides": "simplex"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_OVERFLOW", a3_pdf, spec, str(tmp_path))

    warning = actions[0]["print_area_warning"]
    assert warning is not None, "A3 at actual size on A4 must warn"
    assert not warning["fits"]
    assert warning["overflow_pct"] > 0
    print_planner.cleanup_temp_dir(temp_dir)


def test_planner_does_not_warn_when_shrinking_to_fit(a3_pdf, tmp_path):
    spec = {"nup": 1, "scale_mode": "shrink", "paper_size": "A4",
            "colour_mode": "bw", "sides": "simplex"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_SHRINK", a3_pdf, spec, str(tmp_path))

    assert actions[0]["print_area_warning"] is None
    print_planner.cleanup_temp_dir(temp_dir)


def test_one_up_non_default_scale_still_gets_imposed(a3_pdf, tmp_path):
    """1-up normally skips imposition — a scale choice must not be dropped."""
    spec = {"nup": 1, "scale_mode": "custom", "scale_percent": 50,
            "paper_size": "A4", "colour_mode": "bw", "sides": "simplex"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_1UP_SCALE", a3_pdf, spec, str(tmp_path))

    assert actions[0]["pdf_path"] != a3_pdf, "scale pass did not run"
    with fitz.open(actions[0]["pdf_path"]) as doc:
        assert doc[0].rect.width == pytest.approx(A4_W, abs=1)
    print_planner.cleanup_temp_dir(temp_dir)


def test_one_up_default_scale_skips_imposition(a3_pdf, tmp_path):
    """The default path must stay untouched — no needless re-render."""
    spec = {"nup": 1, "scale_mode": "fit", "paper_size": "A4",
            "colour_mode": "bw", "sides": "simplex"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_1UP_FIT", a3_pdf, spec, str(tmp_path))

    assert actions[0]["pdf_path"] == a3_pdf
    print_planner.cleanup_temp_dir(temp_dir)


def test_invalid_scale_mode_falls_back_to_fit(a3_pdf, tmp_path):
    spec = {"nup": 1, "scale_mode": "nonsense", "paper_size": "A4",
            "colour_mode": "bw", "sides": "simplex"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_BAD_SCALE", a3_pdf, spec, str(tmp_path))

    assert actions[0]["pdf_path"] == a3_pdf  # treated as "fit" -> no scale pass
    print_planner.cleanup_temp_dir(temp_dir)
