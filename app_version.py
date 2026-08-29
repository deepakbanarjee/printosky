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


# ── Is a box running the current build? ──────────────────────────────────────
#
# OSP ran code from 21 August for eight days without a single alert. The boot
# chain reports a failed update to ops_watchdog as `store_pc.boot_update` — but
# only if AUTO_UPDATE.bat runs at all, and on OSP it never did: the box is
# started with START_PRINTOSKY.bat, which has no git in it. Nothing reports, so
# nothing alerts. Silence by construction, which is the Nattika shape again.
#
# The cloud can see it, because every box reports its running build to
# `store_devices.app_version`. These helpers are the pure half of that check;
# api/index.py's store-pc-check cron supplies the live data.


def short_sha(version: str | None) -> str | None:
    """The bare commit sha out of a reported version string.

    ``main@a1b2c3d+dirty`` -> ``a1b2c3d``. Returns None for unknown/missing, so
    a box that cannot report its version is never mistaken for a current one.
    """
    if not version:
        return None
    text = str(version).strip()
    if not text or text == UNKNOWN:
        return None
    text = text.split("@", 1)[1] if "@" in text else text
    text = text.split("+", 1)[0].strip()          # drop the +dirty marker
    return text or None


def same_build(reported: str | None, current: str | None) -> bool:
    """Do these two refer to the same commit?

    Compared on the shorter of the two prefixes: a box reports 7 characters
    while a deployment env var carries the full 40.
    """
    a, b = short_sha(reported), short_sha(current)
    if not a or not b:
        return False
    width = min(len(a), len(b))
    return a[:width].lower() == b[:width].lower()


def decide_build_staleness(*, reported, current, version_since_hours,
                           stale_after_hours, already_alerted):
    """Should we alert that this box is running old code?

    Pure, so the rule is testable without Vercel or Supabase.

    ``version_since_hours`` is how long the box has been on ``reported``;
    ``already_alerted`` is the version string we last alerted about for it
    (None if we have not). A box gets a full update cycle plus margin before it
    counts as stale — it only updates at boot, so a few hours behind is normal,
    not news.
    """
    if not short_sha(current):
        # We cannot tell what current is. Saying nothing would recreate the very
        # silence this check exists to break, so it is reported as its own fault.
        return {"stale": False, "alert": already_alerted != "unknown-current",
                "recovered": False, "key": "unknown-current",
                "reason": "cannot determine the deployed commit "
                          "(VERCEL_GIT_COMMIT_SHA missing) — build staleness is unchecked"}

    if same_build(reported, current):
        return {"stale": False, "alert": False,
                "recovered": bool(already_alerted), "key": None,
                "reason": "running the current build"}

    if not short_sha(reported):
        reason = f"is not reporting a build version ({reported or 'none'})"
    else:
        reason = (f"is running {short_sha(reported)}, the deployed build is "
                  f"{short_sha(current)}")

    if version_since_hours is not None and version_since_hours < stale_after_hours:
        # Behind, but not yet long enough — it has not missed its boot window.
        return {"stale": False, "alert": False, "recovered": False,
                "key": None, "reason": reason}

    key = short_sha(reported) or "no-version"
    return {"stale": True, "alert": already_alerted != key, "recovered": False,
            "key": key, "reason": reason}


if __name__ == "__main__":
    print(VERSION)
