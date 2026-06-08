#!/usr/bin/env python3
"""
TASK-015 — Fail-fast environment variable pre-flight check.

Validates that every required env var for a deployment target is present and
roughly well-formed, BEFORE the app starts and hits a runtime exception deep
in a webhook handler. Reads the manifest at config/env_manifest.json.

Dependency-free (stdlib only) so it can run on the store PC, in a Vercel build,
or in CI without installing anything.

Usage:
    python scripts/check_env.py vercel
    python scripts/check_env.py store_pc
    python scripts/check_env.py netlify

    # Also load a .env file before checking (handy on the store PC):
    python scripts/check_env.py store_pc --dotenv .env

Exit codes:
    0  all required vars present + valid (optional vars, if present, also valid)
    1  one or more required vars missing/malformed (details printed to stderr)
    2  usage / manifest error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "config" / "env_manifest.json"


def load_dotenv_into(path: Path, env: dict) -> None:
    """Merge KEY=VALUE lines from a .env file into env (does not override
    already-set process vars)."""
    if not path.exists():
        print(f"warning: --dotenv {path} not found, skipping", file=sys.stderr)
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env.setdefault(k.strip(), v.strip())


def check_target(target: str, spec: dict, env: dict) -> tuple[list, list]:
    """Return (errors, warnings) for one target's required/optional vars."""
    errors: list[str] = []
    warnings: list[str] = []

    for entry in spec.get("required", []):
        name, pattern = entry["name"], entry["pattern"]
        val = env.get(name)
        if val is None or val == "":
            errors.append(f"MISSING required {name}")
        elif not re.match(pattern, val):
            errors.append(f"MALFORMED {name} (does not match {pattern})")

    for entry in spec.get("optional", []):
        name, pattern = entry["name"], entry["pattern"]
        val = env.get(name)
        if val:  # only validate when present
            if not re.match(pattern, val):
                warnings.append(f"MALFORMED optional {name} (does not match {pattern})")

    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description="Env var pre-flight check.")
    ap.add_argument("target", choices=["vercel", "store_pc", "netlify"])
    ap.add_argument("--dotenv", metavar="PATH",
                    help="Load this .env file before checking (does not override real env).")
    ap.add_argument("--manifest", default=str(MANIFEST),
                    help="Path to env manifest JSON (default: config/env_manifest.json).")
    args = ap.parse_args()

    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"ERROR: cannot read manifest {args.manifest}: {exc}", file=sys.stderr)
        return 2

    spec = manifest.get(args.target)
    if not spec:
        print(f"ERROR: target '{args.target}' not in manifest", file=sys.stderr)
        return 2

    env = dict(os.environ)
    if args.dotenv:
        load_dotenv_into(Path(args.dotenv), env)

    errors, warnings = check_target(args.target, spec, env)

    n_req = len(spec.get("required", []))
    for w in warnings:
        print(f"  ! {w}", file=sys.stderr)

    if errors:
        print(f"FAIL {args.target}: {len(errors)} problem(s) in {n_req} required vars:",
              file=sys.stderr)
        for e in errors:
            print(f"  x {e}", file=sys.stderr)
        return 1

    extra = f" ({len(warnings)} optional warning(s))" if warnings else ""
    print(f"OK {args.target}: all {n_req} required env vars present and valid{extra}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
