"""Tests for db_cloud._compute_sla_breaches (pure SLA breach detection).

The webhook logs the bot's auto-reply a few hundred ms BEFORE the inbound that
triggered it, so a strict "outbound after inbound" check used to flag every
immediately-answered message as a breach. The reply-tolerance window fixes that.
"""
from datetime import datetime, timezone, timedelta

import db_cloud

UTC = timezone.utc
NOW = datetime(2026, 6, 7, 12, 0, tzinfo=UTC)


def _row(phone, direction, dt):
    return {"phone": phone, "direction": direction, "created_at": dt.isoformat()}


def _phones(breaches):
    return {b["phone"] for b in breaches}


class TestComputeSlaBreaches:
    def test_immediate_reply_logged_before_inbound_is_not_breach(self):
        # The artifact: outbound 300ms BEFORE the inbound it answered; inbound 2h old.
        t = NOW - timedelta(hours=2)
        rows = [_row("p1", "outbound", t - timedelta(milliseconds=300)),
                _row("p1", "inbound", t)]
        assert db_cloud._compute_sla_breaches(rows, NOW, threshold_hours=1) == []

    def test_genuinely_unanswered_is_breach(self):
        # Last reply was 30 min before the new inbound -> really waiting.
        t = NOW - timedelta(hours=2)
        rows = [_row("p2", "outbound", t - timedelta(minutes=30)),
                _row("p2", "inbound", t)]
        assert _phones(db_cloud._compute_sla_breaches(rows, NOW, threshold_hours=1)) == {"p2"}

    def test_reply_after_inbound_not_breach(self):
        t = NOW - timedelta(hours=2)
        rows = [_row("p3", "inbound", t),
                _row("p3", "outbound", t + timedelta(seconds=5))]
        assert db_cloud._compute_sla_breaches(rows, NOW, threshold_hours=1) == []

    def test_recent_inbound_within_threshold_not_breach(self):
        rows = [_row("p4", "inbound", NOW - timedelta(minutes=20))]  # < 1h
        assert db_cloud._compute_sla_breaches(rows, NOW, threshold_hours=1) == []

    def test_no_inbound_not_breach(self):
        rows = [_row("p5", "outbound", NOW - timedelta(hours=3))]
        assert db_cloud._compute_sla_breaches(rows, NOW, threshold_hours=1) == []

    def test_order_independent_and_multiple_phones(self):
        t = NOW - timedelta(hours=2)
        rows = [
            _row("a", "inbound", t),                                   # artifact reply ->
            _row("a", "outbound", t - timedelta(milliseconds=200)),   #   not a breach
            _row("b", "inbound", t),                                   # no reply -> breach
            _row("c", "outbound", t + timedelta(seconds=2)),          # replied -> not breach
            _row("c", "inbound", t),
        ]
        assert _phones(db_cloud._compute_sla_breaches(rows, NOW, threshold_hours=1)) == {"b"}

    def test_tolerance_boundary(self):
        # Outbound just inside the 120s window before the inbound counts as a reply.
        t = NOW - timedelta(hours=2)
        rows = [_row("p", "outbound", t - timedelta(seconds=119)),
                _row("p", "inbound", t)]
        assert db_cloud._compute_sla_breaches(rows, NOW, threshold_hours=1) == []
        # Outbound well outside the window does not.
        rows2 = [_row("q", "outbound", t - timedelta(seconds=600)),
                 _row("q", "inbound", t)]
        assert _phones(db_cloud._compute_sla_breaches(rows2, NOW, threshold_hours=1)) == {"q"}
