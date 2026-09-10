"""
Make a file printable, or say plainly that it cannot be.

SumatraPDF prints PDFs. Everything else it is handed, it tries to parse as a
PDF and fails:

    PrintFile: file: '...\\OSP-20260725-3907-0058-e918fc.docx'
    cannot recognize version marker
    trying to repair broken xref / repairing PDF document / no objects found
    Error: Couldn't open file '...e918fc.docx' for printing

That is a real paid job at OSP, 2026-09-08 — `nithya coverpage.docx`, a Word
file a customer sent over WhatsApp. Alongside it sat a `.jpg`, the commonest
thing anyone sends over WhatsApp at all. Neither could ever print, and nothing
converted them: `watcher.py` does build a PDF from a Word file, but only to
count its pages, into a temp file it deletes in the `finally`.

So: one place that turns what customers actually send into something the
printer accepts, and — just as important — that distinguishes

  * **converted** — here is a PDF, print it;
  * **already a PDF** — nothing to do;
  * **cannot be converted, ever** — a password-locked file, a format with no
    converter on this box. The caller must alert and STOP, not retry forever.
    A retry loop over a permanent failure is what turned one bad file into a
    day of lost printing.

Word and PowerPoint conversion needs those applications, so it works on a store
PC and is unavailable anywhere else; that is reported as a permanent failure
with the reason, not as a mystery.

Having Office installed is not enough, though — OSP, 2026-09-10, on the first
morning this module ran there:

    Word could not export ... to PDF ((-2147352567, 'Exception occurred.',
    (0, 'Microsoft Word', 'You cannot close Microsoft Word because a dialog box
    is open. Click OK, switch to Word, and then close the dialog box.', ...)))

Nothing was wrong with the document. Someone had left a Word window open with a
dialog in it, and `Dispatch` ATTACHES to a running instance rather than starting
one, so the export inherited the stuck session — and `DisplayAlerts = False`
cannot dismiss a dialog that was already up. Hence `DispatchEx` everywhere: a
fresh out-of-process instance, ours to drive and ours to quit, immune to
whatever a person left on screen.

The message mattered too. It used to end "it may be password protected or
corrupt", asserting a cause it had not established, and the same exception
carries both. An alert that names the wrong cause sends someone to ask a
customer to re-send a perfectly good file while the real fault sits untouched on
the counter, so failures here now offer both and say which to check first.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("printable")

PDF_EXTS = {".pdf"}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}

# Extension -> the Office application that can export it, and the SaveAs/Export
# format code that means PDF. Windows store PCs only; see _office_to_pdf.
OFFICE_APPS = {
    ".doc":  ("Word.Application", 17),
    ".docx": ("Word.Application", 17),
    ".rtf":  ("Word.Application", 17),
    ".odt":  ("Word.Application", 17),
    ".txt":  ("Word.Application", 17),
    ".ppt":  ("PowerPoint.Application", 32),
    ".pptx": ("PowerPoint.Application", 32),
    ".xls":  ("Excel.Application", 0),
    ".xlsx": ("Excel.Application", 0),
}

# Points. A4 is the default because it is what every store stocks.
PAGE_SIZES = {
    "a4": (595.0, 842.0),
    "a3": (842.0, 1191.0),
    "a5": (420.0, 595.0),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
}


class Unprintable(Exception):
    """This file cannot be turned into a PDF on this machine, and retrying will
    not change that. Carries the reason, for the alert."""


def needs_conversion(path: str) -> bool:
    """True when this file is not already something SumatraPDF can print."""
    return os.path.splitext(path)[1].lower() not in PDF_EXTS


def to_printable_pdf(src_path: str, out_dir: str | None = None,
                     paper_size: str | None = None) -> str:
    """Return a path to a PDF of ``src_path``, converting if needed.

    Returns ``src_path`` unchanged when it is already a PDF — so callers can
    wrap every print with this and pay nothing for the common case.

    Raises ``Unprintable`` when no conversion is possible on this machine. That
    is a permanent condition: the caller must alert and stop, not back off and
    try the same file again.
    """
    # A PDF passes straight through WITHOUT an existence check. Callers already
    # handle a missing file — send_to_printer looks in Jobs\Archive for it, and
    # plan_print_job returns a fallback action — and turning a missing PDF into
    # a permanent Unprintable here would quietly take that recovery away.
    ext = os.path.splitext(src_path)[1].lower()
    if ext in PDF_EXTS:
        return src_path

    if not os.path.exists(src_path):
        raise Unprintable(f"file not found: {src_path}")

    out_dir = out_dir or os.path.dirname(os.path.abspath(src_path))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src_path))[0]
    out_path = os.path.join(out_dir, f"{base}.converted.pdf")

    if ext in IMAGE_EXTS:
        _image_to_pdf(src_path, out_path, paper_size)
    elif ext in OFFICE_APPS:
        _office_to_pdf(src_path, out_path, ext)
    else:
        raise Unprintable(
            f"no converter for {ext or 'a file with no extension'} — "
            f"{os.path.basename(src_path)} cannot be printed. Ask the customer "
            "for a PDF, or open it on the counter PC and print it by hand."
        )

    if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
        raise Unprintable(
            f"converting {os.path.basename(src_path)} produced no PDF — "
            "the converter ran and wrote nothing"
        )
    log.info("printable: converted %s -> %s", src_path, out_path)
    return out_path


def _page_rect(paper_size: str | None):
    import fitz

    key = (paper_size or "a4").strip().lower()
    w, h = PAGE_SIZES.get(key, PAGE_SIZES["a4"])
    return fitz.Rect(0, 0, w, h)


def _image_to_pdf(src_path: str, out_path: str, paper_size: str | None) -> None:
    """One image, one page, fitted to the sheet with its aspect ratio kept.

    A photo is never stretched to the paper: a WhatsApp photo of a page of
    notes is usually 3:4 and the sheet is 1:1.41, and stretching it is how a
    customer gets back something they did not send.
    """
    try:
        import fitz
    except ImportError as exc:               # pragma: no cover - env-specific
        raise Unprintable(f"PyMuPDF is not installed on this box ({exc})") from exc

    try:
        page_rect = _page_rect(paper_size)
        img = fitz.open(src_path)
        if img.page_count == 0:
            raise Unprintable(f"{os.path.basename(src_path)} holds no image")
        pdf_bytes = img.convert_to_pdf()
        img.close()
        src = fitz.open("pdf", pdf_bytes)

        out = fitz.open()
        for src_page in src:
            box = src_page.rect
            # Rotate the sheet, not the picture, when the picture is wider than
            # it is tall — a landscape photo on a portrait sheet wastes half the
            # paper and shrinks the writing to unreadable.
            target = page_rect
            if box.width > box.height:
                target = fitz.Rect(0, 0, page_rect.height, page_rect.width)
            scale = min(target.width / box.width, target.height / box.height)
            w, h = box.width * scale, box.height * scale
            x = (target.width - w) / 2
            y = (target.height - h) / 2
            page = out.new_page(width=target.width, height=target.height)
            page.show_pdf_page(fitz.Rect(x, y, x + w, y + h), src, src_page.number)
        out.save(out_path)
        out.close()
        src.close()
    except Unprintable:
        raise
    except Exception as exc:
        raise Unprintable(
            f"could not render {os.path.basename(src_path)} as a page ({exc})"
        ) from exc


def _office_to_pdf(src_path: str, out_path: str, ext: str) -> None:
    """Export a Word/PowerPoint/Excel file through the installed application.

    Same mechanism watcher.py already uses to count pages, kept deliberately
    close to it. Available only where Office is: a store PC. Anywhere else this
    is a permanent failure with the reason said out loud, rather than a silent
    fall-through to handing SumatraPDF a file it cannot read.
    """
    prog_id, fmt = OFFICE_APPS[ext]
    try:
        import win32com.client as wc
    except ImportError as exc:
        raise Unprintable(
            f"{os.path.basename(src_path)} needs {prog_id.split('.')[0]} to become a "
            f"PDF, and this machine has no Office automation available ({exc})"
        ) from exc

    src_path = os.path.abspath(src_path)
    out_path = os.path.abspath(out_path)
    name = prog_id.split(".")[0]

    # DispatchEx, never Dispatch. `Dispatch` hands back an ALREADY RUNNING
    # instance if there is one — which on a shop counter PC there usually is,
    # opened by a person. OSP, 2026-09-10: a Word window left open with a modal
    # dialog in it failed every automated export with "You cannot close
    # Microsoft Word because a dialog box is open", and `DisplayAlerts = False`
    # cannot dismiss a dialog that was already up. `DispatchEx` always starts a
    # fresh out-of-process instance, which is ours to drive and ours to quit.
    try:
        app = wc.DispatchEx(prog_id)
    except Exception as exc:
        raise Unprintable(
            f"{name} is installed but would not start for automation ({exc}) — "
            f"that is a fault on this machine, not with "
            f"{os.path.basename(src_path)}."
        ) from exc

    try:
        try:
            app.Visible = False
        except Exception as exc:
            # PowerPoint refuses to be made invisible. Harmless — the export
            # still runs — but said, not swallowed.
            log.debug("printable: %s would not hide itself (%s)", prog_id, exc)
        try:
            app.DisplayAlerts = False
        except Exception as exc:
            log.debug("printable: %s has no DisplayAlerts (%s)", prog_id, exc)
        if prog_id == "Word.Application":
            doc = app.Documents.Open(src_path, ReadOnly=True)
            doc.ExportAsFixedFormat(out_path, fmt)
            doc.Close(False)
        elif prog_id == "PowerPoint.Application":
            prs = app.Presentations.Open(src_path, ReadOnly=True, WithWindow=False)
            prs.SaveAs(out_path, fmt)
            prs.Close()
        else:
            wb = app.Workbooks.Open(src_path, ReadOnly=True)
            wb.ExportAsFixedFormat(fmt, out_path)
            wb.Close(False)
    except Exception as exc:
        # Two very different causes produce the same exception here, and the
        # message used to assert the first one. Naming only "corrupt" sent
        # someone to ask a customer to re-send a perfectly good file while the
        # actual fault — a Word window open on the counter — sat untouched.
        raise Unprintable(
            f"{name} could not export {os.path.basename(src_path)} to PDF "
            f"({exc}) — the document may be password protected or corrupt, or "
            f"{name} may be stuck on this box: a {name} window left open with a "
            f"dialog in it fails exactly like this and no automation can "
            f"dismiss it. Check for a running {name} on the store PC before "
            f"asking the customer for a PDF."
        ) from exc
    finally:
        try:
            app.Quit()
        except Exception:
            log.debug("printable: %s did not quit cleanly", prog_id)
