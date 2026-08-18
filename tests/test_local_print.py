"""
Local-first printing: a file printed at the counter never goes to the cloud.

A walk-in used to travel browser -> Supabase Storage -> a jobs row carrying a
file_url -> the store PC's puller downloads it back -> prints. A round trip
through the internet for a file that never leaves the room, and counter printing
that stopped working whenever the line did.

/local-print keeps the bytes on the PC that will print them. The job record
still syncs to the cloud, so the console sees it — but with no file_url, which
is also what stops the puller printing it a second time.
"""

import base64
import os
import sqlite3
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

_STUBS = [
    "gspread", "google", "google.auth", "google.auth.transport",
    "google.auth.transport.requests", "google.oauth2", "google.oauth2.service_account",
    "websockets", "requests", "pysnmp", "pysnmp.hlapi",
    "watchdog", "watchdog.observers", "watchdog.events", "razorpay", "dotenv",
]
for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None  # type: ignore

import pytest

import print_server
# Imported before the fixture swaps store_puller for a stub, so this stays the
# real implementation.
from store_puller import select_pullable


PDF = b"%PDF-1.4 fake bytes for a counter job"


def _db(tmp_path) -> str:
    db = str(tmp_path / "jobs.db")
    conn = sqlite3.connect(db)
    conn.execute("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, received_at TEXT, filename TEXT,
            file_extension TEXT, source TEXT, sender TEXT, customer_name TEXT,
            service_type TEXT, colour TEXT, sides TEXT, copies INTEGER,
            finishing TEXT, paper_size TEXT, page_count INTEGER,
            amount_quoted REAL, amount_collected REAL, amount_partial REAL,
            payment_mode TEXT, override_reason TEXT, status TEXT, queued_at TEXT,
            filepath TEXT, notes TEXT, staff_notes TEXT, printer TEXT,
            printed_at TEXT, printed_by TEXT
        )""")
    conn.execute("""
        CREATE TABLE print_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, item_number INTEGER,
            page_list TEXT, paper_type TEXT, colour TEXT, sides TEXT, layout TEXT,
            copies INTEGER, paper_gsm INTEGER, printer TEXT, status TEXT
        )""")
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def store(tmp_path, monkeypatch):
    """print_server pointed at a scratch DB and a scratch local-jobs dir, with
    the printer itself stubbed out."""
    monkeypatch.setattr(print_server, "DB_PATH", _db(tmp_path))
    monkeypatch.setattr(print_server, "LOCAL_JOBS_DIR", str(tmp_path / "Local"))
    monkeypatch.setattr(print_server, "_report_health", lambda *a, **k: None)
    printed = []
    fake_puller = types.ModuleType("store_puller")
    fake_puller.auto_print = lambda job_id, path, colour, copies, **kw: (
        printed.append({"job_id": job_id, "path": path, "colour": colour,
                        "copies": copies, **kw}) or True)
    monkeypatch.setitem(sys.modules, "store_puller", fake_puller)
    return printed


def _body(**over):
    body = {
        "filename": "walkin.pdf",
        "file_data": base64.b64encode(PDF).decode(),
        "customer_name": "Anu", "phone": "919000000000",
        "colour": "bw", "copies": 2, "paper_size": "A4", "sides": "ds", "pages": 4,
        "payment_mode": "Cash", "amount_collected": 20, "amount_quoted": 20,
        "staff_id": "counter", "print_spec": {"copies": 2, "colour_mode": "bw",
                                              "paper_size": "A4", "sides": "duplex"},
    }
    body.update(over)
    return body


# ── The file stays here ───────────────────────────────────────────────────────

def test_the_file_is_written_locally_and_printed(store, tmp_path):
    out = print_server.handle_local_print(_body())

    assert out["ok"] is True and out["local"] is True
    assert out["printed"] is True
    saved = tmp_path / "Local"
    files = list(saved.iterdir())
    assert len(files) == 1 and files[0].read_bytes() == PDF
    assert store[0]["job_id"] == out["job_id"]
    assert store[0]["copies"] == 2


def test_the_job_row_carries_no_file_url_so_the_puller_cannot_reprint_it(store):
    """select_pullable requires a non-empty file_url; a local job has none."""
    out = print_server.handle_local_print(_body())
    conn = sqlite3.connect(print_server.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = dict(conn.execute("SELECT * FROM jobs WHERE job_id=?", (out["job_id"],)).fetchone())
    conn.close()

    assert "file_url" not in row or not row.get("file_url")
    cloud_shaped = {"job_id": out["job_id"], "status": "Paid",
                    "assigned_store_id": "OSP", "file_url": row.get("file_url") or ""}
    assert select_pullable([cloud_shaped], set()) == []


def test_the_file_never_goes_to_the_hot_folder(store, tmp_path):
    """Dropping it there would trigger watcher intake: a second job, and a
    'here is your quote' WhatsApp to a customer already at the counter."""
    out = print_server.handle_local_print(_body())
    assert "Incoming" not in out["filepath"]
    assert out["filepath"].startswith(str(tmp_path / "Local"))


# ── The record ────────────────────────────────────────────────────────────────

def test_the_quote_from_the_screen_is_what_gets_billed(store):
    """Without this the PC would re-quote from colour alone and overcharge a
    mixed job relative to what the customer was just shown."""
    out = print_server.handle_local_print(_body(amount_quoted=137, colour="col"))
    conn = sqlite3.connect(print_server.DB_PATH)
    amount = conn.execute("SELECT amount_quoted FROM jobs WHERE job_id=?",
                          (out["job_id"],)).fetchone()[0]
    conn.close()
    assert amount == 137


def test_a_counter_job_is_queued_and_attributed(store):
    out = print_server.handle_local_print(_body())
    conn = sqlite3.connect(print_server.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (out["job_id"],)).fetchone()
    conn.close()
    assert row["status"] == "Queued"
    assert row["source"] == "Walk-in"
    assert row["customer_name"] == "Anu"
    assert row["filepath"] == out["filepath"]


def test_print_can_be_skipped_for_a_job_that_is_only_being_recorded(store):
    out = print_server.handle_local_print(_body(print=False))
    assert out["ok"] is True
    assert "printed" not in out
    assert store == []


# ── Failure behaviour ─────────────────────────────────────────────────────────

def test_a_print_failure_still_keeps_the_file_and_says_so(store, monkeypatch):
    fake = sys.modules["store_puller"]
    monkeypatch.setattr(fake, "auto_print", lambda *a, **k: False)

    out = print_server.handle_local_print(_body())

    assert out["ok"] is True and out["printed"] is False
    assert "print manually" in out["error"]
    assert os.path.exists(out["filepath"]), "the file must survive for a manual print"


def test_a_printer_exception_is_reported_not_swallowed(store, monkeypatch):
    reported = []
    monkeypatch.setattr(print_server, "_report_health",
                        lambda check, ok, detail="": reported.append((check, ok)))
    fake = sys.modules["store_puller"]

    def _boom(*a, **k):
        raise RuntimeError("SumatraPDF missing")

    monkeypatch.setattr(fake, "auto_print", _boom)
    out = print_server.handle_local_print(_body())

    assert out["printed"] is False
    assert ("print.local", False) in reported


@pytest.mark.parametrize("bad", [
    {"filename": ""},
    {"file_data": ""},
    {"file_data": "not base64!!"},
])
def test_bad_input_is_rejected_before_anything_is_created(store, bad):
    out = print_server.handle_local_print(_body(**bad))
    assert out["ok"] is False
    assert store == []
