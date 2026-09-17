"""
TASK-016 (roadmap-2026-05): unit tests for scripts/check_schema.py.

Covers the pure diff_schemas() function. The psycopg2-backed fetch is not
tested here (it's a thin wrapper around two SQL queries; integration-tested
manually against live Supabase).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest

import check_schema  # noqa: E402


# ═════════════════════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════════════════════

def _schema(tables: dict, views: list[str] | None = None) -> dict:
    return {"tables": tables, "views": views or []}


def _col(t: str = "text", nullable: bool = True) -> dict:
    return {"type": t, "nullable": nullable}


# ═════════════════════════════════════════════════════════════════════════════
# Identical schemas
# ═════════════════════════════════════════════════════════════════════════════

class TestNoChange:
    def test_empty_schemas_match(self) -> None:
        assert check_schema.diff_schemas({}, {}) == []

    def test_identical_single_table(self) -> None:
        s = _schema({"jobs": {"rls": True, "columns": {"id": _col("bigint", False)}}})
        assert check_schema.diff_schemas(s, s) == []

    def test_default_difference_does_not_drift(self) -> None:
        """Defaults are documentation-only; diff ignores them."""
        exp = _schema({"jobs": {"rls": True, "columns": {
            "id": {"type": "bigint", "nullable": False, "default": "nextval(...)"},
        }}})
        act = _schema({"jobs": {"rls": True, "columns": {
            "id": {"type": "bigint", "nullable": False, "default": None},
        }}})
        assert check_schema.diff_schemas(exp, act) == []


# ═════════════════════════════════════════════════════════════════════════════
# Missing / extra tables
# ═════════════════════════════════════════════════════════════════════════════

class TestTableDelta:
    def test_missing_table_in_db(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {}}})
        act = _schema({})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "missing_table"
        assert drifts[0].table == "jobs"

    def test_extra_table_in_db(self) -> None:
        exp = _schema({})
        act = _schema({"jobs": {"rls": True, "columns": {}}})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "extra_table"
        assert drifts[0].table == "jobs"

    def test_view_in_db_not_flagged_when_listed(self) -> None:
        exp = _schema({}, views=["epson_daily"])
        act = _schema({"epson_daily": {"rls": False, "columns": {}}})
        assert check_schema.diff_schemas(exp, act) == []

    def test_view_in_db_flagged_when_not_listed(self) -> None:
        exp = _schema({})
        act = _schema({"epson_daily": {"rls": False, "columns": {}}})
        drifts = check_schema.diff_schemas(exp, act)
        assert any(d.kind == "extra_table" and d.table == "epson_daily" for d in drifts)


# ═════════════════════════════════════════════════════════════════════════════
# Column-level drift
# ═════════════════════════════════════════════════════════════════════════════

class TestColumnDelta:
    def test_missing_column_in_db(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {
            "id": _col("bigint", False),
            "needs_human": _col("boolean", False),
        }}})
        act = _schema({"jobs": {"rls": True, "columns": {
            "id": _col("bigint", False),
        }}})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "missing_column"
        assert drifts[0].column == "needs_human"

    def test_extra_column_in_db(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {"id": _col("bigint", False)}}})
        act = _schema({"jobs": {"rls": True, "columns": {
            "id": _col("bigint", False),
            "secret_admin_flag": _col("boolean"),
        }}})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "extra_column"
        assert drifts[0].column == "secret_admin_flag"

    def test_type_mismatch(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {"amount": _col("integer")}}})
        act = _schema({"jobs": {"rls": True, "columns": {"amount": _col("text")}}})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "type_mismatch"
        assert drifts[0].expected == "integer"
        assert drifts[0].actual == "text"

    def test_nullable_tightened(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {"x": _col("text", True)}}})
        act = _schema({"jobs": {"rls": True, "columns": {"x": _col("text", False)}}})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "nullable_mismatch"

    def test_nullable_loosened(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {"x": _col("text", False)}}})
        act = _schema({"jobs": {"rls": True, "columns": {"x": _col("text", True)}}})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "nullable_mismatch"

    def test_missing_nullable_defaults_to_true(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {"x": {"type": "text"}}}})
        act = _schema({"jobs": {"rls": True, "columns": {"x": {"type": "text"}}}})
        assert check_schema.diff_schemas(exp, act) == []


# ═════════════════════════════════════════════════════════════════════════════
# RLS drift
# ═════════════════════════════════════════════════════════════════════════════

class TestRlsMismatch:
    def test_security_regression_rls_disabled_in_db(self) -> None:
        """The security-critical case: manifest says RLS on, live says off."""
        exp = _schema({"jobs": {"rls": True, "columns": {}}})
        act = _schema({"jobs": {"rls": False, "columns": {}}})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "rls_mismatch"
        assert drifts[0].expected == "true"
        assert drifts[0].actual == "false"

    def test_pinned_rls_disabled_no_drift(self) -> None:
        exp = _schema({"referral_credits": {"rls": False, "columns": {}}})
        act = _schema({"referral_credits": {"rls": False, "columns": {}}})
        assert check_schema.diff_schemas(exp, act) == []

    def test_rls_unexpectedly_enabled(self) -> None:
        exp = _schema({"referral_credits": {"rls": False, "columns": {}}})
        act = _schema({"referral_credits": {"rls": True, "columns": {}}})
        drifts = check_schema.diff_schemas(exp, act)
        assert len(drifts) == 1
        assert drifts[0].kind == "rls_mismatch"


# ═════════════════════════════════════════════════════════════════════════════
# Drift formatting
# ═════════════════════════════════════════════════════════════════════════════

class TestDriftFormat:
    def test_missing_table_format(self) -> None:
        d = check_schema.Drift("missing_table", table="jobs")
        s = d.format()
        assert "MISSING" in s and "jobs" in s

    def test_type_mismatch_includes_both_sides(self) -> None:
        d = check_schema.Drift("type_mismatch", table="jobs", column="amount",
                               expected="integer", actual="text")
        s = d.format()
        assert "TYPE_MISMATCH" in s
        assert "jobs.amount" in s
        assert "integer" in s and "text" in s


# ═════════════════════════════════════════════════════════════════════════════
# Real manifest parses and is self-consistent
# ═════════════════════════════════════════════════════════════════════════════

class TestManifestParses:
    def test_real_manifest_loads(self) -> None:
        m = check_schema.load_manifest()
        assert "tables" in m
        assert len(m["tables"]) > 0
        # After SCHEMA_v17:
        assert "needs_human" in m["tables"]["bot_sessions"]["columns"]
        # After SCHEMA_v18:
        assert "processed_webhooks" in m["tables"]
        # Views are declared:
        assert "epson_daily" in m.get("views", [])

    def test_real_manifest_self_consistent(self) -> None:
        """Manifest diffed against itself = no drift."""
        m = check_schema.load_manifest()
        synthetic_actual = {
            "tables": m["tables"],
            "views": m.get("views", []),
        }
        assert check_schema.diff_schemas(m, synthetic_actual) == []


# ═════════════════════════════════════════════════════════════════════════════
# Tables deliberately outside the contract
# ═════════════════════════════════════════════════════════════════════════════

class TestIgnoredTables:
    """A one-off backup taken during an incident is not schema drift. Without a
    way to say so the choice is documenting a temporary table as permanent, or
    leaving the check red until people stop reading it — and a check nobody
    reads is how this one sat inert while a whole table went undocumented."""

    def test_an_ignored_table_in_the_db_is_not_extra(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {}}})
        exp["ignored_tables"] = ["backup_20260818_nattika_counters"]
        act = _schema({
            "jobs": {"rls": True, "columns": {}},
            "backup_20260818_nattika_counters": {"rls": False, "columns": {}},
        })
        assert check_schema.diff_schemas(exp, act) == []

    def test_an_ignored_table_is_not_missing_when_absent(self) -> None:
        """Dropping the backup must not turn the exception into a new failure."""
        exp = _schema({"jobs": {"rls": True, "columns": {}},
                       "backup_20260818_nattika_counters": {"rls": False, "columns": {}}})
        exp["ignored_tables"] = ["backup_20260818_nattika_counters"]
        act = _schema({"jobs": {"rls": True, "columns": {}}})
        assert check_schema.diff_schemas(exp, act) == []

    def test_an_ignored_table_is_not_rls_checked(self) -> None:
        """The backups have RLS off; that is recorded in docs/SCHEMA.md as a
        known gap, not re-reported on every run."""
        exp = _schema({"backup_20260818_nattika_counters": {"rls": True, "columns": {}}})
        exp["ignored_tables"] = ["backup_20260818_nattika_counters"]
        act = _schema({"backup_20260818_nattika_counters": {"rls": False, "columns": {}}})
        assert check_schema.diff_schemas(exp, act) == []

    def test_a_table_not_ignored_still_drifts(self) -> None:
        exp = _schema({"jobs": {"rls": True, "columns": {}}})
        exp["ignored_tables"] = ["something_else"]
        act = _schema({"jobs": {"rls": True, "columns": {}},
                       "surprise_table": {"rls": True, "columns": {}}})
        drifts = check_schema.diff_schemas(exp, act)
        assert [d.kind for d in drifts] == ["extra_table"]

    def test_the_real_manifest_only_ignores_the_incident_backups(self) -> None:
        """Every entry here is a decision someone has to justify. Keep the list
        short and temporary."""
        ignored = check_schema.load_manifest().get("ignored_tables") or []
        assert sorted(ignored) == [
            "backup_20260818_nattika_counters",
            "backup_20260818_nattika_epson_jobs",
        ]


# ═════════════════════════════════════════════════════════════════════════════
# dump_manifest — a regenerate must not throw away the human parts
# ═════════════════════════════════════════════════════════════════════════════
#
# `--dump` is the documented step after applying a migration. It used to write
# only `views` and `tables`, so running it deleted the header comment (which
# says "DO NOT hand-edit"), the version, and `ignored_tables` — and the very
# next drift check then flagged the two deliberately-excluded incident backups
# as EXTRA. Found while applying SCHEMA_v44.

MANIFEST_HEAD = """# config/schema_manifest.yaml
# Header that explains where this file comes from.

"""


def _write_manifest(path, body: dict) -> None:
    import yaml

    path.write_text(MANIFEST_HEAD + yaml.safe_dump(body, sort_keys=True), encoding="utf-8")


def _prior_manifest() -> dict:
    return {
        "version": 43,
        "description": "the previous snapshot",
        "rls_disabled_known": [],
        "ignored_tables": ["backup_one", "backup_two"],
        "views": ["old_view"],
        "tables": {"jobs": {"rls": True, "columns": {"job_id": {"type": "text", "nullable": False}}}},
    }


def test_dump_preserves_contract_metadata(tmp_path):
    import yaml

    path = tmp_path / "schema_manifest.yaml"
    _write_manifest(path, _prior_manifest())

    live = {
        "views": ["new_view"],
        "tables": {"orders": {"rls": True, "columns": {"order_id": {"type": "text", "nullable": False}}}},
    }
    check_schema.dump_manifest(live, path)
    after = yaml.safe_load(path.read_text())

    # Decisions a person made, which the database cannot regenerate.
    assert after["version"] == 43
    assert after["description"] == "the previous snapshot"
    assert after["ignored_tables"] == ["backup_one", "backup_two"]
    assert after["rls_disabled_known"] == []
    # Facts the database owns, which the dump replaces.
    assert after["views"] == ["new_view"]
    assert set(after["tables"]) == {"orders"}


def test_dump_preserves_the_header_comment(tmp_path):
    path = tmp_path / "schema_manifest.yaml"
    _write_manifest(path, _prior_manifest())
    check_schema.dump_manifest({"views": [], "tables": {}}, path)
    assert path.read_text().startswith("# config/schema_manifest.yaml")


def test_dump_after_dump_is_stable(tmp_path):
    """Two dumps of the same live schema produce byte-identical files."""
    path = tmp_path / "schema_manifest.yaml"
    _write_manifest(path, _prior_manifest())
    live = {"views": ["v"], "tables": {"t": {"rls": True, "columns": {}}}}
    check_schema.dump_manifest(live, path)
    once = path.read_text()
    check_schema.dump_manifest(live, path)
    assert path.read_text() == once


def test_dump_works_on_a_fresh_file(tmp_path):
    """No prior manifest: no header, no preserved keys, and no crash."""
    import yaml

    path = tmp_path / "new.yaml"
    check_schema.dump_manifest({"views": [], "tables": {}}, path)
    after = yaml.safe_load(path.read_text())
    assert after == {"views": [], "tables": {}}


def test_ignored_backups_survive_a_dump_and_stay_out_of_the_diff(tmp_path):
    """The end-to-end reason this matters: dump, then diff, must stay clean."""
    import yaml

    path = tmp_path / "schema_manifest.yaml"
    _write_manifest(path, _prior_manifest())
    live_tables = {"orders": {"rls": True, "columns": {}}}
    check_schema.dump_manifest({"views": [], "tables": live_tables}, path)

    expected = yaml.safe_load(path.read_text())
    actual = {"views": [], "tables": {**live_tables,
                                      "backup_one": {"rls": False, "columns": {}},
                                      "backup_two": {"rls": False, "columns": {}}}}
    assert check_schema.diff_schemas(expected, actual) == []


def test_the_real_manifest_declares_every_v44_table():
    """The live database has these; the contract must say so."""
    import yaml

    manifest = yaml.safe_load(check_schema.MANIFEST_PATH.read_text())
    v44 = {
        "orders", "order_items", "order_tasks", "payment_inbox", "payments",
        "payment_allocations", "outbox_events", "print_attempts", "identities",
        "identity_sessions", "login_failures",
    }
    assert v44 <= set(manifest["tables"]), sorted(v44 - set(manifest["tables"]))
    assert manifest["version"] == 44
    for table in v44:
        assert manifest["tables"][table]["rls"] is True, f"{table} must have RLS enabled"
