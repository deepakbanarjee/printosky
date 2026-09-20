"""
find_sla_breaches: RPC path, fallback, and the cooldown that wraps both.

The SQL/Python equivalence is proved in tests/test_sla_breaches_sql.py against a
real Postgres. This file covers the Python wrapper, which needs no database:

  * the RPC is used when it works
  * an empty RPC result means "no breaches", NOT "RPC unavailable"
  * an unavailable RPC falls back to the old sweep instead of reporting nothing
    (a deploy can land before the migration — the API deploys from a push to
    main, the SQL does not)
  * the cooldown filter applies identically on both paths

Reporting nothing would be the dangerous failure here: the SLA alert exists to
catch a customer waiting, so "no breaches" and "could not tell" must not look
the same.
"""

import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest


@pytest.fixture
def dbc():
    import db_cloud
    return db_cloud


# ─────────────────────────────────────────────────────────────────────────────
# A fake Supabase client
# ─────────────────────────────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    """Chainable no-op query that yields a fixed payload."""

    def __init__(self, rows, recorder=None, name=""):
        self._rows, self._rec, self._name = rows, recorder, name

    def __getattr__(self, _attr):
        return lambda *a, **kw: self

    def execute(self):
        if self._rec is not None:
            self._rec.append(self._name)
        return _Resp(self._rows)


class _FakeClient:
    def __init__(self, rpc_rows=None, rpc_error=None,
                 log_rows=None, contact_rows=None):
        self.rpc_rows = rpc_rows
        self.rpc_error = rpc_error
        self.log_rows = log_rows or []
        self.contact_rows = contact_rows or []
        self.calls = []
        self.rpc_args = None

    def rpc(self, name, params):
        self.calls.append(f"rpc:{name}")
        self.rpc_args = params
        if self.rpc_error is not None:
            raise self.rpc_error
        return _Query(self.rpc_rows or [])

    def table(self, name):
        rows = self.log_rows if name == "conversation_log" else self.contact_rows
        return _Query(rows, self.calls, f"table:{name}")


@pytest.fixture
def patched(dbc, monkeypatch):
    def _install(client):
        monkeypatch.setattr(dbc, "_client", lambda: client)
        return client
    return _install


# ─────────────────────────────────────────────────────────────────────────────
# The RPC path
# ─────────────────────────────────────────────────────────────────────────────

class TestRpcPath:
    def test_uses_the_rpc_and_does_not_pull_rows(self, dbc, patched):
        c = patched(_FakeClient(rpc_rows=[
            {"phone": "+911", "last_inbound_at": "2026-09-20T10:00:00+00:00"},
        ]))
        out = dbc.find_sla_breaches(alert_cooldown_hours=0)

        assert out == [{"phone": "+911",
                        "last_inbound_at": "2026-09-20T10:00:00+00:00"}]
        assert "rpc:sla_breaches" in c.calls
        assert "table:conversation_log" not in c.calls, \
            "the point of the RPC is not transferring the rows"

    def test_passes_the_tuning_parameters_through(self, dbc, patched):
        c = patched(_FakeClient(rpc_rows=[]))
        dbc.find_sla_breaches(threshold_hours=3, lookback_hours=72)
        assert c.rpc_args == {
            "threshold_hours": 3.0,
            "tolerance_seconds": float(dbc.SLA_REPLY_TOLERANCE_SECONDS),
            "lookback_hours": 72.0,
        }

    def test_empty_rpc_result_means_no_breaches(self, dbc, patched):
        """Not the same as the RPC being unavailable — must not fall back."""
        c = patched(_FakeClient(rpc_rows=[], log_rows=[
            {"phone": "+911", "direction": "inbound",
             "created_at": "2020-01-01T00:00:00+00:00"},
        ]))
        assert dbc.find_sla_breaches() == []
        assert "table:conversation_log" not in c.calls

    def test_rows_without_a_phone_are_dropped(self, dbc, patched):
        patched(_FakeClient(rpc_rows=[
            {"phone": None, "last_inbound_at": "x"},
            {"phone": "+912", "last_inbound_at": "y"},
        ]))
        assert [b["phone"] for b in dbc.find_sla_breaches(alert_cooldown_hours=0)] == ["+912"]


# ─────────────────────────────────────────────────────────────────────────────
# Falling back
# ─────────────────────────────────────────────────────────────────────────────

class TestFallback:
    def test_missing_rpc_falls_back_rather_than_reporting_nothing(self, dbc, patched, caplog):
        """The migration may not be applied yet. Silence here would read as
        'nobody is waiting', which is the one answer that must never be wrong."""
        from datetime import datetime, timezone, timedelta
        old = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        c = patched(_FakeClient(
            rpc_error=Exception('function public.sla_breaches does not exist'),
            log_rows=[{"phone": "+911", "direction": "inbound", "created_at": old}],
        ))

        out = dbc.find_sla_breaches(alert_cooldown_hours=0)

        assert [b["phone"] for b in out] == ["+911"]
        assert "table:conversation_log" in c.calls
        assert any("SCHEMA_v44" in r.getMessage() for r in caplog.records), \
            "the fallback must name the missing migration"

    def test_fallback_still_applies_the_threshold(self, dbc, patched):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        patched(_FakeClient(
            rpc_error=Exception("boom"),
            log_rows=[{"phone": "+911", "direction": "inbound", "created_at": recent}],
        ))
        assert dbc.find_sla_breaches(alert_cooldown_hours=0) == []

    def test_fallback_warns_when_it_hits_its_row_cap(self, dbc, patched, caplog):
        """The cap is lossy — a breach older than the newest N messages is
        invisible on this path. It must say so rather than look complete."""
        rows = [{"phone": f"+9{i}", "direction": "outbound",
                 "created_at": "2026-09-20T10:00:00+00:00"}
                for i in range(dbc.SLA_FALLBACK_ROW_CAP)]
        patched(_FakeClient(rpc_error=Exception("nope"), log_rows=rows))

        dbc.find_sla_breaches(alert_cooldown_hours=0)

        assert any("cap" in r.getMessage().lower() for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# Cooldown, shared by both paths
# ─────────────────────────────────────────────────────────────────────────────

class TestCooldown:
    def test_recently_alerted_phone_is_suppressed(self, dbc, patched):
        from datetime import datetime, timezone, timedelta
        just_now = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        patched(_FakeClient(
            rpc_rows=[{"phone": "+911", "last_inbound_at": "x"}],
            contact_rows=[{"phone": "+911", "last_sla_alert_at": just_now}],
        ))
        assert dbc.find_sla_breaches(alert_cooldown_hours=6) == []

    def test_phone_alerted_long_ago_is_reported_again(self, dbc, patched):
        from datetime import datetime, timezone, timedelta
        ages_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        patched(_FakeClient(
            rpc_rows=[{"phone": "+911", "last_inbound_at": "x"}],
            contact_rows=[{"phone": "+911", "last_sla_alert_at": ages_ago}],
        ))
        assert [b["phone"] for b in dbc.find_sla_breaches(alert_cooldown_hours=6)] == ["+911"]

    def test_cooldown_disabled_skips_the_contacts_lookup(self, dbc, patched):
        c = patched(_FakeClient(rpc_rows=[{"phone": "+911", "last_inbound_at": "x"}]))
        dbc.find_sla_breaches(alert_cooldown_hours=0)
        assert "table:whatsapp_contacts" not in c.calls


# ─────────────────────────────────────────────────────────────────────────────
# Total failure
# ─────────────────────────────────────────────────────────────────────────────

class TestHardFailure:
    def test_both_paths_failing_returns_empty_and_logs(self, dbc, monkeypatch, caplog):
        def boom():
            raise Exception("supabase unreachable")
        monkeypatch.setattr(dbc, "_client", boom)

        assert dbc.find_sla_breaches() == []
        assert any("find_sla_breaches" in r.getMessage() for r in caplog.records)
