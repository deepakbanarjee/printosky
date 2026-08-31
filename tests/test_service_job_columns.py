"""
B-2 — the `jobs` columns a post-press service job needs.

Two things are being proved here, and the second matters more than the first:

1. The columns arrive, on a fresh database and on one written years ago, and
   arriving twice is not an error.
2. **Absent means unchanged.** Nothing in this commit reads `service_kind`;
   a `jobs` row without it is a print job, and every existing query, sync and
   quote must behave exactly as it did before the columns existed.

The self-healing shape exists because store PCs update by pulling code and
restarting the watcher — nothing runs `fix_db.py` for them (docs/AUTO_UPDATE.md).
That lesson was learned the hard way on `print_items.scale_mode`.
"""

import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import db_migrations
from db_migrations import (
    SERVICE_JOB_COLUMNS,
    ensure_columns,
    ensure_job_service_columns,
)

REPO = os.path.join(os.path.dirname(__file__), "..")
MIGRATION_SQL = os.path.join(REPO, "api", "migrations", "SCHEMA_v38_service_jobs.sql")

# A `jobs` table as it stood before this change — the shape an old store PC has.
LEGACY_JOBS_DDL = """
    CREATE TABLE jobs (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id           TEXT UNIQUE NOT NULL,
        received_at      TEXT NOT NULL,
        filename         TEXT NOT NULL,
        status           TEXT DEFAULT 'Received',
        customer_name    TEXT,
        amount_quoted    REAL,
        amount_collected REAL,
        payment_mode     TEXT
    )
"""

COLUMN_NAMES = [c for c, _ in SERVICE_JOB_COLUMNS]


def _cols(conn, table="jobs"):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@pytest.fixture
def legacy(tmp_path):
    """A pre-v38 database with one real job row in it."""
    conn = sqlite3.connect(tmp_path / "jobs.db")
    conn.executescript(LEGACY_JOBS_DDL)
    conn.execute(
        "INSERT INTO jobs (job_id, received_at, filename, status, amount_quoted)"
        " VALUES ('OSP-20260101-0001', '2026-01-01T10:00:00', 'notes.pdf', 'Paid', 120.0)"
    )
    conn.commit()
    yield conn
    conn.close()


# ── The columns arrive ────────────────────────────────────────────────────────

def test_legacy_db_gains_every_service_column(legacy):
    assert not (_cols(legacy) & set(COLUMN_NAMES))
    added = ensure_job_service_columns(legacy)
    assert sorted(added) == sorted(COLUMN_NAMES)
    assert set(COLUMN_NAMES) <= _cols(legacy)


def test_running_twice_adds_nothing_and_does_not_raise(legacy):
    ensure_job_service_columns(legacy)
    assert ensure_job_service_columns(legacy) == []
    assert ensure_job_service_columns(legacy) == []


def test_columns_are_nullable_with_no_default(legacy):
    """A row written before the migration must keep meaning what it meant."""
    ensure_job_service_columns(legacy)
    for row in legacy.execute("PRAGMA table_info(jobs)"):
        name, notnull, default = row[1], row[3], row[4]
        if name in COLUMN_NAMES:
            assert notnull == 0, f"{name} is NOT NULL — an old row cannot satisfy it"
            assert default is None, f"{name} has default {default!r} — must be NULL"


def test_existing_row_survives_untouched(legacy):
    before = legacy.execute("SELECT * FROM jobs").fetchall()
    ensure_job_service_columns(legacy)
    after = legacy.execute(
        "SELECT id, job_id, received_at, filename, status,"
        " customer_name, amount_quoted, amount_collected, payment_mode FROM jobs"
    ).fetchall()
    assert after == before


def test_existing_row_reads_as_a_print_job(legacy):
    """service_kind NULL is the whole compatibility contract."""
    ensure_job_service_columns(legacy)
    kind = legacy.execute(
        "SELECT service_kind FROM jobs WHERE job_id='OSP-20260101-0001'"
    ).fetchone()[0]
    assert kind is None
    (n,) = legacy.execute(
        "SELECT COUNT(*) FROM jobs WHERE service_kind IS NULL"
    ).fetchone()
    assert n == 1


def test_a_service_job_round_trips(legacy):
    ensure_job_service_columns(legacy)
    legacy.execute(
        "INSERT INTO jobs (job_id, received_at, filename, status,"
        " service_kind, service_meta, finishing_status, print_amount,"
        " finishing_amount, finishing_internal_amount, finishing_store_id,"
        " item_received_at)"
        " VALUES ('OSP-20260101-0002', '2026-01-01T11:00:00', '-', 'Pending',"
        " 'foil', '{\"sheets\": 14, \"paper_size\": \"A4\"}', 'sent', 0.0,"
        " 420.0, 300.0, 'PRINTK', '2026-01-01T11:05:00')"
    )
    legacy.commit()
    row = legacy.execute(
        "SELECT service_kind, service_meta, finishing_amount, finishing_store_id"
        " FROM jobs WHERE job_id='OSP-20260101-0002'"
    ).fetchone()
    assert row[0] == "foil"
    assert '"sheets": 14' in row[1]
    assert row[2] == 420.0
    assert row[3] == "PRINTK"
    # ...and the old row is still a print job.
    (n,) = legacy.execute(
        "SELECT COUNT(*) FROM jobs WHERE service_kind IS NULL"
    ).fetchone()
    assert n == 1


# ── Failure modes ─────────────────────────────────────────────────────────────

def test_missing_table_is_not_an_error(tmp_path):
    """bootstrap order can call this before setup_database() runs."""
    conn = sqlite3.connect(tmp_path / "empty.db")
    assert ensure_job_service_columns(conn) == []
    conn.close()


class _RefusesOneColumn:
    """A connection whose ALTER for one named column always fails."""

    def __init__(self, conn, column):
        self._conn, self._column = conn, column

    def execute(self, sql, *a, **kw):
        if sql.startswith(f"ALTER TABLE jobs ADD COLUMN {self._column}"):
            raise sqlite3.OperationalError("disk I/O error")
        return self._conn.execute(sql, *a, **kw)

    def commit(self):
        return self._conn.commit()


def test_alter_failure_is_reported_not_swallowed(legacy, monkeypatch):
    """The fail-loud rule: a column that cannot be added reaches a human."""
    seen = []
    monkeypatch.setattr(db_migrations, "_alert", lambda t, d: seen.append((t, d)))
    added = ensure_job_service_columns(_RefusesOneColumn(legacy, "service_kind"))

    assert "service_kind" not in added
    assert seen, "a failed ALTER must alert"
    assert "service_kind" in seen[0][1]
    # The other columns still get added — one bad column is not a total stop.
    assert "service_meta" in added


def test_alert_routes_to_ops_watchdog(monkeypatch):
    calls = []
    import ops_watchdog
    monkeypatch.setattr(
        ops_watchdog, "report",
        lambda name, ok, detail="", **kw: calls.append((name, ok, detail)),
    )
    db_migrations._alert("jobs", "boom")
    assert calls == [("db.migrate.jobs", False, "boom")]


def test_generic_ensure_columns_works_on_any_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY)")
    assert ensure_columns(conn, "widgets", [("colour", "TEXT")]) == ["colour"]
    assert ensure_columns(conn, "widgets", [("colour", "TEXT")]) == []
    assert "colour" in _cols(conn, "widgets")
    conn.close()


# ── The two schemas agree ─────────────────────────────────────────────────────

def _without_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def test_cloud_migration_covers_the_same_columns():
    sql = open(MIGRATION_SQL, encoding="utf-8").read()
    for col in COLUMN_NAMES:
        assert re.search(rf"ADD COLUMN IF NOT EXISTS {col}\b", sql), (
            f"{col} is in SQLite but not in SCHEMA_v38_service_jobs.sql"
        )


def test_cloud_migration_is_additive_only():
    """No DROP, no NOT NULL, no default — nothing that could break a live row."""
    sql = _without_comments(open(MIGRATION_SQL, encoding="utf-8").read())
    statements = [s for s in sql.split(";") if "ALTER TABLE" in s.upper()]
    assert statements
    for stmt in statements:
        upper = " ".join(stmt.split()).upper()
        assert "DROP" not in upper
        assert "NOT NULL" not in upper
        assert "SET DEFAULT" not in upper
        assert "ADD COLUMN IF NOT EXISTS" in upper


def test_cloud_migration_indexes_service_kind_partially():
    sql = " ".join(open(MIGRATION_SQL, encoding="utf-8").read().split())
    assert "CREATE INDEX IF NOT EXISTS jobs_service_kind_idx" in sql
    assert "WHERE service_kind IS NOT NULL" in sql


def test_schema_manifest_carries_every_v38_column():
    """config/schema_manifest.yaml is what the drift check compares live against.

    Missed in B-2 and caught on 2026-08-31 once the migration was applied: the
    live database had eight columns the manifest did not, which is precisely the
    "columns deployed before code" drift that file exists to catch. Types here
    are the Postgres ones, not the SQLite ones.
    """
    import yaml
    path = os.path.join(REPO, "config", "schema_manifest.yaml")
    manifest = yaml.safe_load(open(path, encoding="utf-8"))
    cols = manifest.get("tables", manifest)["jobs"]["columns"]
    expected = {
        "service_kind": "text", "service_meta": "jsonb",
        "finishing_store_id": "text", "finishing_status": "text",
        "print_amount": "real", "finishing_amount": "real",
        "finishing_internal_amount": "real",
        "item_received_at": "timestamp with time zone",
    }
    assert set(expected) == set(COLUMN_NAMES), "the v38 column list moved"
    for col, pg_type in expected.items():
        assert col in cols, f"{col} missing from config/schema_manifest.yaml"
        assert cols[col]["type"] == pg_type
        assert cols[col]["nullable"] is True
        assert cols[col]["default"] is None


def test_schema_doc_lists_every_column():
    doc = open(os.path.join(REPO, "docs", "SCHEMA.md"), encoding="utf-8").read()
    for col in COLUMN_NAMES:
        assert f"| `{col}` |" in doc, f"{col} missing from docs/SCHEMA.md"


def test_kinds_match_the_rate_card():
    """The column stores exactly the kinds B-1 knows how to price."""
    from rate_card import SERVICE_KINDS
    sql = open(MIGRATION_SQL, encoding="utf-8").read()
    for kind in SERVICE_KINDS:
        assert kind in sql, f"{kind} is priceable but undocumented in the migration"


# ── Nothing else moves ────────────────────────────────────────────────────────

def test_cloud_sync_pushes_only_columns_the_cloud_has():
    """collect_jobs names its columns explicitly, and only ones v38 created.

    Until the Supabase migration was applied (2026-08-31) this asserted the
    opposite — that NO v38 column was pushed — which is what made the SQLite
    half safe to ship first. Now that the cloud has them, service_kind and
    service_meta are pushed so the consoles can see a counter-booked service;
    the columns B-8 and B-9 will use are still not, because nothing writes them.
    """
    import supabase_sync
    src = open(supabase_sync.__file__, encoding="utf-8").read()
    start = src.index("def collect_jobs")
    body = src[start:src.index("def collect_printer_counters")]
    assert "service_kind" in body and "service_meta" in body
    for col in COLUMN_NAMES:
        if col in ("service_kind", "service_meta"):
            continue
        assert col not in body, f"collect_jobs pushes {col}, which nothing writes yet"


def test_cloud_sync_survives_a_store_pc_that_has_not_migrated():
    """A box running new code against an old DB must still sync its jobs."""
    import supabase_sync
    conn = sqlite3.connect(":memory:")
    conn.executescript(LEGACY_JOBS_DDL)
    assert supabase_sync._has_service_columns(conn) is False
    ensure_job_service_columns(conn)
    assert supabase_sync._has_service_columns(conn) is True
    conn.close()


def test_service_meta_reaches_jsonb_as_an_object_not_a_string():
    import supabase_sync
    assert supabase_sync._as_json_object('{"sheets": 6}') == {"sheets": 6}
    assert supabase_sync._as_json_object({"sheets": 6}) == {"sheets": 6}
    assert supabase_sync._as_json_object(None) is None
    assert supabase_sync._as_json_object("") is None
    # Unparseable meta becomes NULL and is logged, never a jsonb string.
    assert supabase_sync._as_json_object("sheets=6", "OSP-1") is None


def test_fix_db_and_watcher_use_the_one_list():
    """One source of truth — a column added here reaches every path."""
    for path in ("fix_db.py", "watcher.py"):
        src = open(os.path.join(REPO, path), encoding="utf-8-sig").read()
        assert "db_migrations" in src, f"{path} does not apply the v38 columns"
