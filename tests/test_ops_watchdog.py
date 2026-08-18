"""
The fail-loud rule (ops_watchdog).

Nattika went dark for seven days without a single alert. These tests pin the
behaviour that makes that impossible: the first failure alerts immediately, a
long outage keeps reminding, recovery is announced, and the watchdog itself
never takes down the code it is watching.
"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import ops_watchdog as ow


@pytest.fixture
def wd(tmp_path, monkeypatch):
    """Watchdog on a scratch DB with alerts captured instead of sent."""
    sent = []
    ow.set_db_path(str(tmp_path / "jobs.db"))
    ow.reset()
    monkeypatch.setattr(ow, "_notify", lambda msg: sent.append(msg) or True)
    monkeypatch.setattr(ow, "_store_id", lambda: "PRINTK")
    yield sent
    ow.set_db_path(None)
    ow._memory.clear()


# ── Alert edges ───────────────────────────────────────────────────────────────

def test_first_failure_alerts_immediately(wd):
    """Owner's call 2026-08-18: no sustain window, no store-hours gate."""
    assert ow.report("printer.epson", False, "unreachable at 192.168.1.250") is True
    assert len(wd) == 1
    assert "printer.epson" in wd[0] and "FAILED" in wd[0]
    assert "192.168.1.250" in wd[0]
    assert "PRINTK" in wd[0]


def test_healthy_check_is_not_news(wd):
    assert ow.report("printer.epson", True) is False
    assert wd == []


def test_repeated_failures_do_not_re_alert_every_cycle(wd):
    """A 5-minute poll must not become 288 WhatsApps a day."""
    ow.report("printer.epson", False, "unreachable")
    for _ in range(20):
        ow.report("printer.epson", False, "unreachable")
    assert len(wd) == 1


def test_a_long_outage_re_alerts_after_the_repeat_window(wd, monkeypatch):
    ow.report("printer.epson", False, "unreachable")
    assert len(wd) == 1

    # Pretend the first alert went out longer ago than the repeat window.
    state = ow._load("printer.epson")
    state["last_alert_at"] = (datetime.now() - timedelta(hours=ow.REPEAT_HOURS + 1)).isoformat(" ")
    state["since"] = (datetime.now() - timedelta(days=7)).isoformat(" ")
    ow._save(state)

    assert ow.report("printer.epson", False, "unreachable") is True
    assert "STILL FAILING" in wd[1]
    assert "7 days" in wd[1]


def test_repeat_can_be_switched_off(wd, monkeypatch):
    monkeypatch.setattr(ow, "REPEAT_HOURS", 0)
    ow.report("x.check", False, "boom")
    state = ow._load("x.check")
    state["last_alert_at"] = (datetime.now() - timedelta(days=30)).isoformat(" ")
    ow._save(state)
    assert ow.report("x.check", False, "boom") is False
    assert len(wd) == 1


def test_recovery_is_announced_once(wd):
    ow.report("sync.supabase", False, "upserts failing")
    assert ow.report("sync.supabase", True, "sync OK") is True
    assert "recovered" in wd[1]
    # ...and a second healthy report is silent.
    assert ow.report("sync.supabase", True, "sync OK") is False
    assert len(wd) == 2


def test_each_check_alerts_independently(wd):
    ow.report("printer.epson", False, "unreachable")
    ow.report("sync.supabase", False, "dead")
    assert len(wd) == 2
    assert ow.health()["failing"] == ["printer.epson", "sync.supabase"]


# ── State survives a restart ──────────────────────────────────────────────────

def test_state_persists_so_a_restart_does_not_re_spam(wd, tmp_path):
    ow.report("printer.epson", False, "unreachable")
    assert len(wd) == 1
    ow._memory.clear()                     # simulate the store PC rebooting
    ow.report("printer.epson", False, "unreachable")
    assert len(wd) == 1, "restart re-sent an alert that was already out"


def test_state_persists_so_a_restart_still_reports_recovery(wd):
    ow.report("printer.epson", False, "unreachable")
    ow._memory.clear()
    assert ow.report("printer.epson", True, "reachable") is True
    assert "recovered" in wd[-1]


# ── guard() ───────────────────────────────────────────────────────────────────

def test_guard_alerts_on_exception_and_reraises(wd):
    with pytest.raises(ValueError):
        with ow.guard("epson.weblog"):
            raise ValueError("login failed")
    assert "epson.weblog" in wd[0]
    assert "login failed" in wd[0]


def test_guard_can_swallow_where_the_caller_must_not_crash(wd):
    with ow.guard("epson.weblog", reraise=False):
        raise RuntimeError("timeout")
    assert len(wd) == 1          # swallowed, but someone was told


def test_guard_reports_success_and_clears_a_failure(wd):
    with ow.guard("epson.weblog", reraise=False):
        raise RuntimeError("timeout")
    with ow.guard("epson.weblog"):
        pass
    assert "recovered" in wd[-1]
    assert ow.health()["healthy"] is True


# ── The watchdog must never break its caller ──────────────────────────────────

def test_report_never_raises_even_with_a_broken_backend(wd, monkeypatch):
    monkeypatch.setattr(ow, "_load", lambda check: (_ for _ in ()).throw(sqlite_error()))
    assert ow.report("printer.epson", False, "unreachable") is False   # logged, not raised


def test_report_never_raises_when_the_alert_channel_is_down(tmp_path, monkeypatch):
    """No WhatsApp, no internet — the caller still must not see an exception,
    and the failure must still be recorded so /health and the console show it."""
    ow.set_db_path(str(tmp_path / "jobs.db"))
    ow.reset()
    monkeypatch.setattr(ow, "_notify", lambda msg: (_ for _ in ()).throw(RuntimeError("no wifi")))
    try:
        assert ow.report("printer.epson", False, "unreachable") is True
        assert ow.health()["failing"] == ["printer.epson"]
    finally:
        ow.set_db_path(None)
        ow._memory.clear()


def test_health_reports_a_failing_check_with_its_age(wd):
    ow.report("printer.epson", False, "unreachable at 192.168.1.250")
    h = ow.health()
    assert h["healthy"] is False
    assert h["failing"] == ["printer.epson"]
    entry = h["checks"]["printer.epson"]
    assert entry["ok"] is False
    assert entry["detail"] == "unreachable at 192.168.1.250"
    assert entry["for"] is not None


def sqlite_error():
    import sqlite3
    return sqlite3.OperationalError("database is locked")


# ── Optional quiet hours (off by default) ─────────────────────────────────────

def test_alerts_are_immediate_by_default_including_overnight(wd, monkeypatch):
    """Default config has no quiet window — the owner asked for it that way."""
    assert ow.QUIET_HOURS == ""
    assert ow.report("printer.epson", False, "unreachable") is True


def test_quiet_hours_hold_a_failure_then_release_it(wd, monkeypatch):
    monkeypatch.setattr(ow, "QUIET_HOURS", "21-8")
    monkeypatch.setattr(ow, "_in_quiet_hours", lambda now=None: True)
    assert ow.report("printer.epson", False, "powered off overnight") is False
    assert wd == []
    assert ow.health()["failing"] == ["printer.epson"], "held alert must still show red"

    monkeypatch.setattr(ow, "_in_quiet_hours", lambda now=None: False)
    assert ow.report("printer.epson", False, "still unreachable at 09:05") is True
    assert "FAILED" in wd[0]


def test_quiet_hours_never_hold_a_recovery(wd, monkeypatch):
    ow.report("printer.epson", False, "unreachable")
    monkeypatch.setattr(ow, "_in_quiet_hours", lambda now=None: True)
    assert ow.report("printer.epson", True, "reachable") is True
    assert "recovered" in wd[-1]


@pytest.mark.parametrize("window,hour,expected", [
    ("21-8", 23, True), ("21-8", 3, True), ("21-8", 12, False), ("21-8", 8, False),
    ("13-14", 13, True), ("13-14", 15, False),
    ("garbage", 3, False),
])
def test_quiet_window_parsing(monkeypatch, window, hour, expected):
    monkeypatch.setattr(ow, "QUIET_HOURS", window)
    assert ow._in_quiet_hours(datetime(2026, 8, 18, hour, 0)) is expected


# ── Storage safety ────────────────────────────────────────────────────────────

def test_a_store_path_that_does_not_exist_falls_back_to_memory(monkeypatch, tmp_path):
    """The store default is a Windows path. On a dev box or in CI, SQLite would
    otherwise create a file literally named 'C:\\Printosky\\Data\\jobs.db' in the
    working directory — which is exactly what happened once."""
    ow.set_db_path(None)
    monkeypatch.delenv("PRINTOSKY_DB", raising=False)
    monkeypatch.setattr(ow, "_configured_db_path", lambda: r"C:\Printosky\Data\jobs.db")
    assert ow._db_path() is None

    sent = []
    monkeypatch.setattr(ow, "_notify", lambda m: sent.append(m) or True)
    ow._memory.clear()
    assert ow.report("printer.epson", False, "unreachable") is True   # still works
    assert ow.health()["failing"] == ["printer.epson"]                # from memory
    assert not os.path.exists(r"C:\Printosky\Data\jobs.db")
    ow._memory.clear()


def test_a_real_directory_is_used(tmp_path, monkeypatch):
    monkeypatch.delenv("PRINTOSKY_DB", raising=False)
    ow.set_db_path(None)
    monkeypatch.setattr(ow, "_configured_db_path", lambda: str(tmp_path / "jobs.db"))
    assert ow._db_path() == str(tmp_path / "jobs.db")
