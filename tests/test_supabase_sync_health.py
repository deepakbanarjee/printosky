"""Tests for supabase_sync health/heartbeat — the silent-failure hardening.

Background: the store-PC -> Supabase sync went dark for ~2 months and nobody
noticed, because a disabled/never-started sync only logged at INFO and returned.
get_sync_status() now makes staleness queryable so a health check can alert.

Run: pytest tests/test_supabase_sync_health.py -v
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import supabase_sync as ss  # noqa: E402


def _reset():
    ss._last_successful_sync = None
    ss._last_sync_error = None


class TestGetSyncStatus:
    def test_not_configured_is_unhealthy(self, monkeypatch):
        _reset()
        monkeypatch.setattr(ss, "SUPABASE_URL", "")
        monkeypatch.setattr(ss, "SUPABASE_KEY", "")
        st = ss.get_sync_status()
        assert st["configured"] is False
        assert st["healthy"] is False
        assert st["last_success"] is None

    def test_configured_but_never_synced_is_unhealthy(self, monkeypatch):
        _reset()
        monkeypatch.setattr(ss, "SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setattr(ss, "SUPABASE_KEY", "anon")
        st = ss.get_sync_status()
        # This is exactly the "silent" state: configured, but no successful sync.
        assert st["configured"] is True
        assert st["healthy"] is False
        assert st["age_seconds"] is None

    def test_recent_success_is_healthy(self, monkeypatch):
        _reset()
        monkeypatch.setattr(ss, "SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setattr(ss, "SUPABASE_KEY", "anon")
        ss._record_sync_success()
        st = ss.get_sync_status()
        assert st["healthy"] is True
        assert st["last_error"] is None
        assert st["age_seconds"] < 5

    def test_stale_success_is_unhealthy(self, monkeypatch):
        _reset()
        monkeypatch.setattr(ss, "SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setattr(ss, "SUPABASE_KEY", "anon")
        # Last success older than 2 intervals -> stale -> unhealthy (would have
        # surfaced the 2-month outage instead of hiding it).
        ss._last_successful_sync = datetime.now() - timedelta(seconds=ss.SYNC_INTERVAL * 3)
        st = ss.get_sync_status()
        assert st["healthy"] is False
        assert st["age_seconds"] > ss.SYNC_INTERVAL * 2

    def test_failure_is_recorded_and_clears_on_success(self, monkeypatch):
        _reset()
        monkeypatch.setattr(ss, "SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setattr(ss, "SUPABASE_KEY", "anon")
        ss._record_sync_failure("boom: connection reset")
        assert ss.get_sync_status()["last_error"] == "boom: connection reset"
        ss._record_sync_success()
        assert ss.get_sync_status()["last_error"] is None


class TestStartSyncLoudWhenDisabled:
    def test_disabled_logs_at_error(self, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(ss, "SUPABASE_URL", "")
        monkeypatch.setattr(ss, "SUPABASE_KEY", "")
        with caplog.at_level(logging.ERROR, logger="supabase_sync"):
            result = ss.start_sync("ignored.db")
        assert result is None
        # Must be loud (ERROR), not a silent INFO line.
        assert any(r.levelno >= logging.ERROR and "DISABLED" in r.message.upper()
                   for r in caplog.records), "disabled sync must log at ERROR"


class TestPresenceReporting:
    """`store_devices` sat empty because device_lease.heartbeat() was written
    but never called — so docs/MULTI_BOX.md's "it registers in store_devices"
    was not true, and nobody could tell which commit a store PC was running
    without standing in front of it. sync_once now reports presence + version.
    """

    def test_sync_reports_presence_with_the_running_version(self, monkeypatch):
        seen = {}

        def fake_heartbeat(app_version=None):
            seen["version"] = app_version
            return True

        monkeypatch.setitem(sys.modules, "device_lease",
                            type(sys)("device_lease"))
        sys.modules["device_lease"].heartbeat = fake_heartbeat

        ss._report_presence()
        assert "version" in seen, "sync did not register this box in store_devices"
        assert seen["version"], "registered without naming the running commit"

    def test_presence_failure_does_not_break_the_data_sync(self, monkeypatch):
        """A box that cannot announce itself must still push its jobs."""
        def boom(app_version=None):
            raise RuntimeError("supabase unreachable")

        monkeypatch.setitem(sys.modules, "device_lease",
                            type(sys)("device_lease"))
        sys.modules["device_lease"].heartbeat = boom

        ss._report_presence()   # must not raise
