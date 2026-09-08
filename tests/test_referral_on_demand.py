"""
MY CREDITS mints a referral code on demand.

The live click-to-WhatsApp ad ("Print your Thesis for Rs.0") tells people to
text MY CREDITS and get their share link "in 10 seconds". Until this change the
bot could only LOOK UP a code: codes were created solely by the logged-in web
account page and by review_manager after a 4-5 star review. So a first-time
sender -- which is everyone arriving from the ad -- got:

    "You don't have a Printosky referral code yet. Tip: rate your next order
     4 or 5 stars..."

On 2 Sep a real arrival hit exactly that, and the referral tables held nothing
but test rows for the whole flight of the campaign. Every click that followed
the ad's own instructions was paid for and wasted.
"""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

db_cloud = pytest.importorskip("db_cloud", reason="supabase SDK not installed")


def _sb(existing_label=None, taken_codes=()):
    """A Supabase double: label lookup, code-collision lookup, insert."""
    sb = MagicMock()
    inserted = []

    def table(name):
        t = MagicMock()

        def select(_cols):
            q = MagicMock()

            def eq(col, val):
                r = MagicMock()
                if col == "label":
                    r.execute.return_value = MagicMock(
                        data=[{"code": existing_label}] if existing_label else [])
                else:  # code-collision probe
                    r.execute.return_value = MagicMock(
                        data=[{"code": val}] if val in taken_codes else [])
                return r

            q.eq.side_effect = eq
            return q

        t.select.side_effect = select
        t.insert.side_effect = lambda row: (inserted.append(row), MagicMock(execute=MagicMock()))[1]
        return t

    sb.table.side_effect = table
    sb.inserted = inserted
    return sb


class TestEnsureReferralCode:
    def test_existing_code_is_reused_never_duplicated(self, monkeypatch):
        sb = _sb(existing_label="REF9999AA")
        monkeypatch.setattr(db_cloud, "_client", lambda: sb)
        assert db_cloud.ensure_referral_code("919495706405") == "REF9999AA"
        assert sb.inserted == [], "a second code would split their credit balance"

    def test_first_timer_gets_a_code_minted(self, monkeypatch):
        sb = _sb()
        monkeypatch.setattr(db_cloud, "_client", lambda: sb)
        code = db_cloud.ensure_referral_code("919495706405")
        assert code and code.startswith("REF6405")
        assert sb.inserted == [{"code": code, "label": "919495706405",
                                "platform": "whatsapp_selfserve"}]

    def test_suffix_collision_is_retried(self, monkeypatch):
        """Only 676 two-letter suffixes per phone tail, so collisions happen."""
        sb = _sb(taken_codes={"REF6405AA"})
        monkeypatch.setattr(db_cloud, "_client", lambda: sb)
        with patch.object(db_cloud, "_referral_code_for",
                          side_effect=["REF6405AA", "REF6405ZZ"]):
            assert db_cloud.ensure_referral_code("919495706405") == "REF6405ZZ"

    def test_gives_up_and_returns_none_rather_than_looping(self, monkeypatch):
        sb = _sb(taken_codes={"REF6405AA"})
        monkeypatch.setattr(db_cloud, "_client", lambda: sb)
        with patch.object(db_cloud, "_referral_code_for", return_value="REF6405AA"):
            assert db_cloud.ensure_referral_code("919495706405") is None
        assert sb.inserted == []

    def test_blank_phone_is_declined(self, monkeypatch):
        monkeypatch.setattr(db_cloud, "_client",
                            lambda: pytest.fail("must not touch the DB"))
        assert db_cloud.ensure_referral_code("") is None

    def test_code_format_matches_the_other_minting_site(self):
        """review_manager mints after a 5-star review; a code from the bot must
        be indistinguishable, or the two paths drift apart silently."""
        rm = pytest.importorskip("review_manager")
        import re
        for phone in ("919495706405", "918848623472", "7", ""):
            a = db_cloud._referral_code_for(phone)
            b = rm._generate_referral_code(phone)
            shape = re.compile(r"^REF\d{4}[A-Z]{2}$")
            assert shape.match(a), f"db_cloud produced {a!r}"
            assert shape.match(b), f"review_manager produced {b!r}"
            assert a[:7] == b[:7], "same phone must yield the same REF+tail prefix"


class TestMyCreditsReply:
    """The ad's call to action, end to end."""

    def _wire(self, monkeypatch, code, sent):
        import api.index as api_mod
        monkeypatch.setattr(api_mod, "_normalize_phone", lambda p: p)
        credits = MagicMock()
        credits.table.return_value.select.return_value.eq.return_value \
               .is_.return_value.execute.return_value = MagicMock(data=[])
        monkeypatch.setattr(db_cloud, "_client", lambda: credits)
        monkeypatch.setattr(db_cloud, "ensure_referral_code", lambda p, **k: code)
        import whatsapp_notify
        monkeypatch.setattr(whatsapp_notify, "_send",
                            lambda to, msg: sent.append(msg) or True)
        return api_mod

    def test_first_timer_gets_a_share_link_not_a_brush_off(self, monkeypatch):
        sent: list[str] = []
        api_mod = self._wire(monkeypatch, "REF3472QW", sent)
        api_mod._send_credits_balance("918848623472")
        assert len(sent) == 1
        body = sent[0]
        assert "REF3472QW" in body
        assert "wa.me" in body, "the share link is the whole point of the ad"
        assert "rate your next order" not in body.lower(), "the old dead end"

    def test_mint_failure_alerts_and_says_so(self, monkeypatch):
        sent: list[str] = []
        api_mod = self._wire(monkeypatch, None, sent)
        with patch.object(api_mod, "_alert_ops") as alert:
            api_mod._send_credits_balance("918848623472")
        alert.assert_called_once(), "a paid click silently getting nothing is the banned failure"
        assert sent and "couldn't set up your share link" in sent[0]
