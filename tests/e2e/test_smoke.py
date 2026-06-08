"""
TASK-017: pytest wrapper around scripts/smoke.py.

This hits a live deployment, so it is SKIPPED by default — set RUN_SMOKE=1 to
run it (CI does this in the dedicated smoke workflow; normal `pytest tests/`
offline stays green). Target URL via PRINTOSKY_SMOKE_URL (default production).

    RUN_SMOKE=1 pytest tests/e2e/test_smoke.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

RUN = os.environ.get("RUN_SMOKE") == "1"
pytestmark = pytest.mark.skipif(not RUN, reason="set RUN_SMOKE=1 to run live smoke checks")


def test_required_smoke_checks_pass() -> None:
    import smoke

    url = os.environ.get("PRINTOSKY_SMOKE_URL", smoke.DEFAULT_URL)
    results = smoke.run_checks(url, dict(os.environ))
    failed = [f"{r.name}: {r.detail}" for r in results if r.required and not r.ok]
    assert not failed, "smoke checks failed:\n" + "\n".join(failed)
