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


import routing.intent as ir


class TestRouteFrontDoor:
    def setup_method(self):
        self.sent = []    # (phone, text) plain sends
        self.menus = []   # phone
        self.opened = []  # flow name

    def _patch(self, monkeypatch, intent):
        monkeypatch.setattr(ir, "decide_intent", lambda text: intent)
        monkeypatch.setattr(ir, "_pending_payment_reminder", lambda phone, name=None: None)
        monkeypatch.setattr(ir, "_send_text", lambda phone, msg: self.sent.append((phone, msg)))
        monkeypatch.setattr(ir, "_send_menu", lambda phone: self.menus.append(phone))
        monkeypatch.setattr(ir, "_open_books", lambda phone, name: self.opened.append("books"))
        monkeypatch.setattr(ir, "_open_soc", lambda phone, name: self.opened.append("soc"))

    def test_print_sends_order_link(self, monkeypatch):
        self._patch(monkeypatch, "print")
        ir.route_front_door("91999", "whatever", "Ann")
        assert any("printosky.com/order" in m for _, m in self.sent)
        assert not self.opened and not self.menus

    def test_academic_sends_academic_link(self, monkeypatch):
        self._patch(monkeypatch, "academic")
        ir.route_front_door("91999", "x", None)
        assert any("printosky.com/academic" in m for _, m in self.sent)

    def test_xtraa_opens_books(self, monkeypatch):
        self._patch(monkeypatch, "xtraa")
        ir.route_front_door("91999", "x", None)
        assert self.opened == ["books"]

    def test_malayalam_opens_books_interim(self, monkeypatch):
        self._patch(monkeypatch, "malayalam")
        ir.route_front_door("91999", "x", None)
        assert self.opened == ["books"]

    def test_sociology_opens_soc(self, monkeypatch):
        self._patch(monkeypatch, "sociology")
        ir.route_front_door("91999", "x", None)
        assert self.opened == ["soc"]

    def test_unknown_sends_menu(self, monkeypatch):
        self._patch(monkeypatch, "unknown")
        ir.route_front_door("91999", "???", None)
        assert self.menus == ["91999"]

    def test_menu_rows_cover_every_intent(self):
        ids = {r["id"] for r in ir.build_menu_rows()}
        for intent in ("print", "xtraa", "malayalam", "sociology", "academic"):
            assert f"intent_{intent}" in ids

    def test_pending_payment_reminder_wins_over_intent(self, monkeypatch):
        # A customer who owes payment is reminded first — intent never consulted.
        self._patch(monkeypatch, "print")
        monkeypatch.setattr(ir, "_pending_payment_reminder",
                            lambda phone, name=None: ["Order XTR-1 awaiting payment — send screenshot"])
        called = {"decide": 0}
        monkeypatch.setattr(ir, "decide_intent",
                            lambda text: called.__setitem__("decide", called["decide"] + 1) or "print")
        ir.route_front_door("91999", "anything", None)
        assert any("awaiting payment" in m for _, m in self.sent)
        assert called["decide"] == 0
        assert not self.opened and not self.menus


class TestPendingPaymentReminder:
    def test_awaiting_payment_forwards_start_catalog(self, monkeypatch):
        import db_cloud
        import book_bot
        monkeypatch.setattr(db_cloud, "get_active_book_order",
                            lambda phone: {"status": "awaiting_payment", "order_code": "XTR-1"},
                            raising=False)
        monkeypatch.setattr(book_bot, "start_catalog",
                            lambda phone, name=None: ["Order XTR-1 awaiting payment — send screenshot or reply NEW"],
                            raising=False)
        out = ir._pending_payment_reminder("91999", None)
        assert out and "awaiting payment" in out[0]

    def test_no_order_returns_none(self, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "get_active_book_order", lambda phone: None, raising=False)
        assert ir._pending_payment_reminder("91999", None) is None

    def test_non_payment_status_returns_none(self, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "get_active_book_order",
                            lambda phone: {"status": "collecting"}, raising=False)
        assert ir._pending_payment_reminder("91999", None) is None


import pytest

# (message, expected_intent) through the FULL decide_intent chain. Most real
# messages resolve deterministically (tag/keyword — free, predictable). Only the
# genuinely free-form rows fall through to the (mocked) classifier.
_SIM_CASES = [
    # deterministic — tag
    ("Hi, I want to print a file #print", "print"),
    ("intent_academic", "academic"),
    # deterministic — keyword / book triggers
    ("need a printout of 3 pages", "print"),
    ("can you xerox this", "print"),
    ("enikk oru file print cheyyanam", "print"),
    ("MA sociology sngu book", "sociology"),
    ("sociology theory pusthakam undo", "sociology"),
    ("project report binding venam", "academic"),
    ("malayalam book venam", "xtraa"),          # interim: books → xtraa
    ("easy english book", "xtraa"),
    ("aksharamrutham kittumo", "xtraa"),         # known title, interim → shared catalog
    ("do you sell hindi books", "xtraa"),
    ("upload notes", "notes"),
    # free-form — resolved by the (mocked) classifier
    ("a reader for my child in mother tongue", "malayalam"),
    # unclear → menu
    ("hi", "unknown"),
    ("hello there", "unknown"),
    ("??", "unknown"),
]

# Mock Haiku only for the rows tag/keyword do not catch; others short-circuit.
_MOCK_LLM = {
    "a reader for my child in mother tongue": ("malayalam", 0.9),
}


@pytest.mark.parametrize("msg,expected", _SIM_CASES)
def test_conversation_simulation(msg, expected):
    def fake_classifier(text):
        return _MOCK_LLM.get(text, ("unknown", 0.0))
    assert decide_intent(msg, classifier=fake_classifier) == expected
