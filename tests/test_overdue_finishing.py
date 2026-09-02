"""
B-8 — a job at another store, not back yet.

A job at a finishing store is invisible: not in this shop's queue, not on this
shop's printer, and the customer is still expecting it. Nattika went dark for a
week in August 2026 because locally reasonable silences added up, and a job
sitting at the other shop is exactly that shape.

The section is deliberately silent when there is nothing late. A daily "0 jobs
overdue" line is the kind of green tick people stop reading; this one earns
attention by being rare.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from store_digest import (FINISHING_OVERDUE_HOURS, _as_datetime,
                          compose_closing_message, format_overdue_finishing,
                          overdue_finishing)

NOW = datetime(2026, 9, 1, 18, 0, 0)


def _row(job_id="OSP-1", hours_ago=72, status="at_finisher", store="PRINTK"):
    return {"job_id": job_id, "finishing_store_id": store,
            "finishing_status": status,
            "finishing_sent_at": (NOW - timedelta(hours=hours_ago))
                                 .strftime("%Y-%m-%d %H:%M:%S")}


def test_a_job_out_too_long_is_listed():
    late = overdue_finishing([_row(hours_ago=72)], now=NOW)
    assert len(late) == 1
    assert late[0]["job_id"] == "OSP-1"
    assert late[0]["store"] == "PRINTK"
    assert late[0]["hours"] == 72


def test_a_job_inside_the_window_is_not():
    assert overdue_finishing([_row(hours_ago=47)], now=NOW) == []


def test_the_boundary_is_inclusive():
    assert len(overdue_finishing([_row(hours_ago=FINISHING_OVERDUE_HOURS)], now=NOW)) == 1


def test_a_returned_job_is_not_overdue():
    """It came back. That is the whole point of the status."""
    assert overdue_finishing([_row(hours_ago=200, status="returned")], now=NOW) == []


def test_a_job_never_sent_is_not_overdue():
    assert overdue_finishing([{"job_id": "OSP-2", "finishing_status": None}], now=NOW) == []


def test_a_row_with_no_usable_timestamp_is_skipped_not_guessed():
    """A wrong age is worse than no line."""
    rows = [{"job_id": "OSP-3", "finishing_status": "sent",
             "finishing_sent_at": "not a date"}]
    assert overdue_finishing(rows, now=NOW) == []


def test_received_at_is_never_used_as_the_send_time():
    """Rewritten 2026-09-02, and the reason is the bug it used to enshrine.

    This asserted that a row with no `finishing_sent_at` was aged from
    `received_at` instead. Those measure different intervals: `received_at` is
    when the customer handed the job over, `finishing_sent_at` is when it left
    for the finisher. Nothing ever wrote the latter — it was read by
    overdue_finishing and written by no code path at all — so the fallback was
    the ONLY branch that ever ran, and it reported the wrong number every time.

    A job taken in a month ago and sent to Nattika an hour ago is not 720 hours
    late. The module's own docstring already said a wrong age is worse than no
    age; now the code agrees with it.
    """
    a_month_ago = (NOW - timedelta(hours=720)).strftime("%Y-%m-%d %H:%M:%S")
    an_hour_ago = (NOW - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    rows = [{"job_id": "OSP-4", "finishing_status": "sent",
             "finishing_store_id": "PRINTK",
             "received_at": a_month_ago,
             "finishing_sent_at": an_hour_ago}]
    assert overdue_finishing(rows, now=NOW) == []


def test_a_row_with_no_send_time_is_not_aged_at_all():
    rows = [{"job_id": "OSP-4", "finishing_status": "sent",
             "finishing_store_id": "PRINTK",
             "received_at": (NOW - timedelta(hours=96)).strftime("%Y-%m-%d %H:%M:%S")}]
    assert overdue_finishing(rows, now=NOW) == []


def test_but_it_is_still_reported_rather_than_dropped():
    """Only a store PC that has not restarted since SCHEMA_v40 makes these.
    Silently discarding them would hide a job sitting at another shop, which is
    the exact failure the section exists for."""
    from store_digest import UNDATEABLE, undateable_finishing
    rows = [{"job_id": "OSP-4", "finishing_status": "sent",
             "finishing_store_id": "PRINTK",
             "received_at": (NOW - timedelta(hours=96)).strftime("%Y-%m-%d %H:%M:%S")}]
    out = undateable_finishing(rows)
    assert [r["job_id"] for r in out] == ["OSP-4"]
    assert out[0]["store"] == "PRINTK" and out[0]["hours"] == UNDATEABLE

    text = format_overdue_finishing(rows, now=NOW)
    assert "no send time" in text and "OSP-4" in text


def test_a_returned_job_with_no_send_time_says_nothing():
    """It is home. Undateable is about jobs still out."""
    rows = [{"job_id": "OSP-5", "finishing_status": "returned",
             "finishing_store_id": "PRINTK"}]
    from store_digest import undateable_finishing
    assert undateable_finishing(rows) == []
    assert format_overdue_finishing(rows, now=NOW) == ""


def test_the_longest_wait_is_listed_first():
    rows = [_row("OSP-A", 50), _row("OSP-C", 200), _row("OSP-B", 100)]
    assert [r["job_id"] for r in overdue_finishing(rows, now=NOW)] == \
        ["OSP-C", "OSP-B", "OSP-A"]


# ── The digest section ────────────────────────────────────────────────────────

def test_nothing_late_means_no_section_at_all():
    assert format_overdue_finishing([], now=NOW) == ""
    assert format_overdue_finishing([_row(hours_ago=2)], now=NOW) == ""


def test_the_section_names_the_job_the_store_and_the_wait():
    text = format_overdue_finishing([_row("OSP-9", 72)], now=NOW)
    assert "OSP-9" in text and "PRINTK" in text and "72h" in text
    assert "1 job at a finishing store" in text


def test_the_headline_counts_correctly():
    text = format_overdue_finishing([_row("OSP-1", 72), _row("OSP-2", 96)], now=NOW)
    assert "2 jobs at a finishing store" in text


# ── Wiring into the closing message ───────────────────────────────────────────

def test_the_closing_message_is_unchanged_when_nothing_is_late():
    daily = {"date": "2026-09-01", "total_jobs": 3, "completed": 3, "pending": 0,
             "revenue": 300, "cash": 300, "upi": 0}
    without = compose_closing_message("2026-09-01", daily)
    with_none = compose_closing_message("2026-09-01", daily, finishing_rows=[])
    assert without == with_none


def test_the_closing_message_carries_the_warning_when_something_is_late():
    daily = {"date": "2026-09-01", "total_jobs": 3, "completed": 3, "pending": 0,
             "revenue": 300, "cash": 300, "upi": 0}
    msg = compose_closing_message("2026-09-01", daily,
                                  finishing_rows=[_row("OSP-7", 72)])
    assert "OSP-7" in msg
    assert "finishing store" in msg


# ── The timestamp parser ──────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("2026-09-01 10:30:00", datetime(2026, 9, 1, 10, 30)),
    ("2026-09-01T10:30:00", datetime(2026, 9, 1, 10, 30)),
    ("2026-09-01T10:30:00Z", datetime(2026, 9, 1, 10, 30)),
    ("2026-09-01T10:30:00.123456+05:30", datetime(2026, 9, 1, 10, 30)),
    ("2026-09-01", datetime(2026, 9, 1, 0, 0)),
])
def test_the_timestamp_shapes_this_repo_actually_stores(value, expected):
    assert _as_datetime(value) == expected


@pytest.mark.parametrize("value", [None, "", "yesterday", 0, []])
def test_anything_else_is_none_rather_than_a_guess(value):
    assert _as_datetime(value) is None
