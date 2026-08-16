import os
import types

import pytest
import fitz  # PyMuPDF
import print_planner
import nup_imposer

@pytest.fixture
def temp_pdf(tmp_path):
    """Generate a dummy 10-page PDF for testing."""
    pdf_path = os.path.join(tmp_path, "test.pdf")
    doc = fitz.open()
    for i in range(10):
        page = doc.new_page(width=595.28, height=841.89)  # A4 Portrait
        page.insert_text((50, 50), f"Page {i+1}")
    doc.save(pdf_path)
    doc.close()
    return pdf_path

def test_fallback_no_spec(temp_pdf, tmp_path):
    """If spec is empty or None, return single action using fallback/defaults."""
    actions, temp_dir = print_planner.plan_print_job("J_FALLBACK", temp_pdf, None, str(tmp_path))
    assert len(actions) == 1
    assert actions[0]["pdf_path"] == temp_pdf
    assert actions[0]["colour_mode"] == "auto"
    assert actions[0]["copies"] == 1
    assert temp_dir is None

def test_slice_pages_included(temp_pdf, tmp_path):
    """If pages_included is specified, slice the PDF to those pages."""
    spec = {
        "pages_included": [1, 3, 5],
        "copies": 2,
        "paper_size": "A4",
        "colour_mode": "bw",
        "sides": "simplex"
    }
    actions, temp_dir = print_planner.plan_print_job("J_SLICE", temp_pdf, spec, str(tmp_path))
    assert len(actions) == 1
    assert temp_dir is not None
    assert os.path.exists(actions[0]["pdf_path"])
    
    # Verify the sliced PDF has exactly 3 pages
    doc = fitz.open(actions[0]["pdf_path"])
    assert len(doc) == 3
    # First page should contain text "Page 1", second page "Page 3", etc.
    assert "Page 1" in doc[0].get_text()
    assert "Page 3" in doc[1].get_text()
    assert "Page 5" in doc[2].get_text()
    doc.close()
    
    print_planner.cleanup_temp_dir(temp_dir)
    assert not os.path.exists(temp_dir)

def test_nup_imposition(temp_pdf, tmp_path):
    """If nup > 1, pre-impose the pages and force page-fit settings."""
    spec = {
        "nup": 2,
        "paper_size": "A4",
        "orientation": "portrait",
        "sides": "simplex"
    }
    actions, temp_dir = print_planner.plan_print_job("J_NUP", temp_pdf, spec, str(tmp_path))
    assert len(actions) == 1
    assert temp_dir is not None
    # The imposed PDF already carries the final (landscape) geometry for 2-up, so
    # the action must NOT also carry an orientation flag — passing one made
    # SumatraPDF re-rotate the sheet (landscape 2-up printed portrait). None here
    # means "print the imposed page as-is".
    assert actions[0]["orientation"] is None
    
    # 10 pages in 2-up -> 5 sheets
    doc = fitz.open(actions[0]["pdf_path"])
    assert len(doc) == 5
    doc.close()
    
    print_planner.cleanup_temp_dir(temp_dir)

def test_mixed_colour_simplex_splitting(temp_pdf, tmp_path):
    """Mixed colour simplex splits by consecutive colour page sections."""
    spec = {
        "colour_mode": "mixed",
        "colour_pages": [2, 5],  # pages 2 and 5 are color (1-based)
        "sides": "simplex"
    }
    actions, temp_dir = print_planner.plan_print_job("J_MIX_SIM", temp_pdf, spec, str(tmp_path))
    assert len(actions) == 5
    assert temp_dir is not None
    
    # Action 0: B&W (page 1)
    assert actions[0]["colour_mode"] == "bw"
    doc = fitz.open(actions[0]["pdf_path"])
    assert len(doc) == 1
    assert "Page 1" in doc[0].get_text()
    doc.close()

    # Action 1: Colour (page 2)
    assert actions[1]["colour_mode"] == "colour"
    doc = fitz.open(actions[1]["pdf_path"])
    assert len(doc) == 1
    assert "Page 2" in doc[0].get_text()
    doc.close()

    # Action 2: B&W (pages 3, 4)
    assert actions[2]["colour_mode"] == "bw"
    doc = fitz.open(actions[2]["pdf_path"])
    assert len(doc) == 2
    assert "Page 3" in doc[0].get_text()
    assert "Page 4" in doc[1].get_text()
    doc.close()

    # Action 3: Colour (page 5)
    assert actions[3]["colour_mode"] == "colour"
    doc = fitz.open(actions[3]["pdf_path"])
    assert len(doc) == 1
    assert "Page 5" in doc[0].get_text()
    doc.close()

    # Action 4: B&W (pages 6-10)
    assert actions[4]["colour_mode"] == "bw"
    doc = fitz.open(actions[4]["pdf_path"])
    assert len(doc) == 5
    assert "Page 6" in doc[0].get_text()
    doc.close()
    
    print_planner.cleanup_temp_dir(temp_dir)

def test_mixed_colour_duplex_splitting(temp_pdf, tmp_path):
    """Mixed colour duplex splits by sheet pair boundaries to preserve duplex alignment."""
    spec = {
        "colour_mode": "mixed",
        "colour_pages": [3],  # only page 3 is color. Pair [3, 4] is Sheet 2.
        "sides": "duplex"
    }
    actions, temp_dir = print_planner.plan_print_job("J_MIX_DUP", temp_pdf, spec, str(tmp_path))
    assert len(actions) == 3
    assert temp_dir is not None
    
    # Sheet 1: [1, 2] -> B&W
    # Sheet 2: [3, 4] -> Colour (because page 3 is color)
    # Sheet 3: [5, 6] -> B&W
    # Sheet 4: [7, 8] -> B&W
    # Sheet 5: [9, 10] -> B&W
    
    # Section 1: B&W sheets 1 (pages 1, 2)
    assert actions[0]["colour_mode"] == "bw"
    assert actions[0]["sides"] == "ds"
    doc = fitz.open(actions[0]["pdf_path"])
    assert len(doc) == 2
    assert "Page 1" in doc[0].get_text()
    assert "Page 2" in doc[1].get_text()
    doc.close()

    # Section 2: Colour sheet 2 (pages 3, 4)
    assert actions[1]["colour_mode"] == "colour"
    assert actions[1]["sides"] == "ds"
    doc = fitz.open(actions[1]["pdf_path"])
    assert len(doc) == 2
    assert "Page 3" in doc[0].get_text()
    assert "Page 4" in doc[1].get_text()
    doc.close()

    # Section 3: B&W sheets 3, 4, 5 (pages 5, 6, 7, 8, 9, 10)
    assert actions[2]["colour_mode"] == "bw"
    assert actions[2]["sides"] == "ds"
    doc = fitz.open(actions[2]["pdf_path"])
    assert len(doc) == 6
    assert "Page 5" in doc[0].get_text()
    doc.close()
    
    print_planner.cleanup_temp_dir(temp_dir)

def test_mixed_colour_imposition_duplex(temp_pdf, tmp_path):
    """Test mixed colour combined with N-up imposition and duplex."""
    spec = {
        "nup": 2,
        "colour_mode": "mixed",
        "colour_pages": [1],  # logical page 1 is colour.
        "sides": "duplex"
    }
    actions, temp_dir = print_planner.plan_print_job("J_MIX_NUP_DUP", temp_pdf, spec, str(tmp_path))
    assert len(actions) == 2
    
    # Logical page 1 (0-based page 0) is colour.
    # Imposition (Sequential):
    # 2-up duplex:
    # Sheet 1:
    #   - Front (imposed page 1): contains logical pages 0 and 1. (Logical page 0 is colour!)
    #   - Back (imposed page 2): contains logical pages 2 and 3.
    # Therefore imposed Sheet 1 (pages 1-2 of imposed PDF) is Colour.
    # Other sheets have no colour pages, so they are B&W.
    
    # Colour sub-job first: sheet 1 (imposed pages 1, 2)
    assert actions[0]["colour_mode"] == "colour"
    doc = fitz.open(actions[0]["pdf_path"])
    assert len(doc) == 2
    doc.close()

    # B&W sub-job: sheets 2, 3 (imposed pages 3, 4, 5, 6)
    assert actions[1]["colour_mode"] == "bw"
    doc = fitz.open(actions[1]["pdf_path"])
    assert len(doc) == 4
    doc.close()
    
    print_planner.cleanup_temp_dir(temp_dir)


# ── Duplex back rotation (replaces the binding-edge model) ───────────────────
# The printer is told plain portrait duplex and nothing else. The one thing the
# PDF cannot decide by itself — whether the duplex unit lays the back image down
# turned 180 — is a single measured constant applied identically everywhere.

@pytest.mark.parametrize("nup,direction", [
    (2, "horizontal"), (2, "vertical"), (4, "horizontal"),
    (6, "horizontal"), (9, "horizontal"),
])
def test_planner_passes_the_configured_back_rotation(temp_pdf, tmp_path,
                                                     monkeypatch, nup, direction):
    import store_config
    seen = {}
    real = nup_imposer.perform_nup

    def spy(*args, **kwargs):
        seen["back_rotation"] = kwargs.get("back_rotation")
        return real(*args, **kwargs)

    monkeypatch.setattr(print_planner.nup_imposer, "perform_nup", spy)
    monkeypatch.setattr(
        print_planner, "get_store_config",
        lambda: types.SimpleNamespace(duplex_back_rotation=180))

    spec = {"nup": nup, "nup_direction": direction, "sides": "duplex",
            "colour_mode": "bw", "paper_size": "A4"}
    actions, temp_dir = print_planner.plan_print_job(
        f"J_ROT_{nup}{direction}", temp_pdf, spec, str(tmp_path))

    assert seen["back_rotation"] == 180
    # The printer instruction never varies, whatever the layout.
    assert actions[0]["sides"] == "ds"
    assert actions[0]["orientation"] is None
    print_planner.cleanup_temp_dir(temp_dir)


def test_planner_survives_an_unreadable_store_config(temp_pdf, tmp_path, monkeypatch):
    """A missing or broken config must not stop a print — it falls back to 0."""
    seen = {}
    real = nup_imposer.perform_nup

    def spy(*args, **kwargs):
        seen["back_rotation"] = kwargs.get("back_rotation")
        return real(*args, **kwargs)

    def boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(print_planner.nup_imposer, "perform_nup", spy)
    monkeypatch.setattr(print_planner, "get_store_config", boom)

    spec = {"nup": 2, "sides": "duplex", "colour_mode": "bw", "paper_size": "A4"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_ROT_NOCFG", temp_pdf, spec, str(tmp_path))

    assert seen["back_rotation"] == 0
    assert actions[0]["sides"] == "ds"
    print_planner.cleanup_temp_dir(temp_dir)


def test_simplex_nup_is_not_given_a_back_rotation(temp_pdf, tmp_path, monkeypatch):
    seen = {}
    real = nup_imposer.perform_nup

    def spy(*args, **kwargs):
        seen["is_duplex"] = kwargs.get("is_duplex")
        return real(*args, **kwargs)

    monkeypatch.setattr(print_planner.nup_imposer, "perform_nup", spy)

    spec = {"nup": 4, "sides": "simplex", "colour_mode": "bw", "paper_size": "A4"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_EDGE_SIMPLEX", temp_pdf, spec, str(tmp_path))

    assert seen["is_duplex"] is False
    assert actions[0]["sides"] == "ss"
    print_planner.cleanup_temp_dir(temp_dir)


def test_landscape_nup_no_longer_forces_short_edge(temp_pdf, tmp_path):
    """Regression: forcing duplexshort on landscape N-up made the driver rotate
    the back image 180 degrees, which cancelled the imposer's column mirroring
    (right page order) but printed every back sheet upside down."""
    spec = {"nup": 2, "nup_direction": "horizontal", "sides": "duplex",
            "colour_mode": "bw", "paper_size": "A4"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_NO_SHORT", temp_pdf, spec, str(tmp_path))

    assert actions[0]["sides"] != "duplexshort"
    assert actions[0]["sides"] == "ds"
    print_planner.cleanup_temp_dir(temp_dir)


# ── Portrait canvas rule ──────────────────────────────────────────────────────
# Every imposed sheet is portrait. "Long edge" and "short edge" are defined
# relative to the sheet's own aspect, so a landscape sheet inverts their meaning
# and every duplex decision has to be re-derived — which is where back sheets
# kept coming out flipped on the Konica. Portrait sheets make the long edge
# unambiguously vertical, so duplexlong is always the plain book flip.

@pytest.mark.parametrize("nup,direction", [
    (2, "horizontal"), (2, "vertical"), (4, "horizontal"),
    (6, "horizontal"), (9, "horizontal"),
])
def test_every_imposed_sheet_is_portrait(temp_pdf, tmp_path, nup, direction):
    spec = {"nup": nup, "nup_direction": direction, "sides": "duplex",
            "colour_mode": "bw", "paper_size": "A4"}
    actions, temp_dir = print_planner.plan_print_job(
        f"J_PORTRAIT_{nup}{direction}", temp_pdf, spec, str(tmp_path))

    with fitz.open(actions[0]["pdf_path"]) as doc:
        for i, page in enumerate(doc):
            assert page.rect.height > page.rect.width, (
                f"{nup}-up {direction}: sheet {i + 1} is landscape "
                f"({page.rect.width:.0f}x{page.rect.height:.0f})")
    print_planner.cleanup_temp_dir(temp_dir)


@pytest.mark.parametrize("nup,direction", [
    (2, "horizontal"), (2, "vertical"), (4, "horizontal"),
    (6, "horizontal"), (9, "horizontal"),
])
def test_no_layout_asks_for_short_edge(temp_pdf, tmp_path, nup, direction):
    """Short-edge mode makes the driver rotate the back image 180 degrees."""
    spec = {"nup": nup, "nup_direction": direction, "sides": "duplex",
            "colour_mode": "bw", "paper_size": "A4"}
    actions, temp_dir = print_planner.plan_print_job(
        f"J_LONGEDGE_{nup}{direction}", temp_pdf, spec, str(tmp_path))

    assert actions[0]["sides"] == "ds"
    print_planner.cleanup_temp_dir(temp_dir)


def test_both_2up_directions_produce_the_same_sheet(temp_pdf, tmp_path):
    """Under the portrait-canvas rule the two 2-up directions converge: both
    are a 1x2 portrait sheet read with a quarter turn. They only differed
    because "horizontal" meant "emit a landscape sheet"."""
    out = {}
    for direction in ("horizontal", "vertical"):
        spec = {"nup": 2, "nup_direction": direction, "sides": "duplex",
                "colour_mode": "bw", "paper_size": "A4"}
        actions, temp_dir = print_planner.plan_print_job(
            f"J_2UP_{direction}", temp_pdf, spec, str(tmp_path / direction))
        with fitz.open(actions[0]["pdf_path"]) as doc:
            out[direction] = (len(doc), doc[0].rect.width, doc[0].rect.height)
        print_planner.cleanup_temp_dir(temp_dir)

    assert out["horizontal"] == out["vertical"]
