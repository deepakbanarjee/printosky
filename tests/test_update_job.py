"""
TDD tests for print_server.handle_update_job() — S7-3 fix.

Bug: saveJobSpecs in admin.html never sends paper_type.
     handle_update_job defaults paper_type to 'A4_BW' for every item,
     so colour jobs get quoted at B&W prices.

Fix: derive paper_type from colour when not explicitly provided.
"""

import sys
import os
import types
import sqlite3

# ── Stub every external dep print_server tries to import ─────────────────────
_STUBS = [
    "gspread", "google", "google.auth", "google.auth.transport",
    "google.auth.transport.requests", "google.oauth2", "google.oauth2.service_account",
    "websockets", "requests", "pysnmp", "pysnmp.hlapi",
    "watchdog", "watchdog.observers", "watchdog.events",
    "razorpay", "dotenv",
]
for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.modules["dotenv"].load_dotenv = lambda: None  # type: ignore

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import print_server


# ── Fixture: in-memory DB with jobs + print_items tables ─────────────────────

def _make_db(tmp_path) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = str(tmp_path / "jobs.db")
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE jobs (
            job_id        TEXT PRIMARY KEY,
            finishing     TEXT DEFAULT 'none',
            is_student    INTEGER DEFAULT 0,
            urgent        INTEGER DEFAULT 0,
            paper_size    TEXT DEFAULT 'A4',
            notes         TEXT,
            amount_quoted REAL DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE print_items (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id       TEXT NOT NULL,
            item_number  INTEGER NOT NULL,
            page_list    TEXT DEFAULT 'all',
            paper_type   TEXT DEFAULT 'A4_BW',
            colour       TEXT DEFAULT 'bw',
            sides        TEXT DEFAULT 'ss',
            layout       TEXT DEFAULT '1-up',
            copies       INTEGER DEFAULT 1,
            paper_gsm    INTEGER DEFAULT 70,
            printer      TEXT DEFAULT 'konica',
            status       TEXT DEFAULT 'Pending'
        )
    """)
    conn.execute("INSERT INTO jobs (job_id) VALUES ('OSP-TEST-0001')")
    conn.commit()
    conn.close()
    return db


def _update(tmp_path, items: list, **kwargs) -> dict:
    """Helper: call handle_update_job with patched DB_PATH."""
    db = _make_db(tmp_path)
    original = print_server.DB_PATH
    try:
        print_server.DB_PATH = db
        body = {
            "job_id":      "OSP-TEST-0001",
            "staff_id":    "staff1",
            "finishing":   kwargs.get("finishing", "none"),
            "is_student":  kwargs.get("is_student", False),
            "urgent":      kwargs.get("urgent", False),
            "paper_size":  kwargs.get("paper_size", "A4"),
            "print_items": items,
        }
        return print_server.handle_update_job(body)
    finally:
        print_server.DB_PATH = original


# ═════════════════════════════════════════════════════════════════════════════
# S7-3: paper_type derived from colour when not provided
# ═════════════════════════════════════════════════════════════════════════════

class TestUpdateJobPaperType:
    """
    handle_update_job receives items from the admin panel without paper_type.
    The server must derive paper_type from colour so the quote is correct.
    """

    def test_col_item_without_paper_type_gets_colour_quote(self, tmp_path):
        """
        10 A4 SS colour pages should quote at colour rates (Rs.100),
        not B&W rates (Rs.30). Bug: paper_type defaults to A4_BW → Rs.30.
        """
        items = [{"colour": "col", "sides": "ss", "layout": "1-up",
                  "copies": 1, "pages": 10}]
        result = _update(tmp_path, items)
        assert result["ok"] is True
        assert result["quote"]["total"] > 30.0, (
            f"Colour job quoted at B&W price ({result['quote']['total']}) — "
            "paper_type defaulted to A4_BW instead of A4_col"
        )

    def test_col_item_quote_equals_explicit_col_paper_type(self, tmp_path):
        """
        Omitting paper_type with colour=col must give the same total
        as explicitly passing paper_type=A4_col.
        """
        items_no_pt  = [{"colour": "col", "sides": "ss", "layout": "1-up",
                          "copies": 1, "pages": 10}]
        items_with_pt = [{"colour": "col", "sides": "ss", "layout": "1-up",
                           "copies": 1, "pages": 10, "paper_type": "A4_col"}]

        r_no_pt   = _update(tmp_path / "a", items_no_pt)
        r_with_pt = _update(tmp_path / "b", items_with_pt)

        assert r_no_pt["quote"]["total"] == r_with_pt["quote"]["total"], (
            f"Implicit colour ({r_no_pt['quote']['total']}) != "
            f"explicit A4_col ({r_with_pt['quote']['total']})"
        )

    def test_bw_item_without_paper_type_gets_bw_quote(self, tmp_path):
        """B&W items without paper_type must still get B&W pricing."""
        items = [{"colour": "bw", "sides": "ss", "layout": "1-up",
                  "copies": 1, "pages": 10}]
        result = _update(tmp_path, items)
        assert result["ok"] is True
        assert result["quote"]["total"] == 30.0, (
            f"B&W job should be Rs.30 for 10 SS pages, got {result['quote']['total']}"
        )

    def test_col_quote_higher_than_bw_same_pages(self, tmp_path):
        """Sanity: colour quote must exceed B&W quote for identical page count."""
        col_items = [{"colour": "col", "sides": "ss", "layout": "1-up",
                      "copies": 1, "pages": 10}]
        bw_items  = [{"colour": "bw",  "sides": "ss", "layout": "1-up",
                      "copies": 1, "pages": 10}]
        r_col = _update(tmp_path / "col", col_items)
        r_bw  = _update(tmp_path / "bw",  bw_items)
        assert r_col["quote"]["total"] > r_bw["quote"]["total"]

    def test_a3_col_derives_correct_paper_type(self, tmp_path):
        """A3 colour items must use A3_col, not A4_BW."""
        items = [{"colour": "col", "sides": "ss", "layout": "1-up",
                  "copies": 1, "pages": 5}]
        result = _update(tmp_path, items, paper_size="A3")
        # A3 col SS = Rs.20/sheet → 5 sheets = Rs.100
        assert result["quote"]["total"] == 100.0, (
            f"A3 colour 5 pages should be Rs.100, got {result['quote']['total']}"
        )

    def test_paper_type_stored_in_db_matches_colour(self, tmp_path):
        """
        After handle_update_job, the print_items row in DB must have
        paper_type=A4_col for a colour item, not A4_BW.
        """
        db = _make_db(tmp_path)
        original = print_server.DB_PATH
        try:
            print_server.DB_PATH = db
            body = {
                "job_id": "OSP-TEST-0001", "staff_id": "staff1",
                "finishing": "none", "is_student": False, "urgent": False,
                "paper_size": "A4",
                "print_items": [{"colour": "col", "sides": "ss",
                                  "layout": "1-up", "copies": 1, "pages": 10}],
            }
            print_server.handle_update_job(body)
        finally:
            print_server.DB_PATH = original

        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT paper_type FROM print_items WHERE job_id='OSP-TEST-0001'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "A4_col", (
            f"Expected paper_type='A4_col' in DB, got '{row[0]}'"
        )
