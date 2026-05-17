"""Render Claude Vision JSON to .docx.

Walks the list of per-page JSON results returned by vision.analyze_page
and produces an A4 Word document with proper headings, alignment,
bold/italic spans, tables, and embedded images.

Page-image pairing: each page JSON may have N "image" elements; the
caller supplies the actual image bytes extracted from that PDF page
(in source order). We embed them at the position the image element
appears in the JSON stream.
"""
from __future__ import annotations

import io
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Inches, Pt


ALIGN_MAP = {
    "left":    WD_ALIGN_PARAGRAPH.LEFT,
    "center":  WD_ALIGN_PARAGRAPH.CENTER,
    "right":   WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

# Page kinds that should start on a fresh sheet.
PAGE_BREAK_BEFORE = {
    "title", "certificate", "acknowledgement", "declaration",
    "abstract", "toc", "list_of_figures", "list_of_tables",
    "references", "appendix",
}

# Element types that Word handles natively or that we drop.
SKIP_TYPES = {"page_number", "footer", "skip"}


def _setup_doc() -> "Document":
    """A4, 1-inch margins, Times New Roman 12 body."""
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    section.top_margin = Inches(1.0)
    section.bottom_margin = Inches(1.0)

    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(12)
    return doc


def _add_run(paragraph, text: str, *, bold: bool, italic: bool) -> None:
    """Add a run with bold/italic applied."""
    run = paragraph.add_run(text)
    if bold:
        run.bold = True
    if italic:
        run.italic = True


def _emit_heading(doc, el: dict[str, Any]) -> None:
    level = el.get("level") or 1
    if level not in (1, 2, 3):
        level = 2
    h = doc.add_heading(level=level)
    h.alignment = ALIGN_MAP.get(el.get("alignment", "left"),
                                 WD_ALIGN_PARAGRAPH.LEFT)
    _add_run(h, el.get("text", ""),
             bold=bool(el.get("bold", True)),
             italic=bool(el.get("italic", False)))


def _emit_body(doc, el: dict[str, Any]) -> None:
    p = doc.add_paragraph()
    p.alignment = ALIGN_MAP.get(el.get("alignment", "justify"),
                                 WD_ALIGN_PARAGRAPH.JUSTIFY)
    _add_run(p, el.get("text", ""),
             bold=bool(el.get("bold", False)),
             italic=bool(el.get("italic", False)))


def _emit_list_item(doc, el: dict[str, Any]) -> None:
    style = ("List Number" if el.get("list_style") == "number"
             else "List Bullet")
    p = doc.add_paragraph(style=style)
    p.alignment = ALIGN_MAP.get(el.get("alignment", "left"),
                                 WD_ALIGN_PARAGRAPH.LEFT)
    _add_run(p, el.get("text", ""),
             bold=bool(el.get("bold", False)),
             italic=bool(el.get("italic", False)))


def _emit_caption(doc, el: dict[str, Any]) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER  # captions always centered
    _add_run(p, el.get("text", ""),
             bold=True,                       # captions usually bold
             italic=bool(el.get("italic", False)))


def _emit_table(doc, el: dict[str, Any]) -> None:
    rows = el.get("table_rows") or []
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    tbl = doc.add_table(rows=len(rows), cols=n_cols)
    tbl.style = "Table Grid"
    for r_idx, row in enumerate(rows):
        cells = tbl.rows[r_idx].cells
        for c_idx in range(n_cols):
            val = row[c_idx] if c_idx < len(row) else ""
            cells[c_idx].text = str(val) if val is not None else ""
            if r_idx == 0:  # header row bold
                for run in cells[c_idx].paragraphs[0].runs:
                    run.bold = True
    doc.add_paragraph()  # breathing room


def _emit_image(doc, image_bytes: bytes,
                position: str | None) -> None:
    if not image_bytes:
        return
    try:
        width = Inches(5.5) if position == "full_width" else Inches(4.5)
        doc.add_picture(io.BytesIO(image_bytes), width=width)
        if position in ("centered", "full_width", None):
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        # Bad image bytes - skip silently, don't kill the doc
        pass


def render_pages(
    pages: list[dict[str, Any]],
    page_images: list[list[bytes]],
) -> bytes:
    """Render the full document.

    Args:
        pages: List of per-page JSON dicts from vision.analyze_page.
        page_images: Parallel list - page_images[i] is the list of image
            bytes extracted from PDF page i+1 (in source order). The
            renderer consumes them in order as it encounters "image"
            elements in the JSON.

    Returns:
        DOCX file bytes.
    """
    doc = _setup_doc()
    prev_kind: str | None = None

    for p_idx, page in enumerate(pages):
        kind = page.get("page_kind", "other")
        # Force a page break before a major section change.
        if (kind in PAGE_BREAK_BEFORE
                and prev_kind is not None
                and prev_kind != kind):
            doc.add_page_break()

        imgs = list(page_images[p_idx]) if p_idx < len(page_images) else []
        img_cursor = 0

        for el in page.get("elements", []):
            etype = el.get("type", "body")
            if etype in SKIP_TYPES:
                continue
            if etype == "heading":
                _emit_heading(doc, el)
            elif etype == "body":
                _emit_body(doc, el)
            elif etype == "list_item":
                _emit_list_item(doc, el)
            elif etype == "caption":
                _emit_caption(doc, el)
            elif etype == "table":
                _emit_table(doc, el)
            elif etype == "image":
                blob = imgs[img_cursor] if img_cursor < len(imgs) else b""
                img_cursor += 1
                _emit_image(doc, blob, el.get("image_position"))
            else:
                # Unknown type - render as body so we don't lose text
                _emit_body(doc, el)

        prev_kind = kind

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
