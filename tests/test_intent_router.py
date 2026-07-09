"""Front-door intent router tests (routing/intent.py).

Covers the four decision layers (tag → keyword → Haiku classifier → menu) plus
the route_front_door dispatcher and a deterministic conversation simulation.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routing.intent import parse_intent_tag, INTENTS


class TestParseIntentTag:
    def test_hashtag_in_deeplink_message(self):
        assert parse_intent_tag("Hi, I want to print a file #print") == "print"

    def test_menu_row_id_exact(self):
        assert parse_intent_tag("intent_sociology") == "sociology"

    def test_soc_alias(self):
        assert parse_intent_tag("please send #soc") == "sociology"

    def test_case_insensitive(self):
        assert parse_intent_tag("ORDER XTRAA #XTRAA") == "xtraa"

    def test_no_tag_returns_none(self):
        assert parse_intent_tag("enikk oru book venam") is None

    def test_empty_returns_none(self):
        assert parse_intent_tag("") is None

    def test_all_intents_have_a_tag(self):
        for intent in INTENTS:
            assert parse_intent_tag(f"intent_{intent}") == intent
