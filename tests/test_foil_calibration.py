"""
Tests for tools/foil_calibration.py — the TTF foil calibration sheet.

The sheet is a measuring instrument, so the things worth testing are the ones
that would make it lie: a patch labelled 50% that is not 50% ink, a screen that
comes out grey instead of bilevel, or a block that walks off the bottom of the
page and takes a test row with it.
"""

import io
import pathlib
import sys

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'tools'))

foil_calibration = pytest.importorskip('foil_calibration')


def _patch_array(tone, lpi, w=20.0, h=10.0, dpi=600):
    png = foil_calibration.halftone_patch(tone, lpi, w, h, dpi=dpi)
    return np.asarray(Image.open(io.BytesIO(png)).convert('L'))


@pytest.mark.parametrize('lpi', foil_calibration.HALFTONE_LPI)
@pytest.mark.parametrize('tone', foil_calibration.HALFTONE_TONES)
def test_patch_carries_the_tone_it_is_labelled_with(tone, lpi):
    ink = _patch_array(tone, lpi) < 128
    assert abs(100 * ink.mean() - tone) < 1.0


@pytest.mark.parametrize('lpi', foil_calibration.HALFTONE_LPI)
def test_patch_is_bilevel(lpi):
    assert set(np.unique(_patch_array(50, lpi)).tolist()) <= {0, 255}


def test_patch_is_sized_at_the_requested_dpi():
    arr = _patch_array(50, 45, w=25.4, h=25.4, dpi=600)
    assert arr.shape == (600, 600)


def test_finer_ruling_makes_more_dots():
    """The whole block is meaningless if the rulings are not actually different."""
    def clusters(lpi):
        ink = _patch_array(25, lpi) < 128
        # Count ink runs along one row: more, shorter runs = finer screen.
        row = ink[ink.shape[0] // 2]
        return int(np.count_nonzero(np.diff(row.astype(np.int8)) == 1))

    assert clusters(85) > clusters(55) > clusters(35)


def test_build_makes_one_a4_page(tmp_path):
    fitz = pytest.importorskip('fitz')
    out = tmp_path / 'sheet.pdf'
    foil_calibration.build(str(out), dpi=300)

    doc = fitz.open(str(out))
    assert doc.page_count == 1
    page = doc[0]
    assert round(page.rect.width / foil_calibration.MM) == 210
    assert round(page.rect.height / foil_calibration.MM) == 297
    doc.close()


def test_overflow_fails_loud_instead_of_cropping(tmp_path, monkeypatch):
    """A block off the bottom of the page silently drops a test row. Never ship that."""
    monkeypatch.setattr(foil_calibration, 'LINE_WIDTHS_MM',
                        foil_calibration.LINE_WIDTHS_MM * 3)
    with pytest.raises(ValueError, match='overflows A4'):
        foil_calibration.build(str(tmp_path / 'overflow.pdf'), dpi=150)


def test_pt_to_mm():
    assert foil_calibration.pt_to_mm(72) == pytest.approx(25.4)
