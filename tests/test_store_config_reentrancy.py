"""Regression: the missing-config alert must actually send.

On a store PC with no store_config.json, get_store_config() alerts through
ops_watchdog.report() — and report() needs the store config itself, both for
the store id on the alert and for the health DB path. @lru_cache publishes a
result only when the call returns, so the nested lookup used to find the cache
still empty, take the same missing-file branch, and report again, until the
stack blew. report() swallowed the RecursionError and logged "could not send
alert", so the alert announcing that a machine has no config was the one alert
that never arrived — a silent failure inside the fail-loud system itself, on a
freshly-provisioned box.

These tests pin the two things that were wrong: it must not recurse, and the
alert must be delivered exactly once and must name the store rather than "?".
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest  # noqa: E402

import ops_watchdog  # noqa: E402
import store_config as sc  # noqa: E402


@pytest.fixture
def cold(tmp_path, monkeypatch):
    """A cold process that can find no config file, with alerts captured.

    Isolated the way tests/test_ops_watchdog.py does it — a scratch health DB
    and a cleared dedup memory — so one test's alert cannot be suppressed as a
    repeat of another's. `_store_id` is deliberately NOT stubbed: which store
    the alert names is part of what is under test.
    """
    sent = []
    ops_watchdog.set_db_path(str(tmp_path / "jobs.db"))
    monkeypatch.setattr(ops_watchdog, "_notify", lambda msg: sent.append(msg) or True)
    monkeypatch.setattr(ops_watchdog, "_live_store_id", None, raising=False)
    monkeypatch.setattr(sc, "_candidate_paths", lambda: [])

    # Order matters. reset() has to resolve the health-DB path, and on a
    # config-less box that resolution runs through get_store_config() — which
    # records the very alert these tests are about, leaving the dedup state
    # non-empty and the next real alert suppressed as a repeat. So prime the
    # cache first (absorbing that startup alert), then clear state, then go
    # cold again so the call inside the test is the first one that counts.
    sc.get_store_config.cache_clear()
    sc.get_store_config()
    ops_watchdog.reset()
    sc.get_store_config.cache_clear()
    sent.clear()

    yield sent
    ops_watchdog.set_db_path(None)
    ops_watchdog._memory.clear()
    sc.get_store_config.cache_clear()


class TestMissingConfigAlert:
    def test_does_not_recurse(self, cold):
        cfg = sc.get_store_config()          # used to raise RecursionError
        assert cfg.store_id == sc._LEGACY_OXYGEN_DEFAULTS["store_id"]
        assert cfg.source_path is None

    def test_alert_is_delivered_once(self, cold):
        sent = cold
        sc.get_store_config()
        assert len(sent) == 1, "the missing-config alert must reach the ops channel"

    def test_alert_names_the_store(self, cold):
        sent = cold
        sc.get_store_config()
        assert "Store: ?" not in sent[0], "alert must not fall back to an unnamed store"

    def test_config_is_still_returned_to_the_caller(self, cold):
        """The alert is a side effect; callers still get a usable fallback."""
        cfg = sc.get_store_config()
        assert cfg.printers.epson_ip, "fallback config must still carry printer addresses"

    def test_reentrant_lookup_returns_config_without_realerting(self, cold, monkeypatch):
        """A lookup from inside report() gets the fallback and does not re-alert.

        This is the exact nesting that used to recurse: report() asks for the
        config while the outer get_store_config() has not yet returned.
        """
        sent = cold
        seen = []

        def nosy_report(check, ok, detail="", **kw):
            # re-enter exactly as ops_watchdog.report() does
            seen.append(sc.get_store_config().store_id)
            return True

        monkeypatch.setattr(sc, "report", nosy_report)
        sc.get_store_config()
        assert seen == [sc._LEGACY_OXYGEN_DEFAULTS["store_id"]], (
            "nested lookup must return the fallback config, exactly once"
        )
        assert sent == [], "nested lookup must not raise a second alert"
