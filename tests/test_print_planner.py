import os
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


# ── Binding-edge sync (guards the duplex double-flip fix) ─────────────────────
# The imposer reverses back-sheet slots along the axis the sheet is flipped
# about, so the edge it imposes for MUST be the edge the printer is told to
# bind on. If these drift apart the back sheets come out mirrored.

@pytest.mark.parametrize("nup,direction,expected_sides,expected_edge", [
    (2, "horizontal", "duplexshort", "short"),   # 2x1 landscape sheet
    (2, "vertical",   "ds",          "long"),    # 1x2 portrait sheet
    (4, "horizontal", "ds",          "long"),    # 2x2 portrait sheet
    (6, "horizontal", "duplexshort", "short"),   # 3x2 landscape sheet
    (9, "horizontal", "ds",          "long"),    # 3x3 portrait sheet
])
def test_binding_edge_matches_duplex_mode(temp_pdf, tmp_path, monkeypatch,
                                          nup, direction, expected_sides,
                                          expected_edge):
    seen = {}
    real = nup_imposer.perform_nup

    def spy(*args, **kwargs):
        seen["binding_edge"] = kwargs.get("binding_edge")
        return real(*args, **kwargs)

    monkeypatch.setattr(print_planner.nup_imposer, "perform_nup", spy)

    spec = {"nup": nup, "nup_direction": direction, "sides": "duplex",
            "colour_mode": "bw", "paper_size": "A4"}
    actions, temp_dir = print_planner.plan_print_job(
        f"J_EDGE_{nup}{direction}", temp_pdf, spec, str(tmp_path))

    assert seen["binding_edge"] == expected_edge
    assert actions[0]["sides"] == expected_sides
    print_planner.cleanup_temp_dir(temp_dir)


def test_simplex_nup_does_not_request_a_binding_edge(temp_pdf, tmp_path, monkeypatch):
    seen = {}
    real = nup_imposer.perform_nup

    def spy(*args, **kwargs):
        seen["is_duplex"] = kwargs.get("is_duplex")
        seen["binding_edge"] = kwargs.get("binding_edge")
        return real(*args, **kwargs)

    monkeypatch.setattr(print_planner.nup_imposer, "perform_nup", spy)

    spec = {"nup": 4, "sides": "simplex", "colour_mode": "bw", "paper_size": "A4"}
    actions, temp_dir = print_planner.plan_print_job(
        "J_EDGE_SIMPLEX", temp_pdf, spec, str(tmp_path))

    assert seen["is_duplex"] is False
    assert actions[0]["sides"] == "ss"
    print_planner.cleanup_temp_dir(temp_dir)
