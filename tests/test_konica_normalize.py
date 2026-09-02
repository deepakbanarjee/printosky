"""
konica_normalize — one field shape for konica_jobs, whoever wrote the row.

These tests exist because three silent divergences between the table's two
writers froze the MIS Konica panel on February-March data for five months while
it kept rendering plausible-looking numbers. Each divergence gets a test named
after what it actually cost.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import konica_normalize as kn


# ── job_type ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("PRINT", "Print"), ("Print", "Print"), ("print", "Print"), (" PrInT ", "Print"),
    ("COPY", "Copy"),   ("Copy", "Copy"),   ("copy", "Copy"),
    ("SCAN", "Scan"),   ("Scan", "Scan"),   ("scan", "Scan"),
])
def test_both_writers_vocabularies_land_on_one(raw, expected):
    assert kn.normalize_job_type(raw) == expected


def test_the_bug_this_prevents_upper_case_matched_neither_bucket():
    """MIS bucketed on `job_type === "Print"`, so 12,864 SOAP rows counted as
    neither a print nor a copy. Normalised, they land in a bucket."""
    soap_rows = [{"job_type": "PRINT"}, {"job_type": "COPY"}, {"job_type": "SCAN"}]
    types = [kn.normalize_job_type(r["job_type"]) for r in soap_rows]
    assert types == ["Print", "Copy", "Scan"]
    assert all(t in kn.JOB_TYPES for t in types)


def test_an_empty_job_type_is_none_not_a_guess():
    assert kn.normalize_job_type("") is None
    assert kn.normalize_job_type(None) is None
    assert kn.normalize_job_type("   ") is None


def test_an_unknown_job_type_is_kept_verbatim_never_reshaped():
    """A firmware change adding a new type must not be silently title-cased
    into a type it is not — the row survives so a human can see it."""
    assert kn.normalize_job_type("FAX") == "FAX"
    assert kn.normalize_job_type("BOX PRINT") == "BOX PRINT"


def test_an_unknown_job_type_alerts(monkeypatch):
    seen = []
    monkeypatch.setattr(kn, "_report", lambda c, ok, d="", **k: seen.append((c, ok)))
    kn.normalize_job_type("FAX")
    assert ("konica.job_type", False) in seen


def test_a_known_job_type_does_not_alert(monkeypatch):
    seen = []
    monkeypatch.setattr(kn, "_report", lambda c, ok, d="", **k: seen.append((c, ok)))
    for raw in ("PRINT", "Copy", "scan", "", None):
        kn.normalize_job_type(raw)
    assert seen == []


# ── result ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("OK", "No Error"), ("No Error", "No Error"), ("NOERROR", "No Error"),
    ("USERCANCEL", "Canceled"), ("Canceled", "Canceled"), ("Cancelled", "Canceled"),
    ("UNKNOWNERROR", "Error"), ("Error", "Error"),
])
def test_result_codes_map_to_the_vocabulary_the_consoles_read(raw, expected):
    assert kn.normalize_result(raw) == expected


def test_the_bug_this_prevents_result_filter_excluded_every_live_row():
    """MIS filtered `result=eq.No Error`. The SOAP fetcher writes `OK`, so from
    2026-04-13 the filter matched only the retired importer's 1,980 rows."""
    assert kn.normalize_result("OK") == kn.RESULT_OK
    assert kn.is_ok("OK") and kn.is_ok("No Error")


def test_is_ok_is_false_for_work_the_machine_did_not_finish():
    for raw in ("USERCANCEL", "Canceled", "UNKNOWNERROR", "Error", "", None):
        assert not kn.is_ok(raw), raw


def test_an_unknown_result_is_kept_and_alerts(monkeypatch):
    seen = []
    monkeypatch.setattr(kn, "_report", lambda c, ok, d="", **k: seen.append((c, ok)))
    assert kn.normalize_result("PAPERJAM") == "PAPERJAM"
    assert ("konica.result", False) in seen
    assert not kn.is_ok("PAPERJAM")


# ── job_date ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("2026/09/02 09:18:59", "2026-09-02 09:18:59"),
    ("2026/04/13 18:05:21", "2026-04-13 18:05:21"),
    ("2026/4/3 9:05:01",    "2026-04-03 09:05:01"),
    ("2026/06/22 15:06",    "2026-06-22 15:06:00"),
    ("2026-02-27 18:30:12", "2026-02-27 18:30:12"),
    ("2026-02-27T18:30:12", "2026-02-27 18:30:12"),
    ("2026-02-27",          "2026-02-27"),
])
def test_every_stored_date_shape_becomes_iso(raw, expected):
    assert kn.normalize_job_date(raw) == expected


def test_the_bug_this_prevents_slash_dates_passed_every_window_filter():
    """'/' (0x2F) sorts above '-' (0x2D) as a string, so every slash-dated row
    passed `job_date=gte.<any ISO date>` — today, week, month and year were the
    same query. Normalised, the comparison means what it says."""
    slash = "2026/04/13 18:05:21"
    assert slash >= "2026-09-02"                       # the bug, still true raw
    assert kn.normalize_job_date(slash) < "2026-09-02"  # and gone once normalised


def test_the_csv_date_format_still_parses():
    assert kn.normalize_job_date("16/Mar/2026 9:46:14 AM") == "2026-03-16 09:46:14"


def test_an_unparseable_date_is_none_not_a_guess(monkeypatch):
    """A wrong timestamp puts a job in the wrong day's revenue. No date beats
    an invented one — the rule store_digest.overdue_finishing follows too."""
    seen = []
    monkeypatch.setattr(kn, "_report", lambda c, ok, d="", **k: seen.append((c, ok)))
    assert kn.normalize_job_date("last tuesday") is None
    assert ("konica.job_date", False) in seen


def test_a_blank_date_is_none_and_does_not_alert(monkeypatch):
    seen = []
    monkeypatch.setattr(kn, "_report", lambda c, ok, d="", **k: seen.append(c))
    assert kn.normalize_job_date("") is None
    assert kn.normalize_job_date(None) is None
    assert seen == []


# ── paper_size ────────────────────────────────────────────────────────────────

def test_paper_size_is_one_bucket_per_size():
    assert kn.normalize_paper_size("Legal") == kn.normalize_paper_size("LEGAL") == "LEGAL"
    assert kn.normalize_paper_size("a4") == "A4"


def test_the_three_ways_of_saying_nothing_become_one():
    for raw in ("", "   ", None, "UNKNOWN", "unknown", "-", "None"):
        assert kn.normalize_paper_size(raw) is None, raw


# ── normalize_row ─────────────────────────────────────────────────────────────

def test_normalize_row_touches_only_the_keys_present():
    row = {"job_type": "COPY", "pages_printed": 12}
    out = kn.normalize_row(row)
    assert out == {"job_type": "Copy", "pages_printed": 12}
    assert "result" not in out and "job_date" not in out


def test_normalize_row_does_not_mutate_its_input():
    row = {"job_type": "COPY", "result": "OK"}
    kn.normalize_row(row)
    assert row == {"job_type": "COPY", "result": "OK"}


def test_normalize_row_is_idempotent():
    row = {"job_type": "COPY", "result": "OK",
           "job_date": "2026/09/02 09:18:59", "paper_size": "Legal"}
    once = kn.normalize_row(row)
    assert kn.normalize_row(once) == once


# ── The self-applying backfill ────────────────────────────────────────────────

def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE konica_jobs (
        job_number INTEGER, job_type TEXT, result TEXT,
        job_date TEXT, paper_size TEXT)""")
    conn.executemany("INSERT INTO konica_jobs VALUES (?,?,?,?,?)", rows)
    conn.commit()
    return conn


def _all(conn):
    return conn.execute(
        "SELECT job_type, result, job_date, paper_size FROM konica_jobs "
        "ORDER BY job_number").fetchall()


def test_backfill_rewrites_soap_rows_into_the_canonical_shape():
    conn = _db([(1, "COPY", "OK", "2026/09/02 09:18:59", "LEGAL")])
    changed = kn.backfill_sqlite(conn)
    assert _all(conn) == [("Copy", "No Error", "2026-09-02 09:18:59", "LEGAL")]
    assert changed["job_type"] == 1 and changed["result"] == 1 and changed["job_date"] == 1
    assert changed["paper_size"] == 0     # already upper-case


def test_backfill_leaves_already_canonical_rows_alone():
    canonical = (1, "Copy", "No Error", "2026-02-27 18:30:12", "A4")
    conn = _db([canonical])
    before = _all(conn)
    changed = kn.backfill_sqlite(conn)
    assert _all(conn) == before
    assert sum(changed.values()) == 0


def test_backfill_is_idempotent():
    conn = _db([(1, "COPY", "OK", "2026/09/02 09:18:59", "legal")])
    kn.backfill_sqlite(conn)
    first = _all(conn)
    changed = kn.backfill_sqlite(conn)
    assert _all(conn) == first
    assert sum(changed.values()) == 0


def test_backfill_batches_without_losing_rows():
    rows = [(i, "COPY", "OK", f"2026/09/02 09:{i % 60:02d}:00", "A4") for i in range(1, 12)]
    conn = _db(rows)
    kn.backfill_sqlite(conn, batch=3)
    out = _all(conn)
    assert len(out) == 11
    assert all(r[0] == "Copy" and r[1] == "No Error" and "/" not in r[2] for r in out)


def test_backfill_keeps_an_unknown_type_rather_than_dropping_the_row():
    conn = _db([(1, "FAX", "OK", "2026/09/02 09:18:59", "A4")])
    kn.backfill_sqlite(conn)
    assert _all(conn) == [("FAX", "No Error", "2026-09-02 09:18:59", "A4")]


def test_backfill_alerts_rather_than_raising_when_the_table_is_missing(monkeypatch):
    seen = []
    monkeypatch.setattr(kn, "_report", lambda c, ok, d="", **k: seen.append((c, ok)))
    conn = sqlite3.connect(":memory:")
    changed = kn.backfill_sqlite(conn)
    assert sum(changed.values()) == 0
    assert ("konica.backfill", False) in seen
