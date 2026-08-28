"""
Tests for tools/foil_prep.py — the TTF foiling converter.

What matters for foil is not that the output looks right, it is that the output
contains no grey. A single mid-tone that survives becomes a halftone dot field on
the Konica and speckled foil on the cover.
"""

import pathlib
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'tools'))

foil_prep = pytest.importorskip('foil_prep')


def _grey_ramp(w: int = 64, h: int = 8) -> Image.Image:
    """A left-to-right 0..255 ramp — every value the threshold has to decide on."""
    row = np.linspace(0, 255, w, dtype=np.uint8)
    return Image.fromarray(np.tile(row, (h, 1)), 'L')


def test_binarise_leaves_no_grey():
    out = np.asarray(foil_prep.binarise(_grey_ramp(), threshold=55))
    assert set(np.unique(out).tolist()) <= {0, 255}


def test_higher_threshold_keeps_more_ink():
    light = np.asarray(foil_prep.binarise(_grey_ramp(), threshold=30)) < 128
    heavy = np.asarray(foil_prep.binarise(_grey_ramp(), threshold=70)) < 128
    assert heavy.sum() > light.sum()


def test_thicken_grows_strokes_and_zero_is_a_no_op():
    arr = np.full((21, 21), 255, dtype=np.uint8)
    arr[10, :] = 0
    img = Image.fromarray(arr, 'L')

    grown = np.asarray(foil_prep.thicken(img, 1))
    assert (grown < 128).sum() > (arr < 128).sum()
    assert np.array_equal(np.asarray(foil_prep.thicken(img, 0)), arr)


def test_close_pinholes_fills_a_hole_in_a_solid():
    arr = np.zeros((21, 21), dtype=np.uint8)
    arr[10, 10] = 255
    filled = np.asarray(foil_prep.close_pinholes(Image.fromarray(arr, 'L'), 1))
    assert filled[10, 10] == 0


def test_stroke_widths_measures_a_known_bar():
    arr = np.full((40, 40), 255, dtype=np.uint8)
    arr[:, 10:14] = 0                       # a 4px-wide vertical bar
    widths = foil_prep.stroke_widths(Image.fromarray(arr, 'L'))
    assert widths.size == 40 * 4
    assert np.median(widths) == 4


def test_risk_report_flags_a_hairline():
    arr = np.full((200, 200), 255, dtype=np.uint8)
    arr[:, 100] = 0                         # 1px at 600 dpi = 0.04mm, way under
    report = foil_prep.risk_report(Image.fromarray(arr, 'L'), dpi=600)
    assert report['lost_pct'] == 100.0
    assert report['median_mm'] < foil_prep.MIN_STROKE_MM


def test_mm_to_px_round_trips_at_600dpi():
    assert foil_prep.mm_to_px(0.0, 600) == 0
    assert foil_prep.mm_to_px(25.4, 600) == 600
    assert foil_prep.mm_to_px(0.06, 600) == 1
