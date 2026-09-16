"""
What Meta is asked to buy.

The launcher used to create OUTCOME_TRAFFIC campaigns optimising for
LINK_CLICKS. That is the cheapest thing an ad account can be asked for and
exactly what it delivered: ad 120248100283360186 bought 8 clicks, 7 people and
Rs.0 over 7-14 Sep 2026. It was never asked for customers.

These pin the two things that are easy to get quietly wrong: an objective and
an optimisation goal that do not go together (Meta rejects the pair, so the
campaign simply never launches), and a `purchase` deploy that goes ahead
without the dataset -- which Meta accepts, then optimises against events
nothing is sending it.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "marketing"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

launcher = pytest.importorskip("meta_campaign_launcher",
                               reason="requests unavailable")

PAGE = "999350999936953"
WA = "919495706405"
DATASET = "1234567890"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("META_PAGE_ID", "STORE_WHATSAPP_PHONE", "META_CAPI_DATASET_ID"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("META_PAGE_ID", PAGE)
    monkeypatch.setenv("STORE_WHATSAPP_PHONE", WA)
    monkeypatch.setenv("META_CAPI_DATASET_ID", DATASET)


class TestNoLongerBuyingClicks:
    def test_link_clicks_is_gone(self):
        goals = {m["optimization_goal"] for m in launcher.OPTIMIZATION_MODES.values()}
        assert "LINK_CLICKS" not in goals, "the goal that bought 8 clicks and Rs.0"

    def test_outcome_traffic_is_gone(self):
        objectives = {m["objective"] for m in launcher.OPTIMIZATION_MODES.values()}
        assert "OUTCOME_TRAFFIC" not in objectives

    def test_the_default_is_the_one_this_shop_has_volume_for(self):
        """Conversion optimisation needs ~50 conversions per ad set per week.
        This shop has had zero. Defaulting to purchase would produce an ad set
        that barely delivers, not one that spends carefully."""
        assert launcher.DEFAULT_MODE == "conversations"


class TestObjectiveAndGoalTravelTogether:
    """Meta rejects an optimisation goal the campaign objective does not
    offer, so a mismatched pair means the campaign never launches at all."""

    def test_conversations_pairs_with_engagement(self):
        m = launcher.OPTIMIZATION_MODES["conversations"]
        assert m["objective"] == "OUTCOME_ENGAGEMENT"
        assert m["optimization_goal"] == "CONVERSATIONS"

    def test_purchase_pairs_with_sales(self):
        m = launcher.OPTIMIZATION_MODES["purchase"]
        assert m["objective"] == "OUTCOME_SALES"
        assert m["optimization_goal"] == "OFFSITE_CONVERSIONS"
        assert m["custom_event_type"] == "PURCHASE"

    @pytest.mark.parametrize("mode", sorted(launcher.OPTIMIZATION_MODES))
    def test_the_campaign_carries_its_modes_objective(self, mode, monkeypatch):
        sent = {}

        class _Resp:
            @staticmethod
            def json():
                return {"id": "c1"}

        monkeypatch.setattr(launcher.requests, "post",
                            lambda url, data=None: sent.update(data) or _Resp())
        launcher.create_campaign("tok", "act_1", mode=mode)
        assert sent["objective"] == launcher.OPTIMIZATION_MODES[mode]["objective"]


class TestPromotedObject:
    def test_a_ctwa_adset_names_the_page_and_the_number(self, configured):
        p = launcher.build_promoted_object("conversations")
        assert p["page_id"] == PAGE
        assert p["whatsapp_phone_number"] == WA

    def test_conversations_does_not_claim_a_dataset(self, configured):
        """It is not optimising against events, so naming a dataset would be
        a lie Meta might act on."""
        p = launcher.build_promoted_object("conversations")
        assert "pixel_id" not in p and "custom_event_type" not in p

    def test_purchase_names_the_dataset_and_the_event(self, configured):
        p = launcher.build_promoted_object("purchase")
        assert p["pixel_id"] == DATASET
        assert p["custom_event_type"] == "PURCHASE"

    def test_it_is_the_same_dataset_meta_capi_posts_to(self, configured):
        """Two dataset ids would mean Meta optimising against events nobody
        sends it — the failure this whole change exists to end."""
        capi = pytest.importorskip("meta_capi")
        assert launcher.build_promoted_object("purchase")["pixel_id"] == capi._dataset_id()


class TestReadinessCheck:
    def test_a_configured_mode_has_no_gaps(self, configured):
        assert launcher.check_mode_ready("conversations") == []
        assert launcher.check_mode_ready("purchase") == []

    def test_purchase_without_a_dataset_is_blocked(self, monkeypatch):
        monkeypatch.setenv("META_PAGE_ID", PAGE)
        monkeypatch.setenv("STORE_WHATSAPP_PHONE", WA)
        gaps = launcher.check_mode_ready("purchase")
        assert any("META_CAPI_DATASET_ID" in g for g in gaps)

    def test_conversations_does_not_need_a_dataset(self, monkeypatch):
        monkeypatch.setenv("META_PAGE_ID", PAGE)
        monkeypatch.setenv("STORE_WHATSAPP_PHONE", WA)
        assert launcher.check_mode_ready("conversations") == []

    def test_a_missing_whatsapp_number_is_caught(self, monkeypatch):
        monkeypatch.setenv("META_PAGE_ID", PAGE)
        gaps = launcher.check_mode_ready("conversations")
        assert any("STORE_WHATSAPP_PHONE" in g for g in gaps)

    def test_gaps_are_listed_not_raised(self):
        """--dry-run shows the whole list at once instead of dying on the
        first gap, and a deploy refuses before creating an orphan campaign."""
        gaps = launcher.check_mode_ready("purchase")
        assert isinstance(gaps, list) and len(gaps) >= 2


class TestAdSet:
    def _capture(self, monkeypatch):
        sent = {}

        class _Resp:
            @staticmethod
            def json():
                return {"id": "as1"}

        monkeypatch.setattr(launcher.requests, "post",
                            lambda url, data=None: sent.update(data) or _Resp())
        return sent

    def test_it_declares_itself_a_whatsapp_destination(self, configured, monkeypatch):
        """Without destination_type the tap does not open a chat — the entire
        mechanic the ad sells."""
        sent = self._capture(monkeypatch)
        launcher.create_adset("tok", "act_1", "c1", "Cluster 1", [], 100)
        assert sent["destination_type"] == "WHATSAPP"

    def test_it_carries_the_promoted_object_as_json(self, configured, monkeypatch):
        import json
        sent = self._capture(monkeypatch)
        launcher.create_adset("tok", "act_1", "c1", "Cluster 1", [], 100,
                              mode="purchase")
        assert json.loads(sent["promoted_object"])["custom_event_type"] == "PURCHASE"

    def test_the_goal_matches_the_mode(self, configured, monkeypatch):
        sent = self._capture(monkeypatch)
        launcher.create_adset("tok", "act_1", "c1", "Cluster 1", [], 100,
                              mode="purchase")
        assert sent["optimization_goal"] == "OFFSITE_CONVERSIONS"

    def test_budget_is_still_in_paise(self, configured, monkeypatch):
        sent = self._capture(monkeypatch)
        launcher.create_adset("tok", "act_1", "c1", "Cluster 1", [], 100)
        assert sent["daily_budget"] == 10000

    def test_it_still_launches_paused(self, configured, monkeypatch):
        """Nothing here should start spending without a human looking first."""
        sent = self._capture(monkeypatch)
        launcher.create_adset("tok", "act_1", "c1", "Cluster 1", [], 100)
        assert sent["status"] == "PAUSED"
