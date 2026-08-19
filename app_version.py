"""Which commit is this box actually running?

A store PC keeps running whatever it last pulled (see docs/AUTO_UPDATE.md), so
"is OSP on the latest code?" was a question you could only answer by physically
getting to that PC. This module names the running version so each box can report
it to Supabase (`store_devices.app_version`) and the answer is one query away.

Format: ``main@a1b2c3d``, plus ``+dirty`` when the working tree has local edits
(a box someone hand-patched, which is worth seeing before you debug it).

**Captured once, at import — deliberately.** ``git reset`` rewrites files on
disk, but a running Python process keeps the modules it already imported in
memory. So after a mid-day pull the files say one thing and the running code
another; the import-time value is the honest one, because it is the code
actually executing. It refreshes when the process restarts, which is exactly
when the new code takes effect.
"""

from __future__ import annotations

import os
import subprocess

_ROOT = os.path.dirname(os.path.abspath(__file__))

UNKNOWN = "unknown"


def _git(*args: str) -> str | None:
    """Run a git command in the repo root. None if git or the repo is absent."""
    try:
        out = subprocess.run(
            ("git",) + args,
            cwd=_ROOT, capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return (out.stdout or "").strip()


def _read_head_fallback() -> str | None:
    """Short SHA straight out of .git, for a box with no git binary on PATH.

    Handles both a symbolic HEAD (``ref: refs/heads/main``) and a detached one.
    """
    try:
        head_path = os.path.join(_ROOT, ".git", "HEAD")
        with open(head_path, encoding="utf-8") as fh:
            head = fh.read().strip()
        if head.startswith("ref:"):
            ref = head.split(None, 1)[1].strip()
            with open(os.path.join(_ROOT, ".git", ref), encoding="utf-8") as fh:
                return fh.read().strip()[:7]
        return head[:7]                      # detached HEAD is the SHA itself
    except (OSError, IndexError):
        return None


def _compute() -> str:
    sha = _git("rev-parse", "--short", "HEAD") or _read_head_fallback()
    if not sha:
        return UNKNOWN

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    version = f"{branch}@{sha}" if branch and branch != "HEAD" else sha

    # `git status --porcelain` is empty on a clean tree. A None here means we
    # could not tell, which must not be reported as clean.
    dirty = _git("status", "--porcelain")
    if dirty:
        version += "+dirty"
    return version


VERSION = _compute()


def get_version() -> str:
    """The version string for this running process. See the module docstring."""
    return VERSION


if __name__ == "__main__":
    print(VERSION)
