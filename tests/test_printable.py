"""
What customers actually send has to reach the printer.

OSP, 2026-09-08: two paid jobs, `nithya coverpage.docx` and a WhatsApp photo,
downloaded and handed straight to SumatraPDF, which prints PDFs and nothing
else. It parsed both as PDFs, failed on the version marker, and exited 1. They
had been sitting Paid since July.

These tests pin the conversion and — the part that cost the day — the boundary
between "try again later" and "this will never work, stop and tell someone".
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import printable
from printable import Unprintable, needs_conversion, to_printable_pdf


fitz = pytest.importorskip("fitz", reason="PyMuPDF is how images become pages")


def _make_pdf(path, pages=1):
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    return str(path)


def _make_image(path, w=800, h=600):
    """A real raster file on disk, not a stub — the conversion is the thing
    under test, and a stub would pass while the real path failed."""
    doc = fitz.open()
    page = doc.new_page(width=w, height=h)
    page.draw_rect(fitz.Rect(10, 10, w - 10, h - 10), fill=(0.2, 0.4, 0.9))
    pix = page.get_pixmap()
    doc.close()
    pix.save(str(path))
    return str(path)


# ── what needs doing at all ───────────────────────────────────────────────────

def test_a_pdf_needs_nothing():
    assert needs_conversion("a.pdf") is False
    assert needs_conversion("A.PDF") is False


def test_the_two_things_customers_send_do_need_converting():
    assert needs_conversion("nithya coverpage.docx") is True
    assert needs_conversion("919446903907_20260725_063022.jpg") is True


def test_a_pdf_is_returned_untouched(tmp_path):
    """The common case must cost nothing — no copy, no re-save, same path."""
    src = _make_pdf(tmp_path / "job.pdf")
    assert to_printable_pdf(src, str(tmp_path)) == src


def test_a_missing_pdf_is_passed_through_not_declared_unprintable(tmp_path):
    """send_to_printer looks in Jobs\\Archive for a file that is not where the
    row says, and plan_print_job has its own fallback. Failing here would take
    both recoveries away from every ordinary job, to no purpose."""
    ghost = str(tmp_path / "moved-to-archive.pdf")
    assert to_printable_pdf(ghost, str(tmp_path)) == ghost


# ── images ────────────────────────────────────────────────────────────────────

def test_a_photo_becomes_a_one_page_pdf(tmp_path):
    src = _make_image(tmp_path / "whatsapp.png")
    out = to_printable_pdf(src, str(tmp_path))
    assert out != src and out.endswith(".pdf")
    doc = fitz.open(out)
    assert doc.page_count == 1
    doc.close()


def test_a_portrait_photo_lands_on_a_portrait_sheet(tmp_path):
    src = _make_image(tmp_path / "notes.png", w=600, h=800)
    doc = fitz.open(to_printable_pdf(src, str(tmp_path)))
    page = doc[0]
    assert page.rect.height > page.rect.width
    doc.close()


def test_a_landscape_photo_turns_the_sheet_not_the_picture(tmp_path):
    """A landscape photo squeezed onto a portrait sheet wastes half the paper
    and shrinks handwriting to unreadable."""
    src = _make_image(tmp_path / "wide.png", w=1000, h=500)
    doc = fitz.open(to_printable_pdf(src, str(tmp_path)))
    page = doc[0]
    assert page.rect.width > page.rect.height
    doc.close()


def test_the_picture_keeps_its_shape(tmp_path):
    """Never stretched to fill the sheet: a 2:1 photo comes back 2:1."""
    src = _make_image(tmp_path / "wide.png", w=1000, h=500)
    doc = fitz.open(to_printable_pdf(src, str(tmp_path)))
    drawn = doc[0].get_image_bbox(doc[0].get_images(full=True)[0])
    doc.close()
    assert drawn.width / drawn.height == pytest.approx(2.0, rel=0.02)


def test_the_sheet_size_is_honoured(tmp_path):
    src = _make_image(tmp_path / "p.png", w=600, h=800)
    a4 = fitz.open(to_printable_pdf(src, str(tmp_path / "a4")))
    a3 = fitz.open(to_printable_pdf(src, str(tmp_path / "a3"), paper_size="A3"))
    assert a3[0].rect.width > a4[0].rect.width
    a4.close(); a3.close()


def test_an_unknown_paper_size_falls_back_to_a4_not_to_nothing(tmp_path):
    src = _make_image(tmp_path / "p.png")
    doc = fitz.open(to_printable_pdf(src, str(tmp_path), paper_size="tabloid-extra"))
    assert doc[0].rect.width == pytest.approx(595.0, abs=1) or \
           doc[0].rect.height == pytest.approx(595.0, abs=1)
    doc.close()


# ── the permanent failures ────────────────────────────────────────────────────

def test_a_format_with_no_converter_is_permanent_and_says_why(tmp_path):
    src = tmp_path / "design.psd"
    src.write_bytes(b"8BPS not really")
    with pytest.raises(Unprintable) as e:
        to_printable_pdf(str(src), str(tmp_path))
    assert ".psd" in str(e.value)
    assert "PDF" in str(e.value) or "by hand" in str(e.value)


def test_a_missing_file_is_permanent(tmp_path):
    with pytest.raises(Unprintable):
        to_printable_pdf(str(tmp_path / "gone.jpg"), str(tmp_path))


def test_a_corrupt_image_is_permanent_not_a_crash(tmp_path):
    src = tmp_path / "truncated.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0 this is not an image")
    with pytest.raises(Unprintable):
        to_printable_pdf(str(src), str(tmp_path))


def test_a_word_file_without_word_is_permanent_and_names_the_reason(tmp_path, monkeypatch):
    """This is the actual OSP file, on any box that is not a Windows store PC."""
    src = tmp_path / "nithya coverpage.docx"
    src.write_bytes(b"PK\x03\x04 a real docx starts like this")
    monkeypatch.setitem(sys.modules, "win32com.client", None)
    with pytest.raises(Unprintable) as e:
        to_printable_pdf(str(src), str(tmp_path))
    assert "Word" in str(e.value)


def test_office_formats_are_recognised_rather_than_rejected_as_unknown():
    """The failure for a .docx must be 'no Word here', never 'no converter for
    .docx' — the two send whoever reads the alert to different places."""
    for ext in (".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"):
        assert ext in printable.OFFICE_APPS


def test_a_converter_that_writes_nothing_is_caught(tmp_path, monkeypatch):
    """Silence is not success. An exporter that returns cleanly having written
    no file must not hand SumatraPDF a path to nowhere."""
    src = _make_image(tmp_path / "p.png")
    monkeypatch.setattr(printable, "_image_to_pdf", lambda *a, **k: None)
    with pytest.raises(Unprintable) as e:
        to_printable_pdf(src, str(tmp_path))
    assert "no PDF" in str(e.value)
