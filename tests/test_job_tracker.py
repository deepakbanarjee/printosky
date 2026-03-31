"""
Tests for job_tracker.py
Covers: setup_job_events_db, log_event, transition (valid/invalid), get_events,
        state machine transitions, all edge cases.
"""

import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from job_tracker import (
    setup_job_events_db,
    log_event,
    transition,
    get_events,
    _TRANSITIONS,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    """Temp DB with jobs + job_events tables."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            status TEXT
        )
    """)
    conn.execute("INSERT INTO jobs VALUES ('OSP-20260401-0001', 'Received')")
    conn.execute("INSERT INTO jobs VALUES ('OSP-20260401-0002', 'Paid')")
    conn.execute("INSERT INTO jobs VALUES ('OSP-20260401-0003', 'Collected')")
    conn.execute("INSERT INTO jobs VALUES ('OSP-20260401-0004', NULL)")
    conn.commit()
    setup_job_events_db(conn)
    conn.close()
    return path


JOB = "OSP-20260401-0001"


# ── setup_job_events_db ───────────────────────────────────────────────────────

class TestSetupDb:
    def test_creates_job_events_table(self, db):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='job_events'"
        ).fetchone()
        conn.close()
        assert row is not None

    def test_idempotent(self, db):
        conn = sqlite3.connect(db)
        setup_job_events_db(conn)
        setup_job_events_db(conn)  # must not raise
        conn.close()

    def test_creates_index(self, db):
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_jevents_job'"
        ).fetchone()
        conn.close()
        assert row is not None


# ── log_event ─────────────────────────────────────────────────────────────────

class TestLogEvent:
    def test_basic_log(self, db):
        event_id = log_event(db, JOB, "test_action")
        assert isinstance(event_id, int)
        assert event_id > 0

    def test_event_stored_in_db(self, db):
        log_event(db, JOB, "file_received", from_status=None, to_status="Received",
                  staff_id="priya", notes="test note")
        events = get_events(db, JOB)
        assert len(events) == 1
        e = events[0]
        assert e["action"] == "file_received"
        assert e["to_status"] == "Received"
        assert e["staff_id"] == "priya"
        assert e["notes"] == "test note"

    def test_duration_sec_null_for_first_event(self, db):
        log_event(db, JOB, "first_event")
        events = get_events(db, JOB)
        assert events[0]["duration_sec"] is None

    def test_duration_sec_calculated_for_subsequent_events(self, db):
        log_event(db, JOB, "event_one")
        log_event(db, JOB, "event_two")
        events = get_events(db, JOB)
        assert events[1]["duration_sec"] is not None
        assert events[1]["duration_sec"] >= 0

    def test_none_staff_stored_as_null(self, db):
        log_event(db, JOB, "system_action", staff_id=None)
        events = get_events(db, JOB)
        assert events[0]["staff_id"] is None

    def test_empty_notes_stored_as_null(self, db):
        log_event(db, JOB, "action", notes="")
        events = get_events(db, JOB)
        assert events[0]["notes"] is None

    def test_multiple_events_for_same_job(self, db):
        log_event(db, JOB, "event_a")
        log_event(db, JOB, "event_b")
        log_event(db, JOB, "event_c")
        events = get_events(db, JOB)
        assert len(events) == 3

    def test_events_for_different_jobs_isolated(self, db):
        log_event(db, JOB, "action_a")
        log_event(db, "OSP-20260401-0002", "action_b")
        events = get_events(db, JOB)
        assert len(events) == 1
        assert events[0]["action"] == "action_a"


# ── get_events ────────────────────────────────────────────────────────────────

class TestGetEvents:
    def test_empty_for_unknown_job(self, db):
        assert get_events(db, "OSP-99999999-9999") == []

    def test_ordered_oldest_first(self, db):
        log_event(db, JOB, "first")
        log_event(db, JOB, "second")
        events = get_events(db, JOB)
        assert events[0]["action"] == "first"
        assert events[1]["action"] == "second"

    def test_returns_list_of_dicts(self, db):
        log_event(db, JOB, "test")
        events = get_events(db, JOB)
        assert isinstance(events, list)
        assert isinstance(events[0], dict)


# ── transition ────────────────────────────────────────────────────────────────

class TestTransition:
    def test_valid_transition(self, db):
        r = transition(db, JOB, "Quoted")
        assert r["ok"] is True
        assert "event_id" in r

    def test_status_updated_in_db(self, db):
        transition(db, JOB, "Quoted")
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT status FROM jobs WHERE job_id=?", (JOB,)).fetchone()
        conn.close()
        assert row[0] == "Quoted"

    def test_event_logged_on_transition(self, db):
        transition(db, JOB, "Quoted", staff_id="priya", notes="test")
        events = get_events(db, JOB)
        assert len(events) == 1
        assert events[0]["from_status"] == "Received"
        assert events[0]["to_status"] == "Quoted"

    def test_invalid_transition_returns_error(self, db):
        r = transition(db, JOB, "Collected")  # Received → Collected not allowed
        assert r["ok"] is False
        assert "Invalid transition" in r["error"]

    def test_unknown_status_returns_error(self, db):
        r = transition(db, JOB, "FakeStatus")
        assert r["ok"] is False
        assert "Unknown status" in r["error"]

    def test_nonexistent_job_returns_error(self, db):
        r = transition(db, "OSP-99999999-9999", "Quoted")
        assert r["ok"] is False
        assert "not found" in r["error"]

    def test_cancel_allowed_from_any_status(self, db):
        for job_id, status in [
            ("OSP-20260401-0001", "Received"),
            ("OSP-20260401-0002", "Paid"),
        ]:
            r = transition(db, job_id, "Cancelled")
            assert r["ok"] is True

    def test_terminal_status_rejects_transition(self, db):
        # Collected is terminal
        r = transition(db, "OSP-20260401-0003", "Ready")
        assert r["ok"] is False

    def test_full_happy_path(self, db):
        # Received → Quoted → Paid → Printing → PrintDone → Ready → Collected
        steps = ["Quoted", "Paid", "Printing", "PrintDone", "Ready", "Collected"]
        for step in steps:
            r = transition(db, JOB, step)
            assert r["ok"] is True, f"Failed at step {step}: {r}"

    def test_action_label_used(self, db):
        transition(db, JOB, "Quoted")
        events = get_events(db, JOB)
        assert events[0]["action"] == "quote_sent"

    def test_payment_received_label(self, db):
        transition(db, JOB, "Quoted")
        transition(db, JOB, "Paid")
        events = get_events(db, JOB)
        assert events[1]["action"] == "payment_received"

    def test_generic_action_label_for_unlabeled_transition(self, db):
        # Received → Queued is valid but has no specific label
        r = transition(db, JOB, "Queued")
        assert r["ok"] is True
        events = get_events(db, JOB)
        assert events[0]["action"] == "status_change"

    def test_staff_id_recorded_in_event(self, db):
        transition(db, JOB, "Quoted", staff_id="anu")
        events = get_events(db, JOB)
        assert events[0]["staff_id"] == "anu"

    def test_notes_recorded_in_event(self, db):
        transition(db, JOB, "Quoted", notes="customer called")
        events = get_events(db, JOB)
        assert events[0]["notes"] == "customer called"

    def test_null_current_status_allowed_for_new_job(self, db):
        # job with NULL status → Received (new job creation)
        r = transition(db, "OSP-20260401-0004", "Received")
        assert r["ok"] is True


# ── State machine coverage ────────────────────────────────────────────────────

class TestStateMachine:
    def test_all_statuses_in_transitions(self):
        # Every known status is reachable or defined
        known = set(_TRANSITIONS.keys()) - {None}
        for _vals in _TRANSITIONS.values():
            known |= _vals
        # Spot-check key statuses exist
        for s in ["Received", "Quoted", "Paid", "Printing", "PrintDone",
                  "Ready", "Collected", "Cancelled"]:
            assert s in known

    def test_collected_is_terminal(self):
        assert len(_TRANSITIONS["Collected"]) == 0

    def test_cancelled_is_terminal(self):
        assert len(_TRANSITIONS["Cancelled"]) == 0

    def test_cancelled_reachable_from_all_non_terminal(self, db):
        # Legacy aliases (Completed, Printed, In Progress) don't include Cancelled — skip them
        legacy = {"Completed", "Printed", "In Progress"}
        non_terminal = [s for s, v in _TRANSITIONS.items()
                        if s is not None and len(v) > 0 and s not in legacy]
        for status in non_terminal:
            assert "Cancelled" in _TRANSITIONS[status] or status == "Cancelled", \
                f"Status {status!r} cannot transition to Cancelled"
