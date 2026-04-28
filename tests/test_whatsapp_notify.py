"""Tests for whatsapp_notify.py — mocks urllib.request so no real HTTP calls."""
import json
import os
import sys
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Force-load the real module from disk — sys.modules["whatsapp_notify"] may be
# a bare stub installed by test_security_bugs.py to satisfy other imports.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "_wn_real", os.path.join(os.path.dirname(__file__), "..", "whatsapp_notify.py")
)
wn = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(wn)


# ── helpers ───────────────────────────────────────────────────────────────────

def _mock_response(status=200, body=b'{"messages":[{"id":"abc"}]}'):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── _send_meta ────────────────────────────────────────────────────────────────

def test_send_meta_missing_creds_returns_false(monkeypatch):
    monkeypatch.setattr(wn, "META_PHONE_ID", "")
    monkeypatch.setattr(wn, "META_TOKEN", "")
    assert wn._send_meta("919999999999", "hello") is False


def test_send_meta_missing_phone_returns_false():
    assert wn._send_meta("", "hello") is False


def test_send_meta_normalises_10_digit_phone(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        return _mock_response()

    monkeypatch.setattr(wn, "META_PHONE_ID", "123")
    monkeypatch.setattr(wn, "META_TOKEN", "tok")
    with patch("urllib.request.urlopen", fake_urlopen):
        result = wn._send_meta("9876543210", "hi")  # 10-digit
    assert captured["body"]["to"] == "919876543210"
    assert result is True


def test_send_meta_strips_at_suffix(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        return _mock_response()

    monkeypatch.setattr(wn, "META_PHONE_ID", "123")
    monkeypatch.setattr(wn, "META_TOKEN", "tok")
    with patch("urllib.request.urlopen", fake_urlopen):
        wn._send_meta("919876543210@c.us", "hi")
    assert captured["body"]["to"] == "919876543210"


def test_send_meta_non_200_returns_false(monkeypatch):
    monkeypatch.setattr(wn, "META_PHONE_ID", "123")
    monkeypatch.setattr(wn, "META_TOKEN", "tok")
    with patch("urllib.request.urlopen", return_value=_mock_response(status=400)):
        result = wn._send_meta("919999999999", "hello")
    assert result is False


def test_send_meta_exception_returns_false(monkeypatch):
    monkeypatch.setattr(wn, "META_PHONE_ID", "123")
    monkeypatch.setattr(wn, "META_TOKEN", "tok")
    with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
        result = wn._send_meta("919999999999", "hello")
    assert result is False


# ── public send helpers (all delegate to _send/_send_meta) ───────────────────

def _patch_send(monkeypatch):
    mock = MagicMock(return_value=True)
    monkeypatch.setattr(wn, "_send_meta", mock)
    return mock


def test_send_file_received_calls_send(monkeypatch):
    m = _patch_send(monkeypatch)
    wn.send_file_received("JOB-001", "file.pdf", "919999999999")
    m.assert_called_once()
    assert "JOB-001" in m.call_args[0][1]


def test_send_file_received_no_sender_skips(monkeypatch):
    m = _patch_send(monkeypatch)
    wn.send_file_received("JOB-001", "file.pdf", "")
    m.assert_not_called()


def test_send_file_received_with_quote_start(monkeypatch):
    m = _patch_send(monkeypatch)
    result = wn.send_file_received_with_quote_start("JOB-002", "doc.pdf", "919999999999")
    assert result is True
    assert "JOB-002" in m.call_args[0][1]


def test_send_file_received_with_quote_start_no_sender(monkeypatch):
    _patch_send(monkeypatch)
    result = wn.send_file_received_with_quote_start("JOB-002", "doc.pdf", "")
    assert result is False


def test_send_payment_link(monkeypatch):
    m = _patch_send(monkeypatch)
    wn.send_payment_link("919999999999", "JOB-003", 150.0, "https://rzp.io/x")
    assert "150.00" in m.call_args[0][1]
    assert "https://rzp.io/x" in m.call_args[0][1]


def test_send_payment_link_with_description(monkeypatch):
    m = _patch_send(monkeypatch)
    wn.send_payment_link("919999999999", "JOB-003", 75.5, "https://rzp.io/x", "10 pages A4")
    assert "10 pages A4" in m.call_args[0][1]


def test_send_payment_confirmed(monkeypatch):
    m = _patch_send(monkeypatch)
    result = wn.send_payment_confirmed("919999999999", "JOB-004", 200.0)
    assert result is True
    assert "200.00" in m.call_args[0][1]


def test_send_job_ready(monkeypatch):
    m = _patch_send(monkeypatch)
    result = wn.send_job_ready("919999999999", "JOB-005")
    assert result is True
    assert "JOB-005" in m.call_args[0][1]


def test_send_staff_alert(monkeypatch):
    m = _patch_send(monkeypatch)
    result = wn.send_staff_alert("Printer jam on floor 1")
    assert result is True
    assert "Printer jam" in m.call_args[0][1]


def test_send_timeout_alert(monkeypatch):
    m = _patch_send(monkeypatch)
    result = wn.send_timeout_alert("JOB-006", "colour")
    assert result is True
    assert "JOB-006" in m.call_args[0][1]
    assert "colour" in m.call_args[0][1]
