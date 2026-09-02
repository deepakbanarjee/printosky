"""
Both writers of `konica_jobs` produce one shape — and fix what they wrote.

The table had two writers that never agreed and nothing that compared them.
These tests are the comparison: whatever path a row takes in, it comes out in
the vocabulary the consoles read.
"""

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import konica_normalize as kn


def _source(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8-sig")


# ── The SOAP fetcher ──────────────────────────────────────────────────────────

def test_the_fetcher_normalises_every_divergent_column_on_insert():
    src = _source("konica_jobs_fetcher.py")
    insert = src[src.index("def _insert_jobs"):src.index("# ── Legacy-row")]
    for call in ("normalize_job_type(j[\"job_type\"])",
                 "normalize_result(j[\"result\"])",
                 "normalize_job_date(j[\"register_time\"])",
                 "normalize_job_date(j[\"print_time\"])",
                 "normalize_paper_size(j[\"media_size\"])"):
        assert call in insert, call


def test_the_fetcher_no_longer_writes_the_raw_soap_fields():
    src = _source("konica_jobs_fetcher.py")
    insert = src[src.index("def _insert_jobs"):src.index("# ── Legacy-row")]
    body = "\n".join(line.split("#")[0] for line in insert.splitlines())
    for raw in ('j["job_type"],', 'j["result"],', 'j["register_time"],',
                'j["media_size"],'):
        assert raw not in body, f"{raw} still reaches the DB unnormalised"


def test_the_fetcher_backfills_itself_because_nothing_runs_fix_db_for_a_store_pc():
    """Store PCs update with PULL_UPDATE.bat and a watcher restart. A migration
    that has to reach a counter must apply itself on the path that needs it."""
    src = _source("konica_jobs_fetcher.py")
    assert "_normalise_once(conn)" in src
    assert "backfill_sqlite(conn)" in src
    fetch = src[src.index("def fetch_and_import"):src.index("# ── Legacy-row")]
    assert "_normalise_once(conn)" in fetch


def test_the_backfill_runs_once_per_process_not_once_per_poll():
    """The fetcher polls every 30 minutes forever; re-scanning the whole table
    each time would be a pointless load on the store PC."""
    src = _source("konica_jobs_fetcher.py")
    fn = src[src.index("def _normalise_once"):src.index("# ── Background thread")]
    assert "global _normalised" in fn
    assert "if _normalised:" in fn and "return" in fn


def test_the_fetcher_module_parses():
    ast.parse(_source("konica_jobs_fetcher.py"))


# ── The CSV importer ──────────────────────────────────────────────────────────

def test_the_csv_importer_uses_the_same_normalisers():
    src = _source("konica_csv_importer.py")
    assert "normalize_job_type(row.get(\"Job Type\", \"\"))" in src
    assert "normalize_result(row.get(\"Result\", \"\"))" in src
    assert "normalize_paper_size(row.get(\"Paper Size\", \"\"))" in src


def test_the_two_writers_agree_on_every_shared_value():
    """The actual property that was broken: the same job, arriving by either
    path, must produce identical stored values."""
    soap = {"job_type": "COPY", "result": "OK", "media_size": "LEGAL",
            "register_time": "2026/09/02 09:18:59"}
    csv_row = {"Job Type": "Copy", "Result": "No Error", "Paper Size": "Legal",
               "Job Reception Date": "02/Sep/2026 9:18:59 AM"}

    assert (kn.normalize_job_type(soap["job_type"])
            == kn.normalize_job_type(csv_row["Job Type"]) == "Copy")
    assert (kn.normalize_result(soap["result"])
            == kn.normalize_result(csv_row["Result"]) == "No Error")
    assert (kn.normalize_paper_size(soap["media_size"])
            == kn.normalize_paper_size(csv_row["Paper Size"]) == "LEGAL")

    from konica_csv_importer import parse_konica_date
    assert (kn.normalize_job_date(soap["register_time"])
            == parse_konica_date(csv_row["Job Reception Date"])
            == "2026-09-02 09:18:59")


# ── End to end on a real SQLite table ─────────────────────────────────────────

def test_a_legacy_table_becomes_readable_by_the_consoles_queries():
    """The whole point, expressed as the query MIS actually runs: rows written
    before this change were invisible to a date window and to a result filter.
    After the backfill they are not."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""CREATE TABLE konica_jobs (
        job_number INTEGER, job_type TEXT, result TEXT,
        job_date TEXT, paper_size TEXT)""")
    conn.executemany("INSERT INTO konica_jobs VALUES (?,?,?,?,?)", [
        (1, "COPY",  "OK",         "2026/09/02 09:18:59", "A4"),   # today, live writer
        (2, "COPY",  "OK",         "2026/04/13 18:05:21", "A4"),   # months ago
        (3, "Copy",  "No Error",   "2026-09-02 11:00:00", "A4"),   # today, old writer
        (4, "COPY",  "USERCANCEL", "2026/09/02 12:00:00", "A4"),   # cancelled
    ])
    conn.commit()

    before = conn.execute(
        "SELECT count(*) FROM konica_jobs "
        "WHERE result = 'No Error' AND job_type = 'Copy' AND job_date >= '2026-09-02'"
    ).fetchone()[0]
    assert before == 1          # only the retired writer's row was ever visible

    kn.backfill_sqlite(conn)

    after = conn.execute(
        "SELECT count(*) FROM konica_jobs "
        "WHERE result = 'No Error' AND job_type = 'Copy' AND job_date >= '2026-09-02'"
    ).fetchone()[0]
    assert after == 2           # today's real copy joins it; April's does not
    assert conn.execute(
        "SELECT count(*) FROM konica_jobs WHERE job_date LIKE '____/%'"
    ).fetchone()[0] == 0


# ── The cloud half ────────────────────────────────────────────────────────────

MIGRATION = ROOT / "api" / "migrations" / "SCHEMA_v39_konica_normalize.sql"


def _sql_without_comments() -> str:
    return "\n".join(line.split("--")[0]
                     for line in MIGRATION.read_text(encoding="utf-8").splitlines())


def test_the_cloud_migration_exists():
    assert MIGRATION.exists()


def test_the_migration_only_updates_never_drops():
    sql = _sql_without_comments().upper()
    for verb in ("DROP", "DELETE", "TRUNCATE", "ALTER"):
        assert verb not in sql, verb


def test_the_migration_covers_every_divergent_column():
    sql = _sql_without_comments()
    for column in ("job_type", "result", "job_date", "print_end_date", "paper_size"):
        assert f"SET {column} =" in sql, column


def test_the_migration_leaves_an_unknown_job_type_alone():
    """initcap() on an unrecognised value would invent a type. The UPDATE is
    guarded to the three types this build knows, matching normalize_job_type."""
    sql = _sql_without_comments()
    assert "lower(job_type) IN ('print', 'copy', 'scan')" in sql


def test_the_migration_is_one_transaction():
    sql = _sql_without_comments().upper()
    assert "BEGIN;" in sql and "COMMIT;" in sql


@pytest.mark.parametrize("code,expected", [
    ("OK", "No Error"), ("USERCANCEL", "Canceled"), ("UNKNOWNERROR", "Error"),
])
def test_the_migrations_result_map_matches_the_python(code, expected):
    sql = _sql_without_comments()
    assert f"WHEN '{code}'" in sql
    assert kn.RESULT_ALIASES[code] == expected
