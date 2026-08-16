"""Duplex geometry for N-up imposition.

The bug these guard (the "double flip"): a duplex unit can only lay the back
image down at 0 or 180 degrees — it cannot mirror slot positions. So the
imposer owns *slot mirroring*, and the driver owns *content rotation*. The
imposer was doing both: it reversed columns (correct) **and** rotated back-page
content 90 where the front got 270 (wrong), a 180-degree difference on top of
the driver's own back-side rotation. Rotated layouts — 2-up vertical — printed
every back sheet upside down.
"""

import io

import pytest

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

import nup_imposer  # noqa: E402


A4_W, A4_H = 595.28, 841.89


def _make_pdf(pages: int) -> bytes:
    doc = fitz.open()
    for i in range(1, pages + 1):
        pg = doc.new_page(width=A4_W, height=A4_H)
        pg.insert_text((250, 420), str(i), fontsize=200)
    data = doc.tobytes()
    doc.close()
    return data


def _layout(stream: io.BytesIO):
    """[(page_label, x, y, rotation_degrees)] per output sheet-side."""
    doc = fitz.open("pdf", stream.getvalue())
    angles = {(1.0, 0.0): 0, (0.0, -1.0): 90, (-1.0, 0.0): 180, (0.0, 1.0): 270}
    out = []
    for pg in doc:
        items = []
        for blk in pg.get_text("dict")["blocks"]:
            for ln in blk.get("lines", []):
                txt = "".join(s["text"] for s in ln["spans"]).strip()
                if not txt:
                    continue
                d = (round(ln["dir"][0], 1), round(ln["dir"][1], 1))
                items.append((txt, round(ln["bbox"][0]), round(ln["bbox"][1]),
                              angles.get(d, -1)))
        items.sort(key=lambda t: (t[2], t[1]))
        out.append(items)
    doc.close()
    return out


def _order(side):
    return [t[0] for t in side]


def _rotations(side):
    return {t[3] for t in side}


# ── duplex_mirror_axis: the model itself ──────────────────────────────────────

@pytest.mark.parametrize("w,h,edge,expected", [
    # portrait sheet: long edge is vertical, short edge is horizontal
    (A4_W, A4_H, "long",  "cols"),
    (A4_W, A4_H, "short", "rows"),
    # landscape sheet: long edge is horizontal, short edge is vertical
    (A4_H, A4_W, "long",  "rows"),
    (A4_H, A4_W, "short", "cols"),
])
def test_mirror_axis_follows_the_physical_flip_edge(w, h, edge, expected):
    assert nup_imposer.duplex_mirror_axis(w, h, edge) == expected


def test_mirror_axis_defaults_to_long_edge():
    assert (nup_imposer.duplex_mirror_axis(A4_W, A4_H, "anything-else")
            == nup_imposer.duplex_mirror_axis(A4_W, A4_H, "long"))


# ── the regression: back content rotation must equal front ────────────────────

def test_2up_vertical_backs_are_not_upside_down():
    """THE double-flip regression. 2-up vertical rotates portrait pages into
    landscape slots; front and back must rotate the same way."""
    sides = _layout(nup_imposer.perform_nup(
        _make_pdf(8), cols=1, rows=2, orientation="Portrait",
        is_duplex=True, layout_direction="vertical", binding_edge="long"))

    front_rot = _rotations(sides[0])
    back_rot = _rotations(sides[1])
    assert front_rot == {270}
    assert back_rot == front_rot, (
        f"back rotated {back_rot} vs front {front_rot} — upside-down backs")


def test_every_sheet_side_uses_one_rotation():
    for cols, rows, orient, direction in [
        (1, 2, "Portrait", "vertical"),
        (2, 1, "Landscape", "horizontal"),
        (2, 2, "Portrait", "horizontal"),
    ]:
        sides = _layout(nup_imposer.perform_nup(
            _make_pdf(8), cols=cols, rows=rows, orientation=orient,
            is_duplex=True, layout_direction=direction, binding_edge="long"))
        rots = {r for side in sides for r in _rotations(side)}
        assert len(rots) == 1, f"{cols}x{rows} {direction}: mixed rotations {rots}"


# ── slot mirroring stays, because the driver cannot do it ─────────────────────

def test_2up_horizontal_short_edge_reverses_columns():
    """Landscape sheet flipped about its short (vertical) edge — columns
    reverse so the back registers with the front."""
    sides = _layout(nup_imposer.perform_nup(
        _make_pdf(4), cols=2, rows=1, orientation="Landscape",
        is_duplex=True, layout_direction="horizontal", binding_edge="short"))

    assert _order(sides[0]) == ["1", "2"]
    assert _order(sides[1]) == ["4", "3"]


def test_4up_long_edge_reverses_columns_not_rows():
    sides = _layout(nup_imposer.perform_nup(
        _make_pdf(8), cols=2, rows=2, orientation="Portrait",
        is_duplex=True, layout_direction="horizontal", binding_edge="long"))

    assert _order(sides[0]) == ["1", "2", "3", "4"]
    # columns swap within each row; rows keep their order
    assert _order(sides[1]) == ["6", "5", "8", "7"]


def test_short_edge_on_portrait_reverses_rows():
    """The axis is derived, not hardcoded to columns: a portrait sheet bound on
    the short edge flips top-to-bottom, so rows reverse instead."""
    sides = _layout(nup_imposer.perform_nup(
        _make_pdf(8), cols=2, rows=2, orientation="Portrait",
        is_duplex=True, layout_direction="horizontal", binding_edge="short"))

    assert _order(sides[0]) == ["1", "2", "3", "4"]
    assert _order(sides[1]) == ["7", "8", "5", "6"]


def test_simplex_never_mirrors():
    sides = _layout(nup_imposer.perform_nup(
        _make_pdf(8), cols=2, rows=2, orientation="Portrait",
        is_duplex=False, layout_direction="horizontal"))

    assert _order(sides[0]) == ["1", "2", "3", "4"]
    assert _order(sides[1]) == ["5", "6", "7", "8"]


def test_front_sides_are_identical_under_both_binding_edges():
    """Binding edge only ever affects backs."""
    kw = dict(cols=2, rows=2, orientation="Portrait", is_duplex=True,
              layout_direction="horizontal")
    long_sides = _layout(nup_imposer.perform_nup(_make_pdf(8), binding_edge="long", **kw))
    short_sides = _layout(nup_imposer.perform_nup(_make_pdf(8), binding_edge="short", **kw))

    assert _order(long_sides[0]) == _order(short_sides[0])
    assert _order(long_sides[1]) != _order(short_sides[1])
