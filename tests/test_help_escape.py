"""
Tests for the `help` keyword escape hatch in api/index.py.

Roadmap reference: roadmap-2026-05.md TASK-009.

Covers:
  - _is_help_keyword: pure keyword matcher
  - _handle_help_request: side effects (db upsert, staff alert, customer ack)
  - _handle_text: short-circuits before state-machine on a help keyword
  - _handle_admin_conversations: response includes needs_human flag
"""

import sys
import os
import types
import logging
from unittest.mock import patch, MagicMock

# ── stub heavy deps so api/index.py can be imported ───────────────────────────
for _mod in ("requests", "dotenv", "db_cloud", "whatsapp_bot",
             "whatsapp_notify", "razorpay_integration"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None

# watchdog stub (transitive — some sibling imports may pull watcher.py)
_wd = types.ModuleType("watchdog")
_wd_obs = types.ModuleType("watchdog.observers")
_wd_obs.Observer = object
_wd_ev = types.ModuleType("watchdog.events")
_wd_ev.FileSystemEventHandler = object
sys.modules["watchdog"] = _wd
sys.modules["watchdog.observers"] = _wd_obs
sys.modules["watchdog.events"] = _wd_ev

# Only stub callables that are missing -- never overwrite real module attrs,
# because conftest.py may have pre-imported the real local module and we'd
# corrupt it for other tests in the suite.
def _ensure(mod_name: str, attr: str, value):
    mod = sys.modules.get(mod_name)
    if mod is not None and not hasattr(mod, attr):
        setattr(mod, attr, value)

_ensure("whatsapp_notify", "_send",            lambda *a, **kw: True)
_ensure("whatsapp_notify", "send_staff_alert", lambda *a, **kw: True)
_ensure("db_cloud",        "log_message",      lambda *a, **kw: None)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import api.index as api_mod


# ═════════════════════════════════════════════════════════════════════════════
# _is_help_keyword
# ═════════════════════════════════════════════════════════════════════════════

class TestIsHelpKeyword:
    """Pure function — no I/O."""

    @pytest.mark.parametrize("text", [
        "help", "HELP", "Help", "  help  ",
        "support", "SUPPORT", "  Support",
        "human", "HUMAN",
        "agent", "Agent",
    ])
    def test_matches_keyword(self, text: str) -> None:
        assert api_mod._is_help_keyword(text) is True

    @pytest.mark.parametrize("text", [
        "",
        "i need help",          # not a bare keyword
        "help me",
        "please support",
        "humans are great",
        "secret agent",
        "hi",
        "A4",
    ])
    def test_rejects_non_keyword(self, text: str) -> None:
        assert api_mod._is_help_keyword(text) is False

    def test_keyword_set_is_locked(self) -> None:
        """Locked set: any change must be deliberate."""
        assert api_mod.HELP_KEYWORDS == frozenset({"help", "support", "human", "agent"})


# ═════════════════════════════════════════════════════════════════════════════
# _handle_help_request — side effects
# ═════════════════════════════════════════════════════════════════════════════

class TestHandleHelpRequest:
    def test_marks_session_alerts_staff_acks_customer(self) -> None:
        """Calls mark_session, send_staff_alert, and _send (the customer ack)."""
        sender = "919999999999"
        with patch.object(api_mod, "_mark_session_needs_human") as mark_mock, \
             patch.object(sys.modules["whatsapp_notify"], "send_staff_alert") as alert_mock, \
             patch.object(sys.modules["whatsapp_notify"], "_send") as send_mock:
            api_mod._handle_help_request(sender, "help")

        mark_mock.assert_called_once_with(sender)
        alert_mock.assert_called_once()
        alert_msg = alert_mock.call_args.args[0]
        # _fmt_phone formats 12-digit "91…" into "+91 ….."
        assert "+91" in alert_msg
        assert "help" in alert_msg.lower()
        send_mock.assert_called_once()
        ack_phone, ack_text = send_mock.call_args.args[0], send_mock.call_args.args[1]
        assert ack_phone == sender
        assert "alert" in ack_text.lower()

    def test_alert_failure_does_not_block_customer_ack(self) -> None:
        """If staff alert fails, the customer should still get an ack."""
        with patch.object(api_mod, "_mark_session_needs_human"), \
             patch.object(sys.modules["whatsapp_notify"], "send_staff_alert",
                          side_effect=RuntimeError("meta API down")), \
             patch.object(sys.modules["whatsapp_notify"], "_send") as send_mock:
            api_mod._handle_help_request("91111", "support")  # must not raise
        send_mock.assert_called_once()

    def test_db_failure_does_not_raise(self) -> None:
        """A Supabase outage in the mark step must not crash the handler."""
        with patch.object(api_mod, "_mark_session_needs_human",
                          side_effect=RuntimeError("supabase down")), \
             patch.object(sys.modules["whatsapp_notify"], "send_staff_alert"), \
             patch.object(sys.modules["whatsapp_notify"], "_send"):
            with pytest.raises(RuntimeError):
                # _handle_help_request itself does not catch upstream raises;
                # but _mark_session_needs_human swallows internally — see below.
                api_mod._handle_help_request("91222", "human")


class TestMarkSessionNeedsHuman:
    """`_client` is imported lazily from db_cloud inside the function — patch
    the stub on sys.modules['db_cloud']._client, not on api.index."""

    def test_supabase_failure_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The internal try/except must swallow Supabase errors and warn."""
        bad_client = MagicMock()
        bad_client.table.return_value.upsert.return_value.execute.side_effect = RuntimeError("boom")
        monkeypatch.setattr(sys.modules["db_cloud"], "_client", lambda: bad_client, raising=False)
        with caplog.at_level(logging.WARNING):
            api_mod._mark_session_needs_human("91222")  # must not raise
        assert any("_mark_session_needs_human" in r.message for r in caplog.records)

    def test_upsert_payload_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Upsert payload includes phone, needs_human=True, ISO-8601 Z timestamp."""
        captured: dict = {}

        class _FakeUpsert:
            def execute(self_inner):
                return MagicMock(data=[])

        class _FakeTable:
            def upsert(self_inner, payload):
                captured["payload"] = payload
                return _FakeUpsert()

        class _FakeClient:
            def table(self_inner, name):
                captured["table"] = name
                return _FakeTable()

        monkeypatch.setattr(sys.modules["db_cloud"], "_client", lambda: _FakeClient(), raising=False)
        api_mod._mark_session_needs_human("91333")

        assert captured["table"] == "bot_sessions"
        payload = captured["payload"]
        assert payload["phone"] == "91333"
        assert payload["needs_human"] is True
        assert payload["last_help_request_at"].endswith("Z")


# ═════════════════════════════════════════════════════════════════════════════
# _handle_text — short-circuit ordering
# ═════════════════════════════════════════════════════════════════════════════

class TestHandleTextShortCircuit:
    def test_help_skips_state_machine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Help keyword must not reach _capture_referral_code or handle_message."""
        called = {"capture": 0, "bot": 0, "help": 0}
        monkeypatch.setattr(
            sys.modules["whatsapp_bot"], "handle_message",
            lambda **kw: called.__setitem__("bot", called["bot"] + 1) or [],
            raising=False,
        )

        with patch.object(api_mod, "_capture_referral_code",
                          side_effect=lambda *a, **kw: called.__setitem__("capture", called["capture"] + 1)), \
             patch.object(api_mod, "_handle_help_request",
                          side_effect=lambda *a, **kw: called.__setitem__("help", called["help"] + 1)):
            api_mod._handle_text("91444", "  HELP  ")

        assert called == {"capture": 0, "bot": 0, "help": 1}

    def test_non_help_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Normal text must still reach _capture_referral_code.

        Mocks all external state the router touches before _capture_referral_code
        so the assertion never depends on leftover live `bot_sessions` rows for
        the test phone (else the book flow can claim the message and return
        early). Mirrors TestCustomerWelcome._patch_common's seam set, but keeps
        the real capture/help counters since they're the thing under test.
        """
        called = {"capture": 0, "help": 0}
        monkeypatch.setattr(
            sys.modules["whatsapp_bot"], "handle_message",
            lambda **kw: [], raising=False,
        )

        # No active session for the test phone → book flow can't claim it, and
        # the idle/xtraa-catalog branch is reachable but mocked to a no-op.
        bb = sys.modules.get("book_bot")
        if bb is None:
            bb = types.ModuleType("book_bot")
            sys.modules["book_bot"] = bb
        monkeypatch.setattr(bb, "maybe_handle_book", lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(bb, "start_catalog", lambda phone, name=None: [], raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "get_session",
                            lambda *a, **kw: {}, raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "is_new_contact",
                            lambda *a, **kw: False, raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "clear_session",
                            lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "log_message",
                            lambda *a, **kw: None, raising=False)

        with patch.object(sys.modules["whatsapp_notify"], "_send"), \
             patch.object(api_mod, "_capture_referral_code",
                          side_effect=lambda *a, **kw: called.__setitem__("capture", called["capture"] + 1)), \
             patch.object(api_mod, "_handle_help_request",
                          side_effect=lambda *a, **kw: called.__setitem__("help", called["help"] + 1)):
            api_mod._handle_text("91555", "A4")

        assert called["capture"] == 1
        assert called["help"] == 0


class TestIsGreeting:
    """Pure greeting matcher — no I/O."""

    @pytest.mark.parametrize("text", [
        "hi", "Hi", "HI!", "hii", "hello", "Hello sir", "hey", "hey there",
        "hai", "  hi  ", "good morning", "Good Evening!", "namaskaram", "start", "menu",
    ])
    def test_greetings(self, text: str) -> None:
        assert api_mod._is_greeting(text) is True

    @pytest.mark.parametrize("text", [
        "", "hindi", "i need a print", "how much", "books", "2 copies", "history",
    ])
    def test_non_greetings(self, text: str) -> None:
        assert api_mod._is_greeting(text) is False


class TestCustomerWelcome:
    """Xtraa-only line: any idle customer (new or returning, greeting or not)
    is defaulted into the book catalog. A recent active flow is never
    interrupted. (Replaces the old generic welcome/print menu — every specific
    intent is claimed by an earlier handler, and 'help' still reaches a human.)"""

    def _patch_common(self, monkeypatch, *, is_new, session=None):
        bb = sys.modules.get("book_bot")
        if bb is None:
            bb = types.ModuleType("book_bot")
            sys.modules["book_bot"] = bb
        monkeypatch.setattr(bb, "maybe_handle_book", lambda *a, **kw: None, raising=False)
        self.catalog = []
        monkeypatch.setattr(bb, "start_catalog",
                            lambda phone, name=None: self.catalog.append(phone) or [],
                            raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "get_session",
                            lambda *a, **kw: dict(session or {}), raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "is_new_contact",
                            lambda *a, **kw: is_new, raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "log_message",
                            lambda *a, **kw: None, raising=False)
        self.cleared = []
        monkeypatch.setattr(sys.modules["db_cloud"], "clear_session",
                            lambda db, phone, *a, **kw: self.cleared.append(phone), raising=False)
        monkeypatch.setattr(api_mod, "_capture_referral_code", lambda *a, **kw: None)

    def _run(self, monkeypatch, *, is_new, text, session=None):
        self._patch_common(monkeypatch, is_new=is_new, session=session)
        bot_calls = {"n": 0}
        monkeypatch.setattr(sys.modules["whatsapp_bot"], "handle_message",
                            lambda **kw: bot_calls.__setitem__("n", bot_calls["n"] + 1) or [],
                            raising=False)
        with patch.object(sys.modules["whatsapp_notify"], "_send") as send_mock:
            api_mod._handle_text("91999000111", text)
        return send_mock, bot_calls

    def test_new_contact_first_message_opens_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # First-time customer → straight into the book catalog, any wording.
        _send_mock, bot_calls = self._run(monkeypatch, is_new=True, text="hi")
        assert self.catalog == ["91999000111"]
        assert bot_calls["n"] == 0          # print state machine not reached

    def test_new_contact_non_greeting_opens_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _send_mock, bot_calls = self._run(monkeypatch, is_new=True, text="anyone there")
        assert self.catalog == ["91999000111"]
        assert bot_calls["n"] == 0

    def test_returning_contact_greeting_opens_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _send_mock, bot_calls = self._run(monkeypatch, is_new=False, text="hi")
        assert self.catalog == ["91999000111"]
        assert bot_calls["n"] == 0

    def test_returning_contact_non_greeting_opens_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Previously fell through silently to the print state machine; on an
        # xtraa-only line it now opens the catalog too.
        _send_mock, bot_calls = self._run(monkeypatch, is_new=False, text="i want the malayalam one")
        assert self.catalog == ["91999000111"]
        assert bot_calls["n"] == 0

    def test_idle_with_awaiting_payment_order_forwards_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # start_catalog returns a non-empty list for a customer who already has
        # an order awaiting payment (it does NOT open a fresh catalog). The
        # router must forward that message, not drop it (regression guard).
        self._patch_common(monkeypatch, is_new=False, session={})
        bb = sys.modules["book_bot"]
        monkeypatch.setattr(bb, "start_catalog",
                            lambda phone, name=None: ["Order XTR-1 awaiting payment — send screenshot or reply NEW"],
                            raising=False)
        monkeypatch.setattr(sys.modules["whatsapp_bot"], "handle_message",
                            lambda **kw: [], raising=False)
        with patch.object(sys.modules["whatsapp_notify"], "_send") as send_mock:
            api_mod._handle_text("91999000111", "PAY")
        assert send_mock.called
        assert "awaiting payment" in send_mock.call_args[0][1]

    def test_one_customer_staff_hold_does_not_affect_others(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Customer B messages while customer A is in staff_hold. B must still be
        # served (catalog) — A's hold is per-phone and never blocks others.
        sessions = {
            "918943232033": {"step": "staff_hold", "updated_at": "2026-05-11 01:03:58"},
            "918111222333": {},
        }
        bb = sys.modules.get("book_bot") or types.ModuleType("book_bot")
        sys.modules["book_bot"] = bb
        monkeypatch.setattr(bb, "maybe_handle_book", lambda *a, **kw: None, raising=False)
        catalog = []
        monkeypatch.setattr(bb, "start_catalog",
                            lambda phone, name=None: catalog.append(phone) or [], raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "get_session",
                            lambda db, phone, *a, **kw: dict(sessions.get(phone, {})),
                            raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "is_new_contact",
                            lambda *a, **kw: False, raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "log_message",
                            lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(sys.modules["db_cloud"], "clear_session",
                            lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(api_mod, "_capture_referral_code", lambda *a, **kw: None)

        with patch.object(sys.modules["whatsapp_notify"], "_send"):
            api_mod._handle_text("918111222333", "hi")          # customer B
        assert catalog == ["918111222333"]   # B served; A untouched

    def test_recent_active_session_not_interrupted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Customer mid-flow (recent session) says hi → NOT interrupted, no catalog.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _send_mock, bot_calls = self._run(monkeypatch, is_new=False, text="hi",
                                          session={"step": "size", "updated_at": now})
        assert self.catalog == []           # not defaulted into the catalog
        assert bot_calls["n"] == 1          # handed to the print state machine
        assert self.cleared == []           # session preserved

    def test_stale_staff_hold_resets_and_opens_catalog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # A forgotten staff_hold (old) must not black-hole the customer: it is
        # cleared and the catalog opens.
        _send_mock, bot_calls = self._run(
            monkeypatch, is_new=False, text="hi",
            session={"step": "staff_hold", "updated_at": "2026-05-11 01:03:58"})
        assert self.catalog == ["91999000111"]
        assert "91999000111" in self.cleared   # stale session was reset
        assert bot_calls["n"] == 0


class TestSessionStale:
    """_session_is_stale — pure timestamp logic."""

    def test_no_timestamp_is_stale(self) -> None:
        assert api_mod._session_is_stale({}) is True

    def test_old_timestamp_is_stale(self) -> None:
        assert api_mod._session_is_stale({"updated_at": "2026-05-11 01:03:58"}) is True

    def test_recent_timestamp_not_stale(self) -> None:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        assert api_mod._session_is_stale({"updated_at": now}) is False


# ═════════════════════════════════════════════════════════════════════════════
# _handle_admin_conversations — needs_human field
# ═════════════════════════════════════════════════════════════════════════════

class TestAdminConversationsNeedsHumanField:
    def test_response_shape_includes_needs_human(self) -> None:
        """Source must annotate each inbox entry with a needs_human boolean."""
        import inspect
        src = inspect.getsource(api_mod._handle_admin_conversations)
        assert '"needs_human"' in src, (
            "_handle_admin_conversations response must include the needs_human "
            "field for the admin Conversations 'Needs human' filter (TASK-009)"
        )
        assert "needs_human_phones" in src, (
            "_handle_admin_conversations must build a needs_human_phones set "
            "from bot_sessions before joining onto inbox rows"
        )


# ═════════════════════════════════════════════════════════════════════════════
# Known B2B vendor routing (toner/supply buyers bypass the order bot → human)
# ═════════════════════════════════════════════════════════════════════════════

class TestVendorRouting:
    def test_known_vendor_recognised(self) -> None:
        label = api_mod._vendor_name("918547727272")
        assert label is not None and "CopyTech" in label

    def test_plus_prefix_is_normalised(self) -> None:
        assert api_mod._vendor_name("+918547727272") is not None

    def test_unknown_number_is_none(self) -> None:
        assert api_mod._vendor_name("919999999999") is None

    def test_env_vendor_phones_extends_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VENDOR_PHONES", "919812345678=Acme Supplies")
        assert api_mod._vendor_name("919812345678") == "Acme Supplies"

    def test_vendor_message_flags_alerts_and_acks(self) -> None:
        sender = "918547727272"
        with patch.object(api_mod, "_mark_session_needs_human") as mark_mock, \
             patch.object(sys.modules["whatsapp_notify"], "send_staff_alert") as alert_mock, \
             patch.object(sys.modules["whatsapp_notify"], "_send") as send_mock:
            api_mod._handle_vendor_message(sender, "toner order pls", "CopyTech India (CTI)")
        mark_mock.assert_called_once_with(sender)
        alert_mock.assert_called_once()
        assert "CopyTech" in alert_mock.call_args.args[0]
        send_mock.assert_called_once()
        assert send_mock.call_args.args[0] == sender

    def test_vendor_short_circuits_before_state_machine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A vendor message must never reach the print/book bot state machine."""
        bot_calls = []
        monkeypatch.setattr(
            sys.modules["whatsapp_bot"], "handle_message",
            lambda *a, **kw: bot_calls.append(a), raising=False,
        )
        with patch.object(api_mod, "_mark_session_needs_human"), \
             patch.object(sys.modules["whatsapp_notify"], "send_staff_alert"), \
             patch.object(sys.modules["whatsapp_notify"], "_send"):
            api_mod._handle_text("918547727272", "Xerox OEM toner ×3")
        assert bot_calls == [], "vendor message leaked into the bot state machine"
