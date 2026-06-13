"""Tests for db_cloud.chat_audit_snapshot() + clear_needs_human().

Covers:
- the naive/aware datetime regression (2026-06-13 deploy bug),
- handoff classification: a chat is "waiting" if its newest message is inbound
  or only an auto-ack; "handled (stale)" once a human's real reply is the last
  word — so the digest escalates signal, not the whole needs_human pile.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import db_cloud  # noqa: E402


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, data):
        self._data = data

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def execute(self):
        return _Resp(self._data)


class _Client:
    def __init__(self, sessions, logs=None):
        self._sessions = sessions
        self._logs = logs or []

    def table(self, name):
        return _Query(self._sessions if name == "bot_sessions" else self._logs)


def _isolate(monkeypatch, sessions, logs=None):
    monkeypatch.setattr(db_cloud, "_client", lambda: _Client(sessions, logs))
    monkeypatch.setattr(db_cloud, "find_sla_breaches", lambda **k: [])
    monkeypatch.setattr(db_cloud, "activity_counts", lambda **k: {"inbound": 0})


# ── datetime regression ──────────────────────────────────────────────────────

def test_naive_updated_at_does_not_zero_the_queue(monkeypatch):
    sessions = [
        {"phone": "918547727272", "step": "staff_hold", "needs_human": True,
         "last_help_request_at": None, "updated_at": "2026-06-06 07:57:24"},  # naive
        {"phone": "919000000001", "step": "staff_hold", "needs_human": True,
         "last_help_request_at": "2026-06-13T06:37:10.5+00:00",
         "updated_at": "2026-06-13 06:37:10"},
    ]
    _isolate(monkeypatch, sessions)
    snap = db_cloud.chat_audit_snapshot()
    # No conversation_log → both stay "waiting"; the naive row must not crash.
    assert len(snap["open_handoffs"]) == 2
    assert all(isinstance(h["age_hours"], (int, float)) for h in snap["open_handoffs"])


def test_handoffs_sorted_oldest_first(monkeypatch):
    sessions = [
        {"phone": "newer", "step": "staff_hold", "needs_human": True,
         "last_help_request_at": "2026-06-13T06:00:00+00:00", "updated_at": None},
        {"phone": "older", "step": "staff_hold", "needs_human": True,
         "last_help_request_at": "2026-06-01T06:00:00+00:00", "updated_at": None},
    ]
    _isolate(monkeypatch, sessions)
    snap = db_cloud.chat_audit_snapshot()
    assert [h["phone"] for h in snap["open_handoffs"]] == ["older", "newer"]


def test_missing_both_timestamps_yields_unknown_age(monkeypatch):
    sessions = [{"phone": "x", "step": None, "needs_human": True,
                 "last_help_request_at": None, "updated_at": None}]
    _isolate(monkeypatch, sessions)
    snap = db_cloud.chat_audit_snapshot()
    assert len(snap["open_handoffs"]) == 1
    assert snap["open_handoffs"][0]["age_hours"] is None


# ── waiting vs handled classification ────────────────────────────────────────

def _session(phone):
    return {"phone": phone, "step": "staff_hold", "needs_human": True,
            "last_help_request_at": "2026-06-10T06:00:00+00:00", "updated_at": None}


def test_real_outbound_reply_marks_handled(monkeypatch):
    logs = [
        {"phone": "p1", "direction": "outbound", "body": "double side 930",
         "created_at": "2026-06-10T07:00:00+00:00"},   # newest = human reply
        {"phone": "p1", "direction": "inbound", "body": "quote pls",
         "created_at": "2026-06-10T06:00:00+00:00"},
    ]
    _isolate(monkeypatch, [_session("p1")], logs)
    snap = db_cloud.chat_audit_snapshot()
    assert snap["open_handoffs"] == []
    assert [h["phone"] for h in snap["handled_stale"]] == ["p1"]


def test_ack_as_last_message_still_waiting(monkeypatch):
    logs = [
        {"phone": "p2", "direction": "outbound",
         "body": "🙏 Thanks — a team member will reply to you shortly",  # ack, not a reply
         "created_at": "2026-06-10T06:00:05+00:00"},
        {"phone": "p2", "direction": "inbound", "body": "help",
         "created_at": "2026-06-10T06:00:00+00:00"},
    ]
    _isolate(monkeypatch, [_session("p2")], logs)
    snap = db_cloud.chat_audit_snapshot()
    assert [h["phone"] for h in snap["open_handoffs"]] == ["p2"]
    assert snap["handled_stale"] == []


def test_inbound_last_is_waiting(monkeypatch):
    logs = [
        {"phone": "p3", "direction": "inbound", "body": "Pls share slip",
         "created_at": "2026-06-10T07:00:00+00:00"},   # newest = customer
        {"phone": "p3", "direction": "outbound", "body": "address ...",
         "created_at": "2026-06-10T06:00:00+00:00"},
    ]
    _isolate(monkeypatch, [_session("p3")], logs)
    snap = db_cloud.chat_audit_snapshot()
    assert [h["phone"] for h in snap["open_handoffs"]] == ["p3"]
    assert snap["handled_stale"] == []


def test_clear_needs_human_best_effort(monkeypatch):
    monkeypatch.setattr(db_cloud, "_client", lambda: _Client([], []))
    assert db_cloud.clear_needs_human("p") is True
