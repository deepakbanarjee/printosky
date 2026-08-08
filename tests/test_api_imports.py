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
