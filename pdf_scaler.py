"""
PRINTOSKY PAGE SCALER
=====================
Fit / Actual size / Custom % — baked into the PDF, never asked of the driver.

Why baked: the Konica driver was caught silently ignoring SumatraPDF's per-job
duplex/simplex override in both directions (2026-08-29, see
docs/PRINT_ROTATION_MATRIX.md). Geometry written into the file is the one thing
every driver honours, which is why the imposer works that way. Scaling follows
the same rule — `noscale` is emitted alongside a baked file only as a guard
against a driver touching it, never as the mechanism.

Two functions, and the split matters:

    scale_rect()   pure geometry — where one page lands on the sheet.
    apply_scale()  bakes it — a real PDF whose pages are already correct.

The print path, the store-PC preview and the customer preview all read
`scale_rect`, so a preview cannot disagree with what actually prints.

Modes (owner decisions, 2026-08-30):
    "fit"     fill the printable area, aspect kept, centred
    "actual"  100 % of the page's own size, centred, cropped if it overflows
    "custom"  `percent` of the page's own size — NOT of the sheet, so 100 %
              means the document's own size wherever it lands, and
              `custom` at 100 % is exactly `actual`

Scaling never rotates. Rotation belongs to nup_imposer and stays there; this
module only ever moves and resizes a page on a portrait sheet.

Scaling is offered for 1-up only. N-up already *is* a fit, so the planner never
calls this for `nup >= 2`.
"""

import io
import logging

# Paper dimensions come from the imposer so there is one table, not two.
from nup_imposer import portrait_sheet

logger = logging.getLogger("pdf_scaler")

MODES = ("fit", "actual", "custom")

# Custom % bounds (owner, 2026-08-30). Clamped, never rejected — a typo should
# print something sane, not fail the job.
MIN_PERCENT = 25
MAX_PERCENT = 400

# Margin used by `fit`, matching nup_imposer's default so a 1-up fit and an
# imposed sheet leave the same border. `actual` and `custom` ignore it: they
# place the page at its true size and let it crop if it must.
FIT_MARGIN_PT = 20.0

# Two sizes within this many points are the same size — PDF page boxes carry
# rounding from whatever produced them.
_SAME_SIZE_TOL = 1.0


def clamp_percent(percent) -> int | None:
    """Bring a custom percentage into range, or None if it isn't a number."""
    try:
        p = int(round(float(percent)))
    except (TypeError, ValueError):
        return None
    return max(MIN_PERCENT, min(MAX_PERCENT, p))


def _placement(page_w: float, page_h: float, sheet: str,
               mode: str, percent: int | None) -> dict:
    """Where this page lands, always — the no-op shortcuts live in scale_rect."""
    sheet_w, sheet_h = portrait_sheet(sheet)

    if mode == "fit":
        avail_w = max(1.0, sheet_w - 2 * FIT_MARGIN_PT)
        avail_h = max(1.0, sheet_h - 2 * FIT_MARGIN_PT)
        scale = min(avail_w / page_w, avail_h / page_h)
    elif mode == "actual":
        scale = 1.0
    else:  # custom
        scale = (percent or 100) / 100.0

    out_w, out_h = page_w * scale, page_h * scale
    # Centred. A page wider than the sheet gets a negative origin, which is
    # exactly right: it overhangs evenly on both sides and crops evenly.
    x0 = (sheet_w - out_w) / 2.0
    y0 = (sheet_h - out_h) / 2.0

    crops = (out_w - sheet_w > _SAME_SIZE_TOL) or (out_h - sheet_h > _SAME_SIZE_TOL)
    return {
        "mode": mode, "percent": percent,
        "sheet": sheet, "sheet_w": sheet_w, "sheet_h": sheet_h,
        "page_w": page_w, "page_h": page_h,
        "x0": x0, "y0": y0, "x1": x0 + out_w, "y1": y0 + out_h,
        "width": out_w, "height": out_h,
        "scale": scale, "crops": crops,
    }


def scale_rect(page_w: float, page_h: float, sheet: str = "A4",
               mode: str = "fit", percent=None) -> dict | None:
    """Where one page lands on the sheet, or None when nothing need be done.

    None is the important return: it means the caller prints the original file
    byte-for-byte, exactly as it does today. Returned for an absent or unknown
    mode, a custom without a usable percent, and an `actual` whose page is
    already the sheet size.

    All measurements are PDF points, origin top-left (PyMuPDF's convention).
    """
    mode = (mode or "").strip().lower()
    if mode not in MODES:
        return None
    if page_w <= 0 or page_h <= 0:
        return None

    if mode == "custom":
        percent = clamp_percent(percent)
        if percent is None:
            return None
        # Custom at 100 % IS Actual size (owner, 2026-08-30) — same mode, same
        # percent, same rect, so the two can never drift apart.
        if percent == 100:
            mode, percent = "actual", None
    else:
        percent = None

    if mode == "actual":
        sheet_w, sheet_h = portrait_sheet(sheet)
        if (abs(page_w - sheet_w) <= _SAME_SIZE_TOL
                and abs(page_h - sheet_h) <= _SAME_SIZE_TOL):
            return None          # already exactly right — leave the file alone

    return _placement(page_w, page_h, sheet, mode, percent)


def page_sizes(pdf_bytes: bytes) -> list[tuple[float, float]]:
    """Every page's size in points. Rotation-aware — a /Rotate 90 page reports
    the size it presents, which is the size that has to fit on the sheet."""
    import fitz
    doc = fitz.open("pdf", pdf_bytes)
    try:
        return [(p.rect.width, p.rect.height) for p in doc]
    finally:
        doc.close()


def count_cropped_pages(pdf_bytes: bytes, mode: str, percent=None,
                        paper_size: str = "A4") -> int:
    """How many pages would lose content at these settings — the number the
    preview shows as "N pages will be cropped"."""
    n = 0
    for w, h in page_sizes(pdf_bytes):
        r = scale_rect(w, h, paper_size, mode, percent)
        if r and r["crops"]:
            n += 1
    return n


def apply_scale(pdf_bytes: bytes, mode: str, percent=None,
                paper_size: str = "A4") -> bytes | None:
    """Bake the scaling into a new PDF, or None when no transform is needed.

    One output page per input page, at sheet size, with the source page drawn
    into `scale_rect`'s rectangle. Pages may differ in size within a document;
    each is placed on its own terms, and the output is uniform sheets.

    Returns None when every page is a no-op, so the caller prints the original
    file untouched rather than a re-written copy of it.

    Content only: `show_pdf_page` copies the rendered page, so annotations,
    form fields and links are not carried across. That is what we want for a
    print — the counter prints what the customer saw.
    """
    import fitz

    mode = (mode or "").strip().lower()
    if mode not in MODES:
        return None

    doc_in = fitz.open("pdf", pdf_bytes)
    try:
        if len(doc_in) == 0:
            return None

        rects = [scale_rect(p.rect.width, p.rect.height, paper_size, mode, percent)
                 for p in doc_in]
        if not any(rects):
            return None          # nothing to do on any page

        sheet_w, sheet_h = portrait_sheet(paper_size)
        doc_out = fitz.open()
        try:
            for idx, page in enumerate(doc_in):
                place = rects[idx] or _placement(
                    page.rect.width, page.rect.height, paper_size,
                    "actual" if mode == "custom" else mode,
                    None,
                )
                out = doc_out.new_page(width=sheet_w, height=sheet_h)
                out.show_pdf_page(
                    fitz.Rect(place["x0"], place["y0"], place["x1"], place["y1"]),
                    doc_in, idx,
                )
            stream = io.BytesIO()
            doc_out.save(stream)
            return stream.getvalue()
        finally:
            doc_out.close()
    finally:
        doc_in.close()
