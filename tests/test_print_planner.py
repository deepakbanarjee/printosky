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

def test_every_imposed_sheet_leaves_as_portrait(temp_pdf, tmp_path):
    """Whatever the customer picks, the printer gets portrait duplex."""
    for nup in (1, 2, 4, 6, 9):
        for orientation in ("portrait", "landscape"):
            actions, temp_dir = print_planner.plan_print_job(
                f"J_ORIENT_{nup}_{orientation}", temp_pdf,
                {"nup": nup, "orientation": orientation, "sides": "duplex"},
                str(tmp_path))
            doc = fitz.open(actions[0]["pdf_path"])
            rect = doc[0].rect
            doc.close()
            assert rect.width < rect.height, (
                f"{nup}-up {orientation} left as {rect.width:.0f}x{rect.height:.0f}pt")
            if actions[0]["pdf_path"] != temp_pdf:
                # An imposed sheet already carries its final geometry, so the
                # printer must not be handed an orientation flag as well.
                assert actions[0]["orientation"] is None
            print_planner.cleanup_temp_dir(temp_dir)


def test_landscape_nup_still_binds_long_edge(temp_pdf, tmp_path):
    """The printer is told `ds` (long edge) for every layout.

    The landscape back-side correction is a 180-degree rigid turn baked into
    the imposition. Asking the driver for short-edge as well would apply that
    turn a second time and cancel it out.
    """
    actions, temp_dir = print_planner.plan_print_job(
        "J_LONG_EDGE", temp_pdf,
        {"nup": 2, "orientation": "landscape", "sides": "duplex"}, str(tmp_path))
    assert actions[0]["sides"] == "ds"
    print_planner.cleanup_temp_dir(temp_dir)


def test_single_page_landscape_is_imposed_but_portrait_is_not(temp_pdf, tmp_path):
    """1-up landscape turns the page 90/-90; 1-up portrait is a pass-through."""
    actions, temp_dir = print_planner.plan_print_job(
        "J_1UP_LAND", temp_pdf,
        {"nup": 1, "orientation": "landscape", "sides": "duplex"}, str(tmp_path))
    assert actions[0]["pdf_path"] != temp_pdf
    doc = fitz.open(actions[0]["pdf_path"])
    assert doc[0].rect.width < doc[0].rect.height  # portrait sheet, turned content
    doc.close()
    print_planner.cleanup_temp_dir(temp_dir)

    actions, temp_dir = print_planner.plan_print_job(
        "J_1UP_PORT", temp_pdf,
        {"nup": 1, "orientation": "portrait", "sides": "duplex"}, str(tmp_path))
    assert actions[0]["pdf_path"] == temp_pdf  # untouched
    print_planner.cleanup_temp_dir(temp_dir)


def test_two_up_is_stacked_on_a_portrait_sheet(temp_pdf, tmp_path):
    """2-up is 1x2 on portrait paper whichever fill direction is asked for."""
    for direction in ("horizontal", "vertical"):
        actions, temp_dir = print_planner.plan_print_job(
            f"J_2UP_{direction}", temp_pdf,
            {"nup": 2, "nup_direction": direction, "sides": "duplex"}, str(tmp_path))
        doc = fitz.open(actions[0]["pdf_path"])
        assert doc[0].rect.width < doc[0].rect.height
        doc.close()
        print_planner.cleanup_temp_dir(temp_dir)
