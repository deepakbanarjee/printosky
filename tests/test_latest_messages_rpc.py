"""
_latest_messages: RPC path, fallback, and how chat_audit_snapshot reads it.

SQL/Python equivalence is proved in tests/test_latest_messages_sql.py against a
real Postgres. This file covers the wrapper, which needs no database.

The failure that matters here is the opposite of the SLA one. chat_audit_snapshot
treats an absent newest message as "no reply yet", so losing a phone's row turns
a chat a human already answered into one reported as still waiting, with its
needs_human flag left set. So "no messages for this phone" and "could not look"
must not be reachable by accident.
"""

import os
import sys

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
    def __init__(self, rows, recorder=None, name=""):
        self._rows, self._rec, self._name = rows, recorder, name
        self.filters = {}
        self.eq_filters = {}

    def in_(self, col, vals):
        self.filters[col] = list(vals)
        return self

    def eq(self, col, val):
        self.eq_filters[col] = val
        return self

    def __getattr__(self, _attr):
        return lambda *a, **kw: self

    def execute(self):
        if self._rec is not None:
            self._rec.append((self._name, dict(self.filters)))
        return _Resp(self._rows)


class _FakeClient:
    def __init__(self, rpc_rows=None, rpc_error=None, log_rows=None):
        self.rpc_rows = rpc_rows
        self.rpc_error = rpc_error
        self.log_rows = log_rows or []
        self.calls = []
        self.rpc_args = None
        self.last_query = None

    def rpc(self, name, params):
        self.calls.append((f"rpc:{name}", {}))
        self.rpc_args = params
        if self.rpc_error is not None:
            raise self.rpc_error
        return _Query(self.rpc_rows or [])

    def table(self, name):
        q = _Query(self.log_rows, self.calls, f"table:{name}")
        self.last_query = q
        return q

    def names(self):
        return [c[0] for c in self.calls]


def _msg(phone, direction="inbound", body="hi"):
    return {"phone": phone, "direction": direction, "body": body,
            "created_at": "2026-09-20T10:00:00+00:00"}


# ─────────────────────────────────────────────────────────────────────────────
# The RPC path
# ─────────────────────────────────────────────────────────────────────────────

class TestRpcPath:
    def test_uses_the_rpc_and_does_not_scan_the_log(self, dbc):
        c = _FakeClient(rpc_rows=[_msg("+911", "outbound", "sorted")])
        out = dbc._latest_messages(c, ["+911"], 336)

        assert out["+911"]["direction"] == "outbound"
        assert "rpc:latest_messages" in c.names()
        assert "table:conversation_log" not in c.names()

    def test_passes_phones_and_window(self, dbc):
        c = _FakeClient(rpc_rows=[])
        dbc._latest_messages(c, ["+911", "+912"], 48)
        assert c.rpc_args == {"phones": ["+911", "+912"], "lookback_hours": 48.0}

    def test_empty_rpc_result_is_a_real_answer(self, dbc):
        """No messages for the flagged phones. Must not fall back — falling back
        would scan the log to rediscover the same nothing."""
        c = _FakeClient(rpc_rows=[], log_rows=[_msg("+911")])
        assert dbc._latest_messages(c, ["+911"], 336) == {}
        assert "table:conversation_log" not in c.names()

    def test_rows_without_a_phone_are_dropped(self, dbc):
        c = _FakeClient(rpc_rows=[_msg(None), _msg("+912")])
        assert list(dbc._latest_messages(c, ["+912"], 336)) == ["+912"]


# ─────────────────────────────────────────────────────────────────────────────
# Falling back
# ─────────────────────────────────────────────────────────────────────────────

class TestFallback:
    def test_missing_rpc_falls_back_and_names_the_migration(self, dbc, caplog):
        c = _FakeClient(
            rpc_error=Exception("function public.latest_messages does not exist"),
            log_rows=[_msg("+911", "outbound", "done")],
        )
        out = dbc._latest_messages(c, ["+911"], 336)

        assert out["+911"]["body"] == "done"
        assert "table:conversation_log" in c.names()
        assert any("SCHEMA_v46" in r.getMessage() for r in caplog.records)

    def test_fallback_now_filters_by_phone(self, dbc):
        """New in this change: the old query had no phone filter at all, so it
        read every number's messages to answer about a handful. Even without
        the migration applied, the fallback is less lossy than what it replaced."""
        c = _FakeClient(rpc_error=Exception("nope"), log_rows=[_msg("+911")])
        dbc._latest_messages(c, ["+911", "+912"], 336)

        log_calls = [f for name, f in c.calls if name == "table:conversation_log"]
        assert log_calls and log_calls[0].get("phone") == ["+911", "+912"]

    def test_fallback_is_whatsapp_only(self, dbc):
        """SCHEMA_v45 put Instagram in conversation_log with an IGSID in the
        phone column. Both paths stay WhatsApp-only; if only the RPC carried
        the filter, an unmigrated deploy would read the wrong channel."""
        c = _FakeClient(rpc_error=Exception("nope"), log_rows=[_msg("+911")])
        dbc._latest_messages(c, ["+911"], 336)

        q = c.last_query
        assert q.eq_filters.get("channel") == "whatsapp"

    def test_fallback_keeps_the_newest_row_per_phone(self, dbc):
        """Rows arrive newest-first; the first seen for a phone wins."""
        c = _FakeClient(rpc_error=Exception("nope"), log_rows=[
            _msg("+911", "outbound", "newest"),
            _msg("+911", "inbound", "older"),
        ])
        out = dbc._latest_messages(c, ["+911"], 336)
        assert out["+911"]["body"] == "newest"

    def test_fallback_warns_at_its_row_cap(self, dbc, caplog):
        rows = [_msg(f"+9{i}") for i in range(dbc.LATEST_MESSAGES_FALLBACK_ROW_CAP)]
        c = _FakeClient(rpc_error=Exception("nope"), log_rows=rows)

        dbc._latest_messages(c, ["+90"], 336)

        assert any("cap" in r.getMessage().lower() for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# Doing no work when there is none
# ─────────────────────────────────────────────────────────────────────────────

class TestDegradedIsReported:
    """A log line is not an alert (docs/FAIL_LOUD.md).

    Without this, an unapplied migration leaves the sweep quietly lossy for as
    long as nobody reads the Vercel logs — which is the whole failure mode this
    PR exists to remove.
    """

    def setup_method(self):
        import db_cloud
        db_cloud.take_degraded()          # start from a clean slate

    def test_fallback_records_a_degradation(self, dbc):
        c = _FakeClient(rpc_error=Exception("nope"), log_rows=[_msg("+911")])
        dbc._latest_messages(c, ["+911"], 336)

        noted = dbc.take_degraded()
        assert len(noted) == 1
        assert "SCHEMA_v46" in noted[0]
        assert "still waiting" in noted[0], "it should say what goes wrong, not just that it did"

    def test_the_rpc_path_records_nothing(self, dbc):
        c = _FakeClient(rpc_rows=[_msg("+911")])
        dbc._latest_messages(c, ["+911"], 336)
        assert dbc.take_degraded() == []

    def test_draining_clears_it(self, dbc):
        c = _FakeClient(rpc_error=Exception("nope"), log_rows=[_msg("+911")])
        dbc._latest_messages(c, ["+911"], 336)
        assert dbc.take_degraded() != []
        assert dbc.take_degraded() == [], "a drained note must not repeat next run"

    def test_repeated_failures_note_it_once(self, dbc):
        c = _FakeClient(rpc_error=Exception("nope"), log_rows=[_msg("+911")])
        dbc._latest_messages(c, ["+911"], 336)
        dbc._latest_messages(c, ["+912"], 336)
        assert len(dbc.take_degraded()) == 1


class TestNoWork:
    def test_no_flagged_phones_does_no_io(self, dbc):
        c = _FakeClient(rpc_rows=[_msg("+911")])
        assert dbc._latest_messages(c, [], 336) == {}
        assert c.names() == []

    def test_all_phones_blank_does_no_io(self, dbc):
        c = _FakeClient(rpc_rows=[])
        assert dbc._latest_messages(c, [None, ""], 336) == {}
        assert c.names() == []


class TestNeverRaises:
    def test_both_paths_failing_returns_empty_and_logs(self, dbc, caplog):
        class _Broken:
            def rpc(self, *a, **kw):
                raise Exception("rpc down")

            def table(self, *a, **kw):
                raise Exception("rest down")

        assert dbc._latest_messages(_Broken(), ["+911"], 336) == {}
        assert any("last-message" in r.getMessage() for r in caplog.records)


# ─────────────────────────────────────────────────────────────────────────────
# What chat_audit_snapshot does with the answer
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotClassification:
    @pytest.fixture
    def snapshot(self, dbc, monkeypatch):
        """Run chat_audit_snapshot with one flagged phone and a chosen last
        message, stubbing the parts this test is not about."""
        def _go(last_message):
            sessions = [{"phone": "+911", "step": "staff_hold",
                         "needs_human": True,
                         "last_help_request_at": "2026-09-20T08:00:00+00:00",
                         "updated_at": "2026-09-20T08:00:00+00:00"}]

            class _C:
                def table(self, _n):
                    return _Query(sessions)

            monkeypatch.setattr(dbc, "_client", lambda: _C())
            monkeypatch.setattr(dbc, "_latest_messages",
                                lambda *a, **kw: ({"+911": last_message}
                                                  if last_message else {}))
            monkeypatch.setattr(dbc, "find_sla_breaches", lambda **kw: [])
            monkeypatch.setattr(dbc, "activity_counts", lambda **kw: {})
            monkeypatch.setattr(dbc, "list_pinned_contacts", lambda: [])
            return dbc.chat_audit_snapshot()
        return _go

    def test_human_reply_marks_the_flag_stale(self, snapshot):
        out = snapshot(_msg("+911", "outbound", "your order is ready"))
        assert [e["phone"] for e in out["handled_stale"]] == ["+911"]
        assert out["open_handoffs"] == []

    def test_bot_ack_is_not_a_human_reply(self, snapshot):
        """The bot's own 'alerted the team' ack must not read as handled."""
        out = snapshot(_msg("+911", "outbound", "I've alerted the team"))
        assert [e["phone"] for e in out["open_handoffs"]] == ["+911"]
        assert out["handled_stale"] == []

    def test_inbound_last_word_is_still_waiting(self, snapshot):
        out = snapshot(_msg("+911", "inbound", "any update?"))
        assert [e["phone"] for e in out["open_handoffs"]] == ["+911"]

    def test_no_message_found_is_still_waiting(self, snapshot):
        """This is the state the old cap produced spuriously. The behaviour is
        right — an unknown last word cannot be called handled — which is exactly
        why the lookup must not lose rows."""
        out = snapshot(None)
        assert [e["phone"] for e in out["open_handoffs"]] == ["+911"]
        assert out["open_handoffs"][0]["last_dir"] is None
