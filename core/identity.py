"""Individual identities, roles, store scope and fail-closed credential checks.

This replaces two things the 2026-09-10 review called out:

* **F01 — the bypass.** ``_handle_auth_legacy`` ended with "if the password is
  non-empty, return ok: true and mint a JWT". Every function here is written so
  that the *only* way out with a session is a credential that verified. There
  is no trailing success branch: the last statement of every verify path raises
  :class:`core.errors.AuthenticationError`.
* **F09 — one shared identity.** A PIN login used to produce a JWT for a single
  shared Supabase Auth user, so the database could not tell Anu from Divya from
  a store PC. A :class:`Principal` here is one person or one device, with a
  role and an explicit list of stores it may touch.

Sessions are **stateless signed tokens**: ``pk2.<payload-b64>.<hmac-b64>``,
HMAC-SHA256 over the payload with ``PRINTOSKY_SESSION_KEY``. Stateless because
the store PC has to be able to check a session while the internet is down, and
a database round-trip per request would make that impossible. Revocation is
therefore explicit: a caller may pass ``is_revoked`` (a jti lookup) when it can
reach the store, and short TTLs bound the damage when it cannot.

Nothing here reads the environment or a database. The transport layer supplies
the signing key and the revocation check; see ``api/v2/deps.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Sequence

from core.errors import AuthenticationError, AuthorizationError, ConfigurationError, ValidationError

__all__ = [
    "ALL_STORES",
    "IdentityRecord",
    "PBKDF2_ITERATIONS",
    "PERMISSIONS",
    "Principal",
    "Role",
    "authenticate",
    "hash_secret",
    "mint_session",
    "next_backoff_seconds",
    "require_permission",
    "require_store",
    "throttle_check",
    "verify_secret",
    "verify_session",
]

# Matches api/index.py::_PBKDF2_ITER so an existing staff PIN hash verifies
# unchanged. Raising it needs a rehash-on-login migration, not just a new number.
PBKDF2_ITERATIONS = 100_000

_TOKEN_PREFIX = "pk2"
_DEFAULT_TTL_SECONDS = 12 * 3600      # one shift
_MAX_TTL_SECONDS = 30 * 24 * 3600     # a store agent's device token
_MAX_TOKEN_BYTES = 4096               # a token bigger than this is an attack, not a session


class Role(str, Enum):
    """Who someone is, in the shop's own vocabulary.

    Ordered loosely by breadth, but authority is decided by the permission
    matrix below, never by comparing two roles — "greater than counter" is the
    kind of check that quietly grants production rights to an owner's phone.
    """

    OWNER = "owner"              # Deepak: everything, including money and staff
    MANAGER = "manager"          # a shift lead: everything operational in their stores
    COUNTER = "counter"          # takes orders, takes cash, hands over
    PRODUCTION = "production"    # prints, finishes, marks ready
    AGENT = "agent"              # a store PC, not a person
    VIEWER = "viewer"            # read-only (reports, an accountant)


# One place that says who may do what. Adding a capability means adding a row
# here, which is a visible decision in a diff — the alternative is an `if
# role == "owner" or role == "manager"` growing in six handlers.
PERMISSIONS: dict[str, frozenset[Role]] = {
    "order:read":       frozenset({Role.OWNER, Role.MANAGER, Role.COUNTER, Role.PRODUCTION, Role.AGENT, Role.VIEWER}),
    "order:create":     frozenset({Role.OWNER, Role.MANAGER, Role.COUNTER, Role.AGENT}),
    "order:price":      frozenset({Role.OWNER, Role.MANAGER, Role.COUNTER, Role.AGENT}),
    "order:accept":     frozenset({Role.OWNER, Role.MANAGER, Role.COUNTER}),
    "order:cancel":     frozenset({Role.OWNER, Role.MANAGER}),
    "payment:record":   frozenset({Role.OWNER, Role.MANAGER, Role.COUNTER}),
    "payment:refund":   frozenset({Role.OWNER}),
    "payment:read":     frozenset({Role.OWNER, Role.MANAGER, Role.VIEWER}),
    "production:claim": frozenset({Role.OWNER, Role.MANAGER, Role.PRODUCTION, Role.AGENT}),
    "production:write": frozenset({Role.OWNER, Role.MANAGER, Role.PRODUCTION, Role.AGENT}),
    "handover:write":   frozenset({Role.OWNER, Role.MANAGER, Role.COUNTER}),
    "staff:manage":     frozenset({Role.OWNER}),
    "ops:read":         frozenset({Role.OWNER, Role.MANAGER, Role.AGENT, Role.VIEWER}),
}

ALL_STORES = "*"


@dataclass(frozen=True)
class Principal:
    """A verified identity: one person, or one device.

    ``store_ids`` is the scope. ``("*",)`` is the owner's everywhere; anything
    else is an explicit list, and :func:`require_store` refuses a store that is
    not in it. A counter PIN at Nattika cannot read Thriprayar's money.
    """

    identity_id: str
    display_name: str
    role: Role
    store_ids: tuple[str, ...] = ()
    session_id: str = ""
    issued_at: int = 0
    expires_at: int = 0
    device_id: str = ""

    def has_permission(self, permission: str) -> bool:
        allowed = PERMISSIONS.get(permission)
        if allowed is None:
            # An unknown permission is a programming error, and the safe answer
            # to "may I do this thing nobody defined?" is no.
            return False
        return self.role in allowed

    def can_access_store(self, store_id: str) -> bool:
        if not store_id:
            return False
        return ALL_STORES in self.store_ids or store_id in self.store_ids

    def to_dict(self) -> dict:
        return {
            "identity_id": self.identity_id,
            "display_name": self.display_name,
            "role": self.role.value,
            "store_ids": list(self.store_ids),
            "session_id": self.session_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "device_id": self.device_id,
        }


def require_permission(principal: Principal | None, permission: str) -> None:
    """Raise unless ``principal`` holds ``permission``. No principal is a 401."""
    if principal is None:
        raise AuthenticationError("sign in to continue")
    if not principal.has_permission(permission):
        raise AuthorizationError(
            f"{principal.role.value} may not {permission}",
            details={"permission": permission, "role": principal.role.value},
        )


def require_store(principal: Principal | None, store_id: str) -> None:
    """Raise unless ``principal`` is scoped to ``store_id``."""
    if principal is None:
        raise AuthenticationError("sign in to continue")
    if not principal.can_access_store(store_id):
        raise AuthorizationError(
            f"not scoped to store {store_id}",
            details={"store_id": store_id, "scope": list(principal.store_ids)},
        )


# ── Credentials ──────────────────────────────────────────────────────────────

def hash_secret(secret: str, *, salt: str | None = None, iterations: int = PBKDF2_ITERATIONS) -> tuple[str, str]:
    """PBKDF2-SHA256 a PIN or password. Returns ``(hash_hex, salt_hex)``."""
    if not secret:
        raise ValidationError("empty secret")
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt.encode(), iterations).hex()
    return digest, salt


def verify_secret(provided: str, stored_hash: str | None, stored_salt: str | None = None,
                  *, iterations: int = PBKDF2_ITERATIONS) -> bool:
    """Constant-time credential check that fails closed on every unhappy path.

    Returns ``False`` — never ``True`` — when the credential is empty, when no
    hash is stored, or when the stored hash is unusable. A store that has not
    been configured rejects everyone; it does not accept everyone, which is
    precisely the shape of F01.

    ``stored_salt is None`` verifies a legacy unsalted SHA-256 hash so existing
    staff rows keep working through the migration; those rows should be
    rehashed on next successful login by the caller.
    """
    if not provided or not stored_hash:
        return False
    try:
        if stored_salt is None:
            candidate = hashlib.sha256(provided.encode()).hexdigest()
        else:
            candidate = hashlib.pbkdf2_hmac(
                "sha256", provided.encode(), stored_salt.encode(), iterations
            ).hex()
    except (ValueError, TypeError, UnicodeError):
        return False
    return hmac.compare_digest(candidate, stored_hash)


# ── Login throttling ─────────────────────────────────────────────────────────

def throttle_check(recent_failures: Sequence[float], now: float | None = None,
                   *, window_seconds: int = 900, max_attempts: int = 5) -> tuple[bool, int]:
    """Durable-throttle decision as a pure function.

    Returns ``(allowed, retry_after_seconds)``. The caller owns storage — an
    in-process dict on Vercel is not durable across lambdas, so the cloud path
    should persist failures per identity; this function does not care where
    they came from.
    """
    now = time.time() if now is None else now
    live = [t for t in recent_failures if now - t < window_seconds]
    if len(live) < max_attempts:
        return True, 0
    oldest = min(live)
    return False, max(1, int(window_seconds - (now - oldest)))


def next_backoff_seconds(consecutive_failures: int, *, base: int = 2, cap: int = 900) -> int:
    """Exponential backoff for an agent retrying a login. Deterministic."""
    if consecutive_failures <= 0:
        return 0
    return min(cap, base ** min(consecutive_failures, 16))


# ── Session tokens ───────────────────────────────────────────────────────────

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def mint_session(principal: Principal, signing_key: str, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS,
                 now: int | None = None, session_id: str | None = None) -> tuple[str, Principal]:
    """Sign a session token for ``principal``. Returns ``(token, principal)``.

    The returned principal carries the ``session_id`` (jti) and expiry that were
    actually signed, so the caller can persist them for revocation without
    re-parsing the token.
    """
    if not signing_key:
        raise ConfigurationError("PRINTOSKY_SESSION_KEY is not set — refusing to mint a session")
    if ttl_seconds <= 0 or ttl_seconds > _MAX_TTL_SECONDS:
        raise ValidationError(f"ttl out of range: {ttl_seconds}", details={"max": _MAX_TTL_SECONDS})

    issued = int(time.time()) if now is None else int(now)
    sid = session_id or uuid.uuid4().hex
    signed = Principal(
        identity_id=principal.identity_id,
        display_name=principal.display_name,
        role=principal.role,
        store_ids=tuple(principal.store_ids),
        session_id=sid,
        issued_at=issued,
        expires_at=issued + ttl_seconds,
        device_id=principal.device_id,
    )
    payload = {
        "sub": signed.identity_id,
        "name": signed.display_name,
        "role": signed.role.value,
        "stores": list(signed.store_ids),
        "jti": sid,
        "iat": issued,
        "exp": signed.expires_at,
        "dev": signed.device_id,
    }
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = _b64(hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).digest())
    return f"{_TOKEN_PREFIX}.{body}.{sig}", signed


def verify_session(token: str, signing_key: str, *, now: int | None = None,
                   is_revoked: Callable[[str], bool] | None = None,
                   leeway_seconds: int = 30) -> Principal:
    """Verify a token and return its :class:`Principal`, or raise.

    Order matters: shape, then signature, then expiry, then revocation. The
    signature is checked before anything in the payload is trusted, so a forged
    ``exp`` never reaches a comparison.
    """
    if not signing_key:
        raise ConfigurationError("PRINTOSKY_SESSION_KEY is not set — cannot verify sessions")
    if not token or len(token) > _MAX_TOKEN_BYTES:
        raise AuthenticationError("invalid session")

    parts = token.split(".")
    if len(parts) != 3 or parts[0] != _TOKEN_PREFIX:
        raise AuthenticationError("invalid session")
    _, body, sig = parts

    expected = _b64(hmac.new(signing_key.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        raise AuthenticationError("invalid session")

    try:
        payload = json.loads(_unb64(body))
        role = Role(payload["role"])
        principal = Principal(
            identity_id=str(payload["sub"]),
            display_name=str(payload.get("name") or payload["sub"]),
            role=role,
            store_ids=tuple(str(s) for s in payload.get("stores") or ()),
            session_id=str(payload.get("jti") or ""),
            issued_at=int(payload.get("iat") or 0),
            expires_at=int(payload.get("exp") or 0),
            device_id=str(payload.get("dev") or ""),
        )
    except (ValueError, KeyError, TypeError) as exc:
        # A well-signed token we cannot parse means our own format changed.
        # Reject it and say so; do not fall back to a partial principal.
        raise AuthenticationError("unreadable session") from exc

    current = int(time.time()) if now is None else int(now)
    if principal.expires_at and current > principal.expires_at + leeway_seconds:
        raise AuthenticationError("session expired", details={"expired_at": principal.expires_at})
    if is_revoked is not None and principal.session_id and is_revoked(principal.session_id):
        raise AuthenticationError("session revoked")
    return principal


@dataclass
class IdentityRecord:
    """A row in ``identities``: what a login looks up.

    Kept as a plain dataclass rather than a dict so a missing ``active`` flag is
    a TypeError at the adapter boundary, not a truthy ``{}.get("active")``
    letting a disabled account sign in.
    """

    identity_id: str
    display_name: str
    role: Role
    store_ids: tuple[str, ...]
    secret_hash: str
    secret_salt: str | None = None
    active: bool = True
    kind: str = "staff"          # staff | device | service
    metadata: dict = field(default_factory=dict)

    def to_principal(self) -> Principal:
        return Principal(
            identity_id=self.identity_id,
            display_name=self.display_name,
            role=self.role,
            store_ids=tuple(self.store_ids),
        )


def authenticate(secret: str, candidates: Iterable[IdentityRecord]) -> Principal:
    """Find the identity whose stored credential verifies. Raise if none does.

    Every candidate is checked even after a match, so the time taken does not
    depend on the position of the matching row. The loop cannot fall through to
    a success: ``winner`` starts as ``None`` and the function raises unless a
    verification set it.
    """
    winner: IdentityRecord | None = None
    for record in candidates:
        if not record.active:
            continue
        if verify_secret(secret, record.secret_hash, record.secret_salt) and winner is None:
            winner = record
    if winner is None:
        raise AuthenticationError("incorrect credentials")
    return winner.to_principal()
