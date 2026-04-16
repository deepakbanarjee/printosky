"""
Tests for work_session_tracker.py
Covers: start/pause/resume/end session, billing math, paused_sec accumulation,
        get_sessions, get_open_session, edge cases.
"""

import sys
import os
import sqlite3
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from work_session_tracker import (
    setup_work_sessions_db,
    start_session,
    pause_session,
    resume_session,
    end_session,
    get_sessions,
    get_open_session,
    _ceil_to_slot,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """In-memory-style temp DB with required jobs table."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            editing_minutes INTEGER DEFAULT 0,
            dtp_pages INTEGER DEFAULT 0,
            graph_count INTEGER DEFAULT 0
        )
    """)
    conn.execute("INSERT INTO jobs VALUES ('OSP-20260401-0001', 0, 0, 0)")
    conn.execute("INSERT INTO jobs VALUES ('OSP-20260401-0002', 0, 0, 0)")
    conn.commit()
    conn.close()
    return path


JOB = "OSP-20260401-0001"
STAFF = "priya"


# ── _ceil_to_slot ─────────────────────────────────────────────────────────────

class TestCeilToSlot:
    def test_zero_seconds_returns_one_slot(self):
        assert _ceil_to_slot(0) == 15

    def test_one_second_returns_one_slot(self):
        assert _ceil_to_slot(1) == 15

    def test_exactly_15_min(self):
        assert _ceil_to_slot(15 * 60) == 15

    def test_16_min_rounds_to_30(self):
        assert _ceil_to_slot(16 * 60) == 30

    def test_30_min_exactly(self):
        assert _ceil_to_slot(30 * 60) == 30

    def test_31_min_rounds_to_45(self):
        assert _ceil_to_slot(31 * 60) == 45

    def test_60_min(self):
        assert _ceil_to_slot(60 * 60) == 60

    def test_large_value(self):
        assert _ceil_to_slot(120 * 60) == 120


# ── setup_work_sessions_db ────────────────────────────────────────────────────

class TestSetupDb:
    def test_creates_table(self, db):
        conn = sqlite3.connect(db)
        setup_work_sessions_db(conn)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='work_sessions'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_idempotent(self, db):
        conn = sqlite3.connect(db)
        setup_work_sessions_db(conn)
        setup_work_sessions_db(conn)  # must not raise
        conn.close()

    def test_paused_sec_column_exists(self, db):
        conn = sqlite3.connect(db)
        setup_work_sessions_db(conn)
        conn.execute("INSERT INTO work_sessions (job_id, staff_id, started_at) VALUES ('x','y','2026-01-01 00:00:00')")
        conn.commit()
        row = conn.execute("SELECT paused_sec FROM work_sessions LIMIT 1").fetchone()
        conn.close()
        assert row[0] == 0


# ── start_session ─────────────────────────────────────────────────────────────

class TestStartSession:
    def test_basic_start(self, db):
        r = start_session(db, JOB, STAFF)
        assert r["ok"] is True
        assert "session_id" in r
        assert r["job_id"] == JOB
        assert r["staff_id"] == STAFF

    def test_session_id_is_integer(self, db):
        r = start_session(db, JOB, STAFF)
        assert isinstance(r["session_id"], int)

    def test_rejects_empty_job_id(self, db):
        r = start_session(db, "", STAFF)
        assert r["ok"] is False
        assert "job_id" in r["error"]

    def test_rejects_empty_staff_id(self, db):
        r = start_session(db, JOB, "")
        assert r["ok"] is False

    def test_rejects_duplicate_open_session(self, db):
        start_session(db, JOB, STAFF)
        r = start_session(db, JOB, "other_staff")
        assert r["ok"] is False
        assert "already open" in r["error"]

    def test_allows_second_session_after_first_ended(self, db):
        r1 = start_session(db, JOB, STAFF)
        end_session(db, r1["session_id"])
        r2 = start_session(db, JOB, STAFF)
        assert r2["ok"] is True

    def test_different_jobs_can_have_concurrent_sessions(self, db):
        r1 = start_session(db, JOB, STAFF)
        r2 = start_session(db, "OSP-20260401-0002", STAFF)
        assert r1["ok"] is True
        assert r2["ok"] is True

    def test_session_stored_in_db(self, db):
        r = start_session(db, JOB, STAFF)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM work_sessions WHERE id=?", (r["session_id"],)).fetchone()
        conn.close()
        assert row["job_id"] == JOB
        assert row["staff_id"] == STAFF
        assert row["ended_at"] is None
        assert row["paused_at"] is None


# ── pause_session ─────────────────────────────────────────────────────────────

class TestPauseSession:
    def test_basic_pause(self, db):
        r = start_session(db, JOB, STAFF)
        p = pause_session(db, r["session_id"])
        assert p["ok"] is True

    def test_paused_at_recorded(self, db):
        r = start_session(db, JOB, STAFF)
        pause_session(db, r["session_id"])
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT paused_at FROM work_sessions WHERE id=?", (r["session_id"],)).fetchone()
        conn.close()
        assert row["paused_at"] is not None

    def test_rejects_nonexistent_session(self, db):
        r = pause_session(db, 9999)
        assert r["ok"] is False
        assert "not found" in r["error"]

    def test_rejects_already_paused(self, db):
        r = start_session(db, JOB, STAFF)
        pause_session(db, r["session_id"])
        r2 = pause_session(db, r["session_id"])
        assert r2["ok"] is False
        assert "already paused" in r2["error"]

    def test_rejects_ended_session(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"])
        r2 = pause_session(db, r["session_id"])
        assert r2["ok"] is False
        assert "ended" in r2["error"]


# ── resume_session ────────────────────────────────────────────────────────────

class TestResumeSession:
    def test_basic_resume(self, db):
        r = start_session(db, JOB, STAFF)
        pause_session(db, r["session_id"])
        res = resume_session(db, r["session_id"])
        assert res["ok"] is True

    def test_paused_at_cleared_after_resume(self, db):
        r = start_session(db, JOB, STAFF)
        pause_session(db, r["session_id"])
        resume_session(db, r["session_id"])
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT paused_at, resumed_at FROM work_sessions WHERE id=?", (r["session_id"],)).fetchone()
        conn.close()
        assert row["paused_at"] is None
        assert row["resumed_at"] is not None

    def test_paused_sec_accumulated_on_resume(self, db):
        r = start_session(db, JOB, STAFF)
        pause_session(db, r["session_id"])
        time.sleep(1)
        resume_session(db, r["session_id"])
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT paused_sec FROM work_sessions WHERE id=?", (r["session_id"],)).fetchone()
        conn.close()
        assert row["paused_sec"] >= 1

    def test_rejects_resume_of_running_session(self, db):
        r = start_session(db, JOB, STAFF)
        res = resume_session(db, r["session_id"])
        assert res["ok"] is False
        assert "not paused" in res["error"]

    def test_rejects_nonexistent_session(self, db):
        r = resume_session(db, 9999)
        assert r["ok"] is False

    def test_rejects_ended_session(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"])
        r2 = resume_session(db, r["session_id"])
        assert r2["ok"] is False

    def test_multiple_pause_resume_accumulates(self, db):
        r = start_session(db, JOB, STAFF)
        sid = r["session_id"]
        # First pause/resume
        pause_session(db, sid)
        time.sleep(1)
        resume_session(db, sid)
        # Second pause/resume
        pause_session(db, sid)
        time.sleep(1)
        resume_session(db, sid)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT paused_sec FROM work_sessions WHERE id=?", (sid,)).fetchone()
        conn.close()
        assert row["paused_sec"] >= 2


# ── end_session ───────────────────────────────────────────────────────────────

class TestEndSession:
    def test_basic_end(self, db):
        r = start_session(db, JOB, STAFF)
        result = end_session(db, r["session_id"])
        assert result["ok"] is True
        assert "total_sec" in result
        assert "billing_minutes" in result
        assert "billing_hours" in result

    def test_billing_minimum_15_min(self, db):
        r = start_session(db, JOB, STAFF)
        result = end_session(db, r["session_id"])
        assert result["billing_minutes"] == 15

    def test_billing_ceiled_to_slot(self, db):
        r = start_session(db, JOB, STAFF)
        # Patch started_at to 16 minutes ago
        conn = sqlite3.connect(db)
        from datetime import datetime, timedelta
        past = (datetime.now() - timedelta(minutes=16)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE work_sessions SET started_at=? WHERE id=?", (past, r["session_id"]))
        conn.commit()
        conn.close()
        result = end_session(db, r["session_id"])
        assert result["billing_minutes"] == 30

    def test_pause_time_subtracted_from_billing(self, db):
        r = start_session(db, JOB, STAFF)
        # Backdate start by 2 minutes
        from datetime import datetime, timedelta
        conn = sqlite3.connect(db)
        past = (datetime.now() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE work_sessions SET started_at=?, paused_sec=90 WHERE id=?", (past, r["session_id"]))
        conn.commit()
        conn.close()
        result = end_session(db, r["session_id"])
        # 2 min elapsed - 90s paused = 30s real work → billed 15 min
        assert result["total_sec"] <= 45
        assert result["billing_minutes"] == 15

    def test_rejects_already_ended_session(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"])
        r2 = end_session(db, r["session_id"])
        assert r2["ok"] is False
        assert "ended" in r2["error"]

    def test_rejects_nonexistent_session(self, db):
        r = end_session(db, 9999)
        assert r["ok"] is False

    def test_dtp_pages_written_to_job(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"], dtp_pages=7)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT dtp_pages FROM jobs WHERE job_id=?", (JOB,)).fetchone()
        conn.close()
        assert row["dtp_pages"] == 7

    def test_graph_count_written_to_job(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"], graph_count=3)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT graph_count FROM jobs WHERE job_id=?", (JOB,)).fetchone()
        conn.close()
        assert row["graph_count"] == 3

    def test_editing_minutes_written_to_job(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"])
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT editing_minutes FROM jobs WHERE job_id=?", (JOB,)).fetchone()
        conn.close()
        assert row["editing_minutes"] == 15

    def test_editing_minutes_accumulate_across_sessions(self, db):
        r1 = start_session(db, JOB, STAFF)
        end_session(db, r1["session_id"])
        r2 = start_session(db, JOB, STAFF)
        end_session(db, r2["session_id"])
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT editing_minutes FROM jobs WHERE job_id=?", (JOB,)).fetchone()
        conn.close()
        assert row["editing_minutes"] == 30

    def test_notes_stored(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"], notes="Test notes")
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT notes FROM work_sessions WHERE id=?", (r["session_id"],)).fetchone()
        conn.close()
        assert row["notes"] == "Test notes"

    def test_can_end_paused_session(self, db):
        r = start_session(db, JOB, STAFF)
        pause_session(db, r["session_id"])
        result = end_session(db, r["session_id"])
        assert result["ok"] is True

    def test_billing_hours_matches_minutes(self, db):
        r = start_session(db, JOB, STAFF)
        result = end_session(db, r["session_id"])
        assert result["billing_hours"] == round(result["billing_minutes"] / 60, 2)

    def test_negative_dtp_pages_clamped_to_zero(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"], dtp_pages=-5)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT dtp_pages FROM jobs WHERE job_id=?", (JOB,)).fetchone()
        conn.close()
        assert row["dtp_pages"] == 0


# ── get_sessions ──────────────────────────────────────────────────────────────

class TestGetSessions:
    def test_empty_for_unknown_job(self, db):
        assert get_sessions(db, "OSP-99999999-9999") == []

    def test_returns_all_sessions(self, db):
        r1 = start_session(db, JOB, STAFF)
        end_session(db, r1["session_id"])
        r2 = start_session(db, JOB, STAFF)
        end_session(db, r2["session_id"])
        sessions = get_sessions(db, JOB)
        assert len(sessions) == 2

    def test_ordered_oldest_first(self, db):
        r1 = start_session(db, JOB, STAFF)
        end_session(db, r1["session_id"])
        r2 = start_session(db, JOB, STAFF)
        sessions = get_sessions(db, JOB)
        assert sessions[0]["id"] < sessions[1]["id"]

    def test_returns_dicts(self, db):
        start_session(db, JOB, STAFF)
        sessions = get_sessions(db, JOB)
        assert isinstance(sessions[0], dict)
        assert "job_id" in sessions[0]
        assert "staff_id" in sessions[0]


# ── get_open_session ──────────────────────────────────────────────────────────

class TestGetOpenSession:
    def test_none_when_no_sessions(self, db):
        assert get_open_session(db, JOB) is None

    def test_returns_open_session(self, db):
        r = start_session(db, JOB, STAFF)
        open_s = get_open_session(db, JOB)
        assert open_s is not None
        assert open_s["id"] == r["session_id"]

    def test_none_after_session_ended(self, db):
        r = start_session(db, JOB, STAFF)
        end_session(db, r["session_id"])
        assert get_open_session(db, JOB) is None

    def test_returns_latest_if_multiple_somehow(self, db):
        # Directly insert two open rows (bypassing the guard)
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE IF NOT EXISTS work_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, staff_id TEXT, started_at TEXT, paused_at TEXT, resumed_at TEXT, ended_at TEXT, total_sec INTEGER, paused_sec INTEGER DEFAULT 0, notes TEXT, created_at TEXT)")
        conn.execute("INSERT INTO work_sessions (job_id, staff_id, started_at) VALUES (?,?,?)", (JOB, STAFF, "2026-01-01 00:00:00"))
        conn.execute("INSERT INTO work_sessions (job_id, staff_id, started_at) VALUES (?,?,?)", (JOB, STAFF, "2026-01-01 01:00:00"))
        conn.commit()
        conn.close()
        open_s = get_open_session(db, JOB)
        assert open_s is not None
        # Should return latest (highest id)
        assert open_s["started_at"] == "2026-01-01 01:00:00"
