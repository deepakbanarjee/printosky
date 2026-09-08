"""
Answering the ad someone actually clicked.

Both real ad arrivals on 7 Sep (ad 120248100283360186) replied "H", were sent
the generic five-option menu -- Print a file / Xtraa books / Malayalam book /
Sociology books / Academic project -- and went quiet. The ad they tapped said
"Print your Thesis for Rs.0, earn Rs.20 per classmate" and the menu mentions
none of that. The ad is the first half of a conversation; these tests pin the
second half.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

intent = pytest.importorskip("routing.intent", reason="routing package unavailable")
db_cloud = pytest.importorskip("db_cloud", reason="supabase SDK not installed")

AD = "120248100283360186"          # the live referral campaign
OTHER_AD = "999999999999999"       # some future campaign, not configured


@pytest.fixture
def wire(monkeypatch):
    """Capture what the front door sends, and stub everything it reaches for."""
    sent: list[str] = []
    state = {"code": None, "ad": None, "minted": []}

    monkeypatch.setattr(intent, "_send_text", lambda phone, msg: sent.append(msg))
    monkeypatch.setattr(intent, "_send_menu",
                        lambda phone: sent.append("<<GENERIC MENU>>"))
    monkeypatch.setattr(intent, "_pending_payment_reminder", lambda p, n: None)
    monkeypatch.setattr(intent, "decide_intent", lambda t: "unknown")

    monkeypatch.setattr(db_cloud, "get_referral_code", lambda p: state["code"])
    monkeypatch.setattr(db_cloud, "recent_ad_click", lambda p, **k: state["ad"])

    def _mint(phone, platform="whatsapp_selfserve"):
        state["minted"].append((phone, platform))
        return state["code"] or "REF5016XY"
    monkeypatch.setattr(db_cloud, "ensure_referral_code", _mint)
    return sent, state


def _ad(source_id=AD):
    return {"phone": "919233255016", "source_id": source_id,
            "headline": "Chat with us", "clicked_at": "2026-09-07T12:27:31+00:00"}


class TestAdArrival:
    def test_ad_arrival_gets_the_offer_not_the_menu(self, wire):
        sent, state = wire
        state["ad"] = _ad()
        intent.route_front_door("919233255016", "H", "Arun")
        body = "\n".join(sent)
        assert "<<GENERIC MENU>>" not in body, "the menu answers a different conversation"
        assert "REF5016XY" in body and "wa.me" in body, "the link is what the ad promised"
        assert "20" in body, "the Rs.20-per-classmate offer is the hook"
        assert state["minted"] == [("919233255016", "ad_click")]

    def test_no_ad_click_still_gets_the_menu(self, wire):
        sent, state = wire
        state["ad"] = None
        intent.route_front_door("919000000001", "H")
        assert sent == ["<<GENERIC MENU>>"]

    def test_unconfigured_ad_falls_through_rather_than_guessing(self, wire):
        """A future campaign selling something else must not be handed a
        referral link just because it is an ad."""
        sent, state = wire
        state["ad"] = _ad(OTHER_AD)
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]
        assert state["minted"] == []

    def test_fires_only_once_per_person(self, wire):
        """Someone who already holds a code has had this message; repeating it
        on every unrecognised reply would be spam."""
        sent, state = wire
        state["ad"] = _ad()
        state["code"] = "REF5016XY"          # they already have one
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]
        assert state["minted"] == []

    def test_a_mint_failure_falls_back_to_the_menu(self, wire, monkeypatch):
        """Never leave the customer with silence."""
        sent, state = wire
        state["ad"] = _ad()
        monkeypatch.setattr(db_cloud, "ensure_referral_code", lambda p, **k: None)
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]

    def test_a_db_error_never_costs_the_customer_their_reply(self, wire, monkeypatch):
        sent, state = wire
        monkeypatch.setattr(db_cloud, "recent_ad_click",
                            lambda p, **k: (_ for _ in ()).throw(RuntimeError("down")))
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]


class TestAdArrivalWithAnIntent:
    def test_welcome_is_sent_alongside_the_answer_not_instead_of_it(self, wire, monkeypatch):
        """Arriving from a print ad and typing "print" should get BOTH the
        order link and the offer that brought them."""
        sent, state = wire
        state["ad"] = _ad()
        monkeypatch.setattr(intent, "decide_intent", lambda t: "print")
        intent.route_front_door("919233255016", "print my thesis")
        body = "\n".join(sent)
        assert "REF5016XY" in body, "the offer they clicked"
        assert "printosky.com/order" in body, "and the thing they asked for"
        assert "<<GENERIC MENU>>" not in body

    def test_a_books_intent_from_an_ad_still_opens_books(self, wire, monkeypatch):
        sent, state = wire
        state["ad"] = _ad()
        opened: list[str] = []
        monkeypatch.setattr(intent, "decide_intent", lambda t: "xtraa")
        monkeypatch.setattr(intent, "_open_books",
                            lambda phone, name: opened.append(phone))
        intent.route_front_door("919233255016", "books")
        assert opened == ["919233255016"]
        assert "REF5016XY" in "\n".join(sent)


class TestShareLink:
    def test_one_definition_of_the_share_url(self):
        link = db_cloud.referral_share_link("REFTEST")
        assert link == f"https://wa.me/{db_cloud.REFERRAL_SHARE_NUMBER}?text=ref_REFTEST"
        assert "ref_REFTEST" in link, "api/index._capture_referral_code reads this back"
