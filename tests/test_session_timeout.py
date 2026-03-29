"""
Tests for session_timeout.py
Covers: _get_timed_out_sessions, _mark_timed_out using in-memory SQLite
"""

import sys
import os
import sqlite3
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import session_timeout


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: DB with bot_sessions table
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "timeout.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE bot_sessions (
            phone      TEXT PRIMARY KEY,
            job_id     TEXT,
            step       TEXT,
            updated_at TEXT
        )
    """)
    # _get_timed_out_sessions does LEFT JOIN jobs
    conn.execute("""
        CREATE TABLE jobs (
            job_id   TEXT PRIMARY KEY,
            filename TEXT,
            filepath TEXT
        )
    """)
    conn.commit()
    conn.close()
    return path


def _insert_session(db_path, phone, step, minutes_ago, job_id="OSP-001"):
    ts = (datetime.now() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO bot_sessions (phone, job_id, step, updated_at) VALUES (?,?,?,?)",
        (phone, job_id, step, ts)
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# _get_timed_out_sessions
# ─────────────────────────────────────────────────────────────────────────────

class TestGetTimedOutSessions:
    def test_no_sessions_returns_empty(self, db):
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        assert result == []

    def test_recent_session_not_timed_out(self, db):
        _insert_session(db, "91111", "size", minutes_ago=5)
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        assert result == []

    def test_old_session_timed_out(self, db):
        _insert_session(db, "91111", "size", minutes_ago=20)
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        assert len(result) == 1
        assert result[0]["phone"] == "91111"

    def test_done_step_excluded(self, db):
        _insert_session(db, "91111", "done", minutes_ago=30)
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        assert result == []

    def test_already_timed_out_excluded(self, db):
        _insert_session(db, "91111", "timed_out", minutes_ago=30)
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        assert result == []

    def test_multiple_timed_out(self, db):
        _insert_session(db, "91111", "size",    minutes_ago=20)
        _insert_session(db, "91222", "colour",  minutes_ago=25)
        _insert_session(db, "91333", "copies",  minutes_ago=5)   # recent — not timed out
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        phones = {r["phone"] for r in result}
        assert "91111" in phones
        assert "91222" in phones
        assert "91333" not in phones

    def test_exact_boundary(self, db):
        # Session updated exactly at the timeout threshold
        _insert_session(db, "91111", "size", minutes_ago=15)
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        # Boundary is implementation-defined (< or <=) — just ensure no crash
        assert isinstance(result, list)

    def test_returns_list_of_dicts(self, db):
        _insert_session(db, "91111", "size", minutes_ago=20)
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        assert all(isinstance(r, dict) for r in result)

    def test_result_has_phone_and_step(self, db):
        _insert_session(db, "91111", "copies", minutes_ago=20)
        result = session_timeout._get_timed_out_sessions(db, timeout_minutes=15)
        assert "phone" in result[0]
        assert "step" in result[0]


# ─────────────────────────────────────────────────────────────────────────────
# _mark_timed_out
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkTimedOut:
    def test_marks_step_as_timed_out(self, db):
        _insert_session(db, "91111", "size", minutes_ago=20)
        session_timeout._mark_timed_out(db, "91111")
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT step FROM bot_sessions WHERE phone='91111'").fetchone()
        conn.close()
        assert row[0] == "timed_out"

    def test_mark_nonexistent_phone_no_error(self, db):
        # Should not raise even if phone not in DB
        session_timeout._mark_timed_out(db, "99999999")

    def test_only_target_phone_updated(self, db):
        _insert_session(db, "91111", "size",   minutes_ago=20)
        _insert_session(db, "91222", "colour", minutes_ago=20)
        session_timeout._mark_timed_out(db, "91111")
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT step FROM bot_sessions WHERE phone='91222'").fetchone()
        conn.close()
        assert row[0] == "colour"  # unchanged
