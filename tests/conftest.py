"""
Pytest configuration for Printosky tests.
Sets PRINTOSKY_DB to an in-memory path so no real DB is needed.
"""
import os
import datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

os.environ.setdefault("PRINTOSKY_DB", ":memory:")

_dotenv_loaded = False


def _load_dotenv_once() -> None:
    global _dotenv_loaded
    if not _dotenv_loaded:
        load_dotenv(Path(__file__).parent.parent / ".env", override=False)
        _dotenv_loaded = True


# ── Gap report ────────────────────────────────────────────────────────────────

_gap_results: list[str] = []


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if "test_browser" not in report.nodeid:
        return
    if report.when == "call" or (report.when == "setup" and report.skipped):
        pass
    else:
        return
    test_id = report.nodeid.split("::")[-1]
    if report.skipped:
        reason = str(report.longrepr).strip()
        if "store_only" in reason.lower():
            _gap_results.append(f"[STORE_ONLY — SKIPPED] {test_id}")
        else:
            _gap_results.append(f"[SKIPPED]              {test_id}: {reason[:80]}")
    elif report.passed:
        _gap_results.append(f"[PASS]                 {test_id}")
    else:
        short = str(report.longrepr).splitlines()[0][:120] if report.longrepr else "unknown"
        if "empty" in short.lower():
            _gap_results.append(f"[WARN — EMPTY DATA]    {test_id}: {short}")
        elif "500" in short or "40" in short[:4]:
            _gap_results.append(f"[FAIL — API ERROR]     {test_id}: {short}")
        else:
            _gap_results.append(f"[FAIL]                 {test_id}: {short}")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if not _gap_results:
        return
    report_path = Path(__file__).parent / "browser_gap_report.txt"
    date_str = datetime.date.today().isoformat()
    lines = [f"=== Printosky Browser Gap Report — {date_str} ===", ""] + _gap_results + [""]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nGap report → {report_path}")


# ── store_only marker ─────────────────────────────────────────────────────────

def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    on_store_pc = os.environ.get("STORE_PC", "").lower() == "true"
    for item in items:
        if item.get_closest_marker("store_only") and not on_store_pc:
            item.add_marker(
                pytest.mark.skip(reason="store_only: set STORE_PC=true on store PC")
            )


# ── Browser fixtures ──────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def base_url() -> str:
    _load_dotenv_once()
    return os.environ.get("PRINTOSKY_BASE_URL", "https://printosky.com")


@pytest.fixture(scope="session")
def admin_password() -> str:
    _load_dotenv_once()
    pw = os.environ.get("PRINTOSKY_ADMIN_PASSWORD", "")
    assert pw, "PRINTOSKY_ADMIN_PASSWORD not set in .env"
    return pw


@pytest.fixture(scope="session")
def staff_pin() -> str:
    _load_dotenv_once()
    pin = os.environ.get("PRINTOSKY_STAFF_PIN", "")
    assert pin, "PRINTOSKY_STAFF_PIN not set in .env"
    return pin


@pytest.fixture(scope="session")
def mis_password() -> str:
    _load_dotenv_once()
    pw = os.environ.get("PRINTOSKY_MIS_PASSWORD", "")
    assert pw, "PRINTOSKY_MIS_PASSWORD not set in .env"
    return pw
