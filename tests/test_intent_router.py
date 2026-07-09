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


from routing.intent import keyword_intent


class TestKeywordIntent:
    def test_print_words(self):
        for msg in ("i need a printout", "can you xerox this", "photocopy 10 pages"):
            assert keyword_intent(msg) == "print"

    def test_sociology_reuses_book_bot_trigger(self):
        assert keyword_intent("MA sociology sngu books") == "sociology"

    def test_academic_words(self):
        assert keyword_intent("need my project report binding") == "academic"

    def test_book_trigger_maps_to_xtraa_interim(self):
        # Plan 1 interim: any book intent opens the shared catalog.
        assert keyword_intent("malayalam book venam") == "xtraa"

    def test_plain_greeting_is_none(self):
        assert keyword_intent("hi") is None
        assert keyword_intent("hello") is None


from routing.intent import decide_intent, CONFIDENCE_THRESHOLD


class TestDecideIntent:
    def test_tag_wins_first(self):
        called = {"n": 0}
        def fake(_):
            called["n"] += 1
            return ("print", 0.99)
        assert decide_intent("open #sociology please", classifier=fake) == "sociology"
        assert called["n"] == 0  # LLM never consulted when a tag matches

    def test_keyword_before_llm(self):
        def fake(_):
            raise AssertionError("LLM should not be called")
        assert decide_intent("need a printout", classifier=fake) == "print"

    def test_llm_used_for_freeform_when_confident(self):
        # A message with NO tag and NO trigger word — so the LLM layer decides.
        def fake(_):
            return ("malayalam", 0.9)
        assert decide_intent("something for my child to read", classifier=fake) == "malayalam"

    def test_low_confidence_falls_to_unknown(self):
        def fake(_):
            return ("academic", CONFIDENCE_THRESHOLD - 0.1)
        assert decide_intent("hmm something", classifier=fake) == "unknown"

    def test_llm_unknown_falls_to_unknown(self):
        def fake(_):
            return ("unknown", 0.0)
        assert decide_intent("blah blah", classifier=fake) == "unknown"

    def test_llm_bad_intent_ignored(self):
        def fake(_):
            return ("weather", 0.99)  # not a real intent
        assert decide_intent("random", classifier=fake) == "unknown"
