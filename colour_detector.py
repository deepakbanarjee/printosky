"""
colour_detector.py — PDF colour page detection using PyMuPDF (fitz).

Scans each page of a PDF and identifies which pages contain colour (non-grayscale)
content. Stores results as JSON in the jobs.colour_page_map column.

Usage:
    from colour_detector import build_colour_map, save_colour_map

    cmap = build_colour_map("/path/to/file.pdf")
    # {"colour": [1, 5, 12], "bw": [2, 3, 4, 6, 7, 8, 9, 10, 11], "total": 12}

    save_colour_map(db_path, job_id, cmap)
"""

import json
import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import fitz  # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False
    logger.warning("PyMuPDF (fitz) not installed — colour detection unavailable")


# ── Colour detection helpers ──────────────────────────────────────────────────

def _is_gray(color: tuple) -> bool:
    """Return True if an RGB tuple is achromatic (grayscale)."""
    if not color or len(color) < 3:
        return True
    r, g, b = color[0], color[1], color[2]
    return abs(r - g) < 0.02 and abs(g - b) < 0.02 and abs(r - b) < 0.02


def _page_has_colour(page: "fitz.Page", doc: "fitz.Document") -> bool:
    """
    Check a single PDF page for non-grayscale content.

    Checks vector drawings first (fast), then raster images (slower).
    Returns True if any colour content is found.
    """
    # 1. Check vector drawings / fills / strokes
    for item in page.get_drawings():
        color = item.get("color")
        fill  = item.get("fill")
        if color and not _is_gray(color):
            return True
        if fill and not _is_gray(fill):
            return True

    # 2. Check raster images embedded on the page
    for img_ref in page.get_images(full=False):
        xref = img_ref[0]
        try:
            img_info = doc.extract_image(xref)
            # colorspace component count: 1=gray, 3=RGB/CMY, 4=CMYK
            if img_info and img_info.get("colorspace", 1) > 1:
                return True
        except Exception:
            continue  # corrupted image reference — skip

    return False


def detect_colour_pages(pdf_path: str) -> list[int]:
    """
    Return a list of 1-indexed page numbers that contain colour content.
    Returns an empty list if PyMuPDF is unavailable or the file cannot be opened.
    """
    if not FITZ_AVAILABLE:
        return []
    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        logger.warning("Cannot open PDF for colour detection %s: %s", pdf_path, exc)
        return []

    colour_pages = []
    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            if _page_has_colour(page, doc):
                colour_pages.append(page_num + 1)  # 1-indexed
    finally:
        doc.close()

    return colour_pages


def build_colour_map(pdf_path: str) -> dict:
    """
    Scan a PDF and return a colour map dict.

    Return format:
        {
            "colour": [1, 5, 12],          # 1-indexed pages with colour content
            "bw":     [2, 3, 4, 6, ...],   # 1-indexed B&W pages
            "total":  12,                   # total page count
            "has_colour": true,
            "has_bw": true,
            "is_mixed": true,               # True if both colour and B&W pages exist
        }
    """
    if not FITZ_AVAILABLE:
        return {"colour": [], "bw": [], "total": 0,
                "has_colour": False, "has_bw": False, "is_mixed": False,
                "error": "PyMuPDF not installed"}

    try:
        doc = fitz.open(pdf_path)
        total = len(doc)
        doc.close()
    except Exception as exc:
        logger.warning("Cannot open PDF %s: %s", pdf_path, exc)
        return {"colour": [], "bw": [], "total": 0,
                "has_colour": False, "has_bw": False, "is_mixed": False,
                "error": str(exc)}

    colour_pages = detect_colour_pages(pdf_path)
    all_pages    = list(range(1, total + 1))
    bw_pages     = [p for p in all_pages if p not in colour_pages]

    result = {
        "colour":     colour_pages,
        "bw":         bw_pages,
        "total":      total,
        "has_colour": len(colour_pages) > 0,
        "has_bw":     len(bw_pages) > 0,
        "is_mixed":   len(colour_pages) > 0 and len(bw_pages) > 0,
    }
    logger.info(
        "Colour detection %s: %d colour pages, %d B&W pages (total %d) — mixed=%s",
        Path(pdf_path).name, len(colour_pages), len(bw_pages), total, result["is_mixed"]
    )
    return result


def save_colour_map(db_path: str, job_id: str, colour_map: dict) -> None:
    """
    Persist the colour_map JSON to jobs.colour_page_map in SQLite.
    Sets colour_confirmed=0 (pending staff review).
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE jobs SET colour_page_map=?, colour_confirmed=0 WHERE job_id=?",
            (json.dumps(colour_map), job_id),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Saved colour_page_map for job %s", job_id)


def confirm_colour_map(
    db_path: str,
    job_id: str,
    colour_pages: Optional[list[int]] = None,
) -> None:
    """
    Mark colour detection as confirmed by staff.

    If colour_pages is provided, updates the stored map with the staff-adjusted list
    (i.e. overrides the auto-detected result). Otherwise just sets colour_confirmed=1.
    """
    conn = sqlite3.connect(db_path)
    try:
        if colour_pages is not None:
            # Recalculate bw from the confirmed list
            row = conn.execute(
                "SELECT colour_page_map FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            total = 0
            if row and row[0]:
                try:
                    total = json.loads(row[0]).get("total", 0)
                except (ValueError, KeyError):
                    pass
            all_pages = list(range(1, total + 1))
            bw_pages  = [p for p in all_pages if p not in colour_pages]
            new_map   = {
                "colour":     sorted(colour_pages),
                "bw":         bw_pages,
                "total":      total,
                "has_colour": len(colour_pages) > 0,
                "has_bw":     len(bw_pages) > 0,
                "is_mixed":   len(colour_pages) > 0 and len(bw_pages) > 0,
                "staff_override": True,
            }
            conn.execute(
                "UPDATE jobs SET colour_page_map=?, colour_confirmed=1 WHERE job_id=?",
                (json.dumps(new_map), job_id),
            )
        else:
            conn.execute(
                "UPDATE jobs SET colour_confirmed=1 WHERE job_id=?", (job_id,)
            )
        conn.commit()
    finally:
        conn.close()
    logger.info("Colour map confirmed for job %s (override=%s)", job_id, colour_pages is not None)
