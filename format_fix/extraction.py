"""Pure PDF→blocks extraction utilities.

Ported from printosky/docx_engine.py (the `_pdf_*` helper family). All
functions are pure transformers: input PDF/blocks, output blocks/strings.
No side effects, no LLM, no doc mutation.

Block tuple shape: (text, max_size, dominant_size, bold, alignment)
  - text: merged paragraph text (str)
  - max_size: largest font size in the block (float, points)
  - dominant_size: most common font size by char count (float, points)
  - bold: True if any span in the block is bold (bool)
  - alignment: 'center' | 'left' | 'right' (str)
"""
from __future__ import annotations

import hashlib
import re

import fitz  # PyMuPDF


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

BULLET_RE        = re.compile(r"^[●•▪◆►]\s*")
NUM_LIST_RE      = re.compile(r"^(\d{1,2})[\.)]\s+")
PAGE_NUM_RE      = re.compile(r"^\(\d+\)$")          # "(12)"
BODY_PAGENUM_RE  = re.compile(r"^\d{1,3}$")          # "1", "12"
ALL_CAPS_RE      = re.compile(r"^[A-Z][A-Z \-,&\.\?:!\d]{2,}$")
# Auto-generated TOC entry: "Chapter 1...........  3" -- text, leader dots, page no
TOC_LEADER_RE    = re.compile(r"\.{4,}\s*\d{1,4}\s*$")
# Single visible glyph or near-empty line ("\", "*", "-")
STRAY_GLYPH_RE   = re.compile(r"^[\\\/\*\-\=\.\,\;\:\|]{1,2}$")
# Numbered sub-headings like "1.1 INTRODUCTION", "2.3.1 RESEARCH GAP"
NUM_HEADING_RE   = re.compile(
    r"^\d+(?:\.\d+){0,2}\s+[A-Z][A-Z \-,&\.\?:!&/]{2,}$"
)
KV_RE            = re.compile(r"^([A-Z][^:\n]{2,40}?)\s*:\s*(.+)$")

NOISE_TOKENS     = ("Map Camera", "Plus Code", " Lat ", " Long ", "GPS")

KNOWN_SECTION_NAMES: set[str] = {
    "ACKNOWLEDGEMENT", "ACKNOWLEDGEMENTS",
    "DECLARATION", "ABSTRACT",
    "TABLE OF CONTENTS", "CONTENTS",
    "LIST OF FIGURES", "LIST OF TABLES", "LIST OF ABBREVIATIONS",
    "INTRODUCTION", "REVIEW OF LITERATURE",
    "METHODOLOGY", "METHODS",
    "RESULTS", "DISCUSSION", "ANALYSIS",
    "CONCLUSION", "CONCLUSIONS",
    "BIBLIOGRAPHY", "REFERENCES", "APPENDIX", "APPENDICES",
    "ANNEXURES", "ANNEXURE",
}


# ---------------------------------------------------------------------------
# Line-level helpers
# ---------------------------------------------------------------------------

def is_noise(line: str) -> bool:
    return any(tok in line for tok in NOISE_TOKENS)


def block_alignment(x0: float, x1: float, page_width: float) -> str:
    """Center if midpoint within 8% of page mid; right if past 35%/75%."""
    if page_width <= 0:
        return "left"
    block_mid = (x0 + x1) / 2
    page_mid  = page_width / 2
    if abs(block_mid - page_mid) < page_width * 0.08:
        return "center"
    if x0 > page_width * 0.35 and x1 > page_width * 0.75:
        return "right"
    return "left"


def merge_sentences(lines: list[str]) -> list[str]:
    """Join Canva-broken visual lines back into full sentences."""
    out: list[str] = []
    for ln in lines:
        s = ln.rstrip()
        if not s:
            continue
        looks_independent = (
            BULLET_RE.match(s)
            or NUM_LIST_RE.match(s)
            or (ALL_CAPS_RE.match(s) and len(s) < 60)
            or PAGE_NUM_RE.match(s)
            or KV_RE.match(s)
        )
        if (out
                and not out[-1].rstrip().endswith((".", "?", "!", ":", ";"))
                and not looks_independent
                and not (ALL_CAPS_RE.match(out[-1]) and len(out[-1]) < 60)):
            out[-1] = out[-1].rstrip() + " " + s.lstrip()
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Block-level extraction
# ---------------------------------------------------------------------------

def blocks_with_font(pdf: fitz.Document,
                      page_no_0based: int) -> list[tuple]:
    """Return 5-tuples (text, max_size, dom_size, bold, align), Y-X sorted.

    Reflows lines within each block (merges Canva visual line breaks).
    """
    page = pdf[page_no_0based]
    page_width = page.rect.width
    d = page.get_text("dict")
    items: list[tuple] = []
    for block in d.get("blocks", []):
        if "lines" not in block:
            continue
        x0, y0, x1, _y1 = block["bbox"]
        align = block_alignment(x0, x1, page_width)
        max_size = 0.0
        size_chars: dict[float, int] = {}
        any_bold = False
        line_strs: list[str] = []
        for line in block["lines"]:
            line_text = ""
            for span in line["spans"]:
                t = span["text"]
                if not t:
                    continue
                sz = round(span.get("size", 12.0), 1)
                line_text += t
                max_size = max(max_size, sz)
                size_chars[sz] = size_chars.get(sz, 0) + len(t)
                if span.get("flags", 0) & 16:  # bold flag
                    any_bold = True
            if line_text.strip():
                line_strs.append(line_text.strip())
        if not line_strs:
            continue
        dominant = (max(size_chars.items(), key=lambda kv: kv[1])[0]
                    if size_chars else 12.0)
        # Reflow lines within block (Canva visual-line breaks)
        merged: list[str] = []
        buf = ""
        for ln in line_strs:
            is_break = (
                BULLET_RE.match(ln)
                or NUM_LIST_RE.match(ln)
                or (ALL_CAPS_RE.match(ln) and len(ln) < 60)
                or PAGE_NUM_RE.match(ln)
            )
            if is_break:
                if buf:
                    merged.append(buf)
                    buf = ""
                merged.append(ln)
            else:
                buf = (buf + " " + ln).strip() if buf else ln
        if buf:
            merged.append(buf)
        for m in merged:
            if not is_noise(m):
                items.append((y0, x0, m, max_size, dominant,
                                any_bold, align))
    items.sort(key=lambda t: (round(t[0], 1), round(t[1], 1)))
    return [(text, max_sz, dom_sz, bold, align)
            for _y, _x, text, max_sz, dom_sz, bold, align in items]


def merge_body_blocks(blocks: list[tuple],
                       body_size: float) -> list[tuple]:
    """Merge consecutive body-sized blocks into paragraphs.

    Word PDFs put each visual line of a paragraph as its own block. Without
    this pass, every line becomes its own paragraph. Stop at sentence-terminal
    punctuation, headings, bullets, page-numbers, KV pairs, alignment shifts.
    """
    out: list[tuple] = []
    for tup in blocks:
        text, max_sz, dom_sz, bold, align = tup
        s = text.strip()
        if not s:
            continue
        # Use dominant size (most chars by count), NOT max size, for break
        # detection. PyMuPDF reports max as the LARGEST glyph in the line,
        # so a single tall character (slightly-bigger first letter, outlier
        # symbol) drags max up to ~14.7pt for what is plainly a 12pt body
        # line — and fragmenting paragraphs on that is wrong.
        effective_sz = dom_sz if dom_sz > 0 else max_sz
        prev_align = out[-1][4] if out else align
        align_break = (align != prev_align
                        and align in ("center", "right")
                        and prev_align in ("center", "right"))
        is_break = (
            effective_sz > body_size + 1
            or BULLET_RE.match(s)
            or NUM_LIST_RE.match(s)
            or (ALL_CAPS_RE.match(s) and len(s) < 60)
            or NUM_HEADING_RE.match(s)
            or PAGE_NUM_RE.match(s)
            or BODY_PAGENUM_RE.match(s)
            or KV_RE.match(s)
            or align_break
        )
        # Compare on dominant size too — same rationale. Only enforce
        # bold-equality for non-body text: one bold word in a body line
        # flips the whole block's bold flag in PyMuPDF.
        prev_dom = out[-1][2] if out else dom_sz
        size_match = abs(prev_dom - effective_sz) < 1.0
        if (out
                and not is_break
                and not out[-1][0].rstrip().endswith((".", "?", "!", ":", ";"))
                and size_match
                and (effective_sz <= body_size + 1 or out[-1][3] == bold)):
            prev = out[-1]
            merged = (prev[0].rstrip() + " " + s.lstrip()).strip()
            out[-1] = (merged, prev[1], prev[2], prev[3], prev[4])
        else:
            out.append(tup)
    return out


def estimate_body_size(pdf: fitz.Document) -> float:
    """Median font size across the doc — the 'body' baseline for thresholds."""
    sizes: list[float] = []
    for page_no in range(len(pdf)):
        for _text, _maxsz, dom_sz, _bold, _align in blocks_with_font(
            pdf, page_no
        ):
            sizes.append(dom_sz)
    if not sizes:
        return 12.0
    sizes.sort()
    return sizes[len(sizes) // 2]


# ---------------------------------------------------------------------------
# Image filtering (decoration detection)
# ---------------------------------------------------------------------------

def pixmap_avg_luma(pix: fitz.Pixmap) -> int:
    """Mean luminance (0-255). Sample-bounded to avoid huge pixmap scans."""
    try:
        sample = pix.samples[:20_000]
        if not sample:
            return 128
        return sum(sample) // len(sample)
    except Exception:
        return 128


def is_decorative_pix(pix: fitz.Pixmap, byte_size: int) -> bool:
    """Per-image decoration heuristics (independent of hash duplication)."""
    if byte_size < 300:
        return True
    if pix.width < 50 or pix.height < 50:
        return True
    luma = pixmap_avg_luma(pix)
    # Pure black or pure white = page background / blank tile
    if luma < 8 or luma > 248:
        return True
    return False


def build_decorative_xrefs(pdf: fitz.Document) -> set[int]:
    """xrefs to drop: hash-on->=3-pages OR per-image heuristics."""
    hash_pages: dict[str, set[int]] = {}
    xref_hash:  dict[int, str]      = {}
    universal: set[int] = set()
    for page_no in range(len(pdf)):
        page = pdf[page_no]
        for info in page.get_images(full=True):
            xref = info[0]
            if xref in xref_hash:
                hash_pages.setdefault(xref_hash[xref], set()).add(page_no)
                continue
            try:
                pix = fitz.Pixmap(pdf, xref)
                if pix.alpha or pix.n > 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                png_bytes = pix.tobytes("png")
                if is_decorative_pix(pix, len(png_bytes)):
                    universal.add(xref)
                h = hashlib.md5(png_bytes).hexdigest()
                pix = None  # free memory
                xref_hash[xref] = h
                hash_pages.setdefault(h, set()).add(page_no)
            except Exception:
                continue
    deco_hashes = {h for h, pgs in hash_pages.items() if len(pgs) >= 3}
    duplicate = {xref for xref, h in xref_hash.items() if h in deco_hashes}
    return universal | duplicate


# ---------------------------------------------------------------------------
# Page-level renderers (full-page image fallback for hostile layouts)
# ---------------------------------------------------------------------------

def render_page_png(pdf: fitz.Document,
                     page_no_0based: int,
                     dpi_matrix: int = 2) -> bytes:
    """Rasterize a full PDF page to PNG bytes."""
    page = pdf[page_no_0based]
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi_matrix, dpi_matrix))
    return pix.tobytes("png")


def is_stray_line(s: str) -> bool:
    """True if the line is a single stray glyph, leader dots, or empty."""
    if not s or not s.strip():
        return True
    t = s.strip()
    if STRAY_GLYPH_RE.match(t):
        return True
    # nothing but punctuation
    if all(not c.isalnum() for c in t):
        return True
    return False


def merge_bullet_continuations(blocks: list[tuple],
                                body_size: float) -> list[tuple]:
    """Merge body-sized continuation blocks into the preceding bullet/numbered
    item so list entries read as full sentences.

    A continuation block is one that is NOT itself a bullet/heading/page-num/
    KV, is body-sized, and follows a bullet whose text doesn't end in
    sentence-terminal punctuation. The merge stops at any break-shaped block.
    """
    out: list[tuple] = []
    for tup in blocks:
        text, max_sz, dom_sz, bold, align = tup
        s = (text or "").strip()
        if not s:
            continue
        # Don't treat a bold + larger-than-body numbered line as a list
        # start -- it's a chapter title like "1. INTRODUCTION" and the
        # following body paragraph would otherwise be glued onto it,
        # breaking the heading classifier's length + ALL_CAPS checks
        # (Step 4.6f -- Water_Tank_Cleaner_v5.pdf had 20 chapter
        # starts that this merge was eating).
        prev_is_heading_disguised_as_list = bool(
            out
            and out[-1][3]                          # prev is bold
            and out[-1][1] > body_size + 1          # prev is heading-sized
            and NUM_LIST_RE.match(out[-1][0])       # prev starts with "N."
        )
        prev_is_list = bool(
            out
            and (BULLET_RE.match(out[-1][0]) or NUM_LIST_RE.match(out[-1][0]))
            and not out[-1][0].rstrip().endswith((".", "?", "!", ":", ";"))
            and not prev_is_heading_disguised_as_list
        )
        current_is_break = (
            max_sz > body_size + 1
            or BULLET_RE.match(s)
            or NUM_LIST_RE.match(s)
            or (ALL_CAPS_RE.match(s) and len(s) < 60)
            or NUM_HEADING_RE.match(s)
            or PAGE_NUM_RE.match(s)
            or BODY_PAGENUM_RE.match(s)
            or KV_RE.match(s)
        )
        if prev_is_list and not current_is_break:
            prev = out[-1]
            merged = (prev[0].rstrip() + " " + s.lstrip()).strip()
            out[-1] = (merged, prev[1], prev[2], prev[3], prev[4])
        else:
            out.append(tup)
    return out


def add_image_xref(doc, pdf: fitz.Document, xref: int,
                    width_inches: float = 5.5) -> bool:
    """Embed an xref-referenced image into a doc. Returns False on failure."""
    import io
    from docx.shared import Inches
    try:
        pix = fitz.Pixmap(pdf, xref)
        if pix.alpha or pix.n > 4:
            pix = fitz.Pixmap(fitz.csRGB, pix)
        png = pix.tobytes("png")
        pix = None
        doc.add_picture(io.BytesIO(png), width=Inches(width_inches))
        return True
    except Exception:
        return False
