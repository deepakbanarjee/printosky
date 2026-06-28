"""
Pytest configuration for Printosky tests.
Sets PRINTOSKY_DB to an in-memory path so no real DB is needed.
"""
import os
import sys
import datetime
from pathlib import Path

import pytest
from dotenv import load_dotenv

os.environ.setdefault("PRINTOSKY_DB", ":memory:")

# razorpay_integration.py reads these at *import* time (os.environ["..."]) and
# raises KeyError if unset. Without them the pre-import loop below fails silently,
# leaving razorpay_integration absent from sys.modules — so the first test file
# using the `if mod not in sys.modules: ModuleType(mod)` guard installs an empty
# stub that pollutes razorpay/webhook/wa-cost tests for the whole run. Set
# dummies first so the real module imports and every guard becomes a no-op.
os.environ.setdefault("RAZORPAY_KEY_ID", "test_key")
os.environ.setdefault("RAZORPAY_KEY_SECRET", "test_secret")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")

# Force the SQLite path in whatsapp_bot.py for unit tests. whatsapp_bot.py
# does an import-time check on SUPABASE_URL and rebinds its module-level
# save_session/get_session/clear_session to db_cloud.* if set. Tests that
# exercise the SQLite session CRUD (test_bot_sessions, test_session_timeout,
# etc.) need the SQLite path. Cloud-path tests use db_cloud directly.
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)
os.environ.pop("SUPABASE_SERVICE_KEY", None)

# Ensure repo root is on sys.path so the pre-imports below resolve the local
# modules in this repo (not a different installed package of the same name).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Pre-import real local modules before any test file's module-level code runs.
#
# Several test files (test_security_bugs.py, test_help_escape.py,
# test_webhook_idempotency.py) stub heavy dependencies via the pattern:
#
#     if _mod not in sys.modules:
#         sys.modules[_mod] = types.ModuleType(_mod)
#
# Without this pre-import, pytest's alphabetical collection means those stubs
# poison sys.modules for files like test_razorpay.py that depend on the *real*
# module. Pre-importing here caches the real modules first; the stub guard then
# correctly becomes a no-op.
#
# Wrapped in try/except so modules with heavy non-installed deps (e.g.
# `db_cloud` imports `supabase`) silently fall through and remain stubbable.
#
# Order matters: razorpay_integration calls load_dotenv() at module load time,
# which re-reads .env and re-sets SUPABASE_URL. whatsapp_bot.py then checks
# SUPABASE_URL at *its* import time to decide between SQLite (store PC) and
# Supabase (Vercel) bindings. For unit tests we want the SQLite path, so we
# import dotenv-loading modules first, re-pop SUPABASE_URL, *then* import
# whatsapp_bot and friends.
for _real_mod in (
    "razorpay_integration",
    "whatsapp_notify",
    "webhook_receiver",
    "webhook_checker",
    "review_manager",
    "rate_card",
):
    try:
        __import__(_real_mod)
    except Exception:
        pass

# razorpay_integration's load_dotenv() may have repopulated SUPABASE_URL.
# Strip it again so the SUPABASE_URL-gated import block in whatsapp_bot.py
# (line ~954) binds the SQLite-mode session functions, not the cloud ones.
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)
os.environ.pop("SUPABASE_SERVICE_KEY", None)

for _real_mod in (
    "whatsapp_bot",
    "db_cloud",
    "db_cloud_academic",
    "academic_whatsapp",
):
    try:
        __import__(_real_mod)
    except Exception:
        pass

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
    print(f"\nGap report -> {report_path}")


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
