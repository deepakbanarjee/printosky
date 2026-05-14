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
