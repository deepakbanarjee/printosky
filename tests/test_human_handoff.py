"""Tests for the human-handoff heuristic in api/index.py.

When an idle, returning customer sends free-text no handler understands, the
webhook holds the conversation for a human instead of ignoring it or dumping a
menu. `_should_handoff_text` decides whether a message is worth a handoff.
"""
import os
import sys
import types

# ── stub optional heavy deps so api.index imports cleanly (mirrors test_help_escape) ──
sys.modules.setdefault("dotenv", types.ModuleType("dotenv"))
sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None
for _m in ("watchdog", "watchdog.observers", "watchdog.events"):
    sys.modules.setdefault(_m, types.ModuleType(_m))
sys.modules["watchdog.observers"].Observer = object
sys.modules["watchdog.events"].FileSystemEventHandler = object

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import api.index as api_mod  # noqa: E402


class TestShouldHandoffText:
    def test_real_question_handed_off(self):
        assert api_mod._should_handoff_text("What is the courier called?") is True

    def test_sentence_handed_off(self):
        assert api_mod._should_handoff_text("I need help with my order please") is True

    def test_malayalam_sentence_handed_off(self):
        assert api_mod._should_handoff_text("എനിക്ക് ഒരു സംശയം ഉണ്ട്") is True

    def test_courtesy_ok_ignored(self):
        assert api_mod._should_handoff_text("ok") is False

    def test_courtesy_thanks_ignored(self):
        assert api_mod._should_handoff_text("thank you") is False

    def test_bare_digit_ignored(self):
        assert api_mod._should_handoff_text("3") is False

    def test_emoji_ignored(self):
        assert api_mod._should_handoff_text("👍") is False

    def test_empty_ignored(self):
        assert api_mod._should_handoff_text("") is False

    def test_too_short_ignored(self):
        assert api_mod._should_handoff_text("hi") is False

    def test_yes_no_ignored(self):
        assert api_mod._should_handoff_text("yes") is False
        assert api_mod._should_handoff_text("no") is False
