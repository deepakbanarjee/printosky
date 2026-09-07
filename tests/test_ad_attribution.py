"""
SCHEMA v42: click-to-WhatsApp ad attribution.

Meta attaches a `referral` object to the first message after someone taps a
click-to-WhatsApp ad, and sends it exactly once. Before v42 the webhook read
id/from/type and dropped it, so ad spend could not be tied to any job or order.

These tests pin the three properties that make the capture worth having:
  1. a referral is recorded, whatever message type it rides in on;
  2. recording it never costs us the customer's actual message;
  3. failing to record it ALERTS rather than logging quietly (docs/FAIL_LOUD.md),
     because Meta does not resend it.
"""
from __future__ import annotations

import sys
import os
import types
from unittest.mock import patch, MagicMock

# ── stub heavy deps so api/index.py can be imported ───────────────────────────
for _mod in ("requests", "dotenv", "db_cloud", "whatsapp_bot",
             "whatsapp_notify", "razorpay_integration",
             "db_cloud_academic", "academic_whatsapp"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None


def _ensure(mod_name: str, attr: str, value):
    """Never overwrite a real attr — conftest may have pre-imported the module."""
    mod = sys.modules.get(mod_name)
    if mod is not None and not hasattr(mod, attr):
        setattr(mod, attr, value)


_ensure("whatsapp_notify", "_send",            lambda *a, **kw: True)
_ensure("whatsapp_notify", "send_staff_alert", lambda *a, **kw: True)
_ensure("whatsapp_notify", "send_ops_alert",   lambda *a, **kw: True)
_ensure("db_cloud", "log_message",     lambda *a, **kw: None)
_ensure("db_cloud", "upsert_contact",  lambda *a, **kw: None)
_ensure("db_cloud", "record_ad_click", lambda *a, **kw: True)

_wd = types.ModuleType("watchdog")
_wd_obs = types.ModuleType("watchdog.observers"); _wd_obs.Observer = object
_wd_ev = types.ModuleType("watchdog.events"); _wd_ev.FileSystemEventHandler = object
sys.modules["watchdog"] = _wd
sys.modules["watchdog.observers"] = _wd_obs
sys.modules["watchdog.events"] = _wd_ev

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
import api.index as api_mod  # noqa: E402


# A real click-to-WhatsApp payload shape, trimmed to the keys we read.
REFERRAL = {
    "source_url":  "https://fb.me/2abcDEF",
    "source_id":   "120210000000000000",
    "source_type": "ad",
    "headline":    "Project printing from 75 paise a page",
    "body":        "Send your PDF on WhatsApp. Ready in 20 minutes.",
    "ctwa_clid":   "ARabc123XYZ",
    "media_type":  "image",
}


def _payload(*, referral: dict | None = None, msg_type: str = "text",
             wamid: str = "wamid.AD1", sender: str = "918848623472") -> dict:
    msg: dict = {"id": wamid, "from": sender, "type": msg_type}
    if msg_type == "text":
        msg["text"] = {"body": "Hi"}
    else:
        msg[msg_type] = {"id": "media.1", "mime_type": "image/jpeg"}
    if referral is not None:
        msg["referral"] = referral
    return {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Ad Clicker"}}],
        "messages": [msg],
    }}]}]}


# ═════════════════════════════════════════════════════════════════════════════
# The webhook call site
# ═════════════════════════════════════════════════════════════════════════════

class TestWebhookCapturesReferral:
    def test_referral_is_recorded(self) -> None:
        with patch.object(api_mod, "_mark_webhook_processed", return_value=True), \
             patch.object(api_mod, "_record_ad_click") as rec, \
             patch.object(api_mod, "_handle_text"):
            api_mod._process_meta_webhook(_payload(referral=REFERRAL))
        rec.assert_called_once()
        sender, wamid, referral = rec.call_args[0]
        assert sender == "918848623472"
        assert wamid == "wamid.AD1"
        assert referral["ctwa_clid"] == "ARabc123XYZ"
        assert referral["source_id"] == "120210000000000000"

    def test_message_without_referral_records_nothing(self) -> None:
        """The overwhelmingly common case must be untouched by v42."""
        with patch.object(api_mod, "_mark_webhook_processed", return_value=True), \
             patch.object(api_mod, "_record_ad_click") as rec, \
             patch.object(api_mod, "_handle_text"):
            api_mod._process_meta_webhook(_payload())
        rec.assert_not_called()

    def test_customer_message_still_handled(self) -> None:
        """Attribution rides along; it must never swallow the actual message."""
        with patch.object(api_mod, "_mark_webhook_processed", return_value=True), \
             patch.object(api_mod, "_record_ad_click"), \
             patch.object(api_mod, "_handle_text") as handled:
            api_mod._process_meta_webhook(_payload(referral=REFERRAL))
        handled.assert_called_once()

    def test_referral_on_a_non_text_message_is_recorded(self) -> None:
        """An ad click that opens with a photo is worth the same as one that
        opens with 'Hi' — which is why the capture sits above the type dispatch."""
        with patch.object(api_mod, "_mark_webhook_processed", return_value=True), \
             patch.object(api_mod, "_record_ad_click") as rec, \
             patch.object(api_mod, "_handle_media", create=True), \
             patch.object(api_mod, "_handle_text"):
            api_mod._process_meta_webhook(_payload(referral=REFERRAL, msg_type="image"))
        rec.assert_called_once()

    def test_duplicate_delivery_does_not_double_record(self) -> None:
        """Meta retries on slow ACKs; the wamid guard runs first."""
        with patch.object(api_mod, "_mark_webhook_processed", return_value=False), \
             patch.object(api_mod, "_record_ad_click") as rec:
            api_mod._process_meta_webhook(_payload(referral=REFERRAL))
        rec.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# _record_ad_click — the fail-loud contract
# ═════════════════════════════════════════════════════════════════════════════

class TestRecordAdClickFailsLoud:
    def test_success_does_not_alert(self) -> None:
        with patch.object(sys.modules["db_cloud"], "record_ad_click",
                          return_value=True, create=True), \
             patch.object(api_mod, "_alert_ops") as alert:
            api_mod._record_ad_click("918848623472", "wamid.AD1", REFERRAL)
        alert.assert_not_called()

    def test_db_error_alerts(self) -> None:
        with patch.object(sys.modules["db_cloud"], "record_ad_click",
                          side_effect=Exception("connection reset by peer"),
                          create=True), \
             patch.object(api_mod, "_alert_ops") as alert:
            api_mod._record_ad_click("918848623472", "wamid.AD1", REFERRAL)
        alert.assert_called_once()
        summary, fallback = alert.call_args[0]
        assert "918848623472" in summary
        assert "120210000000000000" in fallback, "the alert must name the ad"

    def test_declined_payload_alerts(self) -> None:
        """A False return is a silent drop unless someone is told."""
        with patch.object(sys.modules["db_cloud"], "record_ad_click",
                          return_value=False, create=True), \
             patch.object(api_mod, "_alert_ops") as alert:
            api_mod._record_ad_click("918848623472", "wamid.AD1", REFERRAL)
        alert.assert_called_once()

    def test_never_raises_even_if_the_alert_fails(self) -> None:
        """The customer's message is handled after this returns."""
        with patch.object(sys.modules["db_cloud"], "record_ad_click",
                          side_effect=Exception("db down"), create=True), \
             patch.object(api_mod, "_alert_ops", side_effect=Exception("meta down")):
            api_mod._record_ad_click("918848623472", "wamid.AD1", REFERRAL)


# ═════════════════════════════════════════════════════════════════════════════
# db_cloud.record_ad_click — what actually gets written
# ═════════════════════════════════════════════════════════════════════════════

real_db_cloud = pytest.importorskip("db_cloud", reason="supabase SDK not installed")
if not hasattr(real_db_cloud, "record_ad_click"):  # the stub above, not the real module
    pytest.skip("real db_cloud unavailable", allow_module_level=True)


class TestRecordAdClickWrites:
    def _client(self):
        client = MagicMock()
        client.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
        client.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
        (client.table.return_value.update.return_value
               .eq.return_value.is_.return_value.execute.return_value) = MagicMock(data=[])
        return client

    def test_row_carries_the_ids_that_matter(self, monkeypatch) -> None:
        client = self._client()
        monkeypatch.setattr(real_db_cloud, "_client", lambda: client)
        assert real_db_cloud.record_ad_click("918848623472", "wamid.AD1", REFERRAL) is True

        row = client.table.return_value.upsert.call_args_list[0][0][0]
        assert row["phone"] == "918848623472"
        assert row["channel"] == "whatsapp"
        assert row["wamid"] == "wamid.AD1"
        assert row["source_id"] == "120210000000000000"
        assert row["ctwa_clid"] == "ARabc123XYZ"
        assert row["source_type"] == "ad"

    def test_first_touch_only_stamped_when_unset(self, monkeypatch) -> None:
        """is_(first_ad_at, null) is what stops a later ad stealing the credit."""
        client = self._client()
        monkeypatch.setattr(real_db_cloud, "_client", lambda: client)
        real_db_cloud.record_ad_click("918848623472", "wamid.AD1", REFERRAL)

        upd = client.table.return_value.update
        upd.assert_called_once()
        assert upd.call_args[0][0]["first_ad_source_id"] == "120210000000000000"
        upd.return_value.eq.return_value.is_.assert_called_once_with("first_ad_at", "null")

    def test_missing_wamid_still_inserts(self, monkeypatch) -> None:
        """An unattributed duplicate beats a lost click."""
        client = self._client()
        monkeypatch.setattr(real_db_cloud, "_client", lambda: client)
        assert real_db_cloud.record_ad_click("918848623472", "", REFERRAL) is True
        client.table.return_value.insert.assert_called_once()

    def test_empty_referral_is_declined_not_written(self, monkeypatch) -> None:
        client = self._client()
        monkeypatch.setattr(real_db_cloud, "_client", lambda: client)
        assert real_db_cloud.record_ad_click("918848623472", "wamid.X", {}) is False
        assert real_db_cloud.record_ad_click("", "wamid.X", REFERRAL) is False
        client.table.assert_not_called()

    def test_db_error_propagates_so_the_caller_can_alert(self, monkeypatch) -> None:
        client = self._client()
        client.table.return_value.upsert.return_value.execute.side_effect = Exception("boom")
        monkeypatch.setattr(real_db_cloud, "_client", lambda: client)
        with pytest.raises(Exception, match="boom"):
            real_db_cloud.record_ad_click("918848623472", "wamid.AD1", REFERRAL)
