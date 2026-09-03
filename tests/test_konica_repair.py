"""The March CSV repair — what it recovers, and what it refuses to.

The refusals matter more than the recoveries. This tool infers a date the
printer recorded and the import lost, and an inference that quietly guesses
wrong is worse than 22,985 pages staying invisible — the rule store_digest
already follows for a finishing transfer with no send time.
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "konica_repair", ROOT / "tools" / "konica_repair_march_import.py")
repair = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(repair)


DDL = """CREATE TABLE konica_jobs (
    job_number INTEGER UNIQUE, job_type TEXT, file_name TEXT, result TEXT,
    pages_printed INT, job_date TEXT, print_end_date TEXT)"""


def db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(DDL)
    conn.executemany(
        "INSERT INTO konica_jobs (job_number, job_type, file_name, result,"
        " pages_printed, job_date, print_end_date) VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def dated(n, when, pages=5):
    return (n, "Print", "ok.pdf", "No Error", pages, when, when)


def undated(n, pages=5, result="No Error", print_end=None, name="ok.pdf"):
    return (n, "Print", name, result, pages, None, print_end)


# ── What it recovers ──────────────────────────────────────────────────────────

def test_a_recorded_print_end_is_used_and_not_called_an_inference():
    conn = db([dated(1, "2026-03-15 09:00:00"), dated(3, "2026-03-15 10:00:00"),
               undated(2, print_end="2026-03-15 09:30:00")])
    p = repair.plan(conn)
    assert [r[0] for r in p[repair.PRINT_END]] == [2]
    assert p[repair.PRINT_END][0][1] == "2026-03-15 09:30:00"
    assert p[repair.BRACKETED] == []


def test_an_undated_row_is_bracketed_between_its_neighbours():
    conn = db([dated(1, "2026-03-14 10:00:00"), dated(3, "2026-03-14 10:10:00"),
               undated(2, pages=64)])
    p = repair.plan(conn)
    assert [r[0] for r in p[repair.BRACKETED]] == [2]
    assert p[repair.BRACKETED][0][1] == "2026-03-14 10:05:00"   # the midpoint


def test_bracketing_only_needs_the_day_to_agree_not_the_hour():
    conn = db([dated(1, "2026-03-14 08:00:00"), dated(3, "2026-03-14 20:00:00"),
               undated(2)])
    p = repair.plan(conn)
    assert p[repair.BRACKETED][0][1].startswith("2026-03-14")


# ── What it refuses ───────────────────────────────────────────────────────────

def test_neighbours_straddling_midnight_are_left_alone():
    """The true day is one of two and picking is a coin flip."""
    conn = db([dated(1, "2026-03-15 23:58:00"), dated(3, "2026-03-16 00:03:00"),
               undated(2)])
    p = repair.plan(conn)
    assert p[repair.BRACKETED] == []
    assert [r[0] for r in p["ambiguous"]] == [2]


def test_a_row_with_no_neighbour_on_one_side_is_left_alone():
    conn = db([dated(1, "2026-03-14 10:00:00"), undated(999)])
    p = repair.plan(conn)
    assert p[repair.BRACKETED] == []
    assert [r[0] for r in p["unbracketable"]] == [999]


def test_a_shifted_row_is_quarantined_never_dated():
    """Its columns are shifted by a variable amount, so even a correct date
    would sit on numbers that belong to other fields."""
    conn = db([dated(1, "2026-03-14 10:00:00"), dated(3, "2026-03-14 10:10:00"),
               undated(2, result="Trichinosis", name="Scromboid fish poisoning",
                       print_end="2026-03-14 18:22:27")])
    p = repair.plan(conn)
    assert [r[0] for r in p[repair.QUARANTINED]] == [2]
    assert p[repair.PRINT_END] == [] and p[repair.BRACKETED] == []


def test_a_shifted_row_is_quarantined_even_though_it_has_a_print_end():
    """The print_end is readable; the row around it is not. Dating it would
    put trustworthy-looking rows back into the counts with wrong page numbers."""
    conn = db([undated(2, result="lt.pdf", print_end="2026-03-13 18:05:22")])
    p = repair.plan(conn)
    assert [r[0] for r in p[repair.QUARANTINED]] == [2]


# ── Checking print_end rather than trusting it ────────────────────────────────
#
# Most of the surviving print_end values on the real rows sit within minutes of
# 13:00 across a dozen different days — the shape of a scheduled export, not of
# a print finishing. So a recorded time is evidence, not an answer: it is only
# used where the bracket agrees with it.

def test_a_print_end_that_contradicts_the_neighbours_is_left_alone():
    """Two sources, two answers. Neither is worth writing."""
    conn = db([dated(1, "2026-03-14 10:00:00"), dated(3, "2026-03-14 10:10:00"),
               undated(2, print_end="2026-02-27 13:00:11")])
    p = repair.plan(conn)
    assert p[repair.PRINT_END] == [] and p[repair.BRACKETED] == []
    assert [r[0] for r in p["conflict"]] == [2]
    assert "2026-02-27" in p["conflict"][0][3] and "2026-03-14" in p["conflict"][0][3]


def test_a_corroborated_print_end_says_so_on_the_row():
    conn = db([dated(1, "2026-03-14 09:00:00"), dated(3, "2026-03-14 14:00:00"),
               undated(2, print_end="2026-03-14 13:00:11")])
    p = repair.plan(conn)
    assert p[repair.PRINT_END][0][3] == repair.CORROBORATED


def test_a_print_end_inside_a_midnight_straddle_settles_it():
    """Bracketing alone gives up here; the recorded time picks one of the two
    candidate days, which is not a guess."""
    conn = db([dated(1, "2026-03-15 23:58:00"), dated(3, "2026-03-16 00:03:00"),
               undated(2, print_end="2026-03-16 00:01:00")])
    p = repair.plan(conn)
    assert p["ambiguous"] == []
    assert [r[0] for r in p[repair.PRINT_END]] == [2]
    assert p[repair.PRINT_END][0][3] == repair.SETTLES_RANGE


def test_a_print_end_outside_a_midnight_straddle_is_still_a_conflict():
    conn = db([dated(1, "2026-03-15 23:58:00"), dated(3, "2026-03-16 00:03:00"),
               undated(2, print_end="2026-03-11 13:00:04")])
    p = repair.plan(conn)
    assert [r[0] for r in p["conflict"]] == [2]


def test_an_unchecked_print_end_is_used_but_flagged_as_unchecked():
    """No dated neighbour exists, so nothing can corroborate it. It is still
    the only recorded evidence of the day — but the row says it went unchecked
    so a reader is never told more than we know."""
    conn = db([undated(2, print_end="2026-03-14 13:00:11")])
    p = repair.plan(conn)
    assert [r[0] for r in p[repair.PRINT_END]] == [2]
    assert p[repair.PRINT_END][0][3] == repair.UNCORROBORATED


def test_no_print_end_falls_back_to_bracketing_not_to_giving_up():
    conn = db([dated(1, "2026-03-14 10:00:00"), dated(3, "2026-03-14 10:10:00"),
               undated(2, print_end="2026-03-14 13:00:11")])
    p = repair.plan(conn, use_print_end=False)
    assert p[repair.PRINT_END] == []
    assert p[repair.BRACKETED][0][1] == "2026-03-14 10:05:00"


def test_no_print_end_leaves_an_uncheckable_row_alone_rather_than_dating_it():
    conn = db([undated(2, print_end="2026-03-14 13:00:11")])
    p = repair.plan(conn, use_print_end=False)
    assert p[repair.PRINT_END] == []
    assert [r[0] for r in p["unbracketable"]] == [2]


# ── Does print_end_date mean what it looks like it means? ─────────────────────

def test_agreement_is_measured_from_the_rows_that_kept_both():
    conn = db([dated(1, "2026-03-14 10:00:00"), dated(2, "2026-03-15 10:00:00"),
               (3, "Print", "ok.pdf", "No Error", 5, "2026-03-16 23:50:00",
                "2026-03-17 00:10:00")])          # a job that ran past midnight
    assert repair.print_end_agreement(conn) == {"agree": 2, "total": 3}


def test_agreement_declines_to_answer_when_nothing_kept_both():
    """A reassuring 100% computed from zero rows is the lie the check exists
    to prevent."""
    conn = db([(1, "Print", "ok.pdf", "No Error", 5, "2026-03-14 10:00:00", None)])
    assert repair.print_end_agreement(conn) == {"agree": 0, "total": 0}


# ── Applying it ───────────────────────────────────────────────────────────────

def _apply(conn):
    """The write half of main(), against an open connection."""
    p = repair.plan(conn)
    have = {r[1] for r in conn.execute("PRAGMA table_info(konica_jobs)")}
    if "date_source" not in have:
        conn.execute("ALTER TABLE konica_jobs ADD COLUMN date_source TEXT")
    for key in (repair.PRINT_END, repair.BRACKETED):
        conn.executemany(
            "UPDATE konica_jobs SET job_date=?, date_source=? WHERE job_number=?",
            [(d, key, n) for n, d, _, _ in p[key]])
    conn.executemany(
        "UPDATE konica_jobs SET date_source=? WHERE job_number=?",
        [(repair.QUARANTINED, n) for n, _, _, _ in p[repair.QUARANTINED]])
    conn.commit()
    return p


def test_every_inferred_date_is_stamped_so_it_cannot_pass_as_recorded():
    conn = db([dated(1, "2026-03-14 10:00:00"), dated(3, "2026-03-14 10:10:00"),
               undated(2)])
    _apply(conn)
    row = conn.execute("SELECT job_date, date_source FROM konica_jobs "
                       "WHERE job_number=2").fetchone()
    assert row[0] == "2026-03-14 10:05:00"
    assert row[1] == repair.BRACKETED


def test_a_quarantined_row_keeps_its_null_date_so_it_stays_uncounted():
    conn = db([undated(2, result="Trichinosis")])
    _apply(conn)
    row = conn.execute("SELECT job_date, date_source FROM konica_jobs "
                       "WHERE job_number=2").fetchone()
    assert row[0] is None
    assert row[1] == repair.QUARANTINED


def test_running_it_twice_changes_nothing_the_second_time():
    conn = db([dated(1, "2026-03-14 10:00:00"), dated(3, "2026-03-14 10:10:00"),
               undated(2), undated(999)])
    _apply(conn)
    before = conn.execute("SELECT job_number, job_date, date_source FROM konica_jobs"
                          " ORDER BY job_number").fetchall()
    second = _apply(conn)
    assert second[repair.PRINT_END] == [] and second[repair.BRACKETED] == []
    after = conn.execute("SELECT job_number, job_date, date_source FROM konica_jobs"
                         " ORDER BY job_number").fetchall()
    assert before == after


def test_healthy_rows_are_never_touched():
    conn = db([dated(1, "2026-03-14 10:00:00"), dated(2, "2026-03-14 10:05:00"),
               dated(3, "2026-03-14 10:10:00")])
    before = conn.execute("SELECT * FROM konica_jobs ORDER BY job_number").fetchall()
    _apply(conn)
    after = conn.execute(
        "SELECT job_number, job_type, file_name, result, pages_printed,"
        " job_date, print_end_date FROM konica_jobs ORDER BY job_number").fetchall()
    assert before == after


def test_the_dry_run_is_what_apply_does():
    """A dry run that does not match the write is worse than no dry run."""
    rows = [dated(1, "2026-03-14 10:00:00"), dated(3, "2026-03-14 10:10:00"),
            undated(2), undated(4, result="lt.pdf"), undated(999)]
    predicted = repair.plan(db(rows))
    conn = db(rows)
    _apply(conn)
    for key in (repair.PRINT_END, repair.BRACKETED):
        for number, new_date, _, _ in predicted[key]:
            stored = conn.execute("SELECT job_date FROM konica_jobs WHERE job_number=?",
                                  (number,)).fetchone()[0]
            assert stored == new_date, f"job {number} was written differently"
