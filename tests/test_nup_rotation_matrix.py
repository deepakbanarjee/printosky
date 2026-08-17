"""The page-rotation matrix — every combination, locked down.

EVERY IMPOSED SHEET IS PORTRAIT. The whole document goes to the printer as
portrait duplex; "portrait"/"landscape" here name the LAYOUT the customer
chose, never the paper.

The owner's rules, stated in full:

* Choosing LANDSCAPE turns the back side 180°. Portrait stays at 0°.
* On SINGLE (1-up), landscape also turns the front 90°, so the back lands on
  -90° (90 + 180 = 270).

On N-up, landscape does not turn the content — a portrait document must read
without turning the sheet — which is why N-up fronts sit at 0° and their
landscape backs at exactly 180°. Both stated cases are pinned individually
below so a future refactor cannot quietly drift.
"""
import itertools

import fitz  # PyMuPDF

import pytest

import nup_imposer

NUPS = [1, 2, 4, 6, 9]
ORIENTATIONS = ["portrait", "landscape"]
DIRECTIONS = ["horizontal", "vertical"]

ALL_COMBINATIONS = list(itertools.product(NUPS, ORIENTATIONS, DIRECTIONS))


def rotations(nup, orientation, direction, side, pages=None, portrait_source=True):
    """Every rotation angle used on one sheet-side of sheet 1."""
    plan = nup_imposer.impose_plan(
        total_pages=pages if pages is not None else nup * 2,
        nup=nup, orientation=orientation, is_duplex=True, direction=direction,
        source_is_portrait=portrait_source,
    )
    face = next(s for s in plan if s["sheet"] == 1 and s["side"] == side)
    return {s["rotation"] for s in face["slots"] if s["page"] is not None}


# --------------------------------------------------------------------------
# The two rules, exactly as stated
# --------------------------------------------------------------------------

@pytest.mark.parametrize("nup", [2, 4, 6, 9])
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_nup_landscape_turns_the_back_180(nup, direction):
    """"For all landscape, rotate the back page 180 degrees. This is for N-up." """
    assert rotations(nup, "landscape", direction, "front") == {0}
    assert rotations(nup, "landscape", direction, "back") == {180}


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_single_landscape_turns_front_90_and_back_minus_90(direction):
    """"For single, landscape: front page 90 degrees, back page minus 90." """
    assert rotations(1, "landscape", direction, "front") == {90}
    assert rotations(1, "landscape", direction, "back") == {270}  # -90 mod 360


@pytest.mark.parametrize("nup", NUPS)
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_portrait_layouts_never_turn_the_back(nup, direction):
    """Nothing turns unless landscape was asked for."""
    assert rotations(nup, "portrait", direction, "front") == {0}
    assert rotations(nup, "portrait", direction, "back") == {0}


# --------------------------------------------------------------------------
# The law the two rules come from
# --------------------------------------------------------------------------

@pytest.mark.parametrize("nup,orientation,direction", ALL_COMBINATIONS)
def test_back_is_always_exactly_180_from_the_front(nup, orientation, direction):
    """The duplex unit plus the reader's flip is a rigid motion of the sheet.

    The only rigid motions mapping a rectangle onto itself are 0° and 180°, so
    the back can never be anything but front or front+180.
    """
    front = rotations(nup, orientation, direction, "front")
    back = rotations(nup, orientation, direction, "back")
    assert len(front) == 1 and len(back) == 1
    delta = (back.pop() - front.pop()) % 360
    assert delta in (0, 180)
    assert delta == nup_imposer.back_rotation(orientation)


@pytest.mark.parametrize("nup,orientation,direction", ALL_COMBINATIONS)
def test_back_side_is_a_rigid_turn_not_a_mirror(nup, orientation, direction):
    """A 180° back reverses BOTH slot axes. Reversing one alone is a mirror.

    Ink on paper cannot be mirrored, so column-only reversal (the old
    ``eff_col`` code) could never have been the right correction for a physical
    flip. This is the regression guard.
    """
    cols, rows = nup_imposer.sheet_grid(nup, orientation, direction)
    for col, row in itertools.product(range(cols), range(rows)):
        eff = nup_imposer.effective_slot(col, row, cols, rows, True, orientation)
        if nup_imposer.back_rotation(orientation) == 180:
            assert eff == ((cols - 1) - col, (rows - 1) - row)
        else:
            assert eff == (col, row)


@pytest.mark.parametrize("nup,orientation,direction", ALL_COMBINATIONS)
def test_every_page_lands_in_exactly_one_slot(nup, orientation, direction):
    """No slot collisions, no dropped pages, on either side."""
    cols, rows = nup_imposer.sheet_grid(nup, orientation, direction)
    plan = nup_imposer.impose_plan(total_pages=nup * 4, nup=nup,
                                   orientation=orientation, is_duplex=True,
                                   direction=direction)
    seen = []
    for side in plan:
        positions = [(s["col"], s["row"]) for s in side["slots"]]
        assert len(set(positions)) == cols * rows
        assert all(0 <= c < cols and 0 <= r < rows for c, r in positions)
        seen += [s["page"] for s in side["slots"] if s["page"] is not None]
    assert sorted(seen) == list(range(1, nup * 4 + 1))


# --------------------------------------------------------------------------
# Grid shapes and fill order
# --------------------------------------------------------------------------

@pytest.mark.parametrize("nup,expected", [
    (1, (1, 1)), (2, (1, 2)), (4, (2, 2)), (6, (2, 3)), (9, (3, 3)),
])
@pytest.mark.parametrize("orientation", ORIENTATIONS)
def test_grid_shape_is_the_same_on_the_portrait_sheet(nup, orientation, expected):
    """The sheet is always portrait, so a landscape layout uses the same grid.

    Transposing a landscape arrangement onto a portrait sheet swaps its axes,
    which lands back on the portrait grid.
    """
    assert nup_imposer.sheet_grid(nup, orientation) == expected


@pytest.mark.parametrize("nup,orientation,direction", ALL_COMBINATIONS)
def test_grid_holds_exactly_nup_slots(nup, orientation, direction):
    cols, rows = nup_imposer.sheet_grid(nup, orientation, direction)
    assert cols * rows == nup


def test_horizontal_fills_row_major_and_vertical_fills_column_major():
    """Direction changes the fill order of the slots. It never changes an angle."""
    horizontal = nup_imposer.impose_plan(6, nup=6, orientation="portrait",
                                         direction="horizontal")[0]["slots"]
    vertical = nup_imposer.impose_plan(6, nup=6, orientation="portrait",
                                       direction="vertical")[0]["slots"]
    # 2 cols x 3 rows. Row-major: 1 2 / 3 4 / 5 6. Column-major: 1 4 / 2 5 / 3 6.
    assert [(s["col"], s["row"], s["page"]) for s in horizontal] == [
        (0, 0, 1), (1, 0, 2), (0, 1, 3), (1, 1, 4), (0, 2, 5), (1, 2, 6)]
    assert [(s["col"], s["row"], s["page"]) for s in vertical] == [
        (0, 0, 1), (0, 1, 2), (0, 2, 3), (1, 0, 4), (1, 1, 5), (1, 2, 6)]


@pytest.mark.parametrize("nup,orientation,direction", ALL_COMBINATIONS)
def test_direction_never_changes_a_rotation(nup, orientation, direction):
    assert (rotations(nup, orientation, direction, "front")
            == rotations(nup, orientation, "horizontal", "front"))
    assert (rotations(nup, orientation, direction, "back")
            == rotations(nup, orientation, "horizontal", "back"))


# --------------------------------------------------------------------------
# Orientation resolution
# --------------------------------------------------------------------------

@pytest.mark.parametrize("nup", NUPS)
@pytest.mark.parametrize("direction", DIRECTIONS)
def test_auto_orientation_is_portrait(nup, direction):
    """Nothing turns unless someone asks for it."""
    assert nup_imposer.resolve_orientation(nup, "auto", direction) == "portrait"
    assert nup_imposer.resolve_orientation(nup, None, direction) == "portrait"


@pytest.mark.parametrize("nup,orientation,direction", ALL_COMBINATIONS)
def test_explicit_orientation_always_wins(nup, orientation, direction):
    assert nup_imposer.resolve_orientation(nup, orientation, direction) == orientation
    assert nup_imposer.resolve_orientation(nup, orientation.upper(), direction) == orientation


# --------------------------------------------------------------------------
# Fit rotation on its own
# --------------------------------------------------------------------------

def test_a_portrait_page_never_turns_into_an_n_up_slot():
    """Owner's rule: a portrait document reads without turning the sheet."""
    assert nup_imposer.fit_rotation(page_is_portrait=True, slot_is_landscape=True,
                                    is_single_slot=False) == 0


def test_a_landscape_source_still_turns_into_a_portrait_slot():
    """Left upright it would be squeezed smaller and be harder to read."""
    assert nup_imposer.fit_rotation(page_is_portrait=False, slot_is_landscape=False,
                                    is_single_slot=False) == 90


def test_a_page_that_already_matches_its_slot_does_not_turn():
    assert nup_imposer.fit_rotation(True, False, False) == 0
    assert nup_imposer.fit_rotation(False, True, False) == 0


def test_single_slot_honours_the_deliberate_landscape_choice():
    """1-up has no slot to fill — landscape is the customer asking for a turn."""
    assert nup_imposer.layout_rotation("landscape", is_single_slot=True) == 90
    assert nup_imposer.layout_rotation("portrait", is_single_slot=True) == 0


def test_n_up_landscape_follows_the_transpose_switch():
    expected = 90 if nup_imposer.TRANSPOSE_LANDSCAPE_NUP else 0
    assert nup_imposer.layout_rotation("landscape", is_single_slot=False) == expected


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------

@pytest.mark.parametrize("nup,orientation,direction", ALL_COMBINATIONS)
def test_duplex_always_produces_whole_sheets(nup, orientation, direction):
    """An odd tail pads with blanks rather than leaving a sheet half-imposed."""
    plan = nup_imposer.impose_plan(total_pages=nup + 1, nup=nup,
                                   orientation=orientation, is_duplex=True,
                                   direction=direction)
    assert len(plan) % 2 == 0
    assert [s["side"] for s in plan] == ["front", "back"] * (len(plan) // 2)


# --------------------------------------------------------------------------
# The imposed PDF really does what the plan says
# --------------------------------------------------------------------------

def _marked_pdf(pages, width=595.28, height=841.89):
    """A PDF whose every page carries its number near the TOP-LEFT corner."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((40, 60), f"P{i + 1}", fontsize=40)
    data = doc.tobytes()
    doc.close()
    return data


def _marker_positions(page):
    """{"P1": (x, y)} for every marker drawn on an imposed sheet-side."""
    return {w[4]: (w[0], w[1]) for w in page.get_text("words")}


def test_every_imposed_sheet_is_portrait():
    """The whole document goes to the printer as portrait duplex, always."""
    for nup, (cols, rows) in nup_imposer.SHEET_GRIDS.items():
        for orientation in ORIENTATIONS:
            imposed = nup_imposer.perform_nup(_marked_pdf(nup * 2), cols=cols,
                                              rows=rows, orientation=orientation,
                                              is_duplex=True)
            doc = fitz.open("pdf", imposed.getvalue())
            try:
                rect = doc[0].rect
                assert rect.width < rect.height, (
                    f"{nup}-up {orientation} produced a "
                    f"{rect.width:.0f}x{rect.height:.0f}pt sheet")
            finally:
                doc.close()


def test_imposed_pdf_matches_the_plan_for_landscape_two_up():
    """2-up landscape duplex on paper: back slots reversed AND content turned.

    A marker drawn at the top-left of a source page must come out at the
    bottom-right of its slot on the back — both axes reversed, content turned
    with them. Bottom-right alone would be a mirror.
    """
    imposed = nup_imposer.perform_nup(_marked_pdf(4), cols=1, rows=2,
                                      orientation="landscape", is_duplex=True)
    doc = fitz.open("pdf", imposed.getvalue())
    try:
        assert len(doc) == 2  # one sheet, two sides
        sheet_w, sheet_h = doc[0].rect.width, doc[0].rect.height
        assert sheet_w < sheet_h  # portrait sheet, always

        front = _marker_positions(doc[0])
        back = _marker_positions(doc[1])
        assert set(front) == {"P1", "P2"} and set(back) == {"P3", "P4"}

        # Front: pages 1,2 top to bottom.
        assert front["P1"][1] < sheet_h / 2 < front["P2"][1]

        # Back: turned 180 — page 3 moves to the BOTTOM slot, page 4 to the
        # top, and both markers cross to the right of the sheet.
        assert back["P4"][1] < sheet_h / 2 < back["P3"][1]
        assert back["P3"][0] > sheet_w / 2 and back["P4"][0] > sheet_w / 2
    finally:
        doc.close()


def test_imposed_pdf_leaves_a_portrait_back_side_alone():
    """Portrait sheet, back turn 0: same slot order, same upright content."""
    imposed = nup_imposer.perform_nup(_marked_pdf(4), cols=1, rows=2,
                                      orientation="portrait", is_duplex=True)
    doc = fitz.open("pdf", imposed.getvalue())
    try:
        sheet_h = doc[0].rect.height
        assert doc[0].rect.width < sheet_h  # portrait sheet
        front = _marker_positions(doc[0])
        back = _marker_positions(doc[1])
        # Page 1 above page 2 on the front; page 3 above page 4 on the back.
        assert front["P1"][1] < sheet_h / 2 < front["P2"][1]
        assert back["P3"][1] < sheet_h / 2 < back["P4"][1]
    finally:
        doc.close()


def test_imposed_single_page_landscape_turns_90_then_270():
    """1-up landscape: the page turns, and the sheet stays one page per side."""
    imposed = nup_imposer.perform_nup(_marked_pdf(2), cols=1, rows=1,
                                      orientation="landscape", is_duplex=True)
    doc = fitz.open("pdf", imposed.getvalue())
    try:
        assert len(doc) == 2
        sheet_w, sheet_h = doc[0].rect.width, doc[0].rect.height
        assert sheet_w < sheet_h  # portrait sheet, always

        front = _marker_positions(doc[0])["P1"]
        back = _marker_positions(doc[1])["P2"]
        # +90 lands the source's top-left marker in one corner; -90 is 180 away
        # from it, so it must land in the diagonally opposite corner. (Which
        # corner is which is PyMuPDF's handedness — rotate=90 is anticlockwise.
        # The invariant that matters is that the two are diagonal.)
        assert (front[0] < sheet_w / 2) != (back[0] < sheet_w / 2)
        assert (front[1] < sheet_h / 2) != (back[1] < sheet_h / 2)
    finally:
        doc.close()


def test_simplex_has_no_back_sides():
    plan = nup_imposer.impose_plan(total_pages=8, nup=4, orientation="landscape",
                                   is_duplex=False)
    assert {s["side"] for s in plan} == {"front"}
    assert all(s["rotation"] == 0 for side in plan for s in side["slots"])
