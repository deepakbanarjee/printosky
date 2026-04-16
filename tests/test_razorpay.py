"""
Tests for razorpay_integration.py
Covers: verify_webhook, parse_payment_webhook (pure logic, no HTTP needed)
"""

import sys
import os
import hmac
import hashlib
import types

# Set env vars BEFORE importing — module-level code reads them immediately
os.environ.setdefault("RAZORPAY_KEY_ID",         "test_key")
os.environ.setdefault("RAZORPAY_KEY_SECRET",     "test_secret")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")

# Stub dotenv and requests
for _mod in ("dotenv", "requests"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["dotenv"].load_dotenv = lambda: None  # type: ignore

# Give the requests stub a callable post() so create_payment_link tests can patch it
if not hasattr(sys.modules["requests"], "post"):
    sys.modules["requests"].post = None  # type: ignore

# requests.auth needs HTTPBasicAuth
_auth_mod = types.ModuleType("requests.auth")
class _BasicAuth:
    def __init__(self, u, p):
        self.username = u
        self.password = p
_auth_mod.HTTPBasicAuth = _BasicAuth
sys.modules["requests.auth"] = _auth_mod

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import razorpay_integration as rz


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

WEBHOOK_SECRET = "test_webhook_secret"

def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# verify_webhook
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyWebhook:
    def test_valid_signature(self):
        body = b'{"event":"payment.captured"}'
        sig  = _sign(body)
        assert rz.verify_webhook(body, sig) is True

    def test_invalid_signature(self):
        body = b'{"event":"payment.captured"}'
        assert rz.verify_webhook(body, "bad_signature") is False

    def test_tampered_body(self):
        body = b'{"event":"payment.captured"}'
        sig  = _sign(body)
        assert rz.verify_webhook(b'{"event":"payment.captured","extra":1}', sig) is False

    def test_empty_body(self):
        body = b""
        sig  = _sign(body)
        assert rz.verify_webhook(body, sig) is True

    def test_wrong_secret_fails(self):
        body = b'{"event":"test"}'
        sig  = _sign(body, secret="other_secret")
        assert rz.verify_webhook(body, sig) is False

    def test_returns_bool(self):
        body = b'test'
        sig  = _sign(body)
        result = rz.verify_webhook(body, sig)
        assert isinstance(result, bool)


# ─────────────────────────────────────────────────────────────────────────────
# parse_payment_webhook
# ─────────────────────────────────────────────────────────────────────────────

class TestParsePaymentWebhook:
    def _payment_link_paid(self, job_id="OSP-20260101-0001", amount_paise=10000,
                            method="UPI"):
        return {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {
                    "entity": {"reference_id": job_id, "notes": {"job_id": job_id}}
                },
                "payment": {
                    "entity": {
                        "id": "pay_test123",
                        "amount": amount_paise,
                        "method": method,
                    }
                },
            },
        }

    def _payment_captured(self, job_id="OSP-20260101-0001", amount_paise=10000):
        return {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_test456",
                        "amount": amount_paise,
                        "method": "card",
                        "notes": {"job_id": job_id},
                    }
                }
            },
        }

    def test_payment_link_paid_returns_dict(self):
        r = rz.parse_payment_webhook(self._payment_link_paid())
        assert isinstance(r, dict)

    def test_payment_link_paid_job_id(self):
        r = rz.parse_payment_webhook(self._payment_link_paid("OSP-001"))
        assert r["job_id"] == "OSP-001"

    def test_payment_link_paid_amount_in_rupees(self):
        # 10000 paise = Rs.100
        r = rz.parse_payment_webhook(self._payment_link_paid(amount_paise=10000))
        assert r["amount"] == 100.0

    def test_payment_link_paid_method_uppercased(self):
        r = rz.parse_payment_webhook(self._payment_link_paid(method="upi"))
        assert r["method"] == "UPI"

    def test_payment_link_paid_payment_id(self):
        r = rz.parse_payment_webhook(self._payment_link_paid())
        assert r["payment_id"] == "pay_test123"

    def test_payment_captured_event(self):
        r = rz.parse_payment_webhook(self._payment_captured("OSP-002"))
        assert r is not None
        assert r["job_id"] == "OSP-002"
        assert r["amount"] == 100.0

    def test_irrelevant_event_returns_none(self):
        assert rz.parse_payment_webhook({"event": "subscription.charged"}) is None

    def test_unknown_event_returns_none(self):
        assert rz.parse_payment_webhook({"event": "something_else"}) is None

    def test_missing_event_returns_none(self):
        assert rz.parse_payment_webhook({}) is None

    def test_no_job_id_returns_none(self):
        data = {
            "event": "payment_link.paid",
            "payload": {
                "payment_link": {"entity": {"reference_id": None, "notes": {}}},
                "payment": {"entity": {"id": "pay_x", "amount": 5000, "method": "UPI"}},
            },
        }
        assert rz.parse_payment_webhook(data) is None

    def test_malformed_payload_returns_none(self):
        assert rz.parse_payment_webhook({"event": "payment_link.paid", "payload": {}}) is None

    def test_amount_fractional_paise(self):
        r = rz.parse_payment_webhook(self._payment_link_paid(amount_paise=12550))
        assert r["amount"] == 125.5


# ─────────────────────────────────────────────────────────────────────────────
# _auth
# ─────────────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_returns_http_basic_auth(self):
        from requests.auth import HTTPBasicAuth
        auth = rz._auth()
        assert isinstance(auth, HTTPBasicAuth)

    def test_uses_configured_credentials(self, monkeypatch):
        monkeypatch.setattr(rz, "RAZORPAY_KEY_ID", "mykey")
        monkeypatch.setattr(rz, "RAZORPAY_KEY_SECRET", "mysecret")
        auth = rz._auth()
        assert auth.username == "mykey"
        assert auth.password == "mysecret"


# ─────────────────────────────────────────────────────────────────────────────
# create_payment_link
# ─────────────────────────────────────────────────────────────────────────────

def _mock_response(status_code, json_data):
    resp = types.SimpleNamespace()
    resp.status_code = status_code
    resp.json = lambda: json_data
    return resp


class TestCreatePaymentLink:
    def test_success_returns_url_and_link_id(self, monkeypatch):
        api_resp = {"id": "plink_123", "short_url": "https://rzp.io/l/abc"}
        monkeypatch.setattr(rz.requests, "post",
                            lambda *a, **kw: _mock_response(200, api_resp))
        result = rz.create_payment_link("OSP-20260101-0001", 50.0, "Print job")
        assert result["url"] == "https://rzp.io/l/abc"
        assert result["link_id"] == "plink_123"

    def test_amount_converted_to_paise(self, monkeypatch):
        captured = {}
        api_resp = {"id": "pl1", "short_url": "https://rzp.io/l/x"}
        def _post(url, json, auth, timeout):
            captured["paise"] = json["amount"]
            return _mock_response(200, api_resp)
        monkeypatch.setattr(rz.requests, "post", _post)
        rz.create_payment_link("OSP-1", 84.5, "desc")
        assert captured["paise"] == 8450

    def test_job_id_in_reference_and_notes(self, monkeypatch):
        captured = {}
        api_resp = {"id": "pl1", "short_url": "https://rzp.io/l/x"}
        def _post(url, json, auth, timeout):
            captured["payload"] = json
            return _mock_response(200, api_resp)
        monkeypatch.setattr(rz.requests, "post", _post)
        rz.create_payment_link("OSP-20260101-0099", 30.0, "desc")
        assert captured["payload"]["reference_id"] == "OSP-20260101-0099"
        assert captured["payload"]["notes"]["job_id"] == "OSP-20260101-0099"

    def test_phone_10_digit_gets_country_code(self, monkeypatch):
        captured = {}
        api_resp = {"id": "pl1", "short_url": "https://rzp.io/l/x"}
        def _post(url, json, auth, timeout):
            captured["payload"] = json
            return _mock_response(200, api_resp)
        monkeypatch.setattr(rz.requests, "post", _post)
        rz.create_payment_link("OSP-1", 10.0, "d", customer_phone="9876543210")
        assert captured["payload"]["customer"]["contact"] == "+919876543210"

    def test_no_phone_no_customer_field(self, monkeypatch):
        captured = {}
        api_resp = {"id": "pl1", "short_url": "https://rzp.io/l/x"}
        def _post(url, json, auth, timeout):
            captured["payload"] = json
            return _mock_response(200, api_resp)
        monkeypatch.setattr(rz.requests, "post", _post)
        rz.create_payment_link("OSP-1", 10.0, "d")
        assert "customer" not in captured["payload"]
        assert captured["payload"]["notify"]["sms"] is False

    def test_api_error_returns_error_dict(self, monkeypatch):
        error_resp = {"error": {"description": "Bad amount"}}
        monkeypatch.setattr(rz.requests, "post",
                            lambda *a, **kw: _mock_response(400, error_resp))
        result = rz.create_payment_link("OSP-1", -1.0, "d")
        assert "error" in result
        assert result["error"] == "Bad amount"

    def test_network_exception_returns_error_dict(self, monkeypatch):
        def _raise(*a, **kw): raise OSError("no network")
        monkeypatch.setattr(rz.requests, "post", _raise)
        result = rz.create_payment_link("OSP-1", 50.0, "d")
        assert "error" in result
        assert "no network" in result["error"]
