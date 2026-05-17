"""v3 orchestrator: PDF (or DOCX) -> Claude Vision per page -> DOCX.

Entry points:
    run_v3_from_pdf(pdf_bytes, *, max_pages=None, max_workers=5) -> dict
    run_v3_from_docx(docx_bytes, *, ...) -> dict
        (renders docx to PDF first via fitz, then same path)

Parallelism: Claude calls per page are dispatched via ThreadPoolExecutor
to keep total wall-clock under Vercel's 300s ceiling. Default 5 workers
matches Anthropic's burst-friendly tier without tripping rate limits on
most accounts.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import fitz  # PyMuPDF

from . import vision, renderer


DPI = 150  # ~1240x1754 for A4 -> ~1600 image tokens per page


def _render_page_png_and_text(pdf, page_no_0idx: int, dpi: int):
    """Return (png_bytes, raw_text)."""
    page = pdf[page_no_0idx]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png"), page.get_text("text")


def _extract_page_images(pdf, page_no_0idx: int) -> list[bytes]:
    """Return all embedded image bytes on a page in source order.

    Uses PyMuPDF's page.get_images(full=True). Skips decorative tiny
    images (< 100px in either dimension) to avoid header/footer logos
    bloating the doc.
    """
    page = pdf[page_no_0idx]
    out: list[bytes] = []
    for info in page.get_images(full=True):
        xref = info[0]
        try:
            pix = fitz.Pixmap(pdf, xref)
            if pix.width < 100 or pix.height < 100:
                pix = None
                continue
            if pix.alpha or pix.n > 4:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            out.append(pix.tobytes("png"))
            pix = None
        except Exception:
            continue
    return out


def _analyze_one(args: tuple) -> tuple[int, dict[str, Any]]:
    """Worker: call Claude Vision for one page. Returns (idx, json_dict)."""
    p_idx, png, text_hint, total = args
    try:
        result = vision.analyze_page(png, text_hint, p_idx + 1, total)
    except Exception as e:
        result = {
            "page_kind": "other",
            "elements": [],
            "notes": f"vision_error: {e}",
            "_usage": {"input_tokens": 0, "output_tokens": 0},
        }
    return p_idx, result


def run_v3_from_pdf(
    pdf_bytes: bytes,
    *,
    max_pages: int | None = None,
    max_workers: int = 5,
) -> dict[str, Any]:
    """Convert PDF bytes -> styled DOCX bytes via Claude Vision.

    Returns:
        {
            "docx_bytes": bytes,
            "page_count": int,
            "total_input_tokens": int,
            "total_output_tokens": int,
            "estimated_cost_usd": float,
            "estimated_cost_inr": float,
            "elapsed_seconds": float,
            "page_kinds": [str, ...]   # for diagnostics
        }
    """
    t0 = time.time()
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    n_pages = pdf.page_count
    if max_pages is not None:
        n_pages = min(n_pages, max_pages)

    # Step 1: render PNG + extract text + extract images (sequential, fast).
    rendered = []
    page_images: list[list[bytes]] = []
    for p_idx in range(n_pages):
        png, text = _render_page_png_and_text(pdf, p_idx, DPI)
        rendered.append((p_idx, png, text, n_pages))
        page_images.append(_extract_page_images(pdf, p_idx))

    pdf.close()

    # Step 2: parallel Claude calls.
    page_results: list[dict[str, Any] | None] = [None] * n_pages
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for p_idx, result in ex.map(_analyze_one, rendered):
            page_results[p_idx] = result

    # Cost totals.
    total_in = 0
    total_out = 0
    page_kinds = []
    for r in page_results:
        if r is None:
            page_kinds.append("missing")
            continue
        u = r.get("_usage", {})
        total_in += u.get("input_tokens", 0)
        total_out += u.get("output_tokens", 0)
        page_kinds.append(r.get("page_kind", "other"))

    # Step 3: render to DOCX.
    docx_bytes = renderer.render_pages(
        [r for r in page_results if r is not None],
        page_images,
    )

    # Sonnet 4.5 pricing: $3/Mtok in, $15/Mtok out
    cost_usd = (total_in * 3 + total_out * 15) / 1_000_000

    return {
        "docx_bytes": docx_bytes,
        "page_count": n_pages,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "estimated_cost_usd": round(cost_usd, 4),
        "estimated_cost_inr": round(cost_usd * 83, 2),
        "elapsed_seconds": round(time.time() - t0, 1),
        "page_kinds": page_kinds,
    }


def run_v3_from_docx(
    docx_bytes: bytes,
    *,
    max_pages: int | None = None,
    max_workers: int = 5,
) -> dict[str, Any]:
    """DOCX -> PDF (via fitz) -> run_v3_from_pdf path.

    Note: this round-trips through a fitz-rendered PDF, which preserves
    visual layout (the whole point of v3 - Claude sees pixels, not OOXML).
    Requires PyMuPDF build with DOCX support (Windows wheels include it).
    """
    try:
        doc = fitz.open(stream=docx_bytes, filetype="docx")
    except Exception as e:
        raise RuntimeError(
            f"DOCX rendering not supported by this PyMuPDF build: {e}"
        )
    pdf_bytes = doc.convert_to_pdf()
    doc.close()
    return run_v3_from_pdf(
        pdf_bytes, max_pages=max_pages, max_workers=max_workers,
    )
