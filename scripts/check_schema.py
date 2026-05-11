#!/usr/bin/env python
"""
TASK-016 (roadmap-2026-05): schema integrity check.

Compares the live Supabase schema against config/schema_manifest.yaml.
Reports drift: missing/extra tables, missing/extra columns, type and
nullability mismatches, RLS mismatches.

Exit codes:
    0 -- no drift
    1 -- drift detected (prints diff to stderr)
    2 -- configuration error (missing env, manifest, deps)

Usage:
    python scripts/check_schema.py            # check against manifest
    python scripts/check_schema.py --dump     # rewrite manifest from live
                                              # (use after a migration deploy)

Requires DATABASE_URL or SUPABASE_DB_URL in the env (Postgres connection
string for the project database).
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    sys.stderr.write("ERROR: PyYAML is required. Install with: pip install pyyaml\n")
    sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "config" / "schema_manifest.yaml"


# ─────────────────────────────────────────────────────────────────────────────
# Drift model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Drift:
    """One difference between expected and actual schema."""
    kind: str          # 'missing_table' | 'extra_table' | 'missing_column'
                       # | 'extra_column' | 'type_mismatch' | 'nullable_mismatch'
                       # | 'rls_mismatch'
    table: str
    column: str | None = None
    expected: str | None = None
    actual: str | None = None

    def format(self) -> str:
        loc = f"{self.table}" + (f".{self.column}" if self.column else "")
        if self.kind in ("missing_table", "missing_column"):
            return f"  [MISSING]  {loc}  (in manifest, absent in DB)"
        if self.kind in ("extra_table", "extra_column"):
            return f"  [EXTRA]    {loc}  (in DB, absent from manifest)"
        return f"  [{self.kind.upper()}]  {loc}  expected={self.expected!r}  actual={self.actual!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Pure diff function -- testable in isolation
# ─────────────────────────────────────────────────────────────────────────────

def diff_schemas(expected: dict[str, Any], actual: dict[str, Any]) -> list[Drift]:
    """Compare manifest (expected) against live snapshot (actual).

    Both dicts have shape::

        {
            "tables": {
                "<table>": {
                    "rls": bool,
                    "columns": {
                        "<col>": {
                            "type": str,
                            "nullable": bool,
                            "default": str | None,  # optional in expected
                        }
                    }
                }
            },
            "views": [str, ...]   # optional; entries excluded from table diff
        }

    Returns a list of Drift records (empty if schemas match).
    """
    drifts: list[Drift] = []
    exp_tables: dict = expected.get("tables", {}) or {}
    act_tables: dict = actual.get("tables", {}) or {}
    views: set[str] = set(expected.get("views", []) or [])

    exp_names = set(exp_tables.keys())
    act_names = set(act_tables.keys()) - views  # ignore declared views

    for t in sorted(exp_names - act_names):
        drifts.append(Drift("missing_table", table=t))
    for t in sorted(act_names - exp_names):
        drifts.append(Drift("extra_table", table=t))

    for t in sorted(exp_names & act_names):
        exp_t = exp_tables[t] or {}
        act_t = act_tables[t] or {}

        # RLS state
        exp_rls = bool(exp_t.get("rls", False))
        act_rls = bool(act_t.get("rls", False))
        if exp_rls != act_rls:
            drifts.append(Drift(
                "rls_mismatch", table=t,
                expected=str(exp_rls).lower(), actual=str(act_rls).lower(),
            ))

        exp_cols: dict = exp_t.get("columns", {}) or {}
        act_cols: dict = act_t.get("columns", {}) or {}
        for c in sorted(set(exp_cols) - set(act_cols)):
            drifts.append(Drift("missing_column", table=t, column=c))
        for c in sorted(set(act_cols) - set(exp_cols)):
            drifts.append(Drift("extra_column", table=t, column=c))

        for c in sorted(set(exp_cols) & set(act_cols)):
            exp_c = exp_cols[c] or {}
            act_c = act_cols[c] or {}
            if str(exp_c.get("type", "")).strip() != str(act_c.get("type", "")).strip():
                drifts.append(Drift(
                    "type_mismatch", table=t, column=c,
                    expected=str(exp_c.get("type")), actual=str(act_c.get("type")),
                ))
            # nullable: True/False; treat missing as True (Postgres default)
            exp_nul = bool(exp_c.get("nullable", True))
            act_nul = bool(act_c.get("nullable", True))
            if exp_nul != act_nul:
                drifts.append(Drift(
                    "nullable_mismatch", table=t, column=c,
                    expected=str(exp_nul).lower(), actual=str(act_nul).lower(),
                ))

    return drifts


# ─────────────────────────────────────────────────────────────────────────────
# Live fetcher (Postgres direct)
# ─────────────────────────────────────────────────────────────────────────────

_SQL_COLUMNS = """
SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
ORDER BY table_name, ordinal_position
"""

_SQL_TABLES = """
SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled, c.relkind
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind IN ('r', 'v')
"""


def fetch_live_schema_via_psycopg2(dsn: str) -> dict[str, Any]:
    """Pull the live schema via psycopg2. Raises on connection failure."""
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        sys.stderr.write("ERROR: psycopg2-binary not installed. Run:\n"
                         "  pip install psycopg2-binary\n")
        sys.exit(2)

    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(_SQL_TABLES)
        rls_map: dict[str, bool] = {}
        views: list[str] = []
        for row in cur.fetchall():
            if row["relkind"] == "v":
                views.append(row["table_name"])
            else:
                rls_map[row["table_name"]] = bool(row["rls_enabled"])

        cur.execute(_SQL_COLUMNS)
        tables: dict[str, Any] = {}
        for row in cur.fetchall():
            tname = row["table_name"]
            if tname in views:
                continue
            tables.setdefault(tname, {"rls": rls_map.get(tname, False), "columns": {}})
            tables[tname]["columns"][row["column_name"]] = {
                "type": row["data_type"],
                "nullable": row["is_nullable"] == "YES",
                "default": row["column_default"],
            }

        return {"views": sorted(views), "tables": tables}
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Dump / load manifest
# ─────────────────────────────────────────────────────────────────────────────

def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.exists():
        sys.stderr.write(f"ERROR: manifest not found at {path}\n")
        sys.exit(2)
    with path.open() as f:
        return yaml.safe_load(f) or {}


def dump_manifest(live: dict[str, Any], path: Path = MANIFEST_PATH) -> None:
    out: dict[str, Any] = {
        "views": sorted(live.get("views", [])),
        "tables": live.get("tables", {}),
    }
    text = yaml.safe_dump(out, sort_keys=True, default_flow_style=False)
    path.write_text(text, encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get("SUPABASE_DB_URL")
    if not dsn:
        sys.stderr.write(
            "ERROR: DATABASE_URL (or SUPABASE_DB_URL) is required. "
            "Get the Postgres connection string from the Supabase dashboard:\n"
            "  Project Settings -> Database -> Connection string -> URI\n"
        )
        sys.exit(2)
    return dsn


def main() -> int:
    parser = argparse.ArgumentParser(description="Check or dump live Supabase schema.")
    parser.add_argument("--dump", action="store_true",
                        help="Rewrite the manifest from the live schema.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH,
                        help=f"Manifest path (default: {MANIFEST_PATH}).")
    args = parser.parse_args()

    dsn = _get_dsn()
    live = fetch_live_schema_via_psycopg2(dsn)

    if args.dump:
        dump_manifest(live, args.manifest)
        print(f"Wrote {len(live.get('tables', {}))} tables + "
              f"{len(live.get('views', []))} views to {args.manifest}")
        return 0

    expected = load_manifest(args.manifest)
    drifts = diff_schemas(expected, live)

    if not drifts:
        print(f"OK: schema matches manifest "
              f"({len(expected.get('tables', {}))} tables checked)")
        return 0

    print(f"DRIFT: {len(drifts)} difference(s) between manifest and live schema:",
          file=sys.stderr)
    for d in drifts:
        print(d.format(), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
