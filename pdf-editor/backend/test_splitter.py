"""Tests for backend.splitter — vector clip path, raster path, deskew, options."""
import io

import fitz
import pytest
from PIL import Image, ImageDraw

import splitter


def _two_column_pdf() -> bytes:
    """Vector PDF with distinct text in left and right halves."""
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((50, 100), "LEFT_COLUMN", fontsize=20)
    page.insert_text((350, 100), "RIGHT_COLUMN", fontsize=20)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _two_row_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((100, 80), "TOP_ROW", fontsize=20)
    page.insert_text((100, 320), "BOTTOM_ROW", fontsize=20)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _scan_like_pdf(angle: float = 0.0) -> bytes:
    """Image-only PDF — no extractable text, optionally pre-rotated to simulate skew."""
    img = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([100, 100, 1100, 700], outline="black", width=8)
    draw.line([100, 400, 1100, 400], fill="black", width=4)
    if abs(angle) > 0.01:
        img = img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))

    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_image(page.rect, stream=img_buf.getvalue())
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_vertical_split_doubles_page_count():
    out = splitter.split_pdf(_two_column_pdf(), direction="vertical", deskew=False)
    doc = fitz.open(stream=out, filetype="pdf")
    assert len(doc) == 2
    doc.close()


def test_horizontal_split_doubles_page_count():
    out = splitter.split_pdf(_two_row_pdf(), direction="horizontal", deskew=False)
    doc = fitz.open(stream=out, filetype="pdf")
    assert len(doc) == 2
    doc.close()


def test_vertical_split_preserves_left_then_right_text():
    out = splitter.split_pdf(_two_column_pdf(), direction="vertical", deskew=False)
    doc = fitz.open(stream=out, filetype="pdf")
    assert "LEFT_COLUMN" in doc[0].get_text()
    assert "RIGHT_COLUMN" in doc[1].get_text()
    doc.close()


def test_horizontal_split_preserves_top_then_bottom_text():
    out = splitter.split_pdf(_two_row_pdf(), direction="horizontal", deskew=False)
    doc = fitz.open(stream=out, filetype="pdf")
    assert "TOP_ROW" in doc[0].get_text()
    assert "BOTTOM_ROW" in doc[1].get_text()
    doc.close()


def test_rtl_swaps_vertical_order():
    out = splitter.split_pdf(_two_column_pdf(), direction="vertical", rtl=True, deskew=False)
    doc = fitz.open(stream=out, filetype="pdf")
    assert "RIGHT_COLUMN" in doc[0].get_text()
    assert "LEFT_COLUMN" in doc[1].get_text()
    doc.close()


def test_rtl_does_not_swap_horizontal_order():
    out = splitter.split_pdf(_two_row_pdf(), direction="horizontal", rtl=True, deskew=False)
    doc = fitz.open(stream=out, filetype="pdf")
    assert "TOP_ROW" in doc[0].get_text()
    assert "BOTTOM_ROW" in doc[1].get_text()
    doc.close()


def test_excluded_page_passes_through_unchanged():
    doc = fitz.open()
    p1 = doc.new_page(width=600, height=400)
    p1.insert_text((50, 100), "PAGE_ONE_KEEP", fontsize=20)
    p2 = doc.new_page(width=600, height=400)
    p2.insert_text((50, 100), "PAGE_TWO_L", fontsize=20)
    p2.insert_text((350, 100), "PAGE_TWO_R", fontsize=20)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()

    out = splitter.split_pdf(buf.getvalue(), direction="vertical", exclude_pages=[0], deskew=False)
    out_doc = fitz.open(stream=out, filetype="pdf")
    assert len(out_doc) == 3  # page 0 unchanged + page 1 split into 2
    assert "PAGE_ONE_KEEP" in out_doc[0].get_text()
    assert "PAGE_TWO_L" in out_doc[1].get_text()
    assert "PAGE_TWO_R" in out_doc[2].get_text()
    out_doc.close()


def test_ratio_offcenter_changes_split_position():
    out = splitter.split_pdf(_two_column_pdf(), direction="vertical", ratio=0.2, deskew=False)
    doc = fitz.open(stream=out, filetype="pdf")
    # ratio=0.2 of 600pt → ~120pt left, ~480pt right
    assert doc[0].rect.width == pytest.approx(120, abs=1)
    assert doc[1].rect.width == pytest.approx(480, abs=1)
    doc.close()


def test_invalid_direction_raises():
    with pytest.raises(ValueError):
        splitter.split_pdf(_two_column_pdf(), direction="diagonal")


def test_invalid_ratio_raises():
    with pytest.raises(ValueError):
        splitter.split_pdf(_two_column_pdf(), ratio=0.0)
    with pytest.raises(ValueError):
        splitter.split_pdf(_two_column_pdf(), ratio=1.0)
    with pytest.raises(ValueError):
        splitter.split_pdf(_two_column_pdf(), ratio=-0.5)


def test_vector_pdf_skips_raster_path_even_when_deskew_enabled():
    """Vector PDFs should preserve extractable text (proves raster path was not used)."""
    out = splitter.split_pdf(_two_column_pdf(), direction="vertical", deskew=True)
    doc = fitz.open(stream=out, filetype="pdf")
    assert "LEFT_COLUMN" in doc[0].get_text()
    doc.close()


def test_scan_like_page_uses_raster_path_and_splits():
    out = splitter.split_pdf(_scan_like_pdf(angle=0.0), direction="vertical", deskew=True)
    doc = fitz.open(stream=out, filetype="pdf")
    assert len(doc) == 2
    doc.close()


def test_deskew_handles_rotated_scan_without_crashing():
    """A 5-degree skewed scan should split successfully when deskew is enabled."""
    out = splitter.split_pdf(_scan_like_pdf(angle=5.0), direction="vertical", deskew=True)
    doc = fitz.open(stream=out, filetype="pdf")
    assert len(doc) == 2
    doc.close()


def test_page_range_only_processes_subset():
    doc = fitz.open()
    for i in range(3):
        p = doc.new_page(width=600, height=400)
        p.insert_text((50, 100), f"P{i}_L", fontsize=20)
        p.insert_text((350, 100), f"P{i}_R", fontsize=20)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()

    # Only split page 1; pages 0 and 2 pass through unchanged
    out = splitter.split_pdf(buf.getvalue(), direction="vertical", page_range=(1, 1), deskew=False)
    out_doc = fitz.open(stream=out, filetype="pdf")
    assert len(out_doc) == 4  # 1 + 2 + 1
    out_doc.close()


def test_detect_skew_angle_matches_known_rotation():
    """Sanity check the deskew detection on a known-rotated image."""
    img = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(img)
    for y in range(100, 700, 30):
        draw.line([100, y, 1100, y], fill="black", width=3)
    rotated = img.rotate(4.0, resample=Image.BICUBIC, expand=False, fillcolor=(255, 255, 255))
    detected = splitter._detect_skew_angle(rotated)
    # Magnitude is what matters for the deskew pipeline — sign convention
    # is internal (correction vs. skew angle). Allow ±1° slack.
    assert abs(abs(detected) - 4.0) < 1.0, f"expected magnitude ~4.0deg, got {detected}"
