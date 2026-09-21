"""
Answering the questions Meta puts on the ad, without waiting for a human.

A click-to-WhatsApp ad shows tappable ice-breaker questions above the compose
box. They arrive as ordinary text and they are the first thing most ad arrivals
send. On ad 120248100283360186, three of the seven people who ever clicked it
opened with the verbatim string "What services do you offer?".

All three waited. Asked 04:12, 07:45 and 01:27 UTC; all three answered by hand
at 17:01-17:02 the same day, roughly nine hours later, with "Printing and all
types of binding" -- sent twice. Meta charged us about Rs.147 for each of those
leads. None of them ordered anything.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

intent = pytest.importorskip("routing.intent", reason="routing package unavailable")


class TestRecognition:
    def test_the_string_three_real_customers_actually_sent(self):
        assert intent.icebreaker_reply("What services do you offer?") is not None

    @pytest.mark.parametrize("text", [
        "what services do you offer",       # no question mark
        "WHAT SERVICES DO YOU OFFER?",      # shouted
        "  What  services do you offer?  ",  # padded and double-spaced
    ])
    def test_shape_of_the_typing_does_not_matter(self, text):
        assert intent.icebreaker_reply(text) is not None

    @pytest.mark.parametrize("text", [
        "What are your prices?", "How much does it cost?",
        "Where are you located?", "How do I order?",
    ])
    def test_the_other_configured_ice_breakers(self, text):
        assert intent.icebreaker_reply(text) is not None

    @pytest.mark.parametrize("text", [
        "", "hi", "can you print 200 pages by tomorrow morning",
        "do you offer lamination on A3 sheets",
    ])
    def test_anything_else_falls_through_to_the_ordinary_layers(self, text):
        """A stale list must cost nothing: an unlisted question is answered no
        worse than it is today."""
        assert intent.icebreaker_reply(text) is None


class TestAnswers:
    def test_every_answer_asks_for_the_file(self):
        for question in intent.ICE_BREAKERS:
            answer = intent.icebreaker_reply(question).lower()
            assert "send your" in answer, f"{question!r} does not ask for the file"

    def test_prices_come_from_the_rate_card(self):
        from rate_card import get_print_rate
        rate = get_print_rate("A4_BW", "ds", 50, is_student=True)
        shown = f"₹{rate:.2f}".rstrip("0").rstrip(".")
        assert shown in intent.icebreaker_reply("What are your prices?")

    def test_answers_carry_the_brand_and_no_other(self):
        """Customer-facing copy is Printosky only (.agents/AGENTS.md). The
        Sep 9 replies to these same people signed off "Oxygen Students
        Paradise, Thriprayar"."""
        for question in intent.ICE_BREAKERS:
            answer = intent.icebreaker_reply(question)
            assert "Printosky" in answer
            assert "Oxygen" not in answer

    def test_no_answer_is_the_nine_hour_answer(self):
        """"Printing and all types of binding" told the customer nothing they
        could act on. Every answer carries a number or the ask."""
        for question in intent.ICE_BREAKERS:
            assert len(intent.icebreaker_reply(question)) > 80


class TestAskingForAPrice:
    """The longest list, because it is the thing people ask for most.

    "Printing charges?" arrived from a live ad click on 21 Sep 2026, four
    hours after the ice-breaker fix shipped, and was not in the list. It
    worked out — the ad welcome carries the rate card — but by accident, and
    what they got was the generic welcome rather than the rate breakdown they
    had actually asked for.
    """

    @pytest.mark.parametrize("text", [
        "Printing charges?",            # the real one, 21 Sep 2026
        "printing charges",
        "Printing charge",
        "print charges",
        "printing rate",
        "Printing rates",
        "print rate",
        "printing cost",
        "xerox rate",
        "Xerox charges?",
        "Charges",
        "charge",
        "cost",
        "How much?",
        "how much do you charge",
        "What are the charges?",
        "what is the rate",
        "What are your rates?",
        "Rate card",
        "price list",
        "prices",
    ])
    def test_it_is_answered_with_the_rate_breakdown(self, text):
        answer = intent.icebreaker_reply(text)
        assert answer is not None, f"{text!r} is not recognised as a price question"
        assert "a sheet" in answer, "the answer must carry the actual rates"

    def test_the_answer_still_asks_for_the_file(self):
        """A price with no ask is a conversation that ends politely."""
        assert "send your file" in intent.icebreaker_reply("Printing charges?").lower()

    @pytest.mark.parametrize("text", [
        "how much for 200 pages of my thesis",   # a real quote request
        "what is the cost of spiral binding for 3 copies",
    ])
    def test_a_real_question_is_left_to_the_intent_layers(self, text):
        """Matching is on the whole message, so a sentence that happens to
        contain these words still reaches the classifier — which can route it
        to the print flow instead of reciting a rate card at it."""
        assert intent.icebreaker_reply(text) is None


class TestRouting:
    @pytest.fixture
    def wire(self, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(intent, "_send_text", lambda phone, msg: sent.append(msg))
        monkeypatch.setattr(intent, "_send_menu",
                            lambda phone: sent.append("<<GENERIC MENU>>"))
        monkeypatch.setattr(intent, "_pending_payment_reminder", lambda p, n: None)
        monkeypatch.setattr(intent, "_maybe_welcome_ad_arrival",
                            lambda phone, name=None, icebreaker=None: False)
        monkeypatch.setattr(intent, "decide_intent", lambda t: "unknown")
        return sent

    def test_the_bot_answers_instead_of_the_menu(self, wire):
        intent.route_front_door("918907318168", "What services do you offer?")
        body = "\n".join(wire)
        assert "<<GENERIC MENU>>" not in body
        assert "send your" in body.lower()

    def test_it_is_answered_once_not_twice(self, wire):
        intent.route_front_door("918907318168", "What services do you offer?")
        assert len(wire) == 1


class TestTheWelcomeCarriesTheAnswer:
    """Meta delivers a tapped ice-breaker AS the message carrying `referral`.

    So on the one message an ice-breaker can ever arrive on, the ad welcome is
    firing too. The first version of this code answered the ice-breaker only
    `if not welcomed`, which on that message is never true: four of the six ad
    clicks between 17 and 19 Sep 2026 opened with one ("What services do you
    offer?" three times, "Rate send me" once) and not one was answered. The
    feature never fired in production at all.
    """

    @pytest.fixture
    def wire(self, monkeypatch):
        sent: list[str] = []
        got: dict = {}
        monkeypatch.setattr(intent, "_send_text", lambda phone, msg: sent.append(msg))
        monkeypatch.setattr(intent, "_send_menu",
                            lambda phone: sent.append("<<GENERIC MENU>>"))
        monkeypatch.setattr(intent, "_pending_payment_reminder", lambda p, n: None)
        monkeypatch.setattr(intent, "decide_intent",
                            lambda t: got.setdefault("intent_ran", True) and "unknown")

        def _welcome(phone, name=None, icebreaker=None):
            got["icebreaker"] = icebreaker
            sent.append("<<WELCOME>>")
            return True
        monkeypatch.setattr(intent, "_maybe_welcome_ad_arrival", _welcome)
        return sent, got

    def test_the_welcome_is_handed_the_answer(self, wire):
        sent, got = wire
        intent.route_front_door("918907318168", "What services do you offer?")
        assert got["icebreaker"] is not None
        assert "send your" in got["icebreaker"].lower()

    def test_still_exactly_one_message(self, wire):
        """Welcome-then-answer would be two messages saying much the same
        thing in a row, which reads as a bot talking to itself."""
        sent, _ = wire
        intent.route_front_door("918907318168", "What services do you offer?")
        assert sent == ["<<WELCOME>>"]

    def test_the_intent_layers_never_run_on_an_answered_question(self, wire):
        """"Can I book an appointment?" classifies as `xtraa`. Letting the
        intent layers run after the question was answered is how a print-ad
        click got posted the Malayalam book catalogue on 19 Sep 2026."""
        sent, got = wire
        intent.route_front_door("918907318168", "How do I order?")
        assert "intent_ran" not in got
        assert sent == ["<<WELCOME>>"]


class TestTheWelcomeItself:
    @pytest.fixture
    def sent(self, monkeypatch):
        out: list[str] = []
        monkeypatch.setattr(intent, "_send_text", lambda phone, msg: out.append(msg))
        return out

    def test_an_asked_question_replaces_the_rate_card(self, sent):
        """Someone who asked where the shop is gets the address, not a wall of
        prices that ignores them."""
        intent._send_quote_welcome("918907318168", "Vishnu",
                                   icebreaker=intent._location_answer())
        assert len(sent) == 1
        assert "Thriprayar" in sent[0]

    def test_it_still_says_which_ad_they_came_from(self, sent):
        intent._send_quote_welcome("918907318168", None,
                                   icebreaker=intent._location_answer())
        assert "Xerox queue" in sent[0]

    def test_no_question_means_the_ordinary_welcome(self, sent):
        intent._send_quote_welcome("918907318168", "Vishnu")
        assert len(sent) == 1
        assert "Send your PDF right here in this chat" in sent[0]
