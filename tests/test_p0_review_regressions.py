"""Regression tests for the P0 findings in docs/reviews/2026-09-10-professional-review.md.

Three defects were reproduced by that review and are fixed in the live code
paths. Each one gets a test that fails if the old behaviour ever comes back:

  F01  api/index.py::_handle_auth_legacy accepted any non-empty password.
  F02  _process_razorpay_payment recorded the WHOLE batch amount on every job.
  F04  api/handlers_order.py turned a pricing exception into a ₹0 order.

These sit alongside tests/test_core_*.py, which cover the new shared routines.
The distinction matters and is the lesson of F02: the repository already had
allocation tests, but they covered a module that is no longer deployed. These
tests exercise the handlers that actually serve production traffic.
"""
from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest

import api.handlers_order as ho
import api.index as api_mod


def fake_h():
    return MagicMock()


def capture(monkeypatch, module):
    """Capture whatever the handler passes to _json_response."""
    seen: dict = {}
    monkeypatch.setattr(module, "_json_response",
                        lambda h, status, data: seen.update(status=status, data=data))
    return seen


# ═══════════════════════════════════════════════════════════════════════════
# F01 — the legacy auth endpoint must never accept an unverified credential
# ═══════════════════════════════════════════════════════════════════════════

AUTH_ENV = (
    "STAFF_TOKEN_HASH", "STORE_SHA256_HASH", "MIS_SHA256_HASH",
    "SUPERADMIN_SHA256_HASH", "ADMIN_PBKDF2_HASH", "ADMIN_PBKDF2_SALT",
)


@pytest.fixture
def auth_env(monkeypatch):
    """A clean auth environment, with the staff-PIN database unreachable."""
    for name in AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api_mod, "_mint_supabase_jwt", lambda: "JWT-SHOULD-NOT-BE-ISSUED")
    db = sys.modules.get("db_cloud")
    if db is not None:
        monkeypatch.setattr(db, "_client", MagicMock(side_effect=Exception("no db")),
                            raising=False)
    return monkeypatch


def sha256_hex(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("password", ["hunter2", "wrong", "x", "' OR 1=1 --", "1234567890abc"])
def test_wrong_password_is_rejected_when_a_hash_is_configured(auth_env, monkeypatch, password):
    """The exact reproduction in the review: a configured hash that does not match."""
    monkeypatch.setenv("STORE_SHA256_HASH", sha256_hex("the-real-token"))
    seen = capture(monkeypatch, api_mod)
    api_mod._handle_auth_legacy(fake_h(), json.dumps({"type": "store", "password": password}).encode())
    assert seen["status"] == 401
    assert seen["data"]["ok"] is False
    assert "supabase_jwt" not in seen["data"]


def test_an_unconfigured_host_refuses_everyone_with_503(auth_env, monkeypatch):
    """No hashes at all used to mean 'let everyone in'. It now means 'ask nobody in'."""
    seen = capture(monkeypatch, api_mod)
    api_mod._handle_auth_legacy(fake_h(), json.dumps({"password": "anything"}).encode())
    assert seen["status"] == 503
    assert seen["data"]["ok"] is False
    assert "supabase_jwt" not in seen["data"]


def test_the_correct_store_token_still_works(auth_env, monkeypatch):
    monkeypatch.setenv("STORE_SHA256_HASH", sha256_hex("the-real-token"))
    seen = capture(monkeypatch, api_mod)
    api_mod._handle_auth_legacy(
        fake_h(), json.dumps({"type": "store", "password": "the-real-token"}).encode()
    )
    assert seen["status"] == 200
    assert seen["data"]["ok"] is True


def test_the_correct_admin_password_still_works(auth_env, monkeypatch):
    import hashlib

    salt = b"\x01\x02\x03\x04"
    digest = hashlib.pbkdf2_hmac("sha256", b"correct horse", salt, 600_000, 32).hex()
    monkeypatch.setenv("ADMIN_PBKDF2_HASH", digest)
    monkeypatch.setenv("ADMIN_PBKDF2_SALT", salt.hex())
    seen = capture(monkeypatch, api_mod)
    api_mod._handle_auth_legacy(
        fake_h(), json.dumps({"type": "admin", "password": "correct horse"}).encode()
    )
    assert seen["status"] == 200 and seen["data"]["ok"] is True

    api_mod._handle_auth_legacy(
        fake_h(), json.dumps({"type": "admin", "password": "wrong horse"}).encode()
    )
    assert seen["status"] == 401


def test_a_type_is_checked_against_its_own_hash_only(auth_env, monkeypatch):
    """The MIS password must not open the superadmin door."""
    monkeypatch.setenv("MIS_SHA256_HASH", sha256_hex("mis-pw"))
    monkeypatch.setenv("SUPERADMIN_SHA256_HASH", sha256_hex("super-pw"))
    seen = capture(monkeypatch, api_mod)
    api_mod._handle_auth_legacy(
        fake_h(), json.dumps({"type": "superadmin", "password": "mis-pw"}).encode()
    )
    assert seen["status"] == 401


def test_an_empty_password_is_a_400_not_a_login(auth_env, monkeypatch):
    seen = capture(monkeypatch, api_mod)
    api_mod._handle_auth_legacy(fake_h(), json.dumps({"password": ""}).encode())
    assert seen["status"] == 400
    assert seen["data"]["ok"] is False


def test_a_database_outage_does_not_open_the_door(auth_env, monkeypatch):
    """The staff-PIN lookup raising must not fall through to success."""
    monkeypatch.setenv("STORE_SHA256_HASH", sha256_hex("the-real-token"))
    seen = capture(monkeypatch, api_mod)
    api_mod._handle_auth_legacy(fake_h(), json.dumps({"pin": "1234"}).encode())
    assert seen["status"] == 401


# ═══════════════════════════════════════════════════════════════════════════
# F02 — a batch payment is allocated across its jobs, not copied onto each
# ═══════════════════════════════════════════════════════════════════════════

def _wire_batch(monkeypatch, job_ids, quoted_by_job, amount):
    """Stub db_cloud + notifications for _process_razorpay_payment's batch branch."""
    recorded: list[tuple[str, float]] = []
    db = sys.modules["db_cloud"]
    rz = sys.modules["razorpay_integration"]
    wa = sys.modules["whatsapp_notify"]

    monkeypatch.setattr(api_mod, "_mark_webhook_processed", lambda *a, **k: True)
    monkeypatch.setattr(rz, "parse_payment_webhook", lambda data: {
        "job_id": "BATCH-1", "amount": amount, "method": "upi", "payment_id": "pay_1",
    }, raising=False)
    monkeypatch.setattr(db, "get_batch",
                        lambda ref: {"job_ids": ",".join(job_ids), "phone": ""}, raising=False)
    monkeypatch.setattr(db, "get_job",
                        lambda jid: {"amount_quoted": quoted_by_job.get(jid, 0),
                                     "pickup_code": f"PC-{jid}"}, raising=False)
    monkeypatch.setattr(db, "update_job_paid",
                        lambda jid, amt, method, pay_id: recorded.append((jid, amt)),
                        raising=False)
    monkeypatch.setattr(db, "update_batch_paid", lambda ref: None, raising=False)
    monkeypatch.setattr(db, "update_jobs_payment_link", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(wa, "send_payment_confirmed", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(wa, "send_staff_alert", lambda *a, **k: None, raising=False)
    return recorded


def test_a_batch_payment_is_not_multiplied_across_its_jobs(monkeypatch):
    """The review's reproduction: ₹100 over two jobs used to record ₹200."""
    recorded = _wire_batch(monkeypatch, ["J1", "J2"], {"J1": 40.0, "J2": 60.0}, 100.0)
    api_mod._process_razorpay_payment({"id": "evt_1", "event": "payment.captured"})
    assert [jid for jid, _ in recorded] == ["J1", "J2"]
    assert sum(amount for _, amount in recorded) == pytest.approx(100.0)
    assert dict(recorded) == {"J1": 40.0, "J2": 60.0}


def test_a_three_way_batch_totals_exactly_the_payment(monkeypatch):
    recorded = _wire_batch(
        monkeypatch, ["J1", "J2", "J3"], {"J1": 33.33, "J2": 33.33, "J3": 33.34}, 100.0
    )
    api_mod._process_razorpay_payment({"id": "evt_2", "event": "payment.captured"})
    assert sum(amount for _, amount in recorded) == pytest.approx(100.0, abs=0.005)


def test_a_batch_whose_jobs_have_no_quote_still_sums_to_the_payment(monkeypatch):
    """Zero weights must split evenly rather than divide by zero or drop paise."""
    recorded = _wire_batch(monkeypatch, ["J1", "J2", "J3"], {}, 100.0)
    api_mod._process_razorpay_payment({"id": "evt_3", "event": "payment.captured"})
    assert sum(amount for _, amount in recorded) == pytest.approx(100.0, abs=0.005)
    assert len(recorded) == 3


def test_a_single_job_batch_records_the_whole_payment(monkeypatch):
    recorded = _wire_batch(monkeypatch, ["J1"], {"J1": 100.0}, 100.0)
    api_mod._process_razorpay_payment({"id": "evt_4", "event": "payment.captured"})
    assert recorded == [("J1", 100.0)]


# ═══════════════════════════════════════════════════════════════════════════
# F04 — a pricing failure is a refusal, not a free order
# ═══════════════════════════════════════════════════════════════════════════

ORDER_PAYLOAD = {
    "customer": {"name": "Asha", "whatsapp": "919495706405", "delivery": 0},
    "file_url": "https://x/orders/u/report.pdf",
    "file_name": "report.pdf",
    "print_spec": {
        "file_ext": "pdf", "total_pages": 5, "pages_included": [1, 2, 3, 4, 5],
        "colour_mode": "bw", "nup": 1, "copies": 1, "paper_size": "A4",
        "sides": "single", "binding": "none",
    },
}


def _wire_order(monkeypatch, broken=True):
    calls: dict = {}
    monkeypatch.setattr(ho, "_insert_job", lambda **kw: calls.setdefault("insert", kw))
    monkeypatch.setattr(ho, "_persist_settings",
                        lambda job_id, **kw: calls.setdefault("settings", kw))
    monkeypatch.setattr(ho, "_send_confirmation", lambda *a, **k: calls.setdefault("wa", True))
    monkeypatch.setattr(ho, "_resolve_request_account", lambda h: {})
    if broken:
        def explode(items, finishing, size):
            raise RuntimeError("rate table unavailable")
        monkeypatch.setattr(ho, "_quote_total", explode)
    return calls


def test_a_pricing_failure_does_not_create_a_zero_rupee_order(monkeypatch):
    seen = capture(monkeypatch, ho)
    calls = _wire_order(monkeypatch)
    ho._handle_order_create(fake_h(), json.dumps(ORDER_PAYLOAD).encode())

    assert seen["status"] == 503
    assert seen["data"]["code"] == "pricing_unavailable"
    assert "insert" not in calls, "no job may be created without a price"
    assert "wa" not in calls, "the customer must not be told an order exists"


def test_a_pricing_failure_at_the_counter_is_also_refused(monkeypatch):
    """The staff walk-in path had the same `except Exception: total = 0.0`."""
    monkeypatch.setattr(api_mod, "_acad_auth_staff", lambda h: True)
    seen = capture(monkeypatch, ho)
    calls = _wire_order(monkeypatch)
    payload = {**ORDER_PAYLOAD, "store_id": "OSP", "customer_name": "Walk-in"}
    ho._handle_order_staff_create(fake_h(), json.dumps(payload).encode())

    assert seen["status"] == 503
    assert seen["data"]["code"] == "pricing_unavailable"
    assert "insert" not in calls


def test_a_priced_order_is_still_created_normally(monkeypatch):
    seen = capture(monkeypatch, ho)
    calls = _wire_order(monkeypatch, broken=False)
    monkeypatch.setattr(ho, "_quote_total", lambda items, finishing, size: 91.5)
    ho._handle_order_create(fake_h(), json.dumps(ORDER_PAYLOAD).encode())

    assert seen["status"] == 200
    assert calls["settings"]["amount_quoted"] == 91.5


# ═══════════════════════════════════════════════════════════════════════════
# Adjacent fail-loud gap found while fixing F01
# ═══════════════════════════════════════════════════════════════════════════

def test_staff_login_answers_when_the_database_is_down(monkeypatch):
    """/staff/login used to log the error and write no response at all.

    The console got an empty reply and the login screen sat there with no
    message — a silent failure on an authentication path (docs/FAIL_LOUD.md).
    """
    db = sys.modules.get("db_cloud")
    monkeypatch.setattr(db, "_client", MagicMock(side_effect=Exception("connection reset")),
                        raising=False)
    seen = capture(monkeypatch, api_mod)
    api_mod._handle_staff_login(fake_h(), json.dumps({"pin": "1234"}).encode())
    assert seen["status"] == 503
    assert seen["data"]["ok"] is False
