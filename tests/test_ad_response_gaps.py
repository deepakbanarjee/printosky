"""
The four ways an ad lead was still being dropped after the front door was fixed.

Six people tapped ad 120248100283360186 between 17 and 19 Sep 2026, after the
quote welcome shipped. The welcome fired every time, in five or six seconds.
Nobody sent a file, nobody ordered, and `ad_conversions` stayed empty. Reading
the six transcripts, what happened after the welcome was:

  * the question they tapped on the way in went unanswered (covered by
    tests/test_icebreakers.py);
  * a print-ad click that said "Can I book an appointment?" was sold the
    Malayalam book catalogue, because book_bot runs above the front door and
    is_book_trigger matches "book" anywhere;
  * a Hindi speaker asked "Kya hai ye" and got nothing at all, with
    needs_human still false, because the human-handoff net sits below a branch
    every ad arrival returns from;
  * the one answer a human did type went out 27h15m later, past Meta's 24-hour
    window, and was dropped. The `failed` status landed in a table nobody
    reads: 118 of September's 761 messages, 15.5%, never arrived.

These pin each of those shut.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

intent = pytest.importorskip("routing.intent", reason="routing package unavailable")
handoff = pytest.importorskip("routing.handoff")

PHONE = "917594873365"          # the one that was sold books on 19 Sep


# ── the book flow stands down for an ad arrival ──────────────────────────────

class TestBookFlowStandsDown:
    """book_bot.maybe_handle_book runs at api/index._handle_text:1058 and the
    front door at :1166, so the book flow gets first refusal on every message.
    It must decline the one that carries the ad referral."""

    @pytest.fixture
    def book_bot(self):
        return pytest.importorskip("book_bot")

    def test_the_word_book_in_a_sentence_still_opens_the_catalog(self, book_bot):
        """The guard must not cost a real book customer their catalog."""
        assert book_bot.is_book_trigger("Can I book an appointment?") is True
        assert book_bot.is_book_trigger("book") is True

    def test_a_pending_ad_arrival_is_left_to_the_front_door(self, book_bot,
                                                            monkeypatch):
        monkeypatch.setattr(book_bot._dbc, "get_session", lambda db, p: {})
        monkeypatch.setattr(book_bot, "_ad_welcome_owed", lambda p: True)
        assert book_bot.maybe_handle_book(PHONE, "Can I book an appointment?") is None

    def test_without_one_the_book_flow_behaves_exactly_as_before(self, book_bot,
                                                                 monkeypatch):
        monkeypatch.setattr(book_bot._dbc, "get_session", lambda db, p: {})
        monkeypatch.setattr(book_bot, "_ad_welcome_owed", lambda p: False)
        monkeypatch.setattr(book_bot, "_try_website_order", lambda *a, **k: None)
        monkeypatch.setattr(book_bot, "_maybe_tracking_reply", lambda *a, **k: None)
        monkeypatch.setattr(book_bot, "_maybe_book_enquiry", lambda *a, **k: None)
        monkeypatch.setattr(book_bot, "_start", lambda *a, **k: ["<<CATALOG>>"])
        assert book_bot.maybe_handle_book(PHONE, "book") == ["<<CATALOG>>"]

    def test_a_broken_lookup_leaves_the_book_flow_alone(self, book_bot, monkeypatch):
        """False on any doubt: this decides whether ANOTHER flow stands down,
        so an error here must not silently switch the book bot off."""
        def _boom(phone):
            raise RuntimeError("supabase down")
        monkeypatch.setattr(intent, "pending_ad_arrival", _boom)
        assert book_bot._ad_welcome_owed(PHONE) is False


class TestPendingAdArrival:
    def test_no_click_means_nothing_is_owed(self, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "recent_ad_click", lambda p: None)
        assert intent.pending_ad_arrival(PHONE) is None

    def test_an_already_welcomed_click_is_not_owed_again(self, monkeypatch):
        import db_cloud
        ad = {"source_id": "120248100283360186", "clicked_at": "2026-09-19T08:38:37Z"}
        monkeypatch.setattr(db_cloud, "recent_ad_click", lambda p: ad)
        monkeypatch.setattr(db_cloud, "ad_welcome_already_sent", lambda p, s: True)
        assert intent.pending_ad_arrival(PHONE) is None

    def test_a_fresh_click_on_a_known_campaign_is_owed(self, monkeypatch):
        import db_cloud
        ad = {"source_id": "120248100283360186", "clicked_at": "2026-09-19T08:38:37Z"}
        monkeypatch.setattr(db_cloud, "recent_ad_click", lambda p: ad)
        monkeypatch.setattr(db_cloud, "ad_welcome_already_sent", lambda p, s: False)
        assert intent.pending_ad_arrival(PHONE) == ad

    def test_an_ad_we_have_no_copy_for_is_not_owed_one(self, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "recent_ad_click",
                            lambda p: {"source_id": "999", "clicked_at": "x"})
        monkeypatch.setattr(db_cloud, "ad_welcome_already_sent", lambda p, s: False)
        assert intent.pending_ad_arrival(PHONE) is None


# ── the handoff the front door could never reach ─────────────────────────────

class TestTheFrontDoorCanFetchAHuman:
    @pytest.fixture
    def wire(self, monkeypatch):
        seen: dict = {}
        monkeypatch.setattr(intent, "_send_menu",
                            lambda phone: seen.setdefault("menu", True))
        monkeypatch.setattr(handoff, "hand_off_to_human",
                            lambda phone, text, why: seen.setdefault("handoff", why))
        return seen

    def test_an_ad_arrivals_question_fetches_a_human(self, wire, monkeypatch):
        monkeypatch.setattr(intent, "_came_from_an_ad", lambda p: True)
        intent._unhandled(PHONE, "Aap ka photo")
        assert "handoff" in wire and "menu" not in wire

    def test_a_greeting_gets_the_menu_not_a_person(self, wire, monkeypatch):
        """Repeating the menu was the other half of the 18 Sep failure, but a
        person is still the wrong answer to 'hello'."""
        monkeypatch.setattr(intent, "_came_from_an_ad", lambda p: True)
        intent._unhandled(PHONE, "hello")
        assert "menu" in wire and "handoff" not in wire

    def test_walk_in_traffic_keeps_the_menu_it_always_had(self, wire, monkeypatch):
        monkeypatch.setattr(intent, "_came_from_an_ad", lambda p: False)
        intent._unhandled(PHONE, "Do you laminate?")
        assert "menu" in wire and "handoff" not in wire


class TestHandoffHeuristic:
    @pytest.mark.parametrize("greeting", ["hello", "Hey", "namaskaram",
                                          "good morning", "ഹലോ"])
    def test_greetings_do_not_summon_a_person(self, greeting):
        assert handoff.should_handoff_text(greeting) is False

    @pytest.mark.parametrize("real", ["Aap ka photo", "Kya hai ye",
                                      "Do you print on cloth?",
                                      "എനിക്ക് ഒരു സംശയം ഉണ്ട്"])
    def test_a_real_message_does(self, real):
        assert handoff.should_handoff_text(real) is True

    def test_it_is_the_same_function_the_print_flow_uses(self):
        """Two copies of this heuristic is how they drift apart."""
        api = pytest.importorskip("api.index")
        assert api._should_handoff_text("hello") is False
        assert api._should_handoff_text("Do you print on cloth?") is True


class TestHandoffAlwaysMakesANoise:
    def test_staff_are_told(self, monkeypatch):
        alerts: list[str] = []
        acks: list[str] = []
        monkeypatch.setattr(handoff, "mark_needs_human", lambda p: None)
        import whatsapp_notify
        monkeypatch.setattr(whatsapp_notify, "send_staff_alert",
                            lambda m: alerts.append(m) or True)
        monkeypatch.setattr(whatsapp_notify, "_send",
                            lambda p, m: acks.append(m) or True)
        import db_cloud
        monkeypatch.setattr(db_cloud, "save_session", lambda *a, **k: None)

        handoff.hand_off_to_human(PHONE, "Kya hai ye", "Front door couldn't answer")
        # ops_watchdog alerts on the same channel, so filter to ours.
        mine = [a for a in alerts if "🤖→🧑" in a]
        assert len(mine) == 1
        assert "Kya hai ye" in mine[0] and "Needs human" in mine[0]
        assert len(acks) == 1

    def test_the_number_is_readable_in_the_alert(self):
        assert handoff.fmt_phone("919495706405") == "+91 94957 06405"

    def test_an_alert_that_does_not_send_is_reported(self, monkeypatch):
        """A handoff nobody hears about is the silence this exists to end."""
        reports: list[tuple] = []
        monkeypatch.setattr(handoff, "mark_needs_human", lambda p: None)
        import ops_watchdog, whatsapp_notify, db_cloud
        monkeypatch.setattr(ops_watchdog, "report",
                            lambda c, ok, d="", **k: reports.append((c, ok)))
        monkeypatch.setattr(whatsapp_notify, "send_staff_alert", lambda m: False)
        monkeypatch.setattr(whatsapp_notify, "_send", lambda p, m: True)
        monkeypatch.setattr(db_cloud, "save_session", lambda *a, **k: None)

        handoff.hand_off_to_human(PHONE, "Kya hai ye", "test")
        assert ("handoff.alert", False) in reports


# ── a message Meta refused to deliver is news ────────────────────────────────

class TestUndeliveredMessagesAlert:
    """The rollup, not a per-message alert.

    The obvious place for this is the status callback, but that runs on Vercel
    where ops_watchdog has no SQLite to remember an alert in — 10 Sep's 116
    failures would have been 116 staff WhatsApps. The chat-audit cron reads it
    twice a day and reports once, which is also what lets the check announce
    its own recovery.
    """

    def test_the_status_callback_itself_stays_quiet(self, monkeypatch):
        import db_cloud, ops_watchdog
        reports: list = []
        monkeypatch.setattr(ops_watchdog, "report",
                            lambda c, ok, d="", **k: reports.append(c))
        monkeypatch.setattr(db_cloud, "_client", lambda: (_ for _ in ()).throw(
            RuntimeError("no db in tests")))
        db_cloud.record_wa_message_cost("wamid.X", "917306448194", "failed")
        assert reports == []

    def test_failures_in_the_window_are_reported(self, monkeypatch):
        api = pytest.importorskip("api.index")
        import db_cloud, ops_watchdog
        reports: list = []
        monkeypatch.setattr(ops_watchdog, "report",
                            lambda c, ok, d="", **k: reports.append((c, ok, d)))
        monkeypatch.setattr(db_cloud, "undelivered_since", lambda h=24: {
            "failed": 116, "recipients": 12, "sample": ["917306448194"],
            "window_hours": h, "ok": True})
        snap = api._report_undelivered_messages()
        assert snap["failed"] == 116
        assert reports[0][:2] == ("whatsapp.delivery", False)
        assert "116" in reports[0][2] and "12" in reports[0][2]

    def test_a_clean_window_lets_the_check_recover(self, monkeypatch):
        api = pytest.importorskip("api.index")
        import db_cloud, ops_watchdog
        reports: list = []
        monkeypatch.setattr(ops_watchdog, "report",
                            lambda c, ok, d="", **k: reports.append((c, ok)))
        monkeypatch.setattr(db_cloud, "undelivered_since", lambda h=24: {
            "failed": 0, "recipients": 0, "sample": [], "window_hours": h,
            "ok": True})
        api._report_undelivered_messages()
        assert reports == [("whatsapp.delivery", True)]

    def test_a_broken_lookup_is_not_good_news(self, monkeypatch):
        """`ok: False` must not read as "nothing failed" — that is how a check
        goes green over a query that never ran."""
        api = pytest.importorskip("api.index")
        import db_cloud, ops_watchdog
        reports: list = []
        monkeypatch.setattr(ops_watchdog, "report",
                            lambda c, ok, d="", **k: reports.append((c, ok)))
        monkeypatch.setattr(db_cloud, "undelivered_since", lambda h=24: {
            "failed": 0, "recipients": 0, "sample": [], "window_hours": h,
            "ok": False})
        api._report_undelivered_messages()
        assert reports == [("whatsapp.delivery", False)]

    def test_the_rollup_survives_a_dead_database(self, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "_client", lambda: (_ for _ in ()).throw(
            RuntimeError("no db in tests")))
        snap = db_cloud.undelivered_since(24)
        assert snap["ok"] is False and snap["failed"] == 0


class TestTheTwentyFourHourWindow:
    @pytest.fixture
    def admin(self):
        return pytest.importorskip("api.handlers_admin")

    def test_a_closed_window_warns_with_the_numbers(self, admin, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "hours_since_last_inbound", lambda p: 27.25)
        warning = admin._service_window_warning(PHONE)
        assert warning and "24-hour" in warning and "template" in warning

    def test_an_open_window_is_quiet(self, admin, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "hours_since_last_inbound", lambda p: 0.2)
        assert admin._service_window_warning(PHONE) is None

    def test_the_edge_is_flagged_before_it_is_crossed(self, admin, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "hours_since_last_inbound", lambda p: 22.0)
        warning = admin._service_window_warning(PHONE)
        assert warning and "24h" in warning

    def test_an_unknown_age_does_not_cry_wolf(self, admin, monkeypatch):
        import db_cloud
        monkeypatch.setattr(db_cloud, "hours_since_last_inbound", lambda p: None)
        assert admin._service_window_warning(PHONE) is None


class TestStaffRepliesAreLoggedOnce:
    def test_admin_send_leaves_the_logging_to_the_sender(self):
        """whatsapp_notify._send logs on the way out. /admin/send logging it
        again put every staff reply in the thread twice, 0.3s apart, which is
        what made the 9 and 17 Sep replies look double-sent when only one
        message ever left."""
        import inspect
        admin = pytest.importorskip("api.handlers_admin")
        src = inspect.getsource(admin._handle_admin_send)
        assert "log_message" not in src


class TestTheWelcomeIsNotFollowedByADifferentProduct:
    """Standing book_bot down was only half of it. "Can I book an appointment?"
    classifies as `xtraa` inside the front door too, so the intent layer would
    post the catalogue the moment the book flow stopped doing it."""

    @pytest.fixture
    def wire(self, monkeypatch):
        sent: list[str] = []
        monkeypatch.setattr(intent, "_send_text", lambda p, m: sent.append(m))
        monkeypatch.setattr(intent, "_send_menu", lambda p: sent.append("<<MENU>>"))
        monkeypatch.setattr(intent, "_open_books",
                            lambda p, n: sent.append("<<BOOK CATALOG>>"))
        monkeypatch.setattr(intent, "_open_soc",
                            lambda p, n: sent.append("<<SOC CATALOG>>"))
        monkeypatch.setattr(intent, "_pending_payment_reminder", lambda p, n: None)
        return sent

    def _welcome(self, monkeypatch, sent, fires: bool):
        def _w(phone, name=None, icebreaker=None):
            if fires:
                sent.append("<<WELCOME>>")
            return fires
        monkeypatch.setattr(intent, "_maybe_welcome_ad_arrival", _w)

    @pytest.mark.parametrize("classified", ["xtraa", "malayalam", "sociology"])
    def test_no_catalog_rides_along_with_the_ad_welcome(self, wire, monkeypatch,
                                                        classified):
        self._welcome(monkeypatch, wire, True)
        monkeypatch.setattr(intent, "decide_intent", lambda t: classified)
        intent.route_front_door("917594873365", "Can I book an appointment?")
        assert wire == ["<<WELCOME>>"]

    def test_a_print_intent_still_rides_along(self, wire, monkeypatch):
        """Someone off the ad who types "print" wants the order link AND the
        offer they clicked — that pairing is the point of welcoming first."""
        self._welcome(monkeypatch, wire, True)
        monkeypatch.setattr(intent, "decide_intent", lambda t: "print")
        intent.route_front_door("917594873365", "I need printing")
        assert len(wire) == 2 and wire[0] == "<<WELCOME>>"

    def test_an_ordinary_book_customer_still_gets_the_catalog(self, wire,
                                                              monkeypatch):
        self._welcome(monkeypatch, wire, False)
        monkeypatch.setattr(intent, "decide_intent", lambda t: "xtraa")
        intent.route_front_door("919495706405", "malayalam book")
        assert wire == ["<<BOOK CATALOG>>"]

    def test_an_unwelcomed_unknown_still_reaches_the_fallback(self, wire,
                                                             monkeypatch):
        self._welcome(monkeypatch, wire, False)
        monkeypatch.setattr(intent, "decide_intent", lambda t: "unknown")
        monkeypatch.setattr(intent, "_came_from_an_ad", lambda p: False)
        intent.route_front_door("919495706405", "hello")
        assert wire == ["<<MENU>>"]
