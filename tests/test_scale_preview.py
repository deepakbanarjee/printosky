"""
/scale-preview — the store PC renders the page the printer will actually get.

The point of the endpoint is that it is not a drawing of where the page ought
to go: it runs the same pdf_scaler.apply_scale() the print path runs and
photographs the result. So the tests check the picture against scale_rect's
geometry, and check that a failure says so rather than showing something.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fitz
import pytest

import pdf_scaler
import print_server
from nup_imposer import portrait_sheet

A4_W, A4_H = portrait_sheet("A4")
A5_W, A5_H = portrait_sheet("A5")


@pytest.fixture
def job(monkeypatch, tmp_path):
    db = tmp_path / "jobs.db"
    pdf = tmp_path / "job.pdf"

    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=A5_W, height=A5_H).draw_rect(fitz.Rect(5, 5, A5_W - 5, A5_H - 5))
    doc.save(str(pdf))
    doc.close()

    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, filepath TEXT, filename TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('OSP-1',?,'job.pdf')", (str(pdf),))
    conn.commit()
    conn.close()

    monkeypatch.setattr(print_server, "DB_PATH", str(db))
    print_server._PREVIEW_CACHE.clear()
    return {"pdf": pdf, "tmp": tmp_path}


def get(**params):
    qs = {k: [str(v)] for k, v in params.items() if v is not None}
    return print_server.handle_scale_preview(qs)


def png_size(png):
    doc = fitz.open("png", png)
    try:
        return doc[0].rect.width, doc[0].rect.height
    finally:
        doc.close()


class TestBadRequests:
    def test_no_job_id(self, job):
        png, meta = get(mode="fit")
        assert png is None and meta["status"] == 400

    @pytest.mark.parametrize("mode", [None, "", "shrink", "noscale"])
    def test_mode_must_be_real(self, mode, job):
        png, meta = get(job_id="OSP-1", mode=mode)
        assert png is None and meta["status"] == 400

    def test_custom_needs_a_number(self, job):
        png, meta = get(job_id="OSP-1", mode="custom", percent="wide")
        assert png is None and meta["status"] == 400

    def test_missing_file_is_404_not_a_blank_image(self, job):
        os.remove(job["pdf"])
        png, meta = get(job_id="OSP-1", mode="fit")
        assert png is None and meta["status"] == 404

    def test_a_non_pdf_is_declined_rather_than_guessed_at(self, job, monkeypatch):
        other = job["tmp"] / "scan.jpg"
        other.write_bytes(b"\xff\xd8\xff")
        monkeypatch.setattr(print_server, "_resolve_job_file", lambda _: str(other))
        png, meta = get(job_id="OSP-1", mode="fit")
        assert png is None and meta["status"] == 415


class TestTheRenderIsTheArtifact:
    @pytest.mark.parametrize("mode,percent", [("fit", None), ("actual", None),
                                              ("custom", 60), ("custom", 140)])
    def test_the_png_is_the_sheet(self, mode, percent, job):
        png, meta = get(job_id="OSP-1", mode=mode, percent=percent, paper_size="A4")
        assert png is not None
        w, h = png_size(png)
        assert w / h == pytest.approx(A4_W / A4_H, rel=0.01)
        assert meta["scaled"] is True

    def test_it_agrees_with_scale_rect(self, job):
        """The picture and the geometry come from one place, so a preview
        cannot promise something the printer will not do."""
        _, meta = get(job_id="OSP-1", mode="custom", percent=200)
        rect = pdf_scaler.scale_rect(A5_W, A5_H, "A4", "custom", 200)
        assert meta["crops"] == rect["crops"]

    def test_a_no_op_previews_the_original_page(self, job):
        """Actual on a page already the sheet size bakes nothing — so the
        preview shows the page itself, which is what would print."""
        doc = fitz.open()
        doc.new_page(width=A4_W, height=A4_H)
        doc.save(str(job["pdf"]), incremental=False)
        doc.close()
        png, meta = get(job_id="OSP-1", mode="actual", paper_size="A4")
        assert png is not None
        assert meta["scaled"] is False


class TestPaging:
    def test_page_count_is_reported(self, job):
        _, meta = get(job_id="OSP-1", mode="fit")
        assert meta["total_pages"] == 3

    def test_pages_are_switchable(self, job):
        for n in (1, 2, 3):
            png, meta = get(job_id="OSP-1", mode="fit", page=n)
            assert png is not None and meta["page"] == n

    def test_a_page_past_the_end_clamps(self, job):
        _, meta = get(job_id="OSP-1", mode="fit", page=99)
        assert meta["page"] == 3

    def test_only_one_page_is_rendered_per_request(self, job):
        """A 200-page job renders one page, not 200."""
        png, _ = get(job_id="OSP-1", mode="fit")
        doc = fitz.open("png", png)
        try:
            assert len(doc) == 1
        finally:
            doc.close()


class TestCropWarning:
    def test_counts_the_pages_that_lose_content(self, job):
        _, meta = get(job_id="OSP-1", mode="custom", percent=300)
        assert meta["cropped_pages"] == 3
        assert meta["crops"] is True

    def test_fit_never_crops(self, job):
        _, meta = get(job_id="OSP-1", mode="fit")
        assert meta["cropped_pages"] == 0
        assert meta["crops"] is False


class TestCache:
    def test_a_repeat_request_is_served_from_cache(self, job):
        a, _ = get(job_id="OSP-1", mode="fit")
        b, _ = get(job_id="OSP-1", mode="fit")
        assert a is b                      # same object, not merely equal

    def test_it_stays_bounded(self, job):
        for pct in range(25, 25 + print_server._PREVIEW_CACHE_MAX + 10):
            get(job_id="OSP-1", mode="custom", percent=pct)
        assert len(print_server._PREVIEW_CACHE) <= print_server._PREVIEW_CACHE_MAX

    def test_editing_the_file_invalidates_it(self, job):
        first, _ = get(job_id="OSP-1", mode="fit")
        doc = fitz.open()
        doc.new_page(width=A5_W, height=A5_H).draw_circle((100, 100), 50)
        doc.save(str(job["pdf"]), incremental=False)
        doc.close()
        os.utime(job["pdf"], (0, 0))       # force a different mtime
        second, _ = get(job_id="OSP-1", mode="fit")
        assert second is not first
