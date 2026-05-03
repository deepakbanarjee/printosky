#!/usr/bin/env python
"""
Pre-commit syntax check for staged Python files.

Reads the list of staged .py files from `git diff --cached --name-only`,
compiles each one with `py_compile` (no execution), and exits non-zero if
any fails to parse. Designed to catch the failure mode from commit
535560f (an `except` clause placed after `finally:`) before it lands on
main.

Usage (in `.git/hooks/pre-commit`):

    #!/bin/sh
    python scripts/precommit_syntax_check.py || exit 1

The script is self-contained: it does not import project modules, so it
keeps working even if the project's own imports are broken.
"""
from __future__ import annotations

import py_compile
import subprocess
import sys
from pathlib import Path


def staged_python_files() -> list[Path]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        Path(line.strip())
        for line in result.stdout.splitlines()
        if line.strip().endswith(".py")
    ]


def main() -> int:
    files = staged_python_files()
    if not files:
        return 0

    failures: list[tuple[Path, str]] = []
    for path in files:
        if not path.is_file():
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            failures.append((path, str(exc).strip()))

    if not failures:
        return 0

    print("\nPRE-COMMIT SYNTAX CHECK FAILED", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    for path, msg in failures:
        print(f"\n{path}:", file=sys.stderr)
        print(msg, file=sys.stderr)
    print(
        "\nFix the parse errors above, then re-stage and re-commit.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
