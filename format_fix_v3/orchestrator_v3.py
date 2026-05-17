"""v3 orchestrator: PDF (or DOCX) -> Claude Vision per page -> DOCX.

Entry points:
    run_v3_from_pdf(pdf_bytes, *, max_pages=None, max_workers=5,
                     render_kwargs=None) -> dict
    run_v3_from_docx(docx_bytes, *, ...) -> dict

For DOCX inputs we extract embedded images directly via python-docx
(matching the source paragraph-by-paragraph order) rather than relying
on fitz's DOCX->PDF conversion, which rasterizes images and loses the
xref bytes. Aswathy fix #2.

Parallelism: Claude calls per page are dispatched via ThreadPoolExecutor.
Default 5 workers (safe for Anthropic Tier 1: ~33K ITPM sustained, under
the 30K limit by burst tolerance).
"""
from __future__ import annotations

import io
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import fitz  # PyMuPDF

from . import vision, renderer


DPI = 150  # ~1240x1754 for A4 -> ~1600 image tokens per page

# Cost rates
USD_INR = 83.0
SONNET_INPUT_USD_PER_1M = 3.0
SONNET_OUTPUT_USD_PER_1M = 15.0
SONNET_CACHE_READ_USD_PER_1M = 0.30
SONNET_CACHE_WRITE_USD_PER_1M = 3.75


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
            "_usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "elapsed_ms": 0,
                "model": vision.VISION_MODEL,
                "error": f"vision_error: {type(e).__name__}: {str(e)[:200]}",
            },
        }
    return p_idx, result


def _compute_call_cost_usd(usage: dict[str, Any]) -> float:
    """Cost in USD for a single Claude call, given its usage dict."""
    return (
        (usage.get("input_tokens", 0) * SONNET_INPUT_USD_PER_1M
         + usage.get("output_tokens", 0) * SONNET_OUTPUT_USD_PER_1M
         + usage.get("cache_read_tokens", 0) * SONNET_CACHE_READ_USD_PER_1M
         + usage.get("cache_write_tokens", 0) * SONNET_CACHE_WRITE_USD_PER_1M)
        / 1_000_000
    )


def _extract_docx_images_per_page(docx_bytes: bytes) -> list[list[bytes]]:
    """Extract images from a DOCX, grouped per virtual page.

    Walks body in source order. Tracks page breaks (explicit page-breaks
    inside <w:br type="page"/>, and section breaks). For each <a:blip>
    inside a paragraph run, resolves the r:embed -> blob from
    doc.part.related_parts and appends to the CURRENT page list.

    Returns: list[list[bytes]] - one list per virtual page, in source order.
    """
    try:
        from docx import Document as _Doc
    except Exception:
        return []

    try:
        doc = _Doc(io.BytesIO(docx_bytes))
    except Exception:
        return []

    from docx.oxml.ns import qn as _qn

    pages: list[list[bytes]] = [[]]   # at least one page
    related_parts = doc.part.related_parts

    def _images_in_paragraph(p_xml) -> list[bytes]:
        """Walk <a:blip> elements inside a paragraph and resolve to bytes."""
        out: list[bytes] = []
        # Use generic XPath since blip namespace is well-known
        BLIP_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
        EMBED_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
        for blip in p_xml.iter(BLIP_TAG):
            rid = blip.get(EMBED_ATTR)
            if not rid:
                continue
            part = related_parts.get(rid)
            if part is None or not hasattr(part, "blob"):
                continue
            try:
                blob = part.blob
                if blob and len(blob) > 200:
                    # Skip tiny images (icons, decorations <200 bytes)
                    out.append(blob)
            except Exception:
                continue
        return out

    def _paragraph_has_page_break(p_xml) -> bool:
        for br in p_xml.iter(_qn("w:br")):
            if br.get(_qn("w:type")) == "page":
                return True
        return False

    # Walk body children in document order: <w:p>, <w:tbl>, <w:sectPr> etc.
    para_count = 0
    para_per_page_budget = 25   # fallback split if no explicit page breaks
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == _qn("w:p"):
            para_count += 1
            new_imgs = _images_in_paragraph(child)
            if new_imgs:
                pages[-1].extend(new_imgs)
            if _paragraph_has_page_break(child):
                pages.append([])
                para_count = 0
            elif para_count >= para_per_page_budget:
                # Soft page break to keep per-page lists short. May not
                # exactly match the fitz-rendered PDF page boundaries
                # but the renderer picks images in source order regardless.
                pages.append([])
                para_count = 0
        elif tag == _qn("w:tbl"):
            # Tables can also contain images (rare but legal)
            for tcell_p in child.iter(_qn("w:p")):
                new_imgs = _images_in_paragraph(tcell_p)
                if new_imgs:
                    pages[-1].extend(new_imgs)
        elif tag == _qn("w:sectPr"):
            pages.append([])
            para_count = 0
    return pages


def _flatten_images_to_pdf_pages(
    docx_page_images: list[list[bytes]],
    n_pdf_pages: int,
) -> list[list[bytes]]:
    """Best-effort: distribute DOCX-extracted images across PDF pages.

    The DOCX has its own "page" concept (from <w:br type=page> + section
    breaks), but after fitz converts DOCX -> PDF the page boundaries can
    shift slightly. Simplest robust approach: concatenate all images in
    source order, then re-bucket using the same source order across the
    n_pdf_pages buckets. If the DOCX has the same number of "pages" as
    the PDF, we pass through 1:1. Otherwise we flatten and re-distribute.
    """
    if len(docx_page_images) == n_pdf_pages:
        return docx_page_images
    # Flatten in source order; put ALL images on page 0 so the renderer
    # will consume them in the same order Claude emits image elements.
    # (Most "image element on page X" decisions come from Claude's vision
    # analysis, not from our bucket allocation.)
    flat: list[bytes] = []
    for page_imgs in docx_page_images:
        flat.extend(page_imgs)
    out: list[list[bytes]] = [[] for _ in range(max(1, n_pdf_pages))]
    out[0] = flat
    return out


def _build_per_call_telemetry(
    page_results: list[dict[str, Any] | None],
    job_token: str,
    engine: str,
    university: str | None,
) -> list[dict[str, Any]]:
    """Build telemetry rows ready for batch insert into pb_api_calls."""
    rows = []
    for p_idx, r in enumerate(page_results):
        if r is None:
            continue
        u = r.get("_usage", {})
        cost_usd = _compute_call_cost_usd(u)
        rows.append({
            "job_token": job_token,
            "engine": engine,
            "page_no": p_idx + 1,
            "model": u.get("model"),
            "input_tokens":       u.get("input_tokens", 0),
            "output_tokens":      u.get("output_tokens", 0),
            "cache_read_tokens":  u.get("cache_read_tokens", 0),
            "cache_write_tokens": u.get("cache_write_tokens", 0),
            "cost_usd": round(cost_usd, 6),
            "cost_inr": round(cost_usd * USD_INR, 4),
            "elapsed_ms": u.get("elapsed_ms"),
            "error": u.get("error"),
            "university": university,
        })
    return rows


def run_v3_from_pdf(
    pdf_bytes: bytes,
    *,
    max_pages: int | None = None,
    max_workers: int = 5,
    render_kwargs: dict[str, Any] | None = None,
    docx_page_images: list[list[bytes]] | None = None,
    job_token: str | None = None,
    university: str | None = None,
) -> dict[str, Any]:
    """Convert PDF bytes -> styled DOCX bytes via Claude Vision.

    Args:
        pdf_bytes: PDF source.
        max_pages: Limit number of pages processed (for testing).
        max_workers: Parallel Claude calls. 5 is safe for Tier 1.
        render_kwargs: Forwarded to renderer.render_pages (page_numbers,
            page_number_style, header_text, footer_text, etc.)
        docx_page_images: Optional pre-extracted images (used when caller
            converted from DOCX and wants to override the PDF-rasterized
            images with the original DOCX blobs).
        job_token: Used for telemetry rows. If absent, telemetry omitted.
        university: Used for telemetry rows.

    Returns:
        {
            docx_bytes, page_count, total_input_tokens, total_output_tokens,
            total_cache_read_tokens, total_cache_write_tokens,
            estimated_cost_usd, estimated_cost_inr, elapsed_seconds,
            page_kinds, project_title, per_call_telemetry
        }
    """
    t0 = time.time()
    pdf = fitz.open(stream=pdf_bytes, filetype="pdf")
    n_pages = pdf.page_count
    if max_pages is not None:
        n_pages = min(n_pages, max_pages)

    rendered = []
    pdf_extracted_images: list[list[bytes]] = []
    for p_idx in range(n_pages):
        png, text = _render_page_png_and_text(pdf, p_idx, DPI)
        rendered.append((p_idx, png, text, n_pages))
        pdf_extracted_images.append(_extract_page_images(pdf, p_idx))

    pdf.close()

    # Decide which image set to feed the renderer:
    # - If caller supplied docx_page_images (DOCX input path), use those.
    # - Otherwise use what we pulled from the PDF directly.
    if docx_page_images is not None:
        page_images = _flatten_images_to_pdf_pages(
            docx_page_images, n_pages,
        )
    else:
        page_images = pdf_extracted_images

    page_results: list[dict[str, Any] | None] = [None] * n_pages
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for p_idx, result in ex.map(_analyze_one, rendered):
            page_results[p_idx] = result

    total_in = 0
    total_out = 0
    total_cache_read = 0
    total_cache_write = 0
    page_kinds = []
    for r in page_results:
        if r is None:
            page_kinds.append("missing")
            continue
        u = r.get("_usage", {})
        total_in += u.get("input_tokens", 0)
        total_out += u.get("output_tokens", 0)
        total_cache_read += u.get("cache_read_tokens", 0)
        total_cache_write += u.get("cache_write_tokens", 0)
        page_kinds.append(r.get("page_kind", "other"))

    cleaned = [r for r in page_results if r is not None]

    # Render
    rk = dict(render_kwargs or {})
    docx_bytes = renderer.render_pages(cleaned, page_images, **rk)

    # Cost
    cost_usd = (
        (total_in * SONNET_INPUT_USD_PER_1M
         + total_out * SONNET_OUTPUT_USD_PER_1M
         + total_cache_read * SONNET_CACHE_READ_USD_PER_1M
         + total_cache_write * SONNET_CACHE_WRITE_USD_PER_1M)
        / 1_000_000
    )

    # Project title for smart filename
    project_title = renderer.extract_project_title(cleaned)

    # Per-call telemetry rows (caller decides whether to persist them)
    per_call = []
    if job_token:
        per_call = _build_per_call_telemetry(
            page_results, job_token, "format_fix_v3", university,
        )

    return {
        "docx_bytes": docx_bytes,
        "page_count": n_pages,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_write_tokens": total_cache_write,
        "estimated_cost_usd": round(cost_usd, 6),
        "estimated_cost_inr": round(cost_usd * USD_INR, 4),
        "elapsed_seconds": round(time.time() - t0, 1),
        "page_kinds": page_kinds,
        "project_title": project_title,
        "per_call_telemetry": per_call,
    }


def run_v3_from_docx(
    docx_bytes: bytes,
    *,
    max_pages: int | None = None,
    max_workers: int = 5,
    render_kwargs: dict[str, Any] | None = None,
    job_token: str | None = None,
    university: str | None = None,
) -> dict[str, Any]:
    """DOCX -> PDF (visual analysis) + direct DOCX image extraction -> DOCX.

    The DOCX is converted to a PDF via fitz for Claude Vision (so the
    model sees rendered pages), but embedded images are extracted from
    the ORIGINAL DOCX via python-docx — preserving the actual image
    bytes rather than relying on the lossy fitz rasterization.
    Aswathy fix #2.
    """
    # Step 1: extract images directly from DOCX (preserves bytes)
    docx_page_images = _extract_docx_images_per_page(docx_bytes)

    # Step 2: convert DOCX -> PDF for vision analysis
    try:
        doc = fitz.open(stream=docx_bytes, filetype="docx")
    except Exception as e:
        raise RuntimeError(
            f"DOCX rendering not supported by this PyMuPDF build: {e}"
        )
    pdf_bytes = doc.convert_to_pdf()
    doc.close()

    # Step 3: route through the PDF path, but override images with DOCX blobs
    return run_v3_from_pdf(
        pdf_bytes,
        max_pages=max_pages,
        max_workers=max_workers,
        render_kwargs=render_kwargs,
        docx_page_images=docx_page_images,
        job_token=job_token,
        university=university,
    )
