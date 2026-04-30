"""Split PDF pages down the middle (vertical or horizontal), with optional
auto-deskew for scan-like pages.

Vector pages: clipped via PyMuPDF's `show_pdf_page(clip=...)` — preserves
text and vector quality.

Scan-like pages (low extracted-text length): rasterized at high DPI, deskewed
via a numpy-only projection-profile method, then split as images and re-embedded.
"""
from __future__ import annotations

import io
from typing import List, Optional, Tuple

import fitz
import numpy as np
from PIL import Image


SCAN_TEXT_THRESHOLD = 5  # chars; below this a page is considered scan-like

# Deskew search parameters
_SKEW_SEARCH_RANGE = 10.0   # degrees; ±range
_SKEW_COARSE_STEP = 1.0     # degrees; pass 1
_SKEW_FINE_STEP = 0.1       # degrees; pass 2 refinement
_SKEW_MAX_DIM = 800         # downsample target for speed


def _is_scan_like(page: "fitz.Page") -> bool:
    text = page.get_text("text") or ""
    return len(text.strip()) < SCAN_TEXT_THRESHOLD


def _render_page(page: "fitz.Page", dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _projection_variance(binary: np.ndarray, angle: float) -> float:
    """Score alignment at `angle`: variance of horizontal row sums after rotation.
    Higher = text lines better aligned with horizontal axis.
    """
    pil = Image.fromarray(binary.astype(np.uint8) * 255, mode="L")
    rotated = np.array(pil.rotate(angle, resample=Image.NEAREST, fillcolor=0))
    row_sums = rotated.sum(axis=1).astype(np.float64)
    return float(row_sums.var())


def _detect_skew_angle(img: Image.Image) -> float:
    """Detect skew via 2-pass projection-profile search. Returns the *correction*
    angle — the value to pass to PIL.Image.rotate to straighten the image.

    Numpy + PIL only (no scikit-image, no deskew PyPI package).
    """
    w, h = img.size
    if max(w, h) > _SKEW_MAX_DIM:
        scale = _SKEW_MAX_DIM / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))))

    gray = np.array(img.convert("L"))
    binary = gray < gray.mean() * 0.9  # ink = True
    if binary.sum() == 0 or binary.sum() == binary.size:
        return 0.0  # blank or fully black — no structure

    coarse_angles = np.arange(
        -_SKEW_SEARCH_RANGE, _SKEW_SEARCH_RANGE + _SKEW_COARSE_STEP, _SKEW_COARSE_STEP
    )
    best_angle = 0.0
    best_score = -1.0
    for a in coarse_angles:
        s = _projection_variance(binary, float(a))
        if s > best_score:
            best_score = s
            best_angle = float(a)

    fine_angles = np.arange(
        best_angle - _SKEW_COARSE_STEP,
        best_angle + _SKEW_COARSE_STEP + _SKEW_FINE_STEP,
        _SKEW_FINE_STEP,
    )
    for a in fine_angles:
        s = _projection_variance(binary, float(a))
        if s > best_score:
            best_score = s
            best_angle = float(a)

    return best_angle


def _deskew_image(img: Image.Image, threshold_deg: float) -> Image.Image:
    angle = _detect_skew_angle(img)
    if abs(angle) < threshold_deg:
        return img
    return img.rotate(
        angle,
        resample=Image.BICUBIC,
        expand=True,
        fillcolor=(255, 255, 255),
    )


def _split_image(
    img: Image.Image, direction: str, ratio: float
) -> Tuple[Image.Image, Image.Image]:
    w, h = img.size
    if direction == "vertical":
        x = int(w * ratio)
        return img.crop((0, 0, x, h)), img.crop((x, 0, w, h))
    y = int(h * ratio)
    return img.crop((0, 0, w, y)), img.crop((0, y, w, h))


def _png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _split_vector_page(
    out_doc: "fitz.Document",
    src_doc: "fitz.Document",
    page_idx: int,
    direction: str,
    ratio: float,
    rtl: bool,
) -> None:
    rect = src_doc[page_idx].rect
    if direction == "vertical":
        x = rect.x0 + rect.width * ratio
        a_clip = fitz.Rect(rect.x0, rect.y0, x, rect.y1)
        b_clip = fitz.Rect(x, rect.y0, rect.x1, rect.y1)
    else:
        y = rect.y0 + rect.height * ratio
        a_clip = fitz.Rect(rect.x0, rect.y0, rect.x1, y)
        b_clip = fitz.Rect(rect.x0, y, rect.x1, rect.y1)

    halves = [a_clip, b_clip]
    if rtl and direction == "vertical":
        halves.reverse()

    for clip in halves:
        new_page = out_doc.new_page(width=clip.width, height=clip.height)
        target = fitz.Rect(0, 0, clip.width, clip.height)
        new_page.show_pdf_page(target, src_doc, page_idx, clip=clip)


def _split_raster_page(
    out_doc: "fitz.Document",
    src_doc: "fitz.Document",
    page_idx: int,
    direction: str,
    ratio: float,
    rtl: bool,
    deskew: bool,
    deskew_threshold: float,
    dpi: int,
) -> None:
    img = _render_page(src_doc[page_idx], dpi=dpi)
    if deskew:
        img = _deskew_image(img, deskew_threshold)

    a, b = _split_image(img, direction, ratio)
    halves = [a, b]
    if rtl and direction == "vertical":
        halves.reverse()

    pt_per_px = 72.0 / dpi
    for half in halves:
        w_pt = half.width * pt_per_px
        h_pt = half.height * pt_per_px
        new_page = out_doc.new_page(width=w_pt, height=h_pt)
        new_page.insert_image(fitz.Rect(0, 0, w_pt, h_pt), stream=_png_bytes(half))


def _copy_page(
    out_doc: "fitz.Document", src_doc: "fitz.Document", page_idx: int
) -> None:
    out_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)


def split_pdf(
    file_bytes: bytes,
    direction: str = "vertical",
    ratio: float = 0.5,
    exclude_pages: Optional[List[int]] = None,
    rtl: bool = False,
    page_range: Optional[Tuple[int, int]] = None,
    deskew: bool = True,
    deskew_threshold: float = 0.1,
    dpi: int = 300,
) -> bytes:
    """Split each page in `file_bytes` along `direction` at `ratio`.

    Args:
        file_bytes: source PDF bytes.
        direction: "vertical" (left/right halves) or "horizontal" (top/bottom).
        ratio: split position, exclusive (0, 1). 0.5 = exact middle.
        exclude_pages: 0-indexed page numbers to copy through unchanged.
        rtl: for vertical splits, emit right half before left half.
        page_range: inclusive (start, end) 0-indexed range to process.
                    Pages outside the range are copied through.
        deskew: detect and correct skew on scan-like pages before splitting.
        deskew_threshold: minimum |angle| in degrees to apply rotation.
        dpi: render DPI for scan-like pages.

    Returns:
        New PDF as bytes.
    """
    if direction not in ("vertical", "horizontal"):
        raise ValueError(
            f"direction must be 'vertical' or 'horizontal', got {direction!r}"
        )
    if not 0.0 < ratio < 1.0:
        raise ValueError(f"ratio must be in (0, 1), got {ratio}")

    exclude_set = set(exclude_pages or [])
    src_doc = fitz.open(stream=file_bytes, filetype="pdf")
    out_doc = fitz.open()

    try:
        page_count = len(src_doc)
        if page_range is None:
            start, end = 0, page_count - 1
        else:
            start = max(0, page_range[0])
            end = min(page_count - 1, page_range[1])

        for i in range(page_count):
            if i < start or i > end or i in exclude_set:
                _copy_page(out_doc, src_doc, i)
                continue

            if _is_scan_like(src_doc[i]):
                _split_raster_page(
                    out_doc, src_doc, i, direction, ratio, rtl,
                    deskew, deskew_threshold, dpi,
                )
            else:
                _split_vector_page(out_doc, src_doc, i, direction, ratio, rtl)

        buf = io.BytesIO()
        out_doc.save(buf)
        return buf.getvalue()
    finally:
        src_doc.close()
        out_doc.close()
