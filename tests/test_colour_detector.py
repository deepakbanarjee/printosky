"""
Tests for colour_detector.py

Strategy:
- _is_gray: pure function — no mocking needed
- _page_has_colour: pass MagicMock page/doc objects
- detect_colour_pages / build_colour_map: monkeypatch FITZ_AVAILABLE + fitz
- save_colour_map / confirm_colour_map: temp SQLite
"""

import json
import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import colour_detector as cd


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_db(tmp_path, job_id="OSP-20260101-0001", colour_page_map=None):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            colour_page_map TEXT,
            colour_confirmed INTEGER DEFAULT 0
        )
    """)
    conn.execute(
        "INSERT INTO jobs (job_id, colour_page_map) VALUES (?, ?)",
        (job_id, json.dumps(colour_page_map) if colour_page_map else None),
    )
    conn.commit()
    conn.close()
    return db_path


def _fake_fitz(pages_colour_flags: list[bool]):
    """
    Build a fake fitz module whose open() returns a mock document.

    pages_colour_flags: list of booleans — True means that page has colour.
    The mock page's get_drawings() and get_images() are set accordingly.
    """
    mock_fitz = MagicMock()

    mock_doc = MagicMock()
    mock_doc.__len__ = lambda self: len(pages_colour_flags)

    mock_pages = []
    for has_colour in pages_colour_flags:
        page = MagicMock()
        if has_colour:
            # Return one drawing with a non-gray color
            page.get_drawings.return_value = [{"color": (1.0, 0.0, 0.0), "fill": None}]
        else:
            page.get_drawings.return_value = [{"color": (0.5, 0.5, 0.5), "fill": None}]
        page.get_images.return_value = []
        mock_pages.append(page)

    mock_doc.__getitem__ = lambda self, i: mock_pages[i]
    mock_doc.close = MagicMock()
    mock_fitz.open.return_value = mock_doc

    return mock_fitz


# ─────────────────────────────────────────────────────────────────────────────
# _is_gray
# ─────────────────────────────────────────────────────────────────────────────

class TestIsGray:
    def test_pure_black(self):
        assert cd._is_gray((0.0, 0.0, 0.0)) is True

    def test_pure_white(self):
        assert cd._is_gray((1.0, 1.0, 1.0)) is True

    def test_mid_gray(self):
        assert cd._is_gray((0.5, 0.5, 0.5)) is True

    def test_near_gray_within_threshold(self):
        # Difference < 0.02 on all channels → still gray
        assert cd._is_gray((0.50, 0.51, 0.50)) is True

    def test_red(self):
        assert cd._is_gray((1.0, 0.0, 0.0)) is False

    def test_green(self):
        assert cd._is_gray((0.0, 1.0, 0.0)) is False

    def test_blue(self):
        assert cd._is_gray((0.0, 0.0, 1.0)) is False

    def test_near_color_over_threshold(self):
        # R-G = 0.05 > 0.02 → not gray
        assert cd._is_gray((0.60, 0.55, 0.60)) is False

    def test_empty_tuple_returns_true(self):
        assert cd._is_gray(()) is True

    def test_none_returns_true(self):
        assert cd._is_gray(None) is True

    def test_short_tuple_returns_true(self):
        assert cd._is_gray((0.5,)) is True


# ─────────────────────────────────────────────────────────────────────────────
# _page_has_colour
# ─────────────────────────────────────────────────────────────────────────────

class TestPageHasColour:
    def _mock_page(self, drawings=None, images=None):
        page = MagicMock()
        page.get_drawings.return_value = drawings or []
        page.get_images.return_value = images or []
        return page

    def _mock_doc(self, colorspace=1):
        doc = MagicMock()
        doc.extract_image.return_value = {"colorspace": colorspace}
        return doc

    def test_no_drawings_no_images_returns_false(self):
        page = self._mock_page()
        assert cd._page_has_colour(page, MagicMock()) is False

    def test_colour_stroke_detected(self):
        page = self._mock_page(drawings=[{"color": (1.0, 0.0, 0.0), "fill": None}])
        assert cd._page_has_colour(page, MagicMock()) is True

    def test_colour_fill_detected(self):
        page = self._mock_page(drawings=[{"color": None, "fill": (0.0, 0.8, 0.0)}])
        assert cd._page_has_colour(page, MagicMock()) is True

    def test_gray_drawing_not_detected(self):
        page = self._mock_page(drawings=[{"color": (0.5, 0.5, 0.5), "fill": (0.3, 0.3, 0.3)}])
        assert cd._page_has_colour(page, MagicMock()) is False

    def test_colour_image_rgb_detected(self):
        page = self._mock_page(images=[(10, 0, 0, 0, 0, 0, 0, 0, 0)])
        doc = self._mock_doc(colorspace=3)  # RGB
        assert cd._page_has_colour(page, doc) is True

    def test_gray_image_not_detected(self):
        page = self._mock_page(images=[(10, 0, 0, 0, 0, 0, 0, 0, 0)])
        doc = self._mock_doc(colorspace=1)  # grayscale
        assert cd._page_has_colour(page, doc) is False

    def test_cmyk_image_detected(self):
        page = self._mock_page(images=[(5, 0, 0, 0, 0, 0, 0, 0, 0)])
        doc = self._mock_doc(colorspace=4)  # CMYK
        assert cd._page_has_colour(page, doc) is True

    def test_corrupt_image_ref_skipped(self):
        page = self._mock_page(images=[(99, 0, 0, 0, 0, 0, 0, 0, 0)])
        doc = MagicMock()
        doc.extract_image.side_effect = Exception("corrupt")
        assert cd._page_has_colour(page, doc) is False  # skipped, not raised

    def test_none_color_and_none_fill_skipped(self):
        page = self._mock_page(drawings=[{"color": None, "fill": None}])
        assert cd._page_has_colour(page, MagicMock()) is False


# ─────────────────────────────────────────────────────────────────────────────
# detect_colour_pages
# ─────────────────────────────────────────────────────────────────────────────

class TestDetectColourPages:
    def test_returns_empty_when_fitz_unavailable(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", False)
        assert cd.detect_colour_pages("/any/path.pdf") == []

    def test_returns_empty_on_open_exception(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = Exception("bad file")
        monkeypatch.setattr(cd, "fitz", mock_fitz)
        assert cd.detect_colour_pages("/bad/path.pdf") == []

    def test_all_bw_returns_empty(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([False, False, False]))
        result = cd.detect_colour_pages("dummy.pdf")
        assert result == []

    def test_all_colour_returns_all_pages(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([True, True, True]))
        result = cd.detect_colour_pages("dummy.pdf")
        assert result == [1, 2, 3]

    def test_mixed_returns_colour_pages_only(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([True, False, True, False]))
        result = cd.detect_colour_pages("dummy.pdf")
        assert result == [1, 3]

    def test_single_page_colour(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([True]))
        result = cd.detect_colour_pages("dummy.pdf")
        assert result == [1]

    def test_page_numbers_are_one_indexed(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([False, True]))
        result = cd.detect_colour_pages("dummy.pdf")
        assert 2 in result
        assert 1 not in result


# ─────────────────────────────────────────────────────────────────────────────
# build_colour_map
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildColourMap:
    def test_returns_error_dict_when_fitz_unavailable(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", False)
        result = cd.build_colour_map("/any/path.pdf")
        assert result["error"] == "PyMuPDF not installed"
        assert result["total"] == 0
        assert result["has_colour"] is False

    def test_returns_error_dict_on_open_exception(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        mock_fitz = MagicMock()
        mock_fitz.open.side_effect = Exception("not a PDF")
        monkeypatch.setattr(cd, "fitz", mock_fitz)
        result = cd.build_colour_map("/bad/file.pdf")
        assert "error" in result
        assert result["total"] == 0

    def test_all_bw_document(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([False, False]))
        result = cd.build_colour_map("dummy.pdf")
        assert result["has_colour"] is False
        assert result["has_bw"] is True
        assert result["is_mixed"] is False
        assert result["colour"] == []
        assert result["bw"] == [1, 2]
        assert result["total"] == 2

    def test_all_colour_document(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([True, True]))
        result = cd.build_colour_map("dummy.pdf")
        assert result["has_colour"] is True
        assert result["has_bw"] is False
        assert result["is_mixed"] is False
        assert result["colour"] == [1, 2]
        assert result["bw"] == []

    def test_mixed_document(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([True, False, True]))
        result = cd.build_colour_map("dummy.pdf")
        assert result["is_mixed"] is True
        assert result["colour"] == [1, 3]
        assert result["bw"] == [2]
        assert result["total"] == 3

    def test_result_keys_always_present(self, monkeypatch):
        monkeypatch.setattr(cd, "FITZ_AVAILABLE", True)
        monkeypatch.setattr(cd, "fitz", _fake_fitz([False]))
        result = cd.build_colour_map("dummy.pdf")
        for key in ("colour", "bw", "total", "has_colour", "has_bw", "is_mixed"):
            assert key in result


# ─────────────────────────────────────────────────────────────────────────────
# save_colour_map
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveColourMap:
    def test_saves_json_to_db(self, tmp_path):
        db_path = _make_db(tmp_path)
        cmap = {"colour": [1, 3], "bw": [2], "total": 3,
                "has_colour": True, "has_bw": True, "is_mixed": True}
        cd.save_colour_map(db_path, "OSP-20260101-0001", cmap)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT colour_page_map, colour_confirmed FROM jobs WHERE job_id=?",
            ("OSP-20260101-0001",)
        ).fetchone()
        conn.close()

        stored = json.loads(row[0])
        assert stored["colour"] == [1, 3]
        assert stored["total"] == 3
        assert row[1] == 0  # colour_confirmed reset to 0

    def test_overwrites_existing_map(self, tmp_path):
        old_map = {"colour": [1], "bw": [2, 3], "total": 3}
        db_path = _make_db(tmp_path, colour_page_map=old_map)
        new_map = {"colour": [], "bw": [1, 2, 3], "total": 3}
        cd.save_colour_map(db_path, "OSP-20260101-0001", new_map)

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT colour_page_map FROM jobs WHERE job_id=?",
                           ("OSP-20260101-0001",)).fetchone()
        conn.close()
        assert json.loads(row[0])["colour"] == []

    def test_nonexistent_job_no_crash(self, tmp_path):
        db_path = _make_db(tmp_path)
        cd.save_colour_map(db_path, "OSP-NONEXISTENT", {"colour": [], "bw": [], "total": 0})


# ─────────────────────────────────────────────────────────────────────────────
# confirm_colour_map
# ─────────────────────────────────────────────────────────────────────────────

class TestConfirmColourMap:
    def test_confirm_without_override_sets_flag(self, tmp_path):
        db_path = _make_db(tmp_path)
        cd.confirm_colour_map(db_path, "OSP-20260101-0001")

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT colour_confirmed FROM jobs WHERE job_id=?",
                           ("OSP-20260101-0001",)).fetchone()
        conn.close()
        assert row[0] == 1

    def test_confirm_with_override_updates_map(self, tmp_path):
        old_map = {"colour": [1, 2], "bw": [3, 4, 5], "total": 5}
        db_path = _make_db(tmp_path, colour_page_map=old_map)

        # Staff corrects: only page 2 is colour
        cd.confirm_colour_map(db_path, "OSP-20260101-0001", colour_pages=[2])

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT colour_page_map, colour_confirmed FROM jobs WHERE job_id=?",
            ("OSP-20260101-0001",)
        ).fetchone()
        conn.close()

        stored = json.loads(row[0])
        assert stored["colour"] == [2]
        assert stored["bw"] == [1, 3, 4, 5]
        assert stored["staff_override"] is True
        assert stored["has_colour"] is True
        assert stored["is_mixed"] is True
        assert row[1] == 1  # colour_confirmed

    def test_override_with_all_colour_pages(self, tmp_path):
        old_map = {"colour": [1], "bw": [2, 3], "total": 3}
        db_path = _make_db(tmp_path, colour_page_map=old_map)
        cd.confirm_colour_map(db_path, "OSP-20260101-0001", colour_pages=[1, 2, 3])

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT colour_page_map FROM jobs WHERE job_id=?",
                           ("OSP-20260101-0001",)).fetchone()
        conn.close()

        stored = json.loads(row[0])
        assert stored["bw"] == []
        assert stored["has_bw"] is False
        assert stored["is_mixed"] is False

    def test_override_with_no_colour_pages(self, tmp_path):
        old_map = {"colour": [1, 2], "bw": [3], "total": 3}
        db_path = _make_db(tmp_path, colour_page_map=old_map)
        cd.confirm_colour_map(db_path, "OSP-20260101-0001", colour_pages=[])

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT colour_page_map FROM jobs WHERE job_id=?",
                           ("OSP-20260101-0001",)).fetchone()
        conn.close()

        stored = json.loads(row[0])
        assert stored["colour"] == []
        assert stored["has_colour"] is False

    def test_override_with_no_prior_map(self, tmp_path):
        db_path = _make_db(tmp_path)  # colour_page_map is NULL
        cd.confirm_colour_map(db_path, "OSP-20260101-0001", colour_pages=[1])
        # total will be 0 (no prior map) — should not crash
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT colour_confirmed FROM jobs WHERE job_id=?",
                           ("OSP-20260101-0001",)).fetchone()
        conn.close()
        assert row[0] == 1

    def test_nonexistent_job_no_crash(self, tmp_path):
        db_path = _make_db(tmp_path)
        cd.confirm_colour_map(db_path, "OSP-NONEXISTENT")
