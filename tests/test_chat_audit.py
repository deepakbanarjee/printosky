"""Regression tests for db_cloud.chat_audit_snapshot().

The handoffs query mixes a timezone-aware column (last_help_request_at,
timestamptz) with a naive TEXT column (updated_at). A naive value must not
raise "can't subtract offset-naive and offset-aware datetimes" and silently
zero out the whole handoff queue (the original deploy bug, 2026-06-13).
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

    def execute(self):
        return _Resp(self._data)


class _Client:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _Query(self._rows)


def _isolate(monkeypatch, rows):
    monkeypatch.setattr(db_cloud, "_client", lambda: _Client(rows))
    monkeypatch.setattr(db_cloud, "find_sla_breaches", lambda **k: [])
    monkeypatch.setattr(db_cloud, "activity_counts", lambda **k: {"inbound": 0})


def test_naive_updated_at_does_not_zero_the_queue(monkeypatch):
    rows = [
        {  # last_help_request_at NULL → falls back to naive updated_at
            "phone": "918547727272", "step": "staff_hold", "needs_human": True,
            "last_help_request_at": None, "updated_at": "2026-06-06 07:57:24",
        },
        {  # normal tz-aware row
            "phone": "919000000001", "step": "staff_hold", "needs_human": True,
            "last_help_request_at": "2026-06-13T06:37:10.5+00:00",
            "updated_at": "2026-06-13 06:37:10",
        },
    ]
    _isolate(monkeypatch, rows)
    snap = db_cloud.chat_audit_snapshot()
    # Both rows survive — the naive one must NOT abort the loop.
    assert len(snap["open_handoffs"]) == 2
    assert all(isinstance(h["age_hours"], (int, float)) for h in snap["open_handoffs"])


def test_handoffs_sorted_oldest_first(monkeypatch):
    rows = [
        {"phone": "newer", "step": "staff_hold", "needs_human": True,
         "last_help_request_at": "2026-06-13T06:00:00+00:00", "updated_at": None},
        {"phone": "older", "step": "staff_hold", "needs_human": True,
         "last_help_request_at": "2026-06-01T06:00:00+00:00", "updated_at": None},
    ]
    _isolate(monkeypatch, rows)
    snap = db_cloud.chat_audit_snapshot()
    assert [h["phone"] for h in snap["open_handoffs"]] == ["older", "newer"]


def test_missing_both_timestamps_yields_unknown_age(monkeypatch):
    rows = [{"phone": "x", "step": None, "needs_human": True,
             "last_help_request_at": None, "updated_at": None}]
    _isolate(monkeypatch, rows)
    snap = db_cloud.chat_audit_snapshot()
    assert len(snap["open_handoffs"]) == 1
    assert snap["open_handoffs"][0]["age_hours"] is None
