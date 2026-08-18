"""
Cloud-side store monitoring (api/index._store_pc_check_one).

Two gaps this pins shut, both proven by the August 2026 Nattika outage:

  1. The cron watched a single store id (OSP). PRINTK and PRIOFF could die
     without anything firing, because they were never looked at.
  2. Liveness alone is not health. Through that whole week Nattika's PC was up
     and syncing its daily_summary every few minutes — while its printer
     counters had been frozen since the 11th. A heartbeat check would have said
     "fine". The counters-stale check is what actually catches it.
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest


NOW = datetime(2026, 8, 18, 12, 30, 0)


class _FakeTable:
    """Minimal PostgREST-ish chain over canned rows."""

    def __init__(self, rows, sink=None, name=""):
        self._rows, self._sink, self._name = rows, sink, name

    def select(self, *a, **k):  return self
    def eq(self, *a, **k):      return self
    def order(self, *a, **k):   return self
    def limit(self, *a, **k):   return self

    def execute(self):
        return MagicMock(data=self._rows)

    def upsert(self, payload):
        if self._sink is not None:
            self._sink.append(payload)
        return self


class _FakeClient:
    def __init__(self, heartbeat=None, counters=None, status=None, writes=None):
        self._t = {
            "daily_summary":    heartbeat if heartbeat is not None else [],
            "printer_counters": counters if counters is not None else [],
            "store_pc_status":  status if status is not None else [],
        }
        self.writes = writes if writes is not None else []

    def table(self, name):
        return _FakeTable(self._t.get(name, []), sink=self.writes, name=name)


@pytest.fixture
def api(monkeypatch):
    import api.index as idx
    alerts = []
    monkeypatch.setattr(idx, "_alert_ops",
                        lambda summary, fallback: alerts.append((summary, fallback)) or True)
    idx._test_alerts = alerts
    return idx


def _hb(minutes_ago):
    ts = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return [{"synced_at": ts}]


def _counters(minutes_ago):
    ts = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return [{"polled_at": ts}]


def _run(idx, client, store_id="PRINTK", prev_state="up", digest=False):
    import store_digest as sd
    return idx._store_pc_check_one(client, sd, store_id, NOW,
                                   NOW.isoformat(), digest=digest)


# ── Every store is watched, not just the digest one ───────────────────────────

def test_secondary_stores_are_monitored_by_default(api):
    assert "PRINTK" in api.STORE_PC_MONITOR_IDS
    assert "PRIOFF" in api.STORE_PC_MONITOR_IDS
    assert api.STORE_PC_MONITOR_ID in api.STORE_PC_MONITOR_IDS


# ── The outage that hid: PC alive, printers dead ──────────────────────────────

def test_live_pc_with_frozen_counters_alerts(api):
    """PRINTK on 18 Aug: heartbeat 2 minutes old, counters 7 days old."""
    client = _FakeClient(heartbeat=_hb(2), counters=_counters(7 * 1440),
                         status=[{"state": "up"}])
    result = _run(api, client)

    assert result["online"] is True
    assert result["counters_stale"] is True
    assert result["state"] == "up_stale"
    assert "counters_stale" in result["sent"]
    summary, body = api._test_alerts[-1]
    assert "PRINTK" in summary
    assert "7 days" in body
    assert "IP changed" in body


def test_the_stale_alert_is_sent_once_not_every_run(api):
    client = _FakeClient(heartbeat=_hb(2), counters=_counters(7 * 1440),
                         status=[{"state": "up_stale"}])
    result = _run(api, client)
    assert result["counters_stale"] is True
    assert result["sent"] == []          # already told, state unchanged


def test_counters_flowing_again_is_announced(api):
    client = _FakeClient(heartbeat=_hb(2), counters=_counters(4),
                         status=[{"state": "up_stale"}])
    result = _run(api, client)
    assert result["counters_stale"] is False
    assert "counters_ok" in result["sent"]
    assert "flowing again" in api._test_alerts[-1][1]


def test_a_healthy_store_says_nothing(api):
    client = _FakeClient(heartbeat=_hb(3), counters=_counters(5),
                         status=[{"state": "up"}])
    result = _run(api, client)
    assert result["sent"] == []
    assert result["state"] == "up"
    assert api._test_alerts == []


def test_a_store_that_never_wrote_a_counter_is_stale(api):
    client = _FakeClient(heartbeat=_hb(2), counters=[], status=[{"state": "up"}])
    result = _run(api, client)
    assert result["counters_stale"] is True
    assert "never" in api._test_alerts[-1][1]


# ── PC liveness for non-digest stores ─────────────────────────────────────────

def test_offline_secondary_store_gets_a_plain_alert_not_a_digest(api):
    client = _FakeClient(heartbeat=_hb(600), counters=_counters(600),
                         status=[{"state": "up"}])
    result = _run(api, client)
    assert result["online"] is False
    assert "offline" in result["sent"]
    summary, body = api._test_alerts[-1]
    assert "PRINTK store PC offline" == summary
    assert "10.0 h" in body


def test_secondary_store_coming_back_is_announced(api):
    client = _FakeClient(heartbeat=_hb(1), counters=_counters(1),
                         status=[{"state": "down"}])
    result = _run(api, client)
    assert "online" in result["sent"]
    assert "back online" in api._test_alerts[-1][1]


def test_an_offline_store_is_not_also_reported_as_stale(api):
    """One outage, one alert: a PC that is off has frozen counters by definition."""
    client = _FakeClient(heartbeat=_hb(600), counters=_counters(600),
                         status=[{"state": "up"}])
    result = _run(api, client)
    assert result["counters_stale"] is False
    assert [s for s in result["sent"]] == ["offline"]


# ── State written back ────────────────────────────────────────────────────────

def test_state_is_persisted_for_dedup(api):
    writes = []
    client = _FakeClient(heartbeat=_hb(2), counters=_counters(9999),
                         status=[{"state": "up"}], writes=writes)
    _run(api, client)
    assert writes and writes[-1]["state"] == "up_stale"
    assert writes[-1]["store_id"] == "PRINTK"
