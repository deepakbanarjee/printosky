"""Regression guard: a fresh ``import api.index`` must succeed.

``api/index.py`` imports its handler modules (``handlers_admin``,
``handlers_referrals``, ``handlers_order``, …) part-way through its own module
body, and each handler imports helper symbols back *from* ``api.index``. If a
handler imports a name that is defined LOWER in ``api/index.py`` than the
handler's own import point, Python raises::

    ImportError: cannot import name X from partially initialized module
                 'api.index' (most likely due to a circular import)

…and the ENTIRE serverless API fails to boot — every endpoint 500s (order
quote/upload, WhatsApp webhook, Razorpay webhook, staff API, …).

This exact bug shipped once: ``_acad_auth_staff`` was imported at module load
in ``handlers_admin`` but defined below that import point in ``api.index``.

We import in a FRESH subprocess interpreter on purpose: by the time this test
runs, the pytest session (and conftest) have already cached ``api.index`` and
its handlers in ``sys.modules``, which would completely mask a circular-import
regression. A clean subprocess mirrors exactly how Vercel boots the function.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_api_index_imports_in_fresh_interpreter():
    proc = subprocess.run(
        [sys.executable, "-c", "import api.index"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        "`import api.index` failed in a clean interpreter — most likely a "
        "circular import that would 500 the whole API in production:\n\n"
        + proc.stderr
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lazily-imported siblings, which the test above cannot see
# ─────────────────────────────────────────────────────────────────────────────
#
# The test above boots `api.index` and so only exercises imports that run at
# module load. `api/index.py` also imports handler modules INSIDE function
# bodies, wrapped in try/except — those run only when a customer message takes
# that branch, and their failure is caught and logged rather than raised.
#
# That combination hid a live bug for as long as the feature existed. The notes
# marketplace was imported as `from handlers_notes import ...` while every other
# handler uses `from api.handlers_... import ...`. Only the repo ROOT is on
# sys.path, so the bare name never resolved: every inbound WhatsApp message
# logged `No module named 'handlers_notes'`, the whole feature was dead in
# production, and nothing failed loudly enough to notice. It was found by
# reading Vercel's runtime logs, not by a test.
#
# Parsing the source is what catches it. Importing `api.index` will not: the
# broken import sits in a branch no test takes, and its except clause would
# swallow the error even then.

import ast
import os


def _api_sibling_modules() -> set[str]:
    api_dir = REPO_ROOT / "api"
    return {f[:-3] for f in os.listdir(api_dir)
            if f.endswith(".py") and f != "index.py"}


def test_every_api_sibling_is_imported_with_the_api_prefix():
    siblings = _api_sibling_modules()
    tree = ast.parse((REPO_ROOT / "api" / "index.py").read_text(encoding="utf-8"))

    offenders = [
        (node.lineno, node.module)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.module.split(".")[0] in siblings
    ]

    assert not offenders, (
        "api/index.py imports an api/ sibling by its bare name. Only the repo "
        "root is on sys.path, so this raises ModuleNotFoundError in production "
        "— and inside a try/except it does so silently, on every request that "
        "reaches it. Use `from api.<module> import ...`:\n"
        + "\n".join(f"  line {lineno}: from {mod} import ..." for lineno, mod in offenders)
    )


def test_the_api_sibling_scan_can_actually_fail():
    """The guard above passes trivially if the scan finds nothing to look at.

    A rule that cannot fail is not a rule — this run pinned three separate
    faults whose common shape was a green light computed over an empty set, so
    assert the inputs are non-empty and that the detection works on a known-bad
    sample.
    """
    siblings = _api_sibling_modules()
    assert "handlers_notes" in siblings, (
        "the sibling scan found no handlers_notes — it is scanning the wrong "
        "directory, and the guard above is passing over nothing"
    )

    bad = ast.parse("def f():\n    from handlers_notes import x\n")
    caught = [n.module for n in ast.walk(bad)
              if isinstance(n, ast.ImportFrom) and n.module
              and n.module.split(".")[0] in siblings]
    assert caught == ["handlers_notes"], (
        "the scan missed a bare sibling import nested inside a function body, "
        "which is exactly where the real one was hiding"
    )
