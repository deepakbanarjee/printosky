"""core.identity — fail-closed credentials, individual identities, store scope.

Review findings F01 (authentication accepted incorrect passwords) and F09
(one shared identity, no roles, no store scope).
"""
import time

import pytest

from core.errors import AuthenticationError, AuthorizationError, ConfigurationError, ValidationError
from core.identity import (
    IdentityRecord, Principal, Role, authenticate, hash_secret, mint_session,
    next_backoff_seconds, require_permission, require_store, throttle_check,
    verify_secret, verify_session,
)

KEY = "a" * 48


def staff(identity_id="anu", secret="4821", role=Role.COUNTER, stores=("OSP",), active=True):
    digest, salt = hash_secret(secret)
    return IdentityRecord(identity_id, identity_id.title(), role, stores, digest, salt, active)


# ── F01: nothing but a verified credential gets in ───────────────────────────

def test_correct_credential_authenticates():
    principal = authenticate("4821", [staff()])
    assert principal.identity_id == "anu"
    assert principal.role is Role.COUNTER


@pytest.mark.parametrize("wrong", ["", "0000", "4822", "48210", " 4821", "4821 ", "admin"])
def test_wrong_credential_never_authenticates(wrong):
    with pytest.raises(AuthenticationError):
        authenticate(wrong, [staff()])


def test_inactive_identity_cannot_sign_in():
    with pytest.raises(AuthenticationError):
        authenticate("4821", [staff(active=False)])


def test_no_candidates_means_no_login():
    """The exact shape of F01: an unconfigured deployment must reject everyone."""
    with pytest.raises(AuthenticationError):
        authenticate("anything", [])


@pytest.mark.parametrize("stored_hash,stored_salt", [
    ("", "salt"), (None, "salt"), ("garbage", "salt"), ("garbage", None),
])
def test_verify_secret_fails_closed_on_unusable_storage(stored_hash, stored_salt):
    assert verify_secret("4821", stored_hash, stored_salt) is False


def test_verify_secret_accepts_legacy_unsalted_hashes():
    import hashlib

    legacy = hashlib.sha256(b"4821").hexdigest()
    assert verify_secret("4821", legacy, None) is True
    assert verify_secret("4822", legacy, None) is False


# ── F09: roles and store scope ───────────────────────────────────────────────

def test_roles_gate_capabilities():
    counter = staff(role=Role.COUNTER).to_principal()
    owner = staff("deepak", role=Role.OWNER, stores=("*",)).to_principal()
    assert counter.has_permission("payment:record")
    assert not counter.has_permission("payment:refund")
    assert owner.has_permission("payment:refund")


def test_unknown_permission_is_denied_not_allowed():
    assert staff(role=Role.OWNER).to_principal().has_permission("nonexistent:thing") is False


def test_store_scope_is_enforced():
    nattika = staff(stores=("PRINTK",)).to_principal()
    require_store(nattika, "PRINTK")
    with pytest.raises(AuthorizationError):
        require_store(nattika, "OSP")


def test_wildcard_scope_reaches_every_store():
    owner = staff("deepak", role=Role.OWNER, stores=("*",)).to_principal()
    require_store(owner, "OSP")
    require_store(owner, "PRINTK")


def test_require_permission_without_a_principal_is_a_401():
    with pytest.raises(AuthenticationError):
        require_permission(None, "order:read")


# ── Sessions ─────────────────────────────────────────────────────────────────

def test_round_trip():
    principal = staff().to_principal()
    token, signed = mint_session(principal, KEY)
    back = verify_session(token, KEY)
    assert back.identity_id == "anu"
    assert back.session_id == signed.session_id
    assert back.store_ids == ("OSP",)


@pytest.mark.parametrize("mangle", [
    lambda t: t + "x",                      # broken signature
    lambda t: t.replace("pk2", "pk1", 1),   # wrong prefix
    lambda t: t.split(".")[1],              # not a token at all
    lambda t: "",
    lambda t: "pk2.x.y",
])
def test_a_tampered_token_is_refused(mangle):
    token, _ = mint_session(staff().to_principal(), KEY)
    with pytest.raises(AuthenticationError):
        verify_session(mangle(token), KEY)


def test_a_token_signed_with_another_key_is_refused():
    token, _ = mint_session(staff().to_principal(), KEY)
    with pytest.raises(AuthenticationError):
        verify_session(token, "b" * 48)


def test_an_expired_token_is_refused():
    now = int(time.time())
    token, _ = mint_session(staff().to_principal(), KEY, ttl_seconds=60, now=now)
    verify_session(token, KEY, now=now + 30)
    with pytest.raises(AuthenticationError, match="expired"):
        verify_session(token, KEY, now=now + 3600)


def test_a_revoked_session_is_refused():
    token, signed = mint_session(staff().to_principal(), KEY)
    with pytest.raises(AuthenticationError, match="revoked"):
        verify_session(token, KEY, is_revoked=lambda sid: sid == signed.session_id)


def test_no_signing_key_means_no_session_either_way():
    with pytest.raises(ConfigurationError):
        mint_session(staff().to_principal(), "")
    with pytest.raises(ConfigurationError):
        verify_session("pk2.a.b", "")


def test_an_absurd_ttl_is_refused():
    with pytest.raises(ValidationError):
        mint_session(staff().to_principal(), KEY, ttl_seconds=10 ** 9)


def test_an_oversized_token_is_refused_without_parsing():
    with pytest.raises(AuthenticationError):
        verify_session("pk2." + "a" * 9000, KEY)


# ── Throttling ───────────────────────────────────────────────────────────────

def test_throttle_opens_and_closes():
    assert throttle_check([], now=1000)[0] is True
    assert throttle_check([999, 998, 997, 996], now=1000)[0] is True
    allowed, retry_after = throttle_check([999, 998, 997, 996, 995], now=1000)
    assert allowed is False and retry_after > 0


def test_old_failures_fall_out_of_the_window():
    stale = [1, 2, 3, 4, 5]
    assert throttle_check(stale, now=100000)[0] is True


def test_backoff_is_bounded_and_deterministic():
    assert next_backoff_seconds(0) == 0
    assert next_backoff_seconds(1) == 2
    assert next_backoff_seconds(3) == 8
    assert next_backoff_seconds(99) == 900
