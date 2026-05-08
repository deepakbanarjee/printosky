"""
docx_engine.py — Document generation and formatting engine for Printosky Project Builder.

Handles:
- Loading university formatting configs from university_configs/*.json
- Extracting text from uploaded .docx and .pdf files
- Parsing document structure via Claude Haiku
- Generating formatted .docx output for all three product tiers:
    - generate_free_template()  → blank template with placeholders
    - format_fix()              → re-format pasted or extracted text
    - generate_from_form()      → full report from structured form data
"""

import io
import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from dotenv import load_dotenv

load_dotenv()

_CONFIG_DIR = Path(__file__).parent / "university_configs"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_university_config(university_id: str) -> dict:
    """Load and return JSON config for a university by ID."""
    config_path = _CONFIG_DIR / f"{university_id}.json"
    if not config_path.exists():
        logger.warning("Unknown university ID %r — falling back to ktu", university_id)
        config_path = _CONFIG_DIR / "ktu.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def list_universities() -> list:
    """Return [{id, name, short_name, copies_required, cover_color}] for all configs."""
    result = []
    for config_file in sorted(_CONFIG_DIR.glob("*.json")):
        with open(config_file, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        result.append({
            "id":              cfg["id"],
            "name":            cfg["name"],
            "short_name":      cfg["short_name"],
            "copies_required": cfg.get("copies_required", 3),
            "cover_color":     cfg.get("cover_color", ""),
        })
    return result


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract plain text from a .docx file supplied as bytes."""
    doc = Document(io.BytesIO(file_bytes))
    parts = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
    return "\n\n".join(parts)


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract plain text from a PDF supplied as bytes.

    Returns empty string if the PDF cannot be parsed (corrupt, encrypted, etc.)
    rather than raising — callers should handle empty text gracefully.
    """
    import pdfplumber
    try:
        parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    parts.append(page_text.strip())
        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning("extract_text_from_pdf failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Claude Haiku — structure parser
# ---------------------------------------------------------------------------

def _parse_structure_with_claude(text: str) -> dict:
    """
    Call Claude Haiku to identify chapter/section structure.

    Returns a dict matching:
        {
          "title": str,
          "chapters": [{"number": int, "heading": str, "sections": [...], "content": str}],
          "references": [str]
        }
    or {"error": "unstructured"} when structure cannot be identified.
    """
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "unstructured"}

    client = anthropic.Anthropic(api_key=api_key)
    truncated = text[:80000]

    prompt = (
        "Parse this academic project report and identify its structure.\n"
        "Return ONLY valid JSON with this shape:\n"
        "{\n"
        '  "title": "string",\n'
        '  "chapters": [\n'
        "    {\n"
        '      "number": 1,\n'
        '      "heading": "INTRODUCTION",\n'
        '      "sections": [\n'
        '        {"number": "1.1", "heading": "Background", "content": "..."}\n'
        "      ]\n"
        "    }\n"
        "  ],\n"
        '  "references": ["[1] ...", "[2] ..."]\n'
        "}\n\n"
        'If you cannot identify a clear structure, return {"error": "unstructured"}.\n'
        "Return ONLY the JSON object — no markdown fences, no explanation.\n\n"
        f"Document text:\n{truncated}"
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        return json.loads(raw)
    except Exception:
        return {"error": "unstructured"}


# ---------------------------------------------------------------------------
# Style application
# ---------------------------------------------------------------------------

_ALIGN_MAP = {
    "center":  WD_ALIGN_PARAGRAPH.CENTER,
    "left":    WD_ALIGN_PARAGRAPH.LEFT,
    "right":   WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


def _apply_university_styles(doc: Document, config: dict) -> None:
    """Apply margins, body font, and heading styles from a university config."""
    m = config["margins"]
    for section in doc.sections:
        section.left_margin   = Cm(m["left_cm"])
        section.right_margin  = Cm(m["right_cm"])
        section.top_margin    = Cm(m["top_cm"])
        section.bottom_margin = Cm(m["bottom_cm"])

    body_font = config["body_font"]
    body_size = config["body_size_pt"]
    spacing   = config["line_spacing"]
    sp_before = config["para_space_before_pt"]
    sp_after  = config["para_space_after_pt"]

    normal = doc.styles["Normal"]
    normal.font.name = body_font
    normal.font.size = Pt(body_size)
    normal.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    normal.paragraph_format.line_spacing      = spacing
    normal.paragraph_format.space_before      = Pt(sp_before)
    normal.paragraph_format.space_after       = Pt(sp_after)

    heading_specs = {
        "Heading 1": config["heading1"],
        "Heading 2": config["heading2"],
        "Heading 3": config["heading3"],
    }
    for style_name, hcfg in heading_specs.items():
        try:
            hstyle = doc.styles[style_name]
        except KeyError:
            continue
        hstyle.font.name     = body_font
        hstyle.font.size     = Pt(hcfg["size_pt"])
        hstyle.font.bold     = hcfg["bold"]
        hstyle.font.all_caps = bool(hcfg.get("all_caps"))
        hstyle.paragraph_format.alignment = _ALIGN_MAP.get(
            hcfg.get("align", "left"), WD_ALIGN_PARAGRAPH.LEFT
        )
        hstyle.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
        hstyle.paragraph_format.line_spacing      = spacing
        hstyle.paragraph_format.space_before      = Pt(12)
        hstyle.paragraph_format.space_after       = Pt(6)


def _set_section_page_numbering(section, fmt: str = "decimal", start: int = 1) -> None:
    """Set page number format on a section via XML manipulation."""
    sectPr = section._sectPr
    for old in sectPr.findall(qn("w:pgNumType")):
        sectPr.remove(old)
    pgNumType = OxmlElement("w:pgNumType")
    pgNumType.set(qn("w:fmt"), fmt)
    pgNumType.set(qn("w:start"), str(start))
    sectPr.append(pgNumType)


def _apply_margins_to_section(section, config: dict) -> None:
    m = config["margins"]
    section.left_margin   = Cm(m["left_cm"])
    section.right_margin  = Cm(m["right_cm"])
    section.top_margin    = Cm(m["top_cm"])
    section.bottom_margin = Cm(m["bottom_cm"])


# ---------------------------------------------------------------------------
# Low-level paragraph helpers
# ---------------------------------------------------------------------------

def _add_body_para(doc: Document, text: str, config: dict) -> None:
    para = doc.add_paragraph()
    para.paragraph_format.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    para.paragraph_format.line_spacing      = config["line_spacing"]
    para.paragraph_format.space_before      = Pt(config["para_space_before_pt"])
    para.paragraph_format.space_after       = Pt(config["para_space_after_pt"])
    run = para.add_run(text)
    run.font.name = config["body_font"]
    run.font.size = Pt(config["body_size_pt"])


def _add_centered_bold(
    doc: Document, text: str, size_pt: int = 14, space_before: int = 0
) -> None:
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.space_before = Pt(space_before)
    run = para.add_run(text)
    run.bold = True
    run.font.size = Pt(size_pt)


# ---------------------------------------------------------------------------
# TOC field
# ---------------------------------------------------------------------------

def _add_toc_field(doc: Document) -> None:
    """Insert a Word auto-updating TOC field (update on open in Word)."""
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.LEFT

    def _append_elem(elem):
        run = para.add_run()
        run._r.append(elem)

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    _append_elem(begin)

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = ' TOC \\o "1-3" \\h \\z \\u '
    run_instr = para.add_run()
    run_instr._r.append(instr)

    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    _append_elem(sep)

    placeholder = para.add_run()
    placeholder.text = "[Right-click → Update Field → Update entire table]"
    placeholder.font.italic = True

    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    _append_elem(end)


# ---------------------------------------------------------------------------
# Front matter page builders
# ---------------------------------------------------------------------------

def _build_title_page(doc: Document, config: dict, meta: dict) -> None:
    doc.add_paragraph()
    doc.add_paragraph()

    college     = meta.get("college_name", "[COLLEGE NAME]")
    department  = meta.get("department", "[DEPARTMENT]")
    title       = meta.get("title", "[PROJECT TITLE]")
    report_type = meta.get("report_type", "B.Tech Final Year Project")
    acad_year   = meta.get("academic_year", "[ACADEMIC YEAR]")
    degree      = meta.get("degree", "Bachelor of Technology")

    _add_centered_bold(doc, college.upper(), size_pt=16)
    _add_centered_bold(doc, f"DEPARTMENT OF {department.upper()}", size_pt=13)

    doc.add_paragraph()
    doc.add_paragraph()
    _add_centered_bold(doc, title.upper(), size_pt=16)
    doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run(f"A {report_type.upper()} REPORT").bold = True

    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.add_run("Submitted to")

    _add_centered_bold(doc, config["name"].upper(), size_pt=13)

    p3 = doc.add_paragraph()
    p3.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p3.add_run(
        "in partial fulfillment of the requirements for the award of the Degree of"
    )

    _add_centered_bold(
        doc, f"{degree.upper()}\nIN {department.upper()}", size_pt=13
    )

    doc.add_paragraph()
    _add_centered_bold(doc, "Submitted by", size_pt=12)
    for s in meta.get("students", []):
        name = s.get("name", "")
        roll = s.get("roll_no", "")
        if name:
            sp = doc.add_paragraph()
            sp.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sp.add_run(f"{name}  ({roll})")

    doc.add_paragraph()
    _add_centered_bold(doc, acad_year, size_pt=12)

    note = doc.add_paragraph()
    note.alignment = WD_ALIGN_PARAGRAPH.CENTER
    nr = note.add_run(
        f"[Print on {config.get('cover_color', 'Navy Blue')} hard cover "
        f"with {config.get('cover_text_color', 'Gold')} text]"
    )
    nr.italic = True
    nr.font.size = Pt(10)


def _build_certificate_page(doc: Document, config: dict, meta: dict) -> None:
    doc.add_heading("CERTIFICATE", level=1)

    students      = meta.get("students", [])
    student_names = ", ".join(s.get("name", "") for s in students if s.get("name"))
    guide         = meta.get("guide", {})
    hod           = meta.get("hod", {})
    co_guide      = meta.get("co_guide", {})

    cert = config.get("certificate_text", "")
    cert = (
        cert
        .replace("[TITLE]",         meta.get("title", "[TITLE]"))
        .replace("[STUDENT_NAMES]", student_names or "[STUDENT NAMES]")
        .replace("[DEPARTMENT]",    meta.get("department", "[DEPARTMENT]"))
        .replace("[DEGREE]",        meta.get("degree", "Bachelor of Technology"))
        .replace("[GUIDE_NAME]",    guide.get("name", "[GUIDE NAME]"))
    )

    _add_body_para(doc, cert, config)
    doc.add_paragraph()

    sig_table = doc.add_table(rows=1, cols=2)
    sig_table.style = "Table Grid"
    cells = sig_table.rows[0].cells
    cells[0].text = (
        f"Guide:\n{guide.get('name', '[Guide Name]')}\n"
        f"{guide.get('designation', 'Assistant Professor')}"
    )
    cells[1].text = (
        f"Head of Department:\n{hod.get('name', '[HOD Name]')}\n"
        f"{hod.get('designation', 'Professor & Head')}"
    )

    if co_guide and co_guide.get("name"):
        doc.add_paragraph()
        _add_body_para(
            doc,
            f"Co-Guide: {co_guide['name']}, {co_guide.get('designation', '')}",
            config,
        )

    doc.add_paragraph()
    _add_body_para(doc, "Place: _______________", config)
    _add_body_para(doc, "Date:  _______________", config)


def _build_declaration_page(doc: Document, config: dict, meta: dict) -> None:
    doc.add_heading("DECLARATION", level=1)

    students      = meta.get("students", [])
    student_names = ", ".join(s.get("name", "") for s in students if s.get("name"))
    guide         = meta.get("guide", {})

    decl = config.get("declaration_text", "")
    decl = (
        decl
        .replace("[TITLE]",         meta.get("title", "[TITLE]"))
        .replace("[STUDENT_NAMES]", student_names or "[STUDENT NAMES]")
        .replace("[DEPARTMENT]",    meta.get("department", "[DEPARTMENT]"))
        .replace("[DEGREE]",        meta.get("degree", "Bachelor of Technology"))
        .replace("[GUIDE_NAME]",    guide.get("name", "[GUIDE NAME]"))
    )

    _add_body_para(doc, decl, config)
    doc.add_paragraph()
    _add_body_para(doc, "Place: _______________", config)
    _add_body_para(doc, "Date:  _______________", config)
    doc.add_paragraph()
    _add_body_para(doc, "Signature(s):", config)
    for s in students:
        name = s.get("name", "")
        roll = s.get("roll_no", "")
        if name:
            _add_body_para(doc, f"\n_______________\n{name} ({roll})", config)


def _build_acknowledgement_page(doc: Document, config: dict) -> None:
    doc.add_heading("ACKNOWLEDGEMENT", level=1)
    text = (
        "We express our sincere gratitude to our guide for the invaluable guidance, "
        "encouragement, and support extended to us throughout this project. We also thank "
        "the Head of the Department and all faculty members for their help and inspiration. "
        "We are grateful to the Management and Principal of our college for providing the "
        "necessary facilities. We express our deep gratitude to our family and friends for "
        "their continuous support and motivation.\n\n"
        "[Add your personal acknowledgements here]"
    )
    _add_body_para(doc, text, config)


def _build_abstract_page(doc: Document, config: dict, meta: dict) -> None:
    doc.add_heading("ABSTRACT", level=1)
    abstract = meta.get(
        "abstract",
        "[Insert abstract here — 100 to 300 words summarising your project, "
        "methodology, and key findings.]",
    )
    _add_body_para(doc, abstract, config)

    keywords = meta.get("keywords", [])
    if keywords:
        doc.add_paragraph()
        kp = doc.add_paragraph()
        kr = kp.add_run("Keywords: ")
        kr.bold = True
        kr.font.size = Pt(config["body_size_pt"])
        kp.add_run(", ".join(keywords))


def _build_toc_page(doc: Document) -> None:
    doc.add_heading("TABLE OF CONTENTS", level=1)
    _add_toc_field(doc)
    note = doc.add_paragraph()
    nr = note.add_run(
        "Note: Open in Word → right-click the table above → Update Field → "
        "Update entire table."
    )
    nr.italic = True
    nr.font.size = Pt(9)


def _build_list_of_figures_page(doc: Document) -> None:
    doc.add_heading("LIST OF FIGURES", level=1)
    p = doc.add_paragraph()
    p.add_run("[Right-click → Update Field after inserting all figures]").italic = True


def _build_list_of_tables_page(doc: Document) -> None:
    doc.add_heading("LIST OF TABLES", level=1)
    p = doc.add_paragraph()
    p.add_run("[Right-click → Update Field after inserting all tables]").italic = True


def _build_abbreviations_page(doc: Document, config: dict) -> None:
    doc.add_heading("LIST OF ABBREVIATIONS", level=1)
    table = doc.add_table(rows=1, cols=2)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text = "Abbreviation"
    hdr[1].text = "Full Form"
    for _ in range(8):
        row = table.add_row().cells
        row[0].text = ""
        row[1].text = ""


# ---------------------------------------------------------------------------
# Chapter builder
# ---------------------------------------------------------------------------

def _build_chapter(doc: Document, chapter: dict, chapter_num: int, config: dict) -> None:
    heading_text = f"CHAPTER {chapter_num}: {chapter.get('heading', '').upper()}"
    doc.add_heading(heading_text, level=1)

    sections = chapter.get("sections", [])
    if sections:
        for sec in sections:
            sec_num     = sec.get("number", "")
            sec_heading = sec.get("heading", "")
            if sec_heading:
                doc.add_heading(f"{sec_num} {sec_heading}", level=2)
            content = sec.get("content", "")
            for line in content.split("\n"):
                line = line.strip()
                if line:
                    _add_body_para(doc, line, config)
    else:
        content = chapter.get("content", "[Chapter content goes here]")
        for line in content.split("\n"):
            line = line.strip()
            if line:
                _add_body_para(doc, line, config)


def _build_references_page(doc: Document, references: list, config: dict) -> None:
    doc.add_heading("REFERENCES", level=1)
    if references:
        for i, ref in enumerate(references, 1):
            ref = ref.strip()
            if ref:
                if not ref.startswith("["):
                    ref = f"[{i}] {ref}"
                _add_body_para(doc, ref, config)
    else:
        _add_body_para(
            doc,
            "[Add references here in IEEE/APA format, one per line]",
            config,
        )


def _build_appendices_page(doc: Document, appendices: str, config: dict) -> None:
    doc.add_heading("APPENDICES", level=1)
    content = appendices.strip() if appendices else ""
    if content:
        for line in content.split("\n"):
            line = line.strip()
            if line:
                _add_body_para(doc, line, config)
    else:
        _add_body_para(
            doc,
            "[Appendix content — code listings, data tables, circuit diagrams, etc.]",
            config,
        )


# ---------------------------------------------------------------------------
# Page numbering and section management
# ---------------------------------------------------------------------------

def _add_chapter_section(doc: Document, config: dict) -> None:
    """Insert a new page section for chapters; set Arabic numbering from 1."""
    section = doc.add_section(WD_SECTION.NEW_PAGE)
    _apply_margins_to_section(section, config)
    _set_section_page_numbering(section, fmt="decimal", start=1)


def _set_front_matter_numbering(doc: Document) -> None:
    """Set Roman numeral page numbering on section 0 (front matter)."""
    _set_section_page_numbering(doc.sections[0], fmt="upperRoman", start=1)


# ---------------------------------------------------------------------------
# Footer CTA
# ---------------------------------------------------------------------------

def _add_footer_cta(doc: Document) -> None:
    for section in doc.sections:
        footer = section.footer
        if not footer.paragraphs:
            footer.add_paragraph()
        fp = footer.paragraphs[0]
        fp.clear()
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fr = fp.add_run(
            "Once your report is ready — Print, bind & deliver with Printosky, Thrissur. "
            "WhatsApp: 9495706405"
        )
        fr.font.size = Pt(9)
        fr.italic = True


# ---------------------------------------------------------------------------
# Shared front matter dispatch table
# ---------------------------------------------------------------------------

_DEFAULT_FRONT_MATTER_ORDER = [
    "title_page", "certificate", "declaration", "acknowledgement",
    "abstract", "toc", "list_of_figures", "list_of_tables", "list_of_abbreviations",
]


def _build_front_matter(doc: Document, config: dict, meta: dict) -> None:
    """Build all front matter pages in university-specified order."""
    order = config.get("front_matter_order", _DEFAULT_FRONT_MATTER_ORDER)

    builders = {
        "title_page":            lambda: _build_title_page(doc, config, meta),
        "certificate":           lambda: _build_certificate_page(doc, config, meta),
        "declaration":           lambda: _build_declaration_page(doc, config, meta),
        "acknowledgement":       lambda: _build_acknowledgement_page(doc, config),
        "abstract":              lambda: _build_abstract_page(doc, config, meta),
        "toc":                   lambda: _build_toc_page(doc),
        "list_of_figures":       lambda: _build_list_of_figures_page(doc),
        "list_of_tables":        lambda: _build_list_of_tables_page(doc),
        "list_of_abbreviations": lambda: _build_abbreviations_page(doc, config),
    }

    for i, item in enumerate(order):
        if i > 0:
            doc.add_page_break()
        builder = builders.get(item)
        if builder:
            builder()


# ---------------------------------------------------------------------------
# Public API — three product tiers
# ---------------------------------------------------------------------------

def generate_free_template(university_id: str) -> bytes:
    """
    Build a blank .docx template with placeholder text and correct university formatting.
    Returns .docx as bytes.
    """
    config = load_university_config(university_id)
    doc = Document()
    _apply_university_styles(doc, config)
    _set_front_matter_numbering(doc)

    placeholder_meta = {
        "title":        "[YOUR PROJECT TITLE]",
        "college_name": "[YOUR COLLEGE NAME]",
        "department":   "[YOUR DEPARTMENT]",
        "academic_year": "[ACADEMIC YEAR e.g. 2025-26]",
        "report_type":  "B.Tech Final Year Project",
        "degree":       "Bachelor of Technology",
        "students": [
            {"name": "[Student Name 1]", "roll_no": "[Roll No]"},
            {"name": "[Student Name 2]", "roll_no": "[Roll No]"},
        ],
        "guide":    {"name": "[Guide Name]",  "designation": "Assistant Professor"},
        "co_guide": {"name": "",              "designation": ""},
        "hod":      {"name": "[HOD Name]",    "designation": "Professor & Head"},
        "abstract": (
            "[Write your abstract here — 100 to 300 words summarising the problem, "
            "methodology, and results of your project.]"
        ),
        "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
    }

    _build_front_matter(doc, config, placeholder_meta)

    # Chapters section (Arabic numbering from page 1)
    _add_chapter_section(doc, config)

    placeholder_chapters = [
        ("INTRODUCTION", [
            ("1.1", "Background",  "[Background content here]"),
            ("1.2", "Objectives",  "[Project objectives here]"),
            ("1.3", "Scope",       "[Scope of the project here]"),
        ]),
        ("LITERATURE REVIEW",       []),
        ("METHODOLOGY",             []),
        ("IMPLEMENTATION",          []),
        ("RESULTS AND DISCUSSION",  []),
        ("CONCLUSION AND FUTURE WORK", []),
    ]

    for i, (ch_heading, sections) in enumerate(placeholder_chapters, 1):
        if i > 1:
            doc.add_page_break()
        doc.add_heading(f"CHAPTER {i}: {ch_heading}", level=1)
        if sections:
            for sec_num, sec_heading, sec_content in sections:
                doc.add_heading(f"{sec_num} {sec_heading}", level=2)
                _add_body_para(doc, sec_content, config)
        else:
            _add_body_para(doc, f"[{ch_heading.title()} content goes here]", config)

    doc.add_page_break()
    _build_references_page(doc, [], config)
    doc.add_page_break()
    _build_appendices_page(doc, "", config)

    _add_footer_cta(doc)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def format_fix(text: str, university_id: str) -> bytes:
    """
    Re-format a document (pasted text or extracted from uploaded file).
    Uses Claude Haiku to parse structure; falls back gracefully if unstructured.
    Returns .docx as bytes.
    """
    config = load_university_config(university_id)
    doc = Document()
    _apply_university_styles(doc, config)
    _set_front_matter_numbering(doc)

    structure = _parse_structure_with_claude(text)

    if "error" in structure:
        # Unstructured fallback: apply body formatting, add guidance note
        doc.add_heading("FORMATTED DOCUMENT", level=1)
        note_para = doc.add_paragraph()
        note_run = note_para.add_run(
            "Note: We could not automatically identify the chapter structure of your "
            "document. Body text formatting has been applied. Please manually select your "
            "chapter headings in Word and apply Heading 1 / Heading 2 / Heading 3 styles "
            "from the Styles panel."
        )
        note_run.italic = True
        doc.add_paragraph()

        for line in text.split("\n"):
            line = line.strip()
            if line:
                _add_body_para(doc, line, config)
    else:
        title      = structure.get("title", "PROJECT REPORT")
        chapters   = structure.get("chapters", [])
        references = structure.get("references", [])

        doc.add_heading(title.upper(), level=1)
        _add_body_para(
            doc,
            f"University: {config['name']}\n"
            "[Fill in: college name, department, student names, guide, etc.]",
            config,
        )

        doc.add_page_break()
        _build_toc_page(doc)

        _add_chapter_section(doc, config)

        for i, chapter in enumerate(chapters, 1):
            if i > 1:
                doc.add_page_break()
            _build_chapter(doc, chapter, i, config)

        if references:
            doc.add_page_break()
            _build_references_page(doc, references, config)

    _add_footer_cta(doc)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def generate_from_form(form_data: dict, university_id: str) -> bytes:
    """
    Build a complete project report from structured form data (Tier 3).
    Returns .docx as bytes.
    """
    config = load_university_config(university_id)
    doc = Document()
    _apply_university_styles(doc, config)
    _set_front_matter_numbering(doc)

    keywords_raw = form_data.get("keywords", "")
    if isinstance(keywords_raw, str):
        keywords = [k.strip() for k in keywords_raw.split(",") if k.strip()]
    else:
        keywords = list(keywords_raw)

    meta = {
        "title":        form_data.get("title", "[PROJECT TITLE]"),
        "college_name": form_data.get("college_name", "[COLLEGE NAME]"),
        "department":   form_data.get("department", "[DEPARTMENT]"),
        "academic_year": form_data.get("academic_year", "[ACADEMIC YEAR]"),
        "report_type":  form_data.get("report_type", "B.Tech Final Year Project"),
        "degree":       form_data.get("degree", "Bachelor of Technology"),
        "students":     form_data.get("students", []),
        "guide":        form_data.get("guide", {}),
        "co_guide":     form_data.get("co_guide", {}),
        "hod":          form_data.get("hod", {}),
        "abstract":     form_data.get("abstract", ""),
        "keywords":     keywords,
    }

    _build_front_matter(doc, config, meta)

    # Chapters section (Arabic numbering from page 1)
    _add_chapter_section(doc, config)

    chapters = form_data.get("chapters", [])
    for i, chapter in enumerate(chapters, 1):
        if i > 1:
            doc.add_page_break()
        ch_dict = {
            "heading":  chapter.get("title", f"Chapter {i}"),
            "sections": [],
            "content":  chapter.get("content", ""),
        }
        _build_chapter(doc, ch_dict, i, config)

    # References
    refs_raw   = form_data.get("references", "")
    references = [r.strip() for r in refs_raw.split("\n") if r.strip()] if refs_raw else []
    doc.add_page_break()
    _build_references_page(doc, references, config)

    # Appendices (optional)
    appendices = form_data.get("appendices", "")
    if appendices and appendices.strip():
        doc.add_page_break()
        _build_appendices_page(doc, appendices, config)

    _add_footer_cta(doc)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── PDF generation ────────────────────────────────────────────────────────────

def generate_pdf_bytes(docx_bytes: bytes, university_id: str = "ktu") -> bytes:
    """Re-render a generated DOCX as a clean PDF using reportlab.

    Reads paragraph structure from the DOCX and builds a properly margined,
    Times-Roman PDF matching university formatting requirements.
    """
    from docx import Document as _DocxDoc
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    import io as _io

    cfg  = load_university_config(university_id)
    marg = cfg.get("margins", {})

    buf = _io.BytesIO()
    pdf = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=marg.get("left_cm", 3.5) * cm,
        rightMargin=marg.get("right_cm", 2.5) * cm,
        topMargin=marg.get("top_cm", 2.5) * cm,
        bottomMargin=marg.get("bottom_cm", 2.5) * cm,
    )

    body = ParagraphStyle("Body", fontName="Times-Roman", fontSize=12, leading=18,
                          alignment=TA_JUSTIFY, spaceBefore=0, spaceAfter=0)
    h1   = ParagraphStyle("H1",   fontName="Times-Bold",  fontSize=16, leading=22,
                          alignment=TA_CENTER,  spaceBefore=14, spaceAfter=6)
    h2   = ParagraphStyle("H2",   fontName="Times-Bold",  fontSize=14, leading=19,
                          alignment=TA_LEFT,    spaceBefore=12, spaceAfter=4)
    h3   = ParagraphStyle("H3",   fontName="Times-Bold",  fontSize=12, leading=16,
                          alignment=TA_LEFT,    spaceBefore=8,  spaceAfter=2)
    ctr  = ParagraphStyle("Ctr",  fontName="Times-Roman", fontSize=12, leading=18,
                          alignment=TA_CENTER)

    def _safe(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    doc_in = _DocxDoc(_io.BytesIO(docx_bytes))
    story: list = []

    for para in doc_in.paragraphs:
        text = para.text
        if not text.strip():
            story.append(Spacer(1, 6))
            continue
        sname = para.style.name if para.style else "Normal"
        if "Heading 1" in sname or sname in ("Title", "Subtitle"):
            story.append(Paragraph(_safe(text), h1))
        elif "Heading 2" in sname:
            story.append(Paragraph(_safe(text), h2))
        elif "Heading 3" in sname:
            story.append(Paragraph(_safe(text), h3))
        elif para.alignment == 1:   # WD_ALIGN_PARAGRAPH.CENTER
            story.append(Paragraph(_safe(text), ctr))
        else:
            story.append(Paragraph(_safe(text), body))

    if not story:
        story.append(Paragraph("(empty document)", body))

    pdf.build(story)
    return buf.getvalue()
