"""DOCX -> per-page blocks parallel of extraction.blocks_with_font().

Why this exists
---------------
The format_fix orchestrator was designed around PyMuPDF (`fitz`) and walks
PDF pages, asking each handler "is this your page?" by inspecting the
first few blocks. The handlers consume 5-tuples
`(text, max_size, dom_size, bold, align)` and don't care HOW they were
produced.

When the customer uploads a DOCX, there is no PDF, but python-docx
exposes paragraphs in document order with style + run-level font info.
We can synthesize the same 5-tuple stream from a DOCX and group it into
"virtual pages" so the existing handler-dispatch loop works unchanged.

How virtual pages are inferred
------------------------------
Real DOCX files may or may not have explicit page breaks. We split the
paragraph stream at:

1. Explicit page-break runs  (`<w:br w:type="page"/>`)
2. Section-title lines that match any handler's recognised heading
   (ACKNOWLEDGEMENT / DECLARATION / ABSTRACT / TABLE OF CONTENTS /
   REFERENCES / BIBLIOGRAPHY / WORKS CITED / ANNEXURE(S) /
   APPENDIX(ES) / CHAPTER N)

This produces a page layout the handlers' `applies_to(blocks, page_no, ctx)`
can dispatch over without any modification.

Font-size mapping
-----------------
python-docx exposes `run.font.size` only when explicitly set on the run.
For paragraphs that inherit size from the style (most student docs), we
fall back to bumped sizes based on `paragraph.style.name`:

  - "Title"          -> 18pt bold
  - "Heading 1"      -> 16pt bold
  - "Heading 2"      -> 14pt bold
  - "Heading 3"      -> 13pt bold
  - everything else  -> 12pt (the body-size default the orchestrator uses)

The handlers use the resulting max_size only as a sanity check
("title size >= body size"), so order-of-magnitude correctness is what
matters here.
"""
from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH


# 5-tuple shape:  (text, max_size, dom_size, bold, align)
Block = tuple[str, float, float, bool, str]


# Union of every handler's _TITLE_VARIANTS plus chapter detection.
# Hitting any of these in the paragraph stream starts a new virtual page.
_SECTION_TITLES: frozenset[str] = frozenset({
    "ACKNOWLEDGEMENT", "ACKNOWLEDGEMENTS", "ACKNOWLEDGMENT", "ACKNOWLEDGMENTS",
    "DECLARATION",
    "ABSTRACT",
    "CONTENTS", "TABLE OF CONTENTS", "TABLE OF CONTENT",
    "BIBLIOGRAPHY", "REFERENCES", "WORKS CITED",
    "ANNEXURE", "ANNEXURES", "APPENDIX", "APPENDICES",
})

_CHAPTER_RE = re.compile(r"^\s*CHAPTER\s+\d+\b", re.IGNORECASE)


_ALIGN_MAP: dict = {
    WD_ALIGN_PARAGRAPH.LEFT:    "left",
    WD_ALIGN_PARAGRAPH.CENTER:  "center",
    WD_ALIGN_PARAGRAPH.RIGHT:   "right",
    WD_ALIGN_PARAGRAPH.JUSTIFY: "left",
}


def _has_page_break(paragraph) -> bool:
    """Detect <w:br w:type='page'/> in any run of the paragraph."""
    for run in paragraph.runs:
        brs = run.element.findall(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}br"
        )
        for br in brs:
            t = br.get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}type"
            )
            if t == "page":
                return True
    return False


def _paragraph_to_block(paragraph) -> Block | None:
    """Convert a single python-docx Paragraph to a 5-tuple, or None to skip."""
    text = (paragraph.text or "").strip()
    if not text:
        return None

    sizes: list[float] = []
    bolds: list[bool]  = []
    for run in paragraph.runs:
        sz = run.font.size
        if sz is not None:
            sizes.append(sz.pt)
        bolds.append(bool(run.bold))

    max_size = max(sizes) if sizes else 12.0
    dom_size = max_size
    bold     = any(bolds)

    style_name = ((paragraph.style.name or "") if paragraph.style else "").lower()
    if "title" in style_name:
        max_size = max(max_size, 18.0)
        dom_size = max(dom_size, 18.0)
        bold = True
    elif "heading 1" in style_name:
        max_size = max(max_size, 16.0)
        dom_size = max(dom_size, 16.0)
        bold = True
    elif "heading 2" in style_name:
        max_size = max(max_size, 14.0)
        dom_size = max(dom_size, 14.0)
        bold = True
    elif "heading 3" in style_name:
        max_size = max(max_size, 13.0)
        dom_size = max(dom_size, 13.0)
        bold = True

    align = _ALIGN_MAP.get(paragraph.alignment, "left")
    return (text, max_size, dom_size, bold, align)


def _is_section_boundary(text: str) -> bool:
    """Does this line start a new virtual page (section header)?"""
    s = text.strip(" ?:.").upper()
    if s in _SECTION_TITLES:
        return True
    if _CHAPTER_RE.match(s):
        return True
    return False


def parse_docx_to_pages(docx_bytes: bytes) -> tuple[list[list[Block]], int]:
    """Parse a DOCX byte-string into virtual pages of 5-tuple blocks.

    Returns (page_blocks, n_pages) where page_blocks[i] is the list of
    blocks that comprise virtual page i.

    Always returns at least one page. Paragraphs before the first
    detected boundary form page 0 (the cover/front matter).
    """
    doc = Document(io.BytesIO(docx_bytes))

    pages: list[list[Block]] = [[]]

    for para in doc.paragraphs:
        block = _paragraph_to_block(para)
        had_explicit_break = _has_page_break(para)

        if block is None:
            if had_explicit_break and pages[-1]:
                pages.append([])
            continue

        text = block[0]
        # If this paragraph IS a section title, start a new virtual page
        # BEFORE adding it so the title sits at blocks[:5] of the new page
        # where handlers scan.
        if _is_section_boundary(text) and pages[-1]:
            pages.append([])

        pages[-1].append(block)

        if had_explicit_break:
            pages.append([])

    while len(pages) > 1 and not pages[-1]:
        pages.pop()

    if not pages:
        pages = [[]]

    return pages, len(pages)
