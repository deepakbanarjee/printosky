"""
Referral credit for orders paid at the counter.

_credit_referrer is called from exactly two places, both inside
_process_razorpay_payment. So a referred classmate who walks in and pays cash
has always earned the referrer nothing -- and cash is how most of this shop's
printing is paid for. The recruiter watches their balance stay at Rs.0,
concludes sharing does not work, and stops sharing. That is the referral
programme's entire failure mode, and it is silent.

The sweep closes it without touching the payment paths.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

pytest.importorskip("db_cloud", reason="supabase SDK not installed")
import api.index  # noqa: E402,F401  (import first: handlers_referrals <-> api.index is circular)
import api.handlers_referrals as hr  # noqa: E402


class _SB:
    """Supabase double: bot_sessions, jobs, and a growing referral_credits."""

    def __init__(self, sessions, jobs, credits=None):
        self.sessions, self.jobs = sessions, jobs
        self.credits = list(credits or [])

    def table(self, name):
        t, sb = MagicMock(), self

        def select(_cols):
            q = MagicMock()
            if name == "bot_sessions":
                q.not_.is_.return_value.execute.return_value = MagicMock(data=sb.sessions)
            elif name == "jobs":
                q.in_.return_value.gte.return_value.execute.return_value = MagicMock(data=sb.jobs)
            else:                                   # referral_credits
                q.eq.side_effect = lambda col, val: MagicMock(
                    execute=MagicMock(return_value=MagicMock(
                        data=[c for c in sb.credits if c.get("order_id") == val])))
            return q

        t.select.side_effect = select
        return t


def _wire(monkeypatch, sb, credited_ids=()):
    import db_cloud
    monkeypatch.setattr(db_cloud, "_client", lambda: sb)

    def fake_credit(phone, order_id):
        # Stand-in for the real thing: award once, for the orders we say are
        # genuinely referred. Its own dup check is exercised elsewhere.
        if order_id in credited_ids and not any(
                c["order_id"] == order_id for c in sb.credits):
            sb.credits.append({"id": len(sb.credits) + 1, "order_id": order_id})
    monkeypatch.setattr(api.index, "_credit_referrer", fake_credit)


class TestSweep:
    def test_a_counter_paid_referred_order_finally_earns_credit(self, monkeypatch):
        sb = _SB(sessions=[{"phone": "919000000002", "referral_code": "REF1111AA"}],
                 jobs=[{"job_id": "OSP-1", "sender": "919000000002",
                        "status": "Printed", "received_at": "2026-09-09T05:00:00+00:00"}])
        _wire(monkeypatch, sb, credited_ids={"OSP-1"})
        out = hr.sweep_referral_credits()
        assert out["candidates"] == 1 and out["credited"] == 1

    def test_a_customer_who_never_used_a_ref_link_earns_nobody_anything(self, monkeypatch):
        sb = _SB(sessions=[{"phone": "919000000002", "referral_code": "REF1111AA"}],
                 jobs=[{"job_id": "OSP-2", "sender": "919000009999",
                        "status": "Printed", "received_at": "2026-09-09T05:00:00+00:00"}])
        _wire(monkeypatch, sb, credited_ids={"OSP-2"})
        out = hr.sweep_referral_credits()
        assert out["candidates"] == 0 and out["credited"] == 0

    def test_rerunning_credits_nothing_twice(self, monkeypatch):
        """A cron that double-pays on every run would be worse than no cron."""
        sb = _SB(sessions=[{"phone": "919000000002", "referral_code": "REF1111AA"}],
                 jobs=[{"job_id": "OSP-3", "sender": "919000000002",
                        "status": "Delivered", "received_at": "2026-09-09T05:00:00+00:00"}])
        _wire(monkeypatch, sb, credited_ids={"OSP-3"})
        assert hr.sweep_referral_credits()["credited"] == 1
        assert hr.sweep_referral_credits()["credited"] == 0
        assert len(sb.credits) == 1

    def test_no_referred_customers_short_circuits(self, monkeypatch):
        sb = _SB(sessions=[], jobs=[{"job_id": "OSP-4", "sender": "919000000002",
                                     "status": "Paid", "received_at": "2026-09-09T05:00:00+00:00"}])
        _wire(monkeypatch, sb)
        out = hr.sweep_referral_credits()
        assert out == {"candidates": 0, "credited": 0, "referred_customers": 0}

    def test_blank_referral_codes_are_not_customers(self, monkeypatch):
        sb = _SB(sessions=[{"phone": "919000000002", "referral_code": "   "}],
                 jobs=[{"job_id": "OSP-5", "sender": "919000000002",
                        "status": "Printed", "received_at": "2026-09-09T05:00:00+00:00"}])
        _wire(monkeypatch, sb, credited_ids={"OSP-5"})
        assert hr.sweep_referral_credits()["referred_customers"] == 0

    def test_only_served_statuses_are_swept(self):
        """A cart that was never served must not pay a referrer."""
        assert hr.PAID_STATUSES == ("Paid", "Printed", "Delivered")
        assert "Pending" not in hr.PAID_STATUSES


class TestCronEndpoint:
    def _h(self, auth=None):
        return type("H", (), {"headers": {"Authorization": auth} if auth else {}})()

    def test_secret_is_enforced_when_set(self, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", "s3cret")
        seen = {}
        monkeypatch.setattr(api.index, "_json_response",
                            lambda h, s, p: seen.update(status=s, payload=p))
        api.index._handle_cron_referral_credits(self._h())
        assert seen["status"] == 401

    def test_runs_with_the_right_secret(self, monkeypatch):
        monkeypatch.setenv("CRON_SECRET", "s3cret")
        monkeypatch.setattr(hr, "sweep_referral_credits",
                            lambda: {"candidates": 2, "credited": 1, "referred_customers": 3})
        seen = {}
        monkeypatch.setattr(api.index, "_json_response",
                            lambda h, s, p: seen.update(status=s, payload=p))
        api.index._handle_cron_referral_credits(self._h("Bearer s3cret"))
        assert seen["status"] == 200 and seen["payload"]["credited"] == 1
