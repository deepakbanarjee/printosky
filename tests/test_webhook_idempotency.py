"""
TASK-013: webhook idempotency guards.

Verifies that the three webhook handlers in api/index.py
(_process_meta_webhook, _process_razorpay_payment, _handle_acad_razorpay_webhook)
call _mark_webhook_processed and short-circuit on duplicates.

Roadmap reference: roadmap-2026-05.md TASK-013.
"""
from __future__ import annotations

import sys
import os
import json
import types
import logging
from unittest.mock import patch, MagicMock

# ── stub heavy deps so api/index.py can be imported ───────────────────────────
for _mod in ("requests", "dotenv", "db_cloud", "whatsapp_bot",
             "whatsapp_notify", "razorpay_integration",
             "db_cloud_academic", "academic_whatsapp"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None

# Only stub callables that are missing -- never overwrite real module attrs,
# because conftest.py may have pre-imported the real local module and we'd
# corrupt it for other tests in the suite.
def _ensure(mod_name: str, attr: str, value):
    mod = sys.modules.get(mod_name)
    if mod is not None and not hasattr(mod, attr):
        setattr(mod, attr, value)

_ensure("whatsapp_notify", "_send",                  lambda *a, **kw: True)
_ensure("whatsapp_notify", "send_staff_alert",       lambda *a, **kw: True)
_ensure("whatsapp_notify", "send_payment_confirmed", lambda *a, **kw: True)

_ensure("db_cloud", "log_message",              lambda *a, **kw: None)
_ensure("db_cloud", "get_batch",                lambda *a, **kw: None)
_ensure("db_cloud", "get_job",                  lambda *a, **kw: {})
_ensure("db_cloud", "update_job_paid",          lambda *a, **kw: None)
_ensure("db_cloud", "update_batch_paid",        lambda *a, **kw: None)
_ensure("db_cloud", "update_jobs_payment_link", lambda *a, **kw: None)

_ensure("razorpay_integration", "parse_payment_webhook", lambda *a, **kw: None)
_ensure("razorpay_integration", "verify_webhook",        lambda *a, **kw: True)

_ensure("db_cloud_academic", "get_order",     lambda *a, **kw: None)
_ensure("db_cloud_academic", "update_fields", lambda *a, **kw: None)
_ensure("db_cloud_academic", "update_status", lambda *a, **kw: None)
_ensure("academic_whatsapp", "notify_advance_paid", lambda *a, **kw: None)

_wd = types.ModuleType("watchdog")
_wd_obs = types.ModuleType("watchdog.observers"); _wd_obs.Observer = object
_wd_ev = types.ModuleType("watchdog.events"); _wd_ev.FileSystemEventHandler = object
sys.modules["watchdog"] = _wd
sys.modules["watchdog.observers"] = _wd_obs
sys.modules["watchdog.events"] = _wd_ev

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import api.index as api_mod


# ═════════════════════════════════════════════════════════════════════════════
# _mark_webhook_processed — primitive
# ═════════════════════════════════════════════════════════════════════════════

class TestMarkWebhookProcessed:
    def test_empty_event_id_returns_true_fail_open(self) -> None:
        """No event_id -> can't dedupe -> proceed (don't drop)."""
        assert api_mod._mark_webhook_processed("", "meta") is True

    def test_successful_insert_returns_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """First-time event -> insert succeeds with data -> proceed."""
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.return_value = MagicMock(
            data=[{"event_id": "evt_1", "handler": "meta"}]
        )
        monkeypatch.setattr(sys.modules["db_cloud"], "_client", lambda: client, raising=False)
        assert api_mod._mark_webhook_processed("evt_1", "meta") is True

    def test_duplicate_key_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unique-violation -> already processed -> skip."""
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.side_effect = Exception(
            'duplicate key value violates unique constraint "processed_webhooks_pkey"'
        )
        monkeypatch.setattr(sys.modules["db_cloud"], "_client", lambda: client, raising=False)
        assert api_mod._mark_webhook_processed("evt_dup", "meta") is False

    def test_postgres_errno_23505_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.side_effect = Exception(
            "PostgrestAPIError: 23505 unique violation"
        )
        monkeypatch.setattr(sys.modules["db_cloud"], "_client", lambda: client, raising=False)
        assert api_mod._mark_webhook_processed("evt_x", "meta") is False

    def test_other_db_error_returns_true_fail_open(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A non-dup DB error must not drop the event; log a warning."""
        client = MagicMock()
        client.table.return_value.insert.return_value.execute.side_effect = Exception(
            "connection reset by peer"
        )
        monkeypatch.setattr(sys.modules["db_cloud"], "_client", lambda: client, raising=False)
        with caplog.at_level(logging.WARNING):
            assert api_mod._mark_webhook_processed("evt_net", "meta") is True
        assert any("_mark_webhook_processed" in r.message for r in caplog.records)


# ═════════════════════════════════════════════════════════════════════════════
# _process_meta_webhook — per-message guard
# ═════════════════════════════════════════════════════════════════════════════

class TestMetaIdempotency:
    """Meta retries on slow ACKs. Each message has a unique wamid (msg.id)."""

    def _payload(self, wamid: str = "wamid.ABC123") -> dict:
        return {
            "entry": [{
                "changes": [{
                    "value": {
                        "contacts": [{"profile": {"name": "Tester"}}],
                        "messages": [{
                            "id": wamid,
                            "from": "919000001234",
                            "type": "text",
                            "text": {"body": "hi"},
                        }],
                    }
                }]
            }]
        }

    def test_first_delivery_invokes_handler(self) -> None:
        called = {"n": 0}
        with patch.object(api_mod, "_mark_webhook_processed", return_value=True), \
             patch.object(api_mod, "_handle_text",
                          side_effect=lambda *a, **kw: called.__setitem__("n", called["n"] + 1)):
            api_mod._process_meta_webhook(self._payload())
        assert called["n"] == 1

    def test_duplicate_delivery_skipped(self) -> None:
        called = {"n": 0}
        with patch.object(api_mod, "_mark_webhook_processed", return_value=False), \
             patch.object(api_mod, "_handle_text",
                          side_effect=lambda *a, **kw: called.__setitem__("n", called["n"] + 1)):
            api_mod._process_meta_webhook(self._payload())
        assert called["n"] == 0, "duplicate wamid must not reach _handle_text"

    def test_two_messages_one_dup_processes_only_new(self) -> None:
        """Mixed batch: one already-seen wamid + one new."""
        payload = {
            "entry": [{
                "changes": [{
                    "value": {
                        "contacts": [{"profile": {"name": "T"}}],
                        "messages": [
                            {"id": "wamid.OLD", "from": "919000001", "type": "text",
                             "text": {"body": "x"}},
                            {"id": "wamid.NEW", "from": "919000002", "type": "text",
                             "text": {"body": "y"}},
                        ],
                    }
                }]
            }]
        }
        senders: list[str] = []
        with patch.object(api_mod, "_mark_webhook_processed",
                          side_effect=lambda eid, h: eid == "wamid.NEW"), \
             patch.object(api_mod, "_handle_text",
                          side_effect=lambda sender, text, **kw: senders.append(sender)):
            api_mod._process_meta_webhook(payload)
        assert senders == ["919000002"]


# ═════════════════════════════════════════════════════════════════════════════
# _process_razorpay_payment — print-job webhook
# ═════════════════════════════════════════════════════════════════════════════

class TestRazorpayPrintIdempotency:
    def test_duplicate_event_skipped_before_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If guard says dup, parse_payment_webhook must never be called."""
        called = {"parse": 0}
        monkeypatch.setattr(
            sys.modules["razorpay_integration"], "parse_payment_webhook",
            lambda *a, **kw: called.__setitem__("parse", called["parse"] + 1),
            raising=False,
        )
        with patch.object(api_mod, "_mark_webhook_processed", return_value=False):
            api_mod._process_razorpay_payment({"id": "evt_dup", "event": "payment.captured"})
        assert called["parse"] == 0

    def test_first_event_reaches_parse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called = {"parse": 0}
        def fake_parse(data):
            called["parse"] += 1
            return None
        monkeypatch.setattr(
            sys.modules["razorpay_integration"], "parse_payment_webhook",
            fake_parse, raising=False,
        )
        with patch.object(api_mod, "_mark_webhook_processed", return_value=True):
            api_mod._process_razorpay_payment({"id": "evt_new", "event": "payment.captured"})
        assert called["parse"] == 1

    def test_guard_called_with_correct_handler_label(self) -> None:
        seen: list[tuple[str, str]] = []
        def fake_mark(eid, handler):
            seen.append((eid, handler))
            return False
        with patch.object(api_mod, "_mark_webhook_processed", side_effect=fake_mark):
            api_mod._process_razorpay_payment({"id": "evt_abc", "event": "payment.captured"})
        assert seen == [("evt_abc", "razorpay_print")]


# ═════════════════════════════════════════════════════════════════════════════
# _handle_acad_razorpay_webhook — academic-order webhook
# ═════════════════════════════════════════════════════════════════════════════

class _FakeRequestHandler:
    """Minimal stand-in for BaseHTTPRequestHandler. Captures responses + body."""
    def __init__(self, sig: str = "ok") -> None:
        class _Headers:
            def __init__(self_inner): self_inner._sig = sig
            def get(self_inner, k, default=""):
                return self_inner._sig if k == "X-Razorpay-Signature" else default
        self.headers = _Headers()
        self.responses: list[int] = []
        self.bodies: list[bytes] = []
        class _Wfile:
            def __init__(self_inner, parent): self_inner.parent = parent
            def write(self_inner, b): self_inner.parent.bodies.append(b)
        self.wfile = _Wfile(self)
    def send_response(self, code: int) -> None: self.responses.append(code)
    def send_header(self, *a, **kw) -> None: pass
    def end_headers(self) -> None: pass


class TestRazorpayAcadIdempotency:
    def _body(self, event_id: str = "evt_acad_1") -> bytes:
        return json.dumps({
            "id": event_id,
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "notes": {"project_id": "P-001", "payment_type": "advance"}
                    }
                }
            }
        }).encode()

    def test_duplicate_event_returns_already_processed(self) -> None:
        h = _FakeRequestHandler()
        with patch.dict(os.environ, {"RAZORPAY_WEBHOOK_SECRET": "x"}), \
             patch("hmac.compare_digest", return_value=True), \
             patch.object(api_mod, "_mark_webhook_processed", return_value=False):
            api_mod._handle_acad_razorpay_webhook(h, self._body())
        assert 200 in h.responses
        body = b"".join(h.bodies).decode()
        assert "already_processed" in body

    def test_first_event_processed_with_correct_handler_label(self) -> None:
        h = _FakeRequestHandler()
        seen: list[tuple[str, str]] = []
        def fake_mark(eid, handler):
            seen.append((eid, handler))
            return True
        with patch.dict(os.environ, {"RAZORPAY_WEBHOOK_SECRET": "x"}), \
             patch("hmac.compare_digest", return_value=True), \
             patch.object(api_mod, "_mark_webhook_processed", side_effect=fake_mark):
            api_mod._handle_acad_razorpay_webhook(h, self._body(event_id="evt_acad_42"))
        assert seen == [("evt_acad_42", "razorpay_acad")]
