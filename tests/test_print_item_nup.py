"""handle_print_item must impose N-up before printing.

The bug: POST /print (the staff jobs-console path) never called the imposer.
It appended "nup2"/"nup4" to SumatraPDF's -print-settings, tokens SumatraPDF
does not implement and silently discards, then printed the *original* file.
So a staff N-up job came out one page per sheet with a duplex flag and no
imposition at all — while the paid-order path in store_puller imposed
correctly, which is why the two disagreed for months.
"""

import os
import sqlite3
import sys
import types

# ── Stub the external deps print_server imports at module load ───────────────
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

import pytest  # noqa: E402

fitz = pytest.importorskip("fitz", reason="PyMuPDF not installed")

import print_server  # noqa: E402


A4_W, A4_H = 595.28, 841.89


# ── helpers ───────────────────────────────────────────────────────────────────

def _source_pdf(path, pages=8):
    doc = fitz.open()
    for i in range(1, pages + 1):
        pg = doc.new_page(width=A4_W, height=A4_H)
        pg.insert_text((250, 420), str(i), fontsize=200)
    doc.save(path)
    doc.close()
    return path


def _db(tmp_path, filepath, layout="2-up", sides="ds", page_list="all", pages=8):
    db = str(tmp_path / "jobs.db")
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, filepath TEXT, filename TEXT,
            page_count INTEGER, size TEXT, colour TEXT, status TEXT,
            notes TEXT, printer TEXT, printed_by TEXT, printed_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE print_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
            item_number INTEGER NOT NULL, page_list TEXT DEFAULT 'all',
            paper_type TEXT, colour TEXT, sides TEXT, layout TEXT,
            copies INTEGER DEFAULT 1, paper_gsm INTEGER DEFAULT 70,
            printer TEXT DEFAULT 'konica', status TEXT DEFAULT 'Pending',
            printed_at TEXT, printed_by TEXT
        )
    """)
    conn.execute(
        "INSERT INTO jobs (job_id, filepath, filename, page_count, size, colour, status)"
        " VALUES ('OSP-NUP-1', ?, 'src.pdf', ?, 'A4', 'bw', 'Pending')",
        (filepath, pages))
    conn.execute(
        "INSERT INTO print_items (job_id, item_number, page_list, colour, sides,"
        " layout, copies, printer) VALUES ('OSP-NUP-1', 1, ?, 'bw', ?, ?, 1, 'konica')",
        (page_list, sides, layout))
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def spy(tmp_path, monkeypatch):
    """Run handle_print_item with SumatraPDF replaced by a recorder.

    Captures the file and settings string that would have been printed, and
    snapshots the PDF before the temp dir is cleaned up.
    """
    captured = {}

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["settings"] = cmd[cmd.index("-print-settings") + 1]
        printed = os.path.join(kwargs.get("cwd") or "", cmd[-1])
        captured["printed_path"] = printed
        with fitz.open(printed) as doc:
            captured["sheets"] = len(doc)
            captured["size"] = (doc[0].rect.width, doc[0].rect.height)
            captured["pages_on_sheets"] = [
                sorted(
                    "".join(s["text"] for s in ln["spans"]).strip()
                    for blk in pg.get_text("dict")["blocks"]
                    for ln in blk.get("lines", [])
                    if "".join(s["text"] for s in ln["spans"]).strip().isdigit()
                )
                for pg in doc
            ]
        return _Result()

    monkeypatch.setattr(print_server.subprocess, "run", fake_run)
    monkeypatch.setattr(print_server, "find_sumatra", lambda: r"C:\fake\SumatraPDF.exe")
    monkeypatch.setattr(print_server, "_push_job_status_supabase", lambda *a, **k: None)
    monkeypatch.setattr(print_server.threading, "Thread",
                        lambda *a, **k: types.SimpleNamespace(start=lambda: None))
    return captured


def _run(tmp_path, spy, **db_kw):
    src = _source_pdf(str(tmp_path / "src.pdf"), pages=db_kw.pop("pages", 8))
    db = _db(tmp_path, src, pages=8, **db_kw)
    original = print_server.DB_PATH
    try:
        print_server.DB_PATH = db
        result = print_server.handle_print_item("OSP-NUP-1", 1, "staff1")
    finally:
        print_server.DB_PATH = original
    return result, spy


# ── the regression ────────────────────────────────────────────────────────────

def test_2up_actually_imposes_two_pages_per_sheet(tmp_path, spy):
    result, cap = _run(tmp_path, spy, layout="2-up", sides="ds")

    assert result["ok"], result
    assert cap["sheets"] == 4, (
        f"8 pages at 2-up should be 4 sheet-sides, got {cap['sheets']} — "
        "the file was printed un-imposed")
    assert cap["pages_on_sheets"][0] == ["1", "2"]
    assert cap["pages_on_sheets"][1] == ["3", "4"]


def test_4up_imposes_four_pages_per_sheet(tmp_path, spy):
    _, cap = _run(tmp_path, spy, layout="4-up", sides="ds")

    assert cap["sheets"] == 2
    assert cap["pages_on_sheets"][0] == ["1", "2", "3", "4"]


def test_the_dead_nup_tokens_are_gone(tmp_path, spy):
    """SumatraPDF has no nup token; emitting one just hid the missing work."""
    _, cap = _run(tmp_path, spy, layout="2-up", sides="ds")

    assert "nup2" not in cap["settings"]
    assert "nup4" not in cap["settings"]


def test_imposed_sheet_is_portrait_and_duplex_long(tmp_path, spy):
    _, cap = _run(tmp_path, spy, layout="2-up", sides="ds")

    w, h = cap["size"]
    assert h > w, f"imposed sheet should be portrait, got {w:.0f}x{h:.0f}"
    assert "duplexlong" in cap["settings"]
    assert "duplexshort" not in cap["settings"]


def test_1up_is_untouched(tmp_path, spy):
    """The common path must not gain an imposition step."""
    result, cap = _run(tmp_path, spy, layout="1-up", sides="ds")

    assert result["ok"]
    assert cap["sheets"] == 8               # original file, one page each
    assert cap["printed_path"].endswith("src.pdf")


def test_page_range_is_not_applied_twice(tmp_path, spy):
    """The imposer slices pages_included, so no range may also go to Sumatra —
    otherwise the range gets re-applied to the imposed sheets."""
    _, cap = _run(tmp_path, spy, layout="2-up", sides="ds", page_list="1-4")

    assert cap["sheets"] == 2, "4 selected pages at 2-up = 2 sheet-sides"
    assert cap["pages_on_sheets"][0] == ["1", "2"]
    assert "1-4" not in cap["settings"], "page range applied on top of imposition"


def test_1up_still_sends_its_page_range(tmp_path, spy):
    _, cap = _run(tmp_path, spy, layout="1-up", sides="ds", page_list="1-4")

    assert "1-4" in cap["settings"]


def test_imposition_failure_falls_back_to_printing_the_original(tmp_path, spy, monkeypatch):
    """A broken imposer must not stop staff printing at the counter."""
    import print_planner

    def boom(*a, **k):
        raise RuntimeError("imposer exploded")

    monkeypatch.setattr(print_planner, "plan_print_job", boom)

    result, cap = _run(tmp_path, spy, layout="2-up", sides="ds")

    assert result["ok"]
    assert cap["printed_path"].endswith("src.pdf")


# ── helpers used by the above ─────────────────────────────────────────────────

@pytest.mark.parametrize("layout,expected", [
    ("1-up", 1), ("2-up", 2), ("4-up", 4), ("6-up", 6), ("9-up", 9),
    ("", 1), (None, 1), ("garbage", 1), ("3-up", 1),
])
def test_layout_to_nup(layout, expected):
    assert print_server._layout_to_nup(layout) == expected


@pytest.mark.parametrize("page_list,total,expected", [
    ("all", 10, []),
    ("", 10, []),
    (None, 10, []),
    ("1-5", 10, [1, 2, 3, 4, 5]),
    ("1,3,5", 10, [1, 3, 5]),
    ("1-3,7", 10, [1, 2, 3, 7]),
    ("1-3,2-4", 10, [1, 2, 3, 4]),      # overlaps collapse
    ("1-99", 10, list(range(1, 11))),   # clamped to the document
    ("nonsense", 10, []),
])
def test_page_list_to_numbers(page_list, total, expected):
    assert print_server._page_list_to_numbers(page_list, total) == expected
