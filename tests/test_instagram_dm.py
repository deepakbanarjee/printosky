"""
The door that was never built.

Every table in this system is keyed on a phone number. Instagram Direct does
not have one — a person is an IGSID, unique to them and to the business
account — and _normalize_phone() would turn one into a phone number belonging
to a stranger. So there was no webhook, and a DM produced no row, no console
entry, no alert and no reply.

Measured 2026-09-21, before any of this: all 16 conversations Meta billed
between 17 Aug and 21 Sep are WhatsApp, and 14 of the 15 people in them have
an ad_clicks row (the 15th first wrote two days before that table existed). So
there is no Instagram traffic yet. This is built for organic DMs and for the
day an Instagram-Direct ad runs, and these tests are what stands in for the
live traffic that has not arrived.

They pin, in particular, the three things that would fail silently:
an echo answered as if it were a customer, a welcome sent twice, and a reply
that never left with nobody told.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

ig = pytest.importorskip("instagram_dm")

IGSID = "17841400000000123"
ACCOUNT = "17841400000000999"
AD_ID = "120248100283360186"


def _payload(*messaging):
    return {"object": "instagram",
            "entry": [{"id": ACCOUNT, "time": 1758000000,
                       "messaging": list(messaging)}]}


def _msg(text="hi", mid="m1", referral=None, is_echo=False):
    message = {"mid": mid, "text": text}
    if is_echo:
        message["is_echo"] = True
    item = {"sender": {"id": IGSID}, "recipient": {"id": ACCOUNT},
            "timestamp": 1758000000123, "message": message}
    if referral:
        item["referral"] = referral
    return item


class TestTellingTheProductsApart:
    """WhatsApp and Instagram share the app, the callback URL and the
    signature. `object` is the only thing that says which one arrived."""

    def test_an_instagram_body_is_recognised(self):
        assert ig.is_instagram_payload(_payload(_msg())) is True

    def test_a_whatsapp_body_is_not(self):
        assert ig.is_instagram_payload(
            {"object": "whatsapp_business_account", "entry": []}) is False

    @pytest.mark.parametrize("body", [{}, None, {"entry": []}, {"object": ""}])
    def test_junk_is_not_instagram(self, body):
        assert ig.is_instagram_payload(body) is False


class TestParsing:
    def test_a_plain_dm(self):
        events = ig.parse_webhook(_payload(_msg("what are your prices?")))
        assert len(events) == 1
        assert events[0]["igsid"] == IGSID
        assert events[0]["text"] == "what are your prices?"
        assert events[0]["mid"] == "m1"

    def test_our_own_words_coming_back_are_dropped(self):
        """An echo answered as if it were a customer is a bot talking to
        itself forever."""
        assert ig.parse_webhook(_payload(_msg(is_echo=True))) == []

    def test_a_read_receipt_is_not_a_message(self):
        item = {"sender": {"id": IGSID}, "recipient": {"id": ACCOUNT},
                "read": {"mid": "m1"}}
        assert ig.parse_webhook(_payload(item)) == []

    def test_a_referral_with_no_text_still_counts(self):
        """Tapping an ad opens the thread before a word is typed. That is the
        message the ad id rides in on, and losing it loses the attribution."""
        events = ig.parse_webhook(_payload(
            {"sender": {"id": IGSID}, "recipient": {"id": ACCOUNT},
             "referral": {"ref": "x", "source": "ADS", "ad_id": AD_ID}}))
        assert len(events) == 1 and events[0]["referral"]["ad_id"] == AD_ID

    def test_a_sender_with_no_id_is_skipped(self):
        assert ig.parse_webhook(_payload(
            {"sender": {}, "message": {"mid": "m1", "text": "hi"}})) == []

    def test_several_messages_in_one_delivery(self):
        assert len(ig.parse_webhook(_payload(
            _msg("one", "m1"), _msg("two", "m2")))) == 2

    @pytest.mark.parametrize("body", [
        {}, None, {"object": "instagram"},
        {"object": "instagram", "entry": None},
        {"object": "instagram", "entry": [{"messaging": None}]},
        {"object": "instagram", "entry": [{}]},
    ])
    def test_an_unexpected_shape_yields_nothing_rather_than_raising(self, body):
        """This shape could not be checked against Meta's own docs when it was
        written. A parser that raises on a surprise drops the message."""
        assert ig.parse_webhook(body) == []


class TestReferralMapping:
    def test_the_ad_id_becomes_source_id(self):
        """The ad report reads source_id and must not learn two names for the
        same thing."""
        out = ig.referral_to_ad_click(
            {"ref": "promo", "source": "ADS", "type": "OPEN_THREAD",
             "ad_id": AD_ID, "ads_context_data": {"ad_title": "Skip the queue"}})
        assert out["source_id"] == AD_ID
        assert out["source_type"] == "ADS"
        assert out["source_url"] == "promo"
        assert out["headline"] == "Skip the queue"

    def test_there_is_no_click_id(self):
        """Instagram issues no ctwa_clid. Inventing one would send Meta a
        conversion attributed to nobody, which is worse than sending none."""
        assert ig.referral_to_ad_click({"ad_id": AD_ID})["ctwa_clid"] is None

    def test_an_empty_referral_does_not_invent_an_ad(self):
        assert ig.referral_to_ad_click({})["source_id"] is None


class TestInstagramRendersNoMarkdown:
    """The shop's copy is written for WhatsApp and is full of *bold*. Left
    alone, an Instagram customer reads the asterisks and thinks the bot is
    broken before it has said anything else."""

    def test_bold_loses_its_asterisks(self):
        assert ig.strip_wa_markdown("*Send your PDF right here*") == \
            "Send your PDF right here"

    def test_the_welcome_comes_out_clean(self):
        intent = pytest.importorskip("routing.intent")
        out = ig.strip_wa_markdown(intent.quote_welcome_text("Vishnu"))
        assert "*" not in out and "_" not in out
        assert "Send your PDF right here in this chat" in out
        assert "Printosky" in out

    def test_a_lone_asterisk_survives(self):
        assert ig.strip_wa_markdown("2 * 3 = 6") == "2 * 3 = 6"

    @pytest.mark.parametrize("text", ["", None])
    def test_nothing_in_nothing_out(self, text):
        assert ig.strip_wa_markdown(text) == ""


class TestSendingFailsLoud:
    @pytest.fixture(autouse=True)
    def _env(self, monkeypatch):
        monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", ACCOUNT)
        monkeypatch.setenv("INSTAGRAM_TOKEN", "tok")

    @pytest.fixture
    def reports(self, monkeypatch):
        seen: list = []
        import ops_watchdog
        monkeypatch.setattr(ops_watchdog, "report",
                            lambda c, ok, d="", **k: seen.append((c, ok, d)))
        return seen

    def _resp(self, status, payload=None):
        class _R:
            status_code = status

            @staticmethod
            def json():
                if payload is None:
                    raise ValueError("no json")
                return payload
            text = "boom"
        return _R()

    def test_a_good_send_posts_to_the_account_node(self, monkeypatch, reports):
        sent = {}

        def _post(url, json=None, headers=None, timeout=None):
            sent.update(url=url, body=json, headers=headers)
            return self._resp(200, {})
        monkeypatch.setattr("requests.post", _post)

        assert ig.send_text(IGSID, "*hello*") is True
        assert sent["url"].endswith(f"/{ACCOUNT}/messages")
        assert sent["body"] == {"recipient": {"id": IGSID},
                                "message": {"text": "hello"}}
        assert sent["headers"]["Authorization"] == "Bearer tok"

    def test_a_rejected_send_is_reported_with_metas_reason(self, monkeypatch,
                                                           reports):
        """The send node is the one thing here that could not be checked
        against the docs, so it is the thing most likely to be wrong on the
        first live DM. It must say so out loud."""
        monkeypatch.setattr("requests.post", lambda *a, **k: self._resp(
            400, {"error": {"message": "Unsupported post request"}}))
        assert ig.send_text(IGSID, "hello") is False
        assert reports[-1][0] == "instagram.send" and reports[-1][1] is False
        assert "Unsupported post request" in reports[-1][2]

    def test_a_network_error_is_reported_not_raised(self, monkeypatch, reports):
        def _boom(*a, **k):
            raise ConnectionError("dns")
        monkeypatch.setattr("requests.post", _boom)
        assert ig.send_text(IGSID, "hello") is False
        assert reports[-1][:2] == ("instagram.send", False)

    def test_being_unconfigured_is_an_alert_not_a_shrug(self, monkeypatch,
                                                        reports):
        """Receiving works without the token; answering does not. A DM that
        goes unanswered because nobody set an env var is exactly the silence
        docs/FAIL_LOUD.md forbids."""
        monkeypatch.delenv("INSTAGRAM_ACCOUNT_ID")
        monkeypatch.delenv("INSTAGRAM_TOKEN", raising=False)
        monkeypatch.delenv("META_SYSTEM_USER_TOKEN", raising=False)
        assert ig.send_text(IGSID, "hello") is False
        assert reports[-1][:2] == ("instagram.send", False)
        assert "INSTAGRAM_ACCOUNT_ID" in reports[-1][2]

    def test_an_empty_message_is_never_sent(self, monkeypatch, reports):
        monkeypatch.setattr("requests.post", lambda *a, **k: 1 / 0)
        assert ig.send_text(IGSID, "") is False
        assert ig.send_text("", "hi") is False


class TestTheWebhookLadder:
    """The same ladder PR #139 established on WhatsApp: the ad welcome, the
    ice-breaker answer, then a human. Deliberately NOT route_front_door — that
    is phone-shaped all the way down and an IGSID through it would collide
    with somebody's real number."""

    @pytest.fixture
    def wired(self, monkeypatch):
        api = pytest.importorskip("api.index")
        import db_cloud
        state = {"sent": [], "clicks": [], "alerts": [], "touch": [],
                 "thread": {}, "logged": []}

        monkeypatch.setattr(ig, "send_text",
                            lambda i, t: state["sent"].append(t) or True)
        monkeypatch.setattr(db_cloud, "log_message",
                            lambda *a, **k: state["logged"].append((a, k)))
        monkeypatch.setattr(db_cloud, "record_ad_click",
                            lambda *a, **k: state["clicks"].append(k) or True)
        monkeypatch.setattr(db_cloud, "touch_instagram_thread",
                            lambda i, **k: state["touch"].append(k) or True)
        monkeypatch.setattr(db_cloud, "get_instagram_thread",
                            lambda i: dict(state["thread"]))
        monkeypatch.setattr(api, "_alert_ops",
                            lambda s, f: state["alerts"].append(s) or True)
        monkeypatch.setattr(api, "_mark_webhook_processed", lambda i, h: True)
        return api, state

    def test_an_ad_arrival_gets_the_welcome(self, wired):
        api, state = wired
        api._process_instagram_webhook(_payload(_msg(
            "hi", referral={"ref": "x", "source": "ADS", "ad_id": AD_ID})))
        assert len(state["sent"]) == 1
        assert "skip the Xerox queue" in state["sent"][0]
        assert any(t.get("welcomed") for t in state["touch"])

    def test_the_ad_is_recorded_against_the_igsid(self, wired):
        api, state = wired
        api._process_instagram_webhook(_payload(_msg(
            "hi", referral={"ref": "x", "source": "ADS", "ad_id": AD_ID})))
        assert state["clicks"][0]["channel"] == "instagram"
        assert state["clicks"][0]["igsid"] == IGSID

    def test_an_ad_arrivals_icebreaker_rides_in_the_welcome(self, wired):
        api, state = wired
        api._process_instagram_webhook(_payload(_msg(
            "Where are you located?",
            referral={"ref": "x", "source": "ADS", "ad_id": AD_ID})))
        assert len(state["sent"]) == 1
        assert "Thriprayar" in state["sent"][0]

    def test_the_welcome_never_fires_twice(self, wired):
        """A phone thread answers this from conversation_log; a thread with no
        phone cannot be looked up that way, so it is a column instead."""
        api, state = wired
        state["thread"] = {"first_ad_at": "2026-09-21T06:00:00Z",
                           "welcomed_at": "2026-09-21T06:00:05Z"}
        api._process_instagram_webhook(_payload(_msg(
            "hi", referral={"ref": "x", "source": "ADS", "ad_id": AD_ID})))
        assert not any("skip the Xerox queue" in m for m in state["sent"])

    def test_a_greeting_is_answered_rather_than_escalated(self, wired):
        """There is no tap-to-choose menu on this channel, and fetching a
        person to answer "hi" is how an inbox stops being read."""
        api, state = wired
        api._process_instagram_webhook(_payload(_msg("hi")))
        assert len(state["sent"]) == 1
        assert "Send your PDF" in state["sent"][0]
        assert state["alerts"] == []

    def test_the_welcome_still_fires_on_message_two(self, wired):
        """Meta attaches the referral once. A person who opened the thread
        from an ad and then typed again before we answered is still an ad
        arrival — first_ad_at on the thread is what remembers that."""
        api, state = wired
        state["thread"] = {"first_ad_at": "2026-09-21T06:00:00Z"}
        api._process_instagram_webhook(_payload(_msg("are you open?")))
        assert len(state["sent"]) == 1
        assert "skip the Xerox queue" in state["sent"][0]

    def test_a_dead_thread_lookup_fetches_a_person_rather_than_guessing(
            self, wired, monkeypatch):
        """get_instagram_thread returns None on any error. Welcoming on a
        failed read would re-welcome somebody every message; this falls
        through to a human, which is loud and safe."""
        import db_cloud
        api, state = wired
        monkeypatch.setattr(db_cloud, "get_instagram_thread", lambda i: None)
        api._process_instagram_webhook(_payload(_msg("Do you print on cloth?")))
        assert state["alerts"] == ["Instagram DM needs a person"]

    def test_an_icebreaker_without_an_ad_is_still_answered(self, wired):
        api, state = wired
        api._process_instagram_webhook(_payload(_msg("What are your prices?")))
        assert len(state["sent"]) == 1 and "sheet" in state["sent"][0]
        assert state["alerts"] == []

    def test_anything_else_fetches_a_person(self, wired):
        """There is no menu worth sending on this channel, and nobody would
        otherwise ever see the thread."""
        api, state = wired
        api._process_instagram_webhook(_payload(_msg("Do you print on cloth?")))
        assert state["alerts"] == ["Instagram DM needs a person"]
        assert any(t.get("needs_human") for t in state["touch"])

    def test_the_inbound_is_logged_against_the_instagram_channel(self, wired):
        api, state = wired
        api._process_instagram_webhook(_payload(_msg("hello there")))
        assert state["logged"][0][1]["channel"] == "instagram"

    def test_a_retry_is_not_answered_twice(self, wired, monkeypatch):
        api, state = wired
        monkeypatch.setattr(api, "_mark_webhook_processed", lambda i, h: False)
        api._process_instagram_webhook(_payload(_msg("What are your prices?")))
        assert state["sent"] == []

    def test_a_handler_that_blows_up_alerts_rather_than_going_quiet(
            self, wired, monkeypatch):
        api, state = wired
        monkeypatch.setattr(api, "_handle_instagram_message",
                            lambda ev: (_ for _ in ()).throw(RuntimeError("x")))
        api._process_instagram_webhook(_payload(_msg("hi")))
        assert state["alerts"] == ["Instagram DM not handled"]

    def test_a_whatsapp_payload_never_reaches_the_instagram_path(self, wired,
                                                                 monkeypatch):
        api, state = wired
        monkeypatch.setattr(api, "_process_instagram_webhook",
                            lambda d: state["alerts"].append("WRONG BRANCH"))
        api._process_meta_webhook({"object": "whatsapp_business_account",
                                   "entry": []})
        assert "WRONG BRANCH" not in state["alerts"]
