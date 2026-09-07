"""
Phase 2: per-ad clicks and attributed revenue (db_cloud.ad_report).

The money question is attribution, and _compute_ad_report is pure, so these
tests pin the rules directly rather than through the DB:

  * first touch wins -- the ad that introduced a customer keeps them;
  * only orders AFTER the click count, so an existing customer who taps an ad
    does not hand it their entire order history;
  * revenue from before the click is still visible as pre_click_revenue, so a
    quiet ad can be told apart from one whose audience already bought from us.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

db_cloud = pytest.importorskip("db_cloud", reason="supabase SDK not installed")
_compute = db_cloud._compute_ad_report


def click(phone, when, source_id="ad_A", headline="Print from 75p", channel="whatsapp"):
    return {"phone": phone, "clicked_at": when, "source_id": source_id,
            "headline": headline, "channel": channel}


def job(sender, when, amount):
    return {"sender": sender, "received_at": when, "amount_collected": amount}


def book(phone, when, total, status="confirmed"):
    return {"phone": phone, "created_at": when, "grand_total": total, "status": status}


class TestAttributionWindow:
    def test_order_after_the_click_counts(self):
        r = _compute([click("91900", "2026-09-01T10:00:00+00:00")],
                     [job("91900", "2026-09-02T10:00:00+00:00", 250.0)], [])
        ad = r["ads"][0]
        assert ad["print_revenue"] == 250.0
        assert ad["revenue"] == 250.0
        assert ad["converted"] == 1
        assert ad["pre_click_revenue"] == 0.0

    def test_order_before_the_click_is_excluded_but_visible(self):
        """The rule that stops a loyal customer flattering a useless ad."""
        r = _compute([click("91900", "2026-09-01T10:00:00+00:00")],
                     [job("91900", "2026-08-01T10:00:00+00:00", 5000.0)], [])
        ad = r["ads"][0]
        assert ad["revenue"] == 0.0, "pre-click spend must not be credited"
        assert ad["pre_click_revenue"] == 5000.0, "but it must still be visible"
        assert ad["converted"] == 0
        assert r["totals"]["revenue"] == 0.0

    def test_order_exactly_at_click_time_counts(self):
        r = _compute([click("91900", "2026-09-01T10:00:00+00:00")],
                     [job("91900", "2026-09-01T10:00:00+00:00", 40.0)], [])
        assert r["ads"][0]["revenue"] == 40.0


class TestFirstTouch:
    def test_first_ad_keeps_the_customer(self):
        clicks = [click("91900", "2026-09-01T10:00:00+00:00", "ad_FIRST"),
                  click("91900", "2026-09-05T10:00:00+00:00", "ad_SECOND")]
        r = _compute(clicks, [job("91900", "2026-09-06T10:00:00+00:00", 300.0)], [])
        ads = {a["source_id"]: a for a in r["ads"]}
        assert ads["ad_FIRST"]["revenue"] == 300.0
        assert ads["ad_SECOND"]["revenue"] == 0.0, "later click must not steal the credit"

    def test_out_of_order_input_still_picks_the_earliest(self):
        """Rows can arrive newest-first; the rule is by timestamp, not by list order."""
        clicks = [click("91900", "2026-09-05T10:00:00+00:00", "ad_SECOND"),
                  click("91900", "2026-09-01T10:00:00+00:00", "ad_FIRST")]
        r = _compute(clicks, [job("91900", "2026-09-06T10:00:00+00:00", 300.0)], [])
        ads = {a["source_id"]: a for a in r["ads"]}
        assert ads["ad_FIRST"]["revenue"] == 300.0
        assert ads["ad_SECOND"]["revenue"] == 0.0

    def test_both_ads_still_count_their_own_clicks(self):
        clicks = [click("91900", "2026-09-01T10:00:00+00:00", "ad_FIRST"),
                  click("91900", "2026-09-05T10:00:00+00:00", "ad_SECOND")]
        r = _compute(clicks, [], [])
        ads = {a["source_id"]: a for a in r["ads"]}
        assert ads["ad_FIRST"]["clicks"] == 1 and ads["ad_SECOND"]["clicks"] == 1
        assert r["totals"]["clicks"] == 2
        assert r["totals"]["customers"] == 1, "one person, two clicks"


class TestBookOrders:
    def test_only_sold_statuses_count(self):
        clicks = [click("91900", "2026-09-01T10:00:00+00:00")]
        after = "2026-09-02T10:00:00+00:00"
        for status in ("collecting", "awaiting_payment", "payment_review", "cancelled"):
            r = _compute(clicks, [], [book("91900", after, 515.0, status)])
            assert r["ads"][0]["revenue"] == 0.0, f"{status} is not money yet"
        for status in ("confirmed", "dispatched", "delivered"):
            r = _compute(clicks, [], [book("91900", after, 515.0, status)])
            assert r["ads"][0]["revenue"] == 515.0, f"{status} is a sale"

    def test_print_and_books_are_split_and_summed(self):
        clicks = [click("91900", "2026-09-01T10:00:00+00:00")]
        r = _compute(clicks,
                     [job("91900", "2026-09-02T10:00:00+00:00", 14.0)],
                     [book("91900", "2026-09-03T10:00:00+00:00", 515.0)])
        ad = r["ads"][0]
        assert ad["print_revenue"] == 14.0 and ad["print_jobs"] == 1
        assert ad["book_revenue"] == 515.0 and ad["book_orders"] == 1
        assert ad["revenue"] == 529.0
        assert ad["converted"] == 1, "one person, counted once across both"


class TestShape:
    def test_unattributed_revenue_is_ignored(self):
        """Someone who never clicked an ad contributes nothing."""
        r = _compute([click("91900", "2026-09-01T10:00:00+00:00")],
                     [job("91999", "2026-09-02T10:00:00+00:00", 900.0)], [])
        assert r["totals"]["revenue"] == 0.0

    def test_zero_and_negative_amounts_never_count_a_conversion(self):
        clicks = [click("91900", "2026-09-01T10:00:00+00:00")]
        r = _compute(clicks, [job("91900", "2026-09-02T10:00:00+00:00", 0)], [])
        assert r["ads"][0]["converted"] == 0 and r["ads"][0]["print_jobs"] == 0

    def test_revenue_per_click(self):
        clicks = [click("91900", "2026-09-01T10:00:00+00:00"),
                  click("91901", "2026-09-01T11:00:00+00:00")]
        r = _compute(clicks, [job("91900", "2026-09-02T10:00:00+00:00", 100.0)], [])
        assert r["ads"][0]["clicks"] == 2
        assert r["ads"][0]["revenue_per_click"] == 50.0

    def test_naive_job_timestamp_is_read_as_utc_not_dropped(self):
        """jobs.received_at is a legacy string and is sometimes naive."""
        r = _compute([click("91900", "2026-09-01T10:00:00+00:00")],
                     [job("91900", "2026-09-02T10:00:00", 75.0)], [])
        assert r["ads"][0]["revenue"] == 75.0

    def test_unparseable_or_missing_rows_do_not_crash(self):
        r = _compute(
            [click("91900", "2026-09-01T10:00:00+00:00"),
             click("", "2026-09-01T10:00:00+00:00"),
             click("91902", None),
             click("91903", "not-a-date")],
            [job("91900", None, 10.0), job(None, "2026-09-02T10:00:00+00:00", 10.0)],
            [book("91900", "garbage", 10.0)],
        )
        assert r["totals"]["clicks"] == 1
        assert r["totals"]["revenue"] == 0.0

    def test_missing_source_id_buckets_as_unknown(self):
        r = _compute([click("91900", "2026-09-01T10:00:00+00:00", None)], [], [])
        assert r["ads"][0]["source_id"] == "unknown"

    def test_ads_sort_by_revenue_desc(self):
        clicks = [click("91900", "2026-09-01T10:00:00+00:00", "ad_LOW"),
                  click("91901", "2026-09-01T10:00:00+00:00", "ad_HIGH")]
        jobs = [job("91900", "2026-09-02T10:00:00+00:00", 10.0),
                job("91901", "2026-09-02T10:00:00+00:00", 900.0)]
        r = _compute(clicks, jobs, [])
        assert [a["source_id"] for a in r["ads"]] == ["ad_HIGH", "ad_LOW"]

    def test_empty_input(self):
        r = _compute([], [], [])
        assert r["ads"] == []
        assert r["totals"]["clicks"] == 0 and r["totals"]["revenue"] == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# GET /admin/ads/report — the endpoint around the aggregation
# ═════════════════════════════════════════════════════════════════════════════

import api.index  # noqa: E402,F401  (import first: handlers_admin <-> api.index is circular)
import api.handlers_admin as ha  # noqa: E402


class _H:
    def __init__(self, path="/admin/ads/report"):
        self.status = None
        self.payload = None
        self.headers = {}
        self.path = path


def _wire(monkeypatch, *, authed=True):
    monkeypatch.setattr(ha, "_auth_admin_pw", lambda pw: authed)
    monkeypatch.setattr(ha, "_admin_pw_from_request", lambda h: "pw")
    monkeypatch.setattr(ha, "_json_response",
                        lambda h, s, p: (setattr(h, "status", s), setattr(h, "payload", p)))


class TestAdsReportEndpoint:
    def test_requires_admin_password(self, monkeypatch):
        """Spend-adjacent revenue is owner-level, not counter-level."""
        _wire(monkeypatch, authed=False)
        called = []
        monkeypatch.setattr(db_cloud, "ad_report", lambda days=90: called.append(days) or {})
        h = _H()
        ha._handle_admin_ads_report(h)
        assert h.status == 403
        assert not called, "must not touch the DB when unauthorized"

    @pytest.mark.parametrize("query,expected", [
        ("", 90),
        ("?days=30", 30),
        ("?days=365", 365),
        ("?days=0", 1),          # clamped up
        ("?days=99999", 365),    # clamped down
        ("?days=abc", 90),       # unparseable -> default
    ])
    def test_days_parsing_and_clamping(self, monkeypatch, query, expected):
        _wire(monkeypatch)
        seen = []
        monkeypatch.setattr(db_cloud, "ad_report",
                            lambda days=90: seen.append(days) or {"ads": [], "totals": {}})
        h = _H("/admin/ads/report" + query)
        ha._handle_admin_ads_report(h)
        assert h.status == 200
        assert seen == [expected]

    def test_db_failure_surfaces_as_500(self, monkeypatch):
        _wire(monkeypatch)
        monkeypatch.setattr(db_cloud, "ad_report",
                            lambda days=90: (_ for _ in ()).throw(RuntimeError("boom")))
        h = _H()
        ha._handle_admin_ads_report(h)
        assert h.status == 500
        assert "boom" in h.payload["error"]
