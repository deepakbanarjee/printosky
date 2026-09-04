"""The scaling proof's own honesty check.

This tool exists to catch a wrong sheet before paper is spent on it, so the
failure that matters is a GREEN verdict that was not earned. It has produced
one: on 2026-09-04 at OSP both sheets failed to build (their PDFs were open in
a viewer, and Windows will not overwrite a locked file), and the tool printed

    GEOMETRY CHECK — the PDFs on disk, before any paper

    Every check passed.

over zero sheets, then offered to send 0 jobs to the Konica. Nothing was
checked and nothing was wrong, because nothing existed.
"""

import importlib.util
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    "scale_proof", os.path.join(ROOT, "tools", "scale_proof.py"))
scale_proof = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scale_proof)

import fitz  # noqa: E402
import nup_imposer  # noqa: E402


def a4_sheet(path, ink=None, pages=1):
    """A portrait A4 PDF with a rectangle on it, optionally out of bounds."""
    w, h = nup_imposer.portrait_sheet("A4")
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=w, height=h).draw_rect(
            fitz.Rect(*(ink or (20, 20, w - 20, h - 20))))
    doc.save(str(path))
    doc.close()
    return {"id": "SX", "path": str(path), "pages": pages,
            "sizes": {(round(w), round(h))}, "scaled": True,
            "cropped": 0, "sides": "ss", "orientation": None}


# ── A verdict over nothing ────────────────────────────────────────────────────

def test_no_sheets_produces_no_passing_verdicts():
    """The regression. `verify` over an empty run must not manufacture a pass."""
    verdicts, failed = scale_proof.verify([], "A4")
    assert not [v for v in verdicts if v[1]], (
        "a run that built nothing reported something as passing")


def test_a_sheet_that_never_built_is_a_failure_not_a_silence(tmp_path, capsys,
                                                             monkeypatch):
    """A sheet missing from the results reads exactly like one that passed,
    unless the run says out loud that it was never written."""
    def boom(test_id, *a, **k):
        raise PermissionError(13, "Permission denied", f"{test_id}.pdf")

    monkeypatch.setattr(scale_proof, "build", boom)
    monkeypatch.setattr(sys, "argv",
                        ["scale_proof", "--make-source", "--only", "S3", "S4",
                         "--out", str(tmp_path)])
    with pytest.raises(SystemExit):
        scale_proof.main()
    out = capsys.readouterr().out
    assert "Every check passed" not in out, "a green light over zero sheets"
    assert out.count("NOT BUILT") == 2


def test_it_refuses_to_send_when_nothing_was_built(tmp_path, monkeypatch):
    """"Sending 0 jobs" is not a thing to do — it is a run that failed."""
    monkeypatch.setattr(scale_proof, "build",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    monkeypatch.setattr(sys, "argv",
                        ["scale_proof", "--make-source", "--only", "S3",
                         "--send", "--printer", "konica", "--out", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        scale_proof.main()
    assert "refus" in str(exc.value).lower() or "nothing" in str(exc.value).lower()


def test_a_locked_file_says_what_to_do_about_it(tmp_path, capsys, monkeypatch):
    """The cause is almost always a PDF left open in a viewer, and saying so
    turns a puzzling errno into a five-second fix."""
    def locked(test_id, *a, **k):
        raise PermissionError(13, "Permission denied", f"{test_id}.pdf")

    monkeypatch.setattr(scale_proof, "build", locked)
    monkeypatch.setattr(sys, "argv",
                        ["scale_proof", "--make-source", "--only", "S3",
                         "--out", str(tmp_path)])
    with pytest.raises(SystemExit):
        scale_proof.main()
    assert "open in a viewer" in capsys.readouterr().out


# ── The checks themselves ─────────────────────────────────────────────────────

def test_ink_outside_the_paper_fails(tmp_path):
    """The S7 fault: content drawn past the edge of the sheet."""
    r = a4_sheet(tmp_path / "off.pdf", ink=(-120, 40, 700, 800))
    verdicts, failed = scale_proof.verify([r], "A4")
    assert failed and "outside the paper" in failed[0][2]


def test_a_sane_sheet_passes(tmp_path):
    r = a4_sheet(tmp_path / "ok.pdf")
    verdicts, failed = scale_proof.verify([r], "A4")
    assert not failed


def test_fit_and_actual_that_match_are_reported_as_broken(tmp_path):
    """S3 and S4 are the pair the whole proof turns on. Identical ink means
    scaling did nothing, and the other six sheets are wasted paper."""
    s3 = a4_sheet(tmp_path / "s3.pdf"); s3["id"] = "S3"
    s4 = a4_sheet(tmp_path / "s4.pdf"); s4["id"] = "S4"
    verdicts, failed = scale_proof.verify([s3, s4], "A4")
    assert any(v[0] == "S3 vs S4" and not v[1] for v in verdicts)
    assert any("scaling is not being applied" in v[2] for v in verdicts)


def test_fit_visibly_larger_than_actual_passes(tmp_path):
    s3 = a4_sheet(tmp_path / "s3.pdf", ink=(200, 300, 390, 540)); s3["id"] = "S3"
    s4 = a4_sheet(tmp_path / "s4.pdf", ink=(20, 30, 570, 810));   s4["id"] = "S4"
    verdicts, _ = scale_proof.verify([s3, s4], "A4")
    assert any(v[0] == "S3 vs S4" and v[1] for v in verdicts)


def test_the_150_percent_sheet_may_lose_its_edges(tmp_path):
    """S6 is an enlargement — cropping is what was asked for, not a fault."""
    s6 = a4_sheet(tmp_path / "s6.pdf", ink=(-100, -100, 700, 950)); s6["id"] = "S6"
    verdicts, failed = scale_proof.verify([s6], "A4")
    assert not failed
    assert any("as asked" in v[2] for v in verdicts)


def test_a_blank_sheet_is_a_failure(tmp_path):
    doc = fitz.open(); doc.new_page(width=595, height=842)
    doc.save(str(tmp_path / "blank.pdf")); doc.close()
    r = a4_sheet(tmp_path / "x.pdf")
    r["path"] = str(tmp_path / "blank.pdf")
    verdicts, failed = scale_proof.verify([r], "A4")
    assert failed and "blank" in failed[0][2]
