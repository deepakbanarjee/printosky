"""Tests for collect_daily_summary() — the store-revenue reporting bug.

Background: daily_summary reported 0 jobs / Rs.0 revenue for OSP for 38
straight days while the printers ran ~2,900 pages/day. Two causes:

1. It read the local SQLite jobs table only. Most of a store's daily jobs
   (source='web' — staff web-create, WhatsApp bot, Razorpay webhook) are
   written straight to Supabase and never touch the store PC's local DB.
2. It classified "completed" as status='Completed', a workflow step staff
   almost never reach (3 of ~320 OSP jobs, ever) — real jobs stay at
   status='Printed' with amount_collected already set.

collect_daily_summary() now reads Supabase (the merged source of truth) and
treats amount_collected > 0 as the completion signal.

Run: pytest tests/test_daily_summary.py -v
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import supabase_sync as ss  # noqa: E402


class _FakeResponse:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def _configure(monkeypatch):
    monkeypatch.setattr(ss, "SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setattr(ss, "SUPABASE_KEY", "anon")
    monkeypatch.setattr(ss, "STORE_ID", "OSP")


class TestCollectDailySummary:
    def test_not_configured_returns_empty_without_a_request(self, monkeypatch):
        monkeypatch.setattr(ss, "SUPABASE_URL", "")
        monkeypatch.setattr(ss, "SUPABASE_KEY", "")

        def _boom(*a, **k):
            raise AssertionError("must not call Supabase when unconfigured")
        monkeypatch.setattr(ss.requests, "get", _boom)

        assert ss.collect_daily_summary("ignored.db") == []

    def test_web_sourced_jobs_are_counted(self, monkeypatch):
        # This is the exact shape of the 38-day bug: jobs that never touched
        # local SQLite (source='web') must still show up in the summary.
        _configure(monkeypatch)
        rows = [
            {"status": "Printed", "amount_collected": 9, "payment_mode": "cash"},
            {"status": "Printed", "amount_collected": 18, "payment_mode": "Cash"},
        ]
        monkeypatch.setattr(ss.requests, "get", lambda *a, **k: _FakeResponse(rows))

        out = ss.collect_daily_summary("ignored.db")
        assert len(out) == 1
        summary = out[0]
        assert summary["store_id"] == "OSP"
        assert summary["date"] == date.today().isoformat()
        assert summary["total_jobs"] == 2
        assert summary["revenue"] == 27
        assert summary["cash"] == 27

    def test_completed_is_paid_not_status_literal(self, monkeypatch):
        # status='Completed' is a workflow step staff almost never reach.
        # A 'Printed' job that already collected cash must count as completed.
        _configure(monkeypatch)
        rows = [
            {"status": "Printed", "amount_collected": 50, "payment_mode": "cash"},
            {"status": "Received", "amount_collected": 0, "payment_mode": None},
            {"status": "Cancelled", "amount_collected": 0, "payment_mode": None},
        ]
        monkeypatch.setattr(ss.requests, "get", lambda *a, **k: _FakeResponse(rows))

        summary = ss.collect_daily_summary("ignored.db")[0]
        assert summary["total_jobs"] == 3
        assert summary["completed"] == 1     # the paid Printed job
        assert summary["pending"] == 1        # the unpaid Received job
        # Cancelled counts toward total_jobs but neither completed nor pending.

    def test_payment_mode_matched_case_insensitively(self, monkeypatch):
        _configure(monkeypatch)
        rows = [
            {"status": "Printed", "amount_collected": 10, "payment_mode": "CASH"},
            {"status": "Printed", "amount_collected": 20, "payment_mode": "upi"},
        ]
        monkeypatch.setattr(ss.requests, "get", lambda *a, **k: _FakeResponse(rows))

        summary = ss.collect_daily_summary("ignored.db")[0]
        assert summary["cash"] == 10
        assert summary["upi"] == 20
        assert summary["revenue"] == 30

    def test_request_failure_is_caught_and_reported(self, monkeypatch, caplog):
        import logging
        _configure(monkeypatch)

        def _boom(*a, **k):
            raise ConnectionError("network down")
        monkeypatch.setattr(ss.requests, "get", _boom)

        with caplog.at_level(logging.WARNING, logger="supabase_sync"):
            out = ss.collect_daily_summary("ignored.db")
        assert out == []
        assert any("collect_daily_summary" in r.message for r in caplog.records)

    def test_queries_store_id_and_selected_columns(self, monkeypatch):
        _configure(monkeypatch)
        captured = {}

        def _fake_get(url, headers, params, timeout):
            captured["url"] = url
            captured["params"] = dict(params)
            return _FakeResponse([])
        monkeypatch.setattr(ss.requests, "get", _fake_get)

        ss.collect_daily_summary("ignored.db")
        assert captured["url"].endswith("/jobs")
        assert captured["params"]["store_id"] == "eq.OSP"
        assert captured["params"]["select"] == "status,amount_collected,payment_mode"

    def test_date_bounds_are_today_only(self, monkeypatch):
        _configure(monkeypatch)
        seen_params = []

        def _fake_get(url, headers, params, timeout):
            seen_params.extend(params)
            return _FakeResponse([])
        monkeypatch.setattr(ss.requests, "get", _fake_get)

        ss.collect_daily_summary("ignored.db")
        received_at_filters = [v for k, v in seen_params if k == "received_at"]
        today = date.today().isoformat()
        assert any(v == f"gte.{today} 00:00:00" for v in received_at_filters)
        assert any(v.startswith("lt.") for v in received_at_filters)
