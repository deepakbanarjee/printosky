"""
work_session_tracker.py — DTP / editing work session timer for Printosky.

Tracks time spent by staff on non-print jobs (DTP, editing, graph work).
Each work session records start → optional pause/resume cycles → end.
Billing is calculated on end: total_sec → billing_minutes (ceil to 15-min slots).

Usage:
    from work_session_tracker import start_session, pause_session, resume_session, end_session, get_sessions

    sid = start_session(db_path, job_id="OSP-20260331-0001", staff_id="priya")
    pause_session(db_path, sid)
    resume_session(db_path, sid)
    result = end_session(db_path, sid, notes="DTP complete")
    # result["total_sec"]        → 3720
    # result["billing_minutes"]  → 75   (ceil to 15-min slot = 1 h 15 min)
    # result["billing_hours"]    → 1.25
"""

import logging
import math
import sqlite3
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_BILLING_SLOT_MIN = 15   # ceil to nearest N minutes


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _ceil_to_slot(total_sec: int, slot_min: int = _BILLING_SLOT_MIN) -> int:
    """Round total_sec up to the nearest billing slot. Returns billing minutes."""
    total_min = total_sec / 60
    slots = math.ceil(total_min / slot_min)
    return max(slots, 1) * slot_min  # minimum 1 slot


def setup_work_sessions_db(conn: sqlite3.Connection) -> None:
    """Create work_sessions table if it does not exist. Call on DB init."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS work_sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      TEXT    NOT NULL,
            staff_id    TEXT    NOT NULL,
            started_at  TEXT    NOT NULL,
            paused_at   TEXT,
            resumed_at  TEXT,
            ended_at    TEXT,
            total_sec   INTEGER,
            paused_sec  INTEGER DEFAULT 0,  -- cumulative seconds spent paused (updated on each resume)
            notes       TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        )
    """)
    # Idempotent column add for existing DBs that pre-date this migration
    try:
        conn.execute("ALTER TABLE work_sessions ADD COLUMN paused_sec INTEGER DEFAULT 0")
    except Exception:
        pass  # column already exists
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ws_job ON work_sessions (job_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ws_staff ON work_sessions (staff_id)"
    )
    conn.commit()


def start_session(db_path: str, job_id: str, staff_id: str) -> dict:
    """
    Open a new work session for a job.

    Returns {"ok": True, "session_id": N} or {"ok": False, "error": "..."}.
    Rejects if an open session already exists for this job+staff combo.
    """
    if not job_id or not staff_id:
        return {"ok": False, "error": "job_id and staff_id are required"}

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        setup_work_sessions_db(conn)

        # Check for existing open session on this job
        existing = conn.execute(
            "SELECT id FROM work_sessions WHERE job_id=? AND ended_at IS NULL",
            (job_id,),
        ).fetchone()
        if existing:
            return {"ok": False, "error": f"Session {existing['id']} is already open for {job_id}"}

        cursor = conn.execute(
            """
            INSERT INTO work_sessions (job_id, staff_id, started_at)
            VALUES (?, ?, ?)
            """,
            (job_id, staff_id, _now()),
        )
        conn.commit()
        session_id = cursor.lastrowid
    finally:
        conn.close()

    logger.info("Work session %d started: job=%s staff=%s", session_id, job_id, staff_id)
    return {"ok": True, "session_id": session_id, "job_id": job_id, "staff_id": staff_id}


def pause_session(db_path: str, session_id: int) -> dict:
    """
    Pause an open work session (record paused_at).

    Returns {"ok": True} or {"ok": False, "error": "..."}.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        setup_work_sessions_db(conn)

        row = conn.execute(
            "SELECT id, ended_at, paused_at FROM work_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"Session {session_id} not found"}
        if row["ended_at"]:
            return {"ok": False, "error": f"Session {session_id} is already ended"}
        if row["paused_at"]:
            return {"ok": False, "error": f"Session {session_id} is already paused"}

        conn.execute(
            "UPDATE work_sessions SET paused_at=? WHERE id=?",
            (_now(), session_id),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Work session %d paused", session_id)
    return {"ok": True, "session_id": session_id}


def resume_session(db_path: str, session_id: int) -> dict:
    """
    Resume a paused work session.

    Accumulates the pause duration into paused_sec so that end_session
    can correctly subtract all paused time from the billable total.

    Returns {"ok": True} or {"ok": False, "error": "..."}.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        setup_work_sessions_db(conn)

        row = conn.execute(
            "SELECT id, ended_at, paused_at, paused_sec FROM work_sessions WHERE id=?",
            (session_id,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"Session {session_id} not found"}
        if row["ended_at"]:
            return {"ok": False, "error": f"Session {session_id} is already ended"}
        if not row["paused_at"]:
            return {"ok": False, "error": f"Session {session_id} is not paused"}

        paused_dt = _parse_dt(row["paused_at"])
        this_pause_sec = int((datetime.now() - paused_dt).total_seconds()) if paused_dt else 0
        new_paused_sec = (row["paused_sec"] or 0) + this_pause_sec

        conn.execute(
            "UPDATE work_sessions SET paused_at=NULL, resumed_at=?, paused_sec=? WHERE id=?",
            (_now(), new_paused_sec, session_id),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Work session %d resumed (pause_interval=%ds)", session_id, this_pause_sec)
    return {"ok": True, "session_id": session_id}


def end_session(
    db_path: str,
    session_id: int,
    notes: Optional[str] = None,
    dtp_pages: int = 0,
    graph_count: int = 0,
) -> dict:
    """
    End a work session and calculate billable time.

    Calculates total_sec from started_at → now (minus any paused interval).
    Rounds up to next 15-minute billing slot.
    Updates jobs.editing_minutes, jobs.dtp_pages, jobs.graph_count if provided.

    Returns billing info or {"ok": False, "error": "..."}.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        setup_work_sessions_db(conn)

        row = conn.execute(
            "SELECT * FROM work_sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"Session {session_id} not found"}
        if row["ended_at"]:
            return {"ok": False, "error": f"Session {session_id} is already ended"}

        now = datetime.now()
        started = _parse_dt(row["started_at"])
        if not started:
            return {"ok": False, "error": "Invalid started_at timestamp"}

        # Raw elapsed from start → now
        total_sec = int((now - started).total_seconds())

        # Subtract all accumulated pause time (from previous pause/resume cycles)
        accumulated = int(row["paused_sec"] or 0)

        # Also subtract time currently paused (if session ends while still paused)
        if row["paused_at"] and not row["ended_at"]:
            paused_dt = _parse_dt(row["paused_at"])
            if paused_dt:
                accumulated += int((now - paused_dt).total_seconds())

        total_sec = max(0, total_sec - accumulated)

        billing_min = _ceil_to_slot(total_sec)
        billing_hrs = round(billing_min / 60, 2)

        conn.execute(
            """
            UPDATE work_sessions
               SET ended_at=?, total_sec=?, notes=?
             WHERE id=?
            """,
            (_now(), total_sec, notes, session_id),
        )

        # Update job aggregates
        job_id = row["job_id"]
        if dtp_pages > 0:
            conn.execute(
                "UPDATE jobs SET dtp_pages = COALESCE(dtp_pages,0) + ? WHERE job_id=?",
                (dtp_pages, job_id),
            )
        if graph_count > 0:
            conn.execute(
                "UPDATE jobs SET graph_count = COALESCE(graph_count,0) + ? WHERE job_id=?",
                (graph_count, job_id),
            )
        # Always accumulate editing_minutes
        conn.execute(
            "UPDATE jobs SET editing_minutes = COALESCE(editing_minutes,0) + ? WHERE job_id=?",
            (billing_min, job_id),
        )

        conn.commit()
    finally:
        conn.close()

    logger.info(
        "Work session %d ended: job=%s total=%ds billing=%dmin (%.2fh)",
        session_id, row["job_id"], total_sec, billing_min, billing_hrs,
    )
    return {
        "ok": True,
        "session_id": session_id,
        "job_id": row["job_id"],
        "staff_id": row["staff_id"],
        "total_sec": total_sec,
        "billing_minutes": billing_min,
        "billing_hours": billing_hrs,
        "dtp_pages": dtp_pages,
        "graph_count": graph_count,
    }


def get_sessions(db_path: str, job_id: str) -> list[dict]:
    """Return all work sessions for a job, oldest first."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        setup_work_sessions_db(conn)
        rows = conn.execute(
            "SELECT * FROM work_sessions WHERE job_id=? ORDER BY id ASC",
            (job_id,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_open_session(db_path: str, job_id: str) -> Optional[dict]:
    """Return the currently open work session for a job, or None."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        setup_work_sessions_db(conn)
        row = conn.execute(
            "SELECT * FROM work_sessions WHERE job_id=? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None
