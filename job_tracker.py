"""
job_tracker.py — Status machine + event log for Printosky jobs.

Every status transition and significant action is written to the job_events table,
giving a full audit trail: who did what, when, and how long each stage took.

Status machine (valid transitions):
    Received  → Quoted
    Quoted    → Paid
    Paid      → Printing
    Printing  → PrintDone
    PrintDone → Binding | Ready
    Binding   → Ready
    Ready     → Collected

Any status → Cancelled  (staff override)
Any status → Draft      (internal — partial/pending payment)

Import and use:
    from job_tracker import log_event, transition, setup_job_events_db
"""

import logging
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Valid transitions: from_status → set of allowed to_status values
_TRANSITIONS: dict[str | None, set[str]] = {
    None:          {"Received", "Draft", "Queued"},   # new job creation
    "Draft":       {"Queued", "Received", "Cancelled"},
    "Received":    {"Quoted", "Paid", "Queued", "Cancelled"},
    "Queued":      {"Quoted", "Paid", "Printing", "Cancelled"},
    "Quoted":      {"Paid", "Cancelled"},
    "Paid":        {"Printing", "Cancelled"},
    "Printing":    {"PrintDone", "Paid", "Cancelled"},   # Paid = requeue after printer error
    "PrintDone":   {"Binding", "Ready", "Cancelled"},
    "Binding":     {"Ready", "Cancelled"},
    "Lamination":  {"Ready", "Cancelled"},
    "Ready":       {"Collected", "Cancelled"},
    "Collected":   set(),                               # terminal
    "Cancelled":   set(),                               # terminal
    "Completed":   {"Collected"},                       # legacy alias
    "In Progress": {"Ready", "Cancelled"},              # DTP/editing jobs
    "Printed":     {"Ready", "Collected", "Completed"}, # legacy alias for PrintDone
}

# Human-readable action labels for each transition
_ACTION_LABELS: dict[tuple[str | None, str], str] = {
    (None, "Received"):    "file_received",
    (None, "Draft"):       "job_created_draft",
    (None, "Queued"):      "job_created_queued",
    ("Received", "Quoted"):    "quote_sent",
    ("Quoted",   "Paid"):      "payment_received",
    ("Received", "Paid"):      "payment_received",
    ("Queued",   "Paid"):      "payment_received",
    ("Paid",     "Printing"):  "print_sent",
    ("Printing", "PrintDone"): "print_done",
    ("PrintDone","Binding"):   "binding_dispatched",
    ("PrintDone","Ready"):     "job_ready",
    ("Binding",  "Ready"):     "binding_returned",
    ("Ready",    "Collected"): "job_collected",
    ("Printed",  "Ready"):     "job_ready",
    ("Printed",  "Collected"): "job_collected",
    ("Printed",  "Completed"): "job_completed_legacy",
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def setup_job_events_db(conn: sqlite3.Connection) -> None:
    """Create job_events table if it does not exist. Call on DB init."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS job_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id       TEXT    NOT NULL,
            staff_id     TEXT,
            action       TEXT    NOT NULL,
            from_status  TEXT,
            to_status    TEXT,
            notes        TEXT,
            duration_sec INTEGER,
            created_at   TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jevents_job ON job_events (job_id)"
    )
    conn.commit()


def log_event(
    db_path: str,
    job_id: str,
    action: str,
    from_status: Optional[str] = None,
    to_status: Optional[str] = None,
    staff_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> int:
    """
    Write one event row to job_events.

    Calculates duration_sec automatically from the previous event on the same job.
    Returns the new event id.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Ensure table exists (safe to call repeatedly)
    setup_job_events_db(conn)

    # Calculate duration since last event on this job
    prev = conn.execute(
        "SELECT created_at FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    duration_sec: Optional[int] = None
    if prev:
        try:
            prev_dt = datetime.strptime(prev["created_at"], "%Y-%m-%d %H:%M:%S")
            duration_sec = int((datetime.now() - prev_dt).total_seconds())
        except ValueError:
            pass

    cursor = conn.execute(
        """
        INSERT INTO job_events
            (job_id, staff_id, action, from_status, to_status, notes, duration_sec, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (job_id, staff_id or None, action, from_status, to_status,
         notes or None, duration_sec, _now()),
    )
    conn.commit()
    event_id = cursor.lastrowid
    conn.close()

    logger.info(
        "job_event job=%s action=%s %s→%s staff=%s",
        job_id, action, from_status or "—", to_status or "—", staff_id or "system",
    )
    return event_id


def transition(
    db_path: str,
    job_id: str,
    new_status: str,
    staff_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """
    Attempt a status transition on a job.

    Validates the transition against the state machine, updates jobs.status,
    and writes a job_events row.

    Returns {"ok": True, "event_id": N} or {"ok": False, "error": "..."}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT status FROM jobs WHERE job_id=?", (job_id,)
    ).fetchone()
    if not row:
        conn.close()
        return {"ok": False, "error": f"Job {job_id} not found"}

    current = row["status"]
    allowed = _TRANSITIONS.get(current, set())

    if new_status not in allowed and new_status != "Cancelled":
        conn.close()
        return {
            "ok": False,
            "error": f"Invalid transition {current!r} → {new_status!r}",
        }

    # Update status
    conn.execute(
        "UPDATE jobs SET status=? WHERE job_id=?", (new_status, job_id)
    )
    conn.commit()
    conn.close()

    action = _ACTION_LABELS.get((current, new_status), "status_change")
    event_id = log_event(
        db_path, job_id, action,
        from_status=current,
        to_status=new_status,
        staff_id=staff_id,
        notes=notes,
    )
    return {"ok": True, "event_id": event_id}


def get_events(db_path: str, job_id: str) -> list[dict]:
    """Return all events for a job, oldest first."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    setup_job_events_db(conn)
    rows = conn.execute(
        "SELECT * FROM job_events WHERE job_id=? ORDER BY id ASC", (job_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
