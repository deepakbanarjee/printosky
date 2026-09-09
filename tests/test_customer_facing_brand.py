"""
Customer-facing copy is Printosky only.

.agents/AGENTS.md:24 — "The customer-facing brand is strictly Printosky. NEVER
mention 'Oxygen' or 'Oxygen Students Paradise' in any public posts, captions,
marketing assets, carousels, stories, posters, or customer communications."

The rule was being broken in the two highest-traffic messages in the system:
the welcome a new customer reads first, and the print-link reply. An arrival
from a Printosky ad on 7 Sep tapped "Print a file" and was answered by a
different shop's name.

Scoped deliberately to the message constants rather than a repo-wide grep:
store_config, Razorpay `notes` and the internal PDF reports legitimately carry
the legal entity, and a blanket ban would either fail on those or teach people
to skip the test.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

BANNED = "oxygen"


class TestFrontDoorCopy:
    def test_link_messages_are_printosky_only(self):
        from routing.intent import _LINK_MESSAGES
        for intent, msg in _LINK_MESSAGES.items():
            assert BANNED not in msg.lower(), (
                f"_LINK_MESSAGES[{intent!r}] names another brand to the customer")

    def test_menu_rows_are_printosky_only(self):
        from routing.intent import build_menu_rows
        for row in build_menu_rows():
            blob = f"{row.get('title','')} {row.get('description','')}".lower()
            assert BANNED not in blob

    def test_welcome_and_greeting_are_printosky_only(self):
        import api.index as api_mod
        for name in ("WELCOME_MESSAGE", "GREETING_MESSAGE"):
            msg = getattr(api_mod, name, "")
            assert msg, f"{name} vanished — this test is now watching nothing"
            assert BANNED not in msg.lower(), f"{name} names another brand"

    def test_the_welcome_still_says_who_we_are_and_where(self):
        """Dropping the second name must not drop the shop's identity."""
        import api.index as api_mod
        msg = api_mod.WELCOME_MESSAGE
        assert "Printosky" in msg
        assert "Thrissur" in msg or "Thriprayar" in msg


class TestDeliberateException:
    def test_the_payment_instruction_keeps_the_account_name(self):
        """Customers pay into an account in the legal entity's name. Renaming it
        in the message would cause failed and disputed transfers, which is a
        worse outcome than the branding rule is trying to prevent. Pinned here
        so the exception is a decision on the record, not an oversight.
        """
        import pathlib
        src = pathlib.Path(__file__).resolve().parent.parent / "book_bot.py"
        text = src.read_text(encoding="utf-8", errors="ignore")
        assert "Please pay to *Oxygen Students Paradise*" in text, (
            "if the payee name changed, update the bank/UPI account too")
