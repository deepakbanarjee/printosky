"""
SELF-APPLYING SQLITE MIGRATIONS — store PC edition.

Why this module exists
----------------------
Store PCs update by pulling code and restarting the watcher
(``docs/AUTO_UPDATE.md``). **Nothing runs ``fix_db.py`` for them.** So a box can
be running today's Python against a ``jobs.db`` written months ago, and the
first statement that names a new column dies at the counter with
``no such column: ...``.

That exact regression happened once already, on ``print_items.scale_mode``
(2026-08-30, plan §3.8). The fix is not "remember to run the migration" — it is
to make the migration cheap enough to run on the path that needs it. This module
is the shared place for that: an idempotent PRAGMA-and-ALTER that turns
``fix_db.py`` into the tidy-up rather than the prerequisite.

Usage
-----
    from db_migrations import ensure_job_service_columns
    ensure_job_service_columns(conn)      # before any INSERT/UPDATE naming them

Rules
-----
* **Additive only.** Every column is nullable with no default, so a row written
  before the migration keeps meaning exactly what it meant.
* **Idempotent.** Safe to call on every request; the PRAGMA is a memory read.
* **Fail loud.** If a column is genuinely missing and cannot be added, that is
  reported through ``ops_watchdog`` — never swallowed into a log line.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Iterable, Sequence

# ── Post-press service jobs (plan §4.3, B-2) ──────────────────────────────────
#
# A service job is an ordinary `jobs` row with `service_kind` set — not a new
# table — so revenue, payment, pickup codes, WhatsApp notify, the daily summary
# and MIS all keep reading the one table they already read.
#
#   service_kind NULL  ⇒  print job  ⇒  everything behaves exactly as today.
#
# Cloud counterpart: api/migrations/SCHEMA_v38_service_jobs.sql (JSONB /
# timestamptz there, TEXT here — SQLite has neither).
SERVICE_JOB_COLUMNS: tuple[tuple[str, str], ...] = (
    ("service_kind",              "TEXT"),   # NULL = print job. rate_card.SERVICE_KINDS
    ("service_meta",              "TEXT"),   # JSON: per-kind quantities
    # Inter-store finishing (plan §4.7)
    ("finishing_store_id",        "TEXT"),   # store that does the finishing
    ("finishing_status",          "TEXT"),   # sent | at_finisher | returned
    ("finishing_sent_at",         "TEXT"),   # when it LEFT for the finisher
    ("print_amount",              "REAL"),   # ₹ split: printing part
    ("finishing_amount",          "REAL"),   # ₹ split: finishing part (customer)
    ("finishing_internal_amount", "REAL"),   # ₹ split: what the finisher store keeps
    # Drop-off bookings (plan §4.8)
    ("item_received_at",          "TEXT"),   # when the physical item reached the counter
)


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Column names of `table`, or an empty set if the table does not exist."""
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_columns(
    conn: sqlite3.Connection,
    table: str,
    columns: Iterable[Sequence[str]],
) -> list[str]:
    """Add any of `columns` that `table` is missing. Returns the names added.

    A table that does not exist yet is not an error: bootstrap order means this
    can run before `setup_database()` has created it, and the CREATE will carry
    the columns anyway. Returns [] in that case.
    """
    wanted = list(columns)
    try:
        have = _table_columns(conn, table)
    except Exception as exc:
        _alert(table, f"could not read schema: {exc}")
        return []

    if not have:
        return []            # table not created yet — nothing to migrate

    added: list[str] = []
    for col, defn in wanted:
        if col in have:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {defn}")
            added.append(col)
            logging.info("%s: added missing column %s %s", table, col, defn)
        except Exception as exc:
            # Loud: the next statement naming this column will fail hard, and a
            # counter that cannot save a job is not something to discover later.
            _alert(table, f"could not add column {col} {defn}: {exc}")

    if added:
        try:
            conn.commit()
        except Exception as exc:
            _alert(table, f"could not commit added columns {added}: {exc}")

    return added


def ensure_job_service_columns(conn: sqlite3.Connection) -> list[str]:
    """Make `jobs` able to hold a post-press service job. Idempotent."""
    return ensure_columns(conn, "jobs", SERVICE_JOB_COLUMNS)


def _alert(table: str, detail: str) -> None:
    """Report a migration failure. Never raises — the caller's own query will."""
    logging.error("db_migrations[%s]: %s", table, detail)
    try:
        from ops_watchdog import report
        report(f"db.migrate.{table}", False, detail)
    except Exception as exc:                      # watchdog itself unavailable
        logging.error("db_migrations: could not report failure: %s", exc)
