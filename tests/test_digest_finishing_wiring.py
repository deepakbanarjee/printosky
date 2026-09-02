"""
The over-48h finishing line, and the three reasons it never fired.

B-8 shipped a digest section for jobs sitting too long at a finishing store. It
has been dead since the day it landed, in three independent ways, none of which
raised anything — because the section is *silent on a normal day by design*,
which is exactly why nobody noticed a section that was silent on every day:

  1. `supabase_sync.collect_jobs()` never selected the finishing columns, so a
     job sent to Nattika for binding was invisible in the cloud.
  2. `finishing_sent_at` was read by `overdue_finishing` and written by NOTHING.
     The age fell back to `received_at` — a different interval — so even with
     rows present the number would have been wrong.
  3. The cron called `compose_closing_message()` without `finishing_rows`.

Each one is pinned here, because each was individually invisible.
"""

import ast
import os
import re
import sys
from datetime import datetime, timedelta

import pytest

REPO = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, REPO)

import store_digest as sd
from db_migrations import SERVICE_JOB_COLUMNS


def _src(rel):
    return open(os.path.join(REPO, rel), encoding="utf-8-sig").read()


def _fn(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} not found")


# ── 1. The columns reach the cloud ────────────────────────────────────────────

def test_collect_jobs_selects_the_finishing_columns():
    """Without this the cloud never learns a transfer happened at all."""
    src = _src("supabase_sync.py")
    body = _fn(src, "collect_jobs")
    assert "_present_columns(c, _FINISHING_COLUMNS)" in body
    assert 'extra += ", " + ", ".join(finishing)' in body


def test_the_synced_set_covers_every_finishing_column_sqlite_has():
    import supabase_sync
    local = {c for c, _ in SERVICE_JOB_COLUMNS}
    synced = set(supabase_sync._FINISHING_COLUMNS) | {"service_kind", "service_meta"}
    assert local <= synced, f"written locally, never pushed: {local - synced}"


def test_columns_are_selected_defensively_not_assumed():
    """A store PC that has not restarted since the migration does not have them,
    and a SELECT naming a missing column takes the whole sync down."""
    src = _src("supabase_sync.py")
    fn = _fn(src, "_present_columns")
    assert "PRAGMA table_info(jobs)" in fn
    assert "if c in have" in fn


def test_a_pc_missing_the_columns_selects_none_of_them(tmp_path):
    import sqlite3
    import supabase_sync
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE jobs (job_id TEXT, received_at TEXT)")
    assert supabase_sync._present_columns(conn.cursor(),
                                          supabase_sync._FINISHING_COLUMNS) == ()


def test_a_pc_with_some_columns_selects_exactly_those():
    import sqlite3
    import supabase_sync
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE jobs (job_id TEXT, finishing_status TEXT, "
                 "finishing_sent_at TEXT)")
    got = supabase_sync._present_columns(conn.cursor(),
                                         supabase_sync._FINISHING_COLUMNS)
    assert got == ("finishing_status", "finishing_sent_at")


# ── 2. Something writes the send time ─────────────────────────────────────────

def test_finishing_send_records_when_the_job_left():
    src = _src("print_server.py")
    body = _fn(src, "handle_finishing_send")
    assert "finishing_sent_at=?" in body


def test_the_column_exists_locally_and_self_applies():
    names = [c for c, _ in SERVICE_JOB_COLUMNS]
    assert "finishing_sent_at" in names
    src = _src("print_server.py")
    assert "ensure_job_service_columns(conn)" in _fn(src, "handle_finishing_send")


def test_the_send_time_is_the_send_time_not_the_intake_time():
    """The bug this whole file is about, as behaviour rather than as source."""
    now = datetime(2026, 9, 2, 10, 0, 0)
    row = {"job_id": "OSP-1", "finishing_status": "sent",
           "finishing_store_id": "PRINTK",
           "received_at": (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S"),
           "finishing_sent_at": (now - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")}
    assert sd.overdue_finishing([row], now=now) == []


# ── 3. The cron actually passes them ──────────────────────────────────────────

def test_the_closing_message_is_given_the_finishing_rows():
    """The line that made the whole section dead code."""
    src = _src("api/index.py")
    call = re.search(r"sd\.compose_closing_message\((.*?)\)\n", src, re.S)
    assert call, "compose_closing_message is not called"
    assert "finishing_rows=" in call.group(1)


def test_the_cron_fetches_only_jobs_still_out():
    src = _src("api/index.py")
    fn = _fn(src, "_sd_open_finishing")
    assert '"sent", "at_finisher"' in fn
    assert "returned" not in fn.replace("# ", "").split('"""')[2]


def test_the_fetch_is_scoped_to_the_sending_store():
    """The shop still holding the customer's promise is the one that needs the
    reminder — not the shop doing the binding, which has its own console queue."""
    fn = _fn(_src("api/index.py"), "_sd_open_finishing")
    assert '.eq("store_id", store_id)' in fn
    assert '.eq("finishing_store_id"' not in fn


def test_a_broken_lookup_does_not_take_the_whole_digest_down():
    """A digest that does not send because one query failed is worse than a
    digest missing one section."""
    fn = _fn(_src("api/index.py"), "_sd_open_finishing")
    assert "except Exception" in fn
    assert "return []" in fn
    assert "logger.warning" in fn, "silent is not allowed either"


def test_the_fetch_asks_for_the_fields_the_formatter_reads():
    fn = _fn(_src("api/index.py"), "_sd_open_finishing")
    for field in ("job_id", "finishing_store_id", "finishing_status",
                  "finishing_sent_at"):
        assert field in fn, field


# ── End to end, through the real formatter ────────────────────────────────────

NOW = datetime(2026, 9, 2, 10, 0, 0)


#: A day summary in the shape _sd_summary_row returns.
DAILY = {"date": "2026-09-02", "total_jobs": 3, "completed": 3, "pending": 0,
         "revenue": 500, "cash": 300, "upi": 200}


def _sent(job_id, hours_ago, store="PRINTK", status="sent"):
    """A row sent `hours_ago` before **real** now.

    compose_closing_message takes no clock — it ages against datetime.now() —
    so these fixtures are relative to the real one rather than to NOW, which
    only the pure functions accept.
    """
    return {"job_id": job_id, "finishing_status": status,
            "finishing_store_id": store,
            "finishing_sent_at": (datetime.now() - timedelta(hours=hours_ago))
            .strftime("%Y-%m-%d %H:%M:%S")}


def test_a_normal_day_says_nothing():
    msg = sd.compose_closing_message(
        NOW.date(), DAILY,
        finishing_rows=[_sent("OSP-1", 4), _sent("OSP-2", 47)])
    assert "finishing store" not in msg


def test_a_late_job_reaches_the_closing_message():
    msg = sd.compose_closing_message(
        NOW.date(), DAILY,
        finishing_rows=[_sent("OSP-9", 73.5)])
    assert "1 job at a finishing store over 48h" in msg
    assert "OSP-9 — PRINTK, sent, 73h" in msg, msg


def test_no_finishing_rows_at_all_changes_nothing():
    """Every caller that does not pass them must behave as it always did."""
    base = sd.compose_closing_message(NOW.date(), DAILY)
    with_empty = sd.compose_closing_message(NOW.date(), DAILY, finishing_rows=[])
    assert base == with_empty


def test_the_console_and_the_digest_use_one_threshold():
    """Already pinned for jobs.html; restated here because this is now the file
    that explains what the number means."""
    assert sd.FINISHING_OVERDUE_HOURS == 48
    for console in ("jobs.html", "admin.html"):
        src = open(os.path.join(REPO, "website", console), encoding="utf-8").read()
        assert f"const FINISHING_OVERDUE_HOURS = {sd.FINISHING_OVERDUE_HOURS};" in src
