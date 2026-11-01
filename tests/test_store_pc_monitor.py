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

import io
import json
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


def test_a_box_with_no_printers_of_its_own_is_not_nagged(api):
    """PRIOFF shares the counter's Epson and does not poll it, so it will never
    write a counter row. Alerting that its pipeline is dead would fire every
    30 minutes forever — and an alert channel that cries wolf is how the next
    real outage gets ignored."""
    client = _FakeClient(heartbeat=_hb(2), counters=[], status=[{"state": "up"}])
    result = _run(api, client, store_id="PRIOFF")

    assert result["online"] is True
    assert result["counters_expected"] is False
    assert result["counters_stale"] is False
    assert result["sent"] == []
    assert api._test_alerts == []


def test_a_store_with_printers_is_still_nagged(api):
    """The exemption must not leak: PRINTK owns an Epson."""
    client = _FakeClient(heartbeat=_hb(2), counters=[], status=[{"state": "up"}])
    result = _run(api, client, store_id="PRINTK")
    assert result["counters_expected"] is True
    assert result["counters_stale"] is True


def test_an_exempt_box_is_still_watched_for_liveness(api):
    client = _FakeClient(heartbeat=_hb(600), counters=[], status=[{"state": "up"}])
    result = _run(api, client, store_id="PRIOFF")
    assert result["online"] is False
    assert "offline" in result["sent"]


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


# ── The sweep's own status: a blind watchdog must not answer 200 ──────────────
#
# From 2026-10-17 the database refused every read, so _store_pc_check_one raised
# for all three stores on every 30-minute run. The handler logged each one and
# answered 200 with the errors in the body, the cron step ended in `exit 0`, and
# nothing alerted for fourteen days. These pin both halves shut.

class _CronHandler:
    """Minimal stand-in for the BaseHTTPRequestHandler the cron writes to."""

    def __init__(self):
        self.headers = {}
        self.path = "/cron/store-pc-check"
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, *a, **k):
        pass

    def end_headers(self):
        pass

    def body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


@pytest.fixture
def sweep(api, monkeypatch):
    """Run the real sweep handler with the per-store check and alerts stubbed."""
    reported = []
    # raising=False so that against a build without these helpers the status
    # assertion below is what fails, not the patch — the defect being pinned is
    # the HTTP 200, not the absence of a function.
    monkeypatch.setattr(api, "_report_store_pc_sweep_failure",
                        lambda failures: reported.append(("fail", list(failures))),
                        raising=False)
    monkeypatch.setattr(api, "_report_store_pc_sweep_ok",
                        lambda: reported.append(("ok", [])), raising=False)
    monkeypatch.setattr(api, "STORE_PC_MONITOR_IDS", ["OSP", "PRINTK"])
    monkeypatch.setenv("CRON_SECRET", "")
    import db_cloud
    monkeypatch.setattr(db_cloud, "_client", lambda: _FakeClient())
    api._test_reported = reported
    return reported


def _sweep_run(api, per_store):
    """Drive the handler with `per_store` standing in for _store_pc_check_one."""
    h = _CronHandler()
    import api.index as idx
    orig = idx._store_pc_check_one
    idx._store_pc_check_one = per_store
    try:
        idx._handle_cron_store_pc_check(h)
    finally:
        idx._store_pc_check_one = orig
    return h


def test_a_store_whose_check_raises_is_not_reported_as_success(api, sweep):
    """The defect: blind for fourteen days behind an HTTP 200."""
    def boom(c, sd, sid, *a, **k):
        raise RuntimeError("JSON could not be generated / 402 exceed_egress_quota")

    h = _sweep_run(api, boom)

    assert h.status == 503, "a sweep that saw nothing must not answer 200"
    body = h.body()
    assert len(body["failed"]) == 2
    assert "exceed_egress_quota" in body["failed"][0]
    assert ("fail", body["failed"]) in [(k, v) for k, v in sweep]


def test_one_blind_store_still_fails_the_sweep(api, sweep):
    """Partial blindness is blindness: the other store's success cannot mask it."""
    def half(c, sd, sid, *a, **k):
        if sid == "PRINTK":
            raise RuntimeError("counters lookup failed")
        return {"store_id": sid, "sent": []}

    h = _sweep_run(api, half)

    assert h.status == 503
    assert h.body()["failed"] == ["PRINTK: counters lookup failed"]
    assert [k for k, _ in sweep] == ["fail"]


def test_a_clean_sweep_answers_200_and_closes_the_check(api, sweep):
    """A store PC being *down* is this check working, and stays a 200."""
    h = _sweep_run(api, lambda c, sd, sid, *a, **k: {"store_id": sid, "sent": ["offline"]})

    assert h.status == 200
    assert "failed" not in h.body()
    assert [k for k, _ in sweep] == ["ok"], "recovery must be announced"


def test_the_sweep_failing_outright_alerts_too(api, sweep, monkeypatch):
    """No client, no store_digest — it already 500s, now it also says so."""
    import db_cloud
    monkeypatch.setattr(db_cloud, "_client",
                        lambda: (_ for _ in ()).throw(RuntimeError("no database")))

    h = _sweep_run(api, lambda *a, **k: {})

    assert h.status == 500
    assert [k for k, _ in sweep] == ["fail"]
    assert "no database" in sweep[0][1][0]
