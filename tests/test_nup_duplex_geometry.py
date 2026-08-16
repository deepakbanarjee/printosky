"""Duplex back-side geometry.

The model, after five rounds of getting it wrong: the printer's duplex unit plus
the reader's flip is a RIGID MOTION of the sheet. The only rigid motions that map
a rectangle onto itself are 0 and 180 degrees, and ink on paper cannot be
mirrored. So the correction is one bit — back side turned 180, or not — and it is
a single measured constant, identical for every printer.

What these guard is that the 180 stays a *rotation*: both slot axes reverse and
the content turns with them. The previous model reversed columns without rotating
content, which is a mirror, not a rigid motion, and could never have been right.
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
    """Per sheet-side: [(label, x, y, rotation)] in reading order."""
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


def _impose(pages, cols, rows, back_rotation=0, direction="horizontal"):
    return _layout(nup_imposer.perform_nup(
        _make_pdf(pages), cols=cols, rows=rows, orientation="Portrait",
        is_duplex=True, layout_direction=direction, back_rotation=back_rotation))


# ── normalise_back_rotation ───────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (0, 0), (180, 180), ("180", 180), ("0", 0),
    (360, 0), (540, 180), (-180, 180),
    (90, 0), (270, 0),          # not rigid — refused
    (None, 0), ("", 0), ("nonsense", 0),
])
def test_normalise_back_rotation(value, expected):
    assert nup_imposer.normalise_back_rotation(value) == expected


# ── back_rotation = 0 : backs are plain sequential ────────────────────────────

def test_no_rotation_leaves_backs_sequential():
    sides = _impose(8, cols=2, rows=2, back_rotation=0)
    assert _order(sides[0]) == ["1", "2", "3", "4"]
    assert _order(sides[1]) == ["5", "6", "7", "8"]


def test_no_rotation_matches_front_orientation():
    sides = _impose(8, cols=1, rows=2, back_rotation=0, direction="vertical")
    assert _rotations(sides[0]) == _rotations(sides[1]) == {270}


# ── back_rotation = 180 : a rigid turn of the whole side ──────────────────────

def test_180_reverses_both_axes_together():
    """A rotation, not a mirror: rows AND columns reverse."""
    sides = _impose(8, cols=2, rows=2, back_rotation=180)

    assert _order(sides[0]) == ["1", "2", "3", "4"]      # front untouched
    # 1 2 / 3 4  turned 180  ->  8 7 / 6 5
    assert _order(sides[1]) == ["8", "7", "6", "5"]


def test_180_also_turns_the_content():
    """Reversing slots without turning content would be a mirror — impossible
    on paper, and the flaw in the model this replaces."""
    sides = _impose(8, cols=2, rows=2, back_rotation=180)

    assert _rotations(sides[0]) == {0}
    assert _rotations(sides[1]) == {180}


def test_180_on_a_rotated_layout_adds_to_the_slot_rotation():
    sides = _impose(8, cols=1, rows=2, back_rotation=180, direction="vertical")

    assert _rotations(sides[0]) == {270}
    assert _rotations(sides[1]) == {90}        # 270 + 180
    assert _order(sides[0]) == ["1", "2"]
    assert _order(sides[1]) == ["4", "3"]      # single column: rows reverse


def test_fronts_are_identical_whatever_the_setting():
    """The constant only ever touches back sides."""
    a = _impose(8, cols=2, rows=2, back_rotation=0)
    b = _impose(8, cols=2, rows=2, back_rotation=180)

    assert _order(a[0]) == _order(b[0])
    assert _rotations(a[0]) == _rotations(b[0])
    assert _order(a[1]) != _order(b[1])


def test_a_180_is_its_own_inverse():
    """Turning the back twice returns the original placement — the property
    that makes this a rigid motion and the correction exactly one bit."""
    once = _impose(8, cols=2, rows=2, back_rotation=180)[1]
    plain = _impose(8, cols=2, rows=2, back_rotation=0)[1]

    # reversing the 180-turned order and re-turning the content gives the plain side
    assert list(reversed(_order(once))) == _order(plain)
    assert {(r + 180) % 360 for r in _rotations(once)} == _rotations(plain)


# ── simplex is never touched ──────────────────────────────────────────────────

def test_simplex_ignores_back_rotation():
    for rot in (0, 180):
        sides = _layout(nup_imposer.perform_nup(
            _make_pdf(8), cols=2, rows=2, orientation="Portrait",
            is_duplex=False, layout_direction="horizontal", back_rotation=rot))
        assert _order(sides[0]) == ["1", "2", "3", "4"]
        assert _order(sides[1]) == ["5", "6", "7", "8"]
        assert _rotations(sides[0]) == _rotations(sides[1]) == {0}


# ── the retired model is gone ─────────────────────────────────────────────────

def test_binding_edge_model_is_removed():
    """duplex_mirror_axis reversed columns without rotating content — a mirror,
    not a rigid motion. It cannot come back without reintroducing the bug."""
    assert not hasattr(nup_imposer, "duplex_mirror_axis")
