"""The counter's two paths agree about payment — for every payment mode.

A counter job in order-v2 staff mode tries the store PC first
(`print_server /local-print`) and falls back to the cloud
(`/order/staff-create`) if that fails. The fallback prints too, so the operator
sees a sheet either way and a broken local path is invisible from the counter.

It was broken. `order-ui.js` sent a hardcoded `amount_collected: 0` beside
`payment_mode: 'Cash'`, and `handle_create_job` refuses a job that is neither
paid nor overridden. So **Cash and UPI — the modes where money actually changed
hands — failed locally and fell back**, while `hold` passed on its override
reason. Backwards. Confirmed on paper at OSP 2026-09-06: two jobs thirty-one
seconds apart, the hold one local (`OSKY-20260906-0001`, `Walk-in`, a filepath,
no `file_url`) and the cash one in the cloud (`OSKY-20260906-4805-5175`, `web`,
a `file_url`). Five of six attempts across two days took the wrong path.

`tests/test_local_print.py` could not catch it: it calls `handle_local_print`
with a body carrying `amount_collected: 20`. The server was always fine with a
correct payload — it was the client that never sent one. So the gap was never in
either half, it was *between* them, which is what these tests cover.
"""

import base64
import os
import re
import sqlite3
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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

ORDER_UI = ROOT / "website" / "order" / "order-ui.js"
QUOTE = 42.0          # what the screen quoted; any non-zero figure will do

# The contract both halves must honour, one row per payment mode the counter
# offers. `collects` is whether money changed hands at the counter — which is
# exactly what decides whether the server's guard needs an amount or a reason.
COUNTER_MODES = {
    "cash": {"collects": True,  "payment_mode": "Cash"},
    "upi":  {"collects": True,  "payment_mode": "UPI"},
    "hold": {"collects": False, "payment_mode": "Cash"},
}


# ── the client half: what order-ui.js puts in the /local-print body ───────────

def _local_print_body_source() -> str:
    """The object literal tryLocalPrint() posts to /local-print."""
    src = ORDER_UI.read_text(encoding="utf-8")
    start = src.index("async function tryLocalPrint")
    end = src.index("\n}", start)
    body = src[start:end]
    assert "/local-print" in body, "tryLocalPrint no longer posts to /local-print"
    return body


def test_the_client_sends_the_money_it_collected():
    """A paying counter job must send a real amount, not a placeholder.

    The literal `amount_collected: 0` is the bug itself: with `payment_mode`
    reading 'Cash' it tells the store PC that a cash sale collected nothing,
    which the server is right to refuse.
    """
    body = _local_print_body_source()
    line = re.search(r"^\s*amount_collected:\s*(.+?),\s*$", body, re.M)
    assert line, "tryLocalPrint no longer sends amount_collected at all"
    expr = line.group(1)

    assert expr.strip() != "0", (
        "amount_collected is hardcoded to 0 again. Beside payment_mode 'Cash' "
        "that is a cash sale collecting nothing: handle_create_job refuses it, "
        "the counter falls back to the cloud, and nobody notices because the "
        "fallback prints too."
    )
    assert "amount_estimated" in expr, (
        "amount_collected must come from the quote the customer was shown "
        "(spec.amount_estimated) — the same figure sent as amount_quoted. "
        "Anything else files the sale at the wrong number."
    )
    assert "hold" in expr, (
        "a job on hold has collected nothing yet and must still send 0; only "
        "cash and upi carry the amount"
    )


def test_the_client_reports_why_the_store_pc_refused():
    """The refusal reason must survive to the fallback warning.

    `throw new Error('no job id')` discarded `data.error`, so the response body
    — the only place the store PC ever stated the reason — was dropped, and the
    console said nothing about why the counter had gone back to the cloud.
    """
    body = _local_print_body_source()
    assert "data.error" in body, (
        "tryLocalPrint throws away the server's error again; the fallback is "
        "silent enough already"
    )


# ── the server half: what print_server accepts ───────────────────────────────

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
    monkeypatch.setattr(print_server, "DB_PATH", _db(tmp_path))
    monkeypatch.setattr(print_server, "LOCAL_JOBS_DIR", str(tmp_path / "Local"))
    monkeypatch.setattr(print_server, "_report_health", lambda *a, **k: None)
    fake_puller = types.ModuleType("store_puller")
    fake_puller.auto_print = lambda *a, **k: True
    monkeypatch.setitem(sys.modules, "store_puller", fake_puller)


def _body_for(mode: str) -> dict:
    """Rebuild, in Python, exactly what order-ui.js posts for this mode.

    Kept beside the source assertions above: those pin the JS to this shape, so
    if the client drifts, the first pair of tests fails rather than this one
    silently testing a payload nobody sends.
    """
    spec = COUNTER_MODES[mode]
    return {
        "filename": "counter.pdf",
        "file_data": base64.b64encode(b"%PDF-1.4 counter job").decode(),
        "print_spec": {"copies": 1, "colour_mode": "bw", "paper_size": "A4",
                       "sides": "simplex"},
        "colour": "bw", "copies": 1, "paper_size": "A4", "sides": "ss", "pages": 1,
        "amount_quoted": QUOTE,
        "customer_name": "ZZTEST", "phone": "919000000000", "source": "Walk-in",
        "payment_mode": spec["payment_mode"],
        "amount_collected": 0 if mode == "hold" else QUOTE,
        "override_reason": "Counter job — payment on collection" if mode == "hold" else "",
        "staff_id": "counter",
    }


@pytest.mark.parametrize("mode", sorted(COUNTER_MODES))
def test_every_payment_mode_prints_locally(store, mode):
    """No payment mode may be pushed onto the cloud fallback.

    This is the assertion that was missing. Each mode is a way of saying how the
    customer paid; none of them is a reason to send the file on a round trip
    through the internet to reach a printer in the same room.
    """
    out = print_server.handle_local_print(_body_for(mode))

    assert out.get("ok") is True, (
        f"{mode!r} was refused by the store PC ({out.get('error')!r}); "
        "order-ui.js would silently fall back to the cloud"
    )
    assert out.get("job_id"), (
        f"{mode!r} produced no job_id, which is precisely what makes "
        "order-ui.js fall back"
    )
    assert out.get("local") is True


@pytest.mark.parametrize("mode", sorted(COUNTER_MODES))
def test_the_till_records_what_was_actually_collected(store, mode):
    """Getting past the guard is not enough — the money must be right.

    Satisfying the guard with a zero would file every counter sale at ₹0. The
    cloud fallback records the sale properly, so a local path that did not would
    make a store's takings depend on which route its jobs happened to take.
    """
    out = print_server.handle_local_print(_body_for(mode))
    conn = sqlite3.connect(print_server.DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (out["job_id"],)).fetchone()
    conn.close()

    expected = QUOTE if COUNTER_MODES[mode]["collects"] else 0
    assert (row["amount_collected"] or 0) == expected, (
        f"{mode!r} recorded {row['amount_collected']!r} against a {QUOTE} quote"
    )


def test_the_mode_table_is_not_empty_and_covers_a_paying_mode():
    """A parametrised test over an empty or all-unpaid table proves nothing.

    Three faults in this run were a green light computed over nothing, so state
    the inputs rather than trusting the loop above to have run at all.
    """
    assert COUNTER_MODES, "no payment modes to check"
    assert any(m["collects"] for m in COUNTER_MODES.values()), (
        "no mode in the table collects money, so the guard these tests exist "
        "for is never exercised"
    )
    assert any(not m["collects"] for m in COUNTER_MODES.values()), (
        "no deferred-payment mode, so the override branch is never exercised"
    )
