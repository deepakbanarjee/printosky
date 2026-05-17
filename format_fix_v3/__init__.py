"""format_fix_v3 — Claude Vision hybrid PDF/DOCX → Word pipeline.

Parallel to format_fix (kept untouched). Architecture:
    PDF/DOCX → page images (PyMuPDF) + extracted text
            → Claude Vision (Sonnet 4.6) per page → structured JSON
            → renderer.py → python-docx output

Designed so v1 (format_fix) and v2 (format_fix + docx path) keep working
unchanged. v3 lives entirely in this package.
"""
