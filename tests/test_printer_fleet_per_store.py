"""
The EM-C8100 swap (2026-06-29) and the no-Konica stores.

Two things quietly broke when the Epson WF-C21000 was replaced by the EM-C8100:

  1. Jobs are recorded against the Windows *queue* name they were dispatched to
     — "EM-C8100 Series(Network)". Every consumer that recognised the Epson by
     looking for "epson", "wf" or the old IP 192.168.55.202 stopped matching,
     so SNMP page deltas were attributed to nobody.

  2. Nattika (PRINTK) has no Konica at all, yet PRINTERS still carries the
     inherited OSP Konica queue name — so presence has to be read off the
     configured IP, and the consoles have to be told, or they show a printer
     that store does not own.

Covers the Python side; the browser side (admin.html / jobs.html) is asserted
in test_admin_printer_fleet.py.
"""

import os
import sqlite3
import sys
import types

# ── Stub every external dep print_server / epson_jobs_fetcher import ─────────
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

import epson_jobs_fetcher
import print_server


# ── 1. Delta attribution recognises the EM-C8100 queue ───────────────────────

def _attribution_db(printer_name: str) -> sqlite3.Connection:
    """In-memory DB with two Epson SNMP readings 10 pages apart and one job
    printed in that window on `printer_name`."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE printer_counters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            polled_at TEXT, printer TEXT, method TEXT,
            total_pages INTEGER, print_bw INTEGER, copy_bw INTEGER,
            print_colour INTEGER, copy_colour INTEGER, raw_data TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, page_count INTEGER, copies INTEGER,
            printer TEXT, printed_at TEXT
        )
    """)
    epson_jobs_fetcher.init_epson_jobs_table(conn)
    conn.executemany(
        "INSERT INTO printer_counters (polled_at, printer, method, total_pages, print_colour)"
        " VALUES (?, 'epson', 'snmp', ?, ?)",
        [("2026-08-18 10:00:00", 1000, 100), ("2026-08-18 10:05:00", 1010, 104)],
    )
    conn.execute(
        "INSERT INTO jobs (job_id, page_count, copies, printer, printed_at)"
        " VALUES ('OSP-20260818-0001', 10, 1, ?, '2026-08-18 10:02:00')",
        (printer_name,),
    )
    conn.commit()
    return conn


@pytest.mark.parametrize("queue_name", [
    "EM-C8100 Series(Network)",          # the current unit, both stores
    "EPSON EM-C8100 Series",             # queue named with the brand prefix
    "WF-C21000 Series(Network)",         # retired 2026-06-29 — old rows must still match
    "Epson WF-C21000",
])
def test_delta_attribution_matches_epson_queue_names(queue_name):
    conn = _attribution_db(queue_name)
    inserted = epson_jobs_fetcher._delta_attribution(conn)
    assert inserted == 1, f"{queue_name!r} was not recognised as the Epson"
    row = conn.execute(
        "SELECT attributed_job_id, delta_pages FROM epson_jobs WHERE source='delta'"
    ).fetchone()
    assert row == ("OSP-20260818-0001", 10)


def test_delta_attribution_does_not_credit_a_konica_job():
    """A Konica job in the window is not the Epson's work: the delta is still
    recorded, but unattributed (attributed_job_id NULL)."""
    conn = _attribution_db("KONICA MINOLTA 1100 PS")
    assert epson_jobs_fetcher._delta_attribution(conn) == 1
    row = conn.execute(
        "SELECT attributed_job_id, delta_pages FROM epson_jobs WHERE source='delta'"
    ).fetchone()
    assert row == (None, 10)


def test_epson_patterns_include_the_configured_ip(monkeypatch):
    monkeypatch.setattr(epson_jobs_fetcher, "get_epson_ip", lambda: "192.168.55.214")
    pats = epson_jobs_fetcher._epson_printer_patterns()
    assert "%192.168.55.214%" in pats     # today's EM-C8100
    assert "%192.168.55.202%" in pats     # the WF-C21000 it replaced
    assert len(pats) == len(set(pats)), "duplicate LIKE patterns"


# ── 2. Konica presence is decided by the configured IP ───────────────────────

@pytest.mark.parametrize("konica_ip,expected", [
    ("192.168.55.110", True),    # OSP
    ("", False),                 # Nattika: konica_ip blank
    (None, False),               # ...or absent
    ("None", False),             # ...or a JSON null that has been through str()
])
def test_has_konica_reads_the_configured_ip(monkeypatch, konica_ip, expected):
    monkeypatch.setitem(print_server.PRINTER_IPS, "konica", konica_ip)
    assert print_server.has_konica() is expected


def test_konica_dispatch_redirects_to_epson_where_there_is_no_konica(monkeypatch):
    monkeypatch.setitem(print_server.PRINTER_IPS, "konica", "")
    assert print_server._effective_printer_key("konica", "OSP-1") == "epson"
    assert print_server._effective_printer_key("epson", "OSP-1") == "epson"


def test_konica_dispatch_is_left_alone_where_there_is_a_konica(monkeypatch):
    monkeypatch.setitem(print_server.PRINTER_IPS, "konica", "192.168.55.110")
    assert print_server._effective_printer_key("konica", "OSP-1") == "konica"


# ── 3. The Konica dual-queue duplex/simplex workaround ────────────────────────
#
# KONICA MINOLTA 1100 PS silently ignores SumatraPDF's per-job duplex/simplex
# override in both directions (docs/PRINT_ROTATION_MATRIX.md, 2026-08-29) — it
# just follows whatever its Windows Printing Preferences default currently is.
# The fix is to install the same physical printer twice as two named Windows
# queues, each with its own persisted default, and pick the QUEUE instead of
# asking the driver to override. _konica_queue_for_sides is inert (returns
# None, meaning "use the plain 'konica' queue, unchanged") until a store
# configures both queue names.

@pytest.mark.parametrize("sides,variant_key", [
    ("ds", "konica_duplex"), ("duplex", "konica_duplex"),
    ("duplexlong", "konica_duplex"), ("duplexshort", "konica_duplex"),
    ("ss", "konica_simplex"), ("simplex", "konica_simplex"),
])
def test_konica_queue_for_sides_picks_the_configured_variant(monkeypatch, sides, variant_key):
    monkeypatch.setitem(print_server.PRINTERS, "konica_duplex", "KONICA MINOLTA 1100 PS (Duplex)")
    monkeypatch.setitem(print_server.PRINTERS, "konica_simplex", "KONICA MINOLTA 1100 PS (Simplex)")
    assert print_server._konica_queue_for_sides(sides) == variant_key


@pytest.mark.parametrize("sides", ["ds", "ss", "duplex", "simplex", None, "", "auto"])
def test_konica_queue_for_sides_is_a_no_op_until_both_queues_are_configured(monkeypatch, sides):
    monkeypatch.delitem(print_server.PRINTERS, "konica_duplex", raising=False)
    monkeypatch.delitem(print_server.PRINTERS, "konica_simplex", raising=False)
    assert print_server._konica_queue_for_sides(sides) is None


def test_konica_queue_for_sides_ignores_an_unrecognised_sides_value(monkeypatch):
    monkeypatch.setitem(print_server.PRINTERS, "konica_duplex", "KONICA MINOLTA 1100 PS (Duplex)")
    monkeypatch.setitem(print_server.PRINTERS, "konica_simplex", "KONICA MINOLTA 1100 PS (Simplex)")
    assert print_server._konica_queue_for_sides("auto") is None
    assert print_server._konica_queue_for_sides(None) is None


def test_send_to_printer_routes_to_the_duplex_queue_when_configured(monkeypatch, tmp_path):
    monkeypatch.setitem(print_server.PRINTER_IPS, "konica", "192.168.55.110")
    monkeypatch.setitem(print_server.PRINTERS, "konica_duplex", "KONICA MINOLTA 1100 PS (Duplex)")
    monkeypatch.setitem(print_server.PRINTERS, "konica_simplex", "KONICA MINOLTA 1100 PS (Simplex)")
    monkeypatch.setattr(print_server, "find_sumatra", lambda: "C:/fake/SumatraPDF.exe")
    monkeypatch.setattr(print_server, "update_job_status", lambda *a, **k: None)
    monkeypatch.setattr(print_server, "_trigger_printer_poll_now", lambda *a, **k: None)

    seen_cmd = {}
    class _Result:
        returncode = 0
    def _fake_run(cmd, **kwargs):
        seen_cmd["cmd"] = cmd
        return _Result()
    monkeypatch.setattr(print_server.subprocess, "run", _fake_run)

    f = tmp_path / "job.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    ok, _msg = print_server.send_to_printer(
        job_id="OSP-1", filepath=str(f), printer_key="konica",
        sides="duplex", update_status=False,
    )
    assert ok
    assert "KONICA MINOLTA 1100 PS (Duplex)" in seen_cmd["cmd"]
