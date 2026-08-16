"""Tests for Epson SNMP delta attribution in epson_jobs_fetcher.py.

The regression these guard: `jobs.printer` stores the **Windows print-queue
name** print_server dispatched to, never an IP. The original query matched
'%epson%', '%wf%' and '%192.168.55.202%' — so when the WF-C21000 was replaced
by the EM-C8100 on 2026-06-29 (queue "EM-C8100 Series(Network)") every pattern
stopped matching and delta attribution silently attributed nothing.
"""

import sqlite3
import sys
import types

import pytest

# ── Stub heavy deps before import ─────────────────────────────────────────────
for _mod in ("requests", "urllib3"):
    if _mod not in sys.modules:
        _stub = types.ModuleType(_mod)
        if _mod == "urllib3":
            _stub.exceptions = types.SimpleNamespace(InsecureRequestWarning=Warning)
            _stub.disable_warnings = lambda *a, **k: None
        else:
            _stub.Session = object
        sys.modules[_mod] = _stub

import epson_jobs_fetcher as ejf  # noqa: E402


CURRENT_QUEUE = "EM-C8100 Series(Network)"   # installed 2026-06-29
RETIRED_QUEUE = "WF-C21000 Series(Network)"  # rows printed before the swap
KONICA_QUEUE = "KONICA MINOLTA 1100 PS"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("""
        CREATE TABLE jobs (
            job_id     TEXT PRIMARY KEY,
            page_count INTEGER,
            copies     INTEGER,
            printer    TEXT,
            printed_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE printer_counters (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            polled_at    TEXT NOT NULL,
            printer      TEXT NOT NULL,
            total_pages  INTEGER,
            print_colour INTEGER
        )
    """)
    ejf.init_epson_jobs_table(c)
    yield c
    c.close()


def _seed_readings(conn, before, after, col_before=None, col_after=None):
    conn.execute(
        "INSERT INTO printer_counters (polled_at, printer, total_pages, print_colour)"
        " VALUES ('2026-08-16 09:00:00', 'epson', ?, ?)", (before, col_before))
    conn.execute(
        "INSERT INTO printer_counters (polled_at, printer, total_pages, print_colour)"
        " VALUES ('2026-08-16 09:05:00', 'epson', ?, ?)", (after, col_after))


def _seed_job(conn, job_id, queue, pages=10, copies=1):
    conn.execute(
        "INSERT INTO jobs (job_id, page_count, copies, printer, printed_at)"
        " VALUES (?, ?, ?, ?, '2026-08-16 09:02:00')",
        (job_id, pages, copies, queue))


def _attributed(conn):
    return [r[0] for r in conn.execute(
        "SELECT attributed_job_id FROM epson_jobs WHERE source='delta'"
        " AND attributed_job_id IS NOT NULL").fetchall()]


# ── epson_printer_patterns() ──────────────────────────────────────────────────

def test_patterns_match_current_queue_name():
    """The EM-C8100 queue must match — this is the case that broke."""
    patterns = ejf.epson_printer_patterns()
    assert any(
        CURRENT_QUEUE.lower().find(p.strip("%")) >= 0 for p in patterns
    ), f"no pattern in {patterns} matches {CURRENT_QUEUE!r}"


def test_patterns_still_match_retired_queue_name():
    """Historical rows printed on the WF-C21000 must keep attributing."""
    patterns = ejf.epson_printer_patterns()
    assert any(RETIRED_QUEUE.lower().find(p.strip("%")) >= 0 for p in patterns)


def test_patterns_do_not_match_konica():
    patterns = ejf.epson_printer_patterns()
    assert not any(KONICA_QUEUE.lower().find(p.strip("%")) >= 0 for p in patterns)


def test_configured_queue_name_is_included(monkeypatch):
    """A store that renamed its Epson queue still attributes."""
    fake = types.SimpleNamespace(printer_queue_names={"epson": "Back Office Epson"})
    monkeypatch.setattr(ejf, "get_store_config", lambda: fake)
    assert "%back office epson%" in ejf.epson_printer_patterns()


def test_patterns_survive_missing_store_config(monkeypatch):
    """Import-safety contract: no config file must not break attribution."""
    def boom():
        raise RuntimeError("no config")
    monkeypatch.setattr(ejf, "get_store_config", boom)
    assert ejf.epson_printer_patterns()  # falls back to the model-name list


# ── _delta_attribution() end to end ───────────────────────────────────────────

def test_delta_attributes_job_on_current_printer(conn):
    _seed_readings(conn, 1000, 1010)
    _seed_job(conn, "OSKY-20260816-0001", CURRENT_QUEUE, pages=10)

    inserted = ejf._delta_attribution(conn)

    assert inserted == 1
    assert _attributed(conn) == ["OSKY-20260816-0001"]


def test_delta_attributes_job_on_retired_printer(conn):
    _seed_readings(conn, 2000, 2005)
    _seed_job(conn, "OSP-20260601-0007", RETIRED_QUEUE, pages=5)

    ejf._delta_attribution(conn)

    assert _attributed(conn) == ["OSP-20260601-0007"]


def test_delta_ignores_konica_jobs(conn):
    _seed_readings(conn, 3000, 3010)
    _seed_job(conn, "OSKY-20260816-0002", KONICA_QUEUE, pages=10)

    ejf._delta_attribution(conn)

    assert _attributed(conn) == []


def test_delta_splits_proportionally_across_two_epson_jobs(conn):
    _seed_readings(conn, 4000, 4030)
    _seed_job(conn, "OSKY-20260816-0003", CURRENT_QUEUE, pages=10, copies=1)  # weight 10
    _seed_job(conn, "OSKY-20260816-0004", CURRENT_QUEUE, pages=10, copies=1)  # weight 10

    ejf._delta_attribution(conn)

    rows = dict(conn.execute(
        "SELECT attributed_job_id, delta_pages FROM epson_jobs"
        " WHERE source='delta' AND attributed_job_id IS NOT NULL").fetchall())
    assert sorted(rows) == ["OSKY-20260816-0003", "OSKY-20260816-0004"]
