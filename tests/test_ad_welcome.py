"""
Answering the ad someone actually clicked.

Both real ad arrivals on 7 Sep (ad 120248100283360186) replied "H", were sent
the generic five-option menu -- Print a file / Xtraa books / Malayalam book /
Sociology books / Academic project -- and went quiet. The ad is the first half
of a conversation; these tests pin the second half.

They also pin WHICH second half. The ad ran 7-14 Sep and produced 8 clicks,
7 people, 0 print jobs and Rs.0. Five of those seven opened their relationship
with us by being handed a referral link -- "recruit 15-20 classmates and your
printing is free" -- in answer to an ad that said "send your PDF and skip the
queue". A cold click now gets a price and an invitation to send the file; the
referral pitch is kept for an ad aimed at people who already buy from us.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

intent = pytest.importorskip("routing.intent", reason="routing package unavailable")
db_cloud = pytest.importorskip("db_cloud", reason="supabase SDK not installed")

AD = "120248100283360186"          # the live campaign — quote-first
REFERRAL_AD = "120000000000000"    # a referral campaign, registered per-test
OTHER_AD = "999999999999999"       # some future campaign, not configured


@pytest.fixture
def wire(monkeypatch):
    """Capture what the front door sends, and stub everything it reaches for."""
    sent: list[str] = []
    state = {"code": None, "ad": None, "minted": [], "welcomed_already": False}

    monkeypatch.setattr(intent, "_send_text", lambda phone, msg: sent.append(msg))
    monkeypatch.setattr(intent, "_send_menu",
                        lambda phone: sent.append("<<GENERIC MENU>>"))
    monkeypatch.setattr(intent, "_pending_payment_reminder", lambda p, n: None)
    monkeypatch.setattr(intent, "decide_intent", lambda t: "unknown")

    monkeypatch.setattr(db_cloud, "get_referral_code", lambda p: state["code"])
    monkeypatch.setattr(db_cloud, "recent_ad_click", lambda p, **k: state["ad"])
    monkeypatch.setattr(db_cloud, "ad_welcome_already_sent",
                        lambda p, since: state["welcomed_already"])

    def _mint(phone, platform="whatsapp_selfserve"):
        state["minted"].append((phone, platform))
        return state["code"] or "REF5016XY"
    monkeypatch.setattr(db_cloud, "ensure_referral_code", _mint)
    return sent, state


@pytest.fixture
def referral_campaign(monkeypatch):
    """Register a referral-kind ad, so the kind stays covered after the live
    campaign moved to quote-first."""
    monkeypatch.setitem(intent.AD_CAMPAIGN_KIND, REFERRAL_AD, "referral")


def _ad(source_id=AD):
    return {"phone": "919233255016", "source_id": source_id,
            "headline": "Chat with us", "clicked_at": "2026-09-07T12:27:31+00:00"}


class TestColdAdArrival:
    """The live ad sells printing. The reply has to sell printing."""

    def test_arrival_is_asked_for_the_file_not_for_referrals(self, wire):
        sent, state = wire
        state["ad"] = _ad()
        intent.route_front_door("919233255016", "H", "Arun")
        body = "\n".join(sent)
        assert "<<GENERIC MENU>>" not in body, "the menu answers a different conversation"
        assert "send your pdf" in body.lower(), "the ad promised WhatsApp; ask for the file"
        assert "wa.me" not in body, "a stranger has nothing to vouch for yet"
        assert state["minted"] == [], "no code is minted for someone who has not bought"

    def test_the_price_comes_from_the_rate_card(self, wire):
        """A rate the owner changes must not leave a stale number in the one
        message we pay Meta to deliver."""
        from rate_card import get_print_rate
        sent, state = wire
        state["ad"] = _ad()
        intent.route_front_door("919233255016", "H")
        student_rate = get_print_rate("A4_BW", "ds", 50, is_student=True)
        assert f"₹{student_rate:.2f}".rstrip("0").rstrip(".") in "\n".join(sent)

    def test_the_per_page_claim_is_half_the_sheet_rate(self):
        """Rs.2 a sheet duplex is Rs.1 a page, not the 50 paise it looks like
        at a glance -- this shipped wrong once. CLAUDE.md: "75 paise to Rs.1.00
        per page"."""
        from rate_card import get_print_rate
        for sheets in (50, 150):
            rate = get_print_rate("A4_BW", "ds", sheets, is_student=True)
            claim = intent._per_page(rate)
            paise = int(claim.split()[0]) if "paise" in claim else None
            assert (paise / 100 if paise is not None
                    else float(claim.split()[0].lstrip("₹"))) == rate / 2

    def test_money_is_written_as_money(self):
        """Rs.1.5 reads as a bug in a price list; Rs.2.00 reads as amateur."""
        assert intent._rupees(1.5) == "1.50"
        assert intent._rupees(2.0) == "2"
        assert intent._rupees(220) == "220"

    def test_arrival_is_not_pushed_to_the_website(self, wire):
        """printosky.com/order is a second queue in place of the one the ad
        said they could skip. The one arrival who followed it never came back."""
        sent, state = wire
        state["ad"] = _ad()
        intent.route_front_door("919233255016", "H")
        assert "printosky.com/order" not in "\n".join(sent)

    def test_no_ad_click_still_gets_the_menu(self, wire):
        sent, state = wire
        state["ad"] = None
        intent.route_front_door("919000000001", "H")
        assert sent == ["<<GENERIC MENU>>"]

    def test_unconfigured_ad_falls_through_rather_than_guessing(self, wire):
        """A future campaign selling something else must not be handed this
        welcome just because it is an ad."""
        sent, state = wire
        state["ad"] = _ad(OTHER_AD)
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]
        assert state["minted"] == []

    def test_fires_only_once_per_person(self, wire):
        """The quote welcome mints nothing, so "do they hold a code" cannot
        guard it -- without the conversation_log check it would re-send itself
        on every unrecognised reply for 72 hours."""
        sent, state = wire
        state["ad"] = _ad()
        state["welcomed_already"] = True
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]

    def test_a_db_error_never_costs_the_customer_their_reply(self, wire, monkeypatch):
        sent, state = wire
        monkeypatch.setattr(db_cloud, "recent_ad_click",
                            lambda p, **k: (_ for _ in ()).throw(RuntimeError("down")))
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]

    def test_missing_rate_card_still_answers(self, wire, monkeypatch):
        """No price beats a guessed price -- but never beats silence."""
        sent, state = wire
        state["ad"] = _ad()
        monkeypatch.setattr(intent, "price_headlines", dict)
        intent.route_front_door("919233255016", "H")
        assert "send your pdf" in "\n".join(sent).lower()


class TestReferralCampaign:
    """Still supported, for an ad aimed at people who already buy from us."""

    def test_referral_ad_still_sends_the_share_link(self, wire, referral_campaign):
        sent, state = wire
        state["ad"] = _ad(REFERRAL_AD)
        intent.route_front_door("919233255016", "H", "Arun")
        body = "\n".join(sent)
        assert "REF5016XY" in body and "wa.me" in body
        assert "20" in body, "the Rs.20-per-classmate offer is the hook"
        assert state["minted"] == [("919233255016", "ad_click")]

    def test_someone_who_already_holds_a_code_is_not_pitched_twice(
            self, wire, referral_campaign):
        sent, state = wire
        state["ad"] = _ad(REFERRAL_AD)
        state["code"] = "REF5016XY"
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]
        assert state["minted"] == []

    def test_a_mint_failure_falls_back_to_the_menu(self, wire, referral_campaign,
                                                   monkeypatch):
        """Never leave the customer with silence."""
        sent, state = wire
        state["ad"] = _ad(REFERRAL_AD)
        monkeypatch.setattr(db_cloud, "ensure_referral_code", lambda p, **k: None)
        intent.route_front_door("919233255016", "H")
        assert sent == ["<<GENERIC MENU>>"]


class TestAdArrivalWithAnIntent:
    def test_welcome_is_sent_alongside_the_answer_not_instead_of_it(self, wire, monkeypatch):
        """Arriving from the print ad and typing "print" should get BOTH the
        welcome and the print answer."""
        sent, state = wire
        state["ad"] = _ad()
        monkeypatch.setattr(intent, "decide_intent", lambda t: "print")
        intent.route_front_door("919233255016", "print my thesis")
        body = "\n".join(sent)
        assert len(sent) == 2, "the welcome and the answer, not one or the other"
        assert "printosky.com/order" in body, "the thing they asked for"
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
        assert "send your pdf" in "\n".join(sent).lower()


class TestPrintAnswerKeepsTheFileInWhatsApp:
    def test_the_file_is_asked_for_before_the_web_form(self):
        msg = intent._LINK_MESSAGES["print"].lower()
        assert msg.index("send your pdf") < msg.index("printosky.com/order"), (
            "the ad promises WhatsApp; the web form is the fallback, not the ask")


class TestShareLink:
    def test_one_definition_of_the_share_url(self):
        link = db_cloud.referral_share_link("REFTEST")
        assert link == f"https://wa.me/{db_cloud.REFERRAL_SHARE_NUMBER}?text=ref_REFTEST"
        assert "ref_REFTEST" in link, "api/index._capture_referral_code reads this back"
