"""Per-request context: settings, unit of work, and the verified principal.

Settings are read from the environment **once per process** and validated
eagerly, so a missing signing key is a clear 503 on the first request instead
of a confusing authentication failure on the hundredth. Nothing here invents a
default for a secret; :class:`core.errors.ConfigurationError` is the answer to
"not configured", which is the fail-closed half of F01.

``authenticate`` is the single place a caller becomes a
:class:`core.identity.Principal`. It accepts exactly two credentials:

* ``Authorization: Bearer <pk2 token>`` — a v2 session, minted by ``/v2/auth/login``.
* ``Authorization: Bearer <PRINTOSKY_AGENT_TOKEN>`` — a store PC, which gets the
  ``agent`` role scoped to the store it declares in ``X-Device-Id``.

Anything else is unauthenticated. There is no "if it is non-empty, let them in"
branch, and there never will be one.
"""

from __future__ import annotations

import hmac
import logging
import os
from dataclasses import dataclass, field

from core.errors import AuthenticationError, ConfigurationError
from core.identity import Principal, Role, require_permission, verify_session
from api.v2.http import Request

logger = logging.getLogger("api.v2.deps")

__all__ = ["Context", "Settings", "authenticate", "load_settings"]


@dataclass(frozen=True)
class Settings:
    session_key: str = ""
    agent_token: str = ""
    session_ttl_seconds: int = 12 * 3600
    supabase_url: str = ""
    supabase_service_key: str = ""
    environment: str = "production"

    @property
    def sessions_enabled(self) -> bool:
        return bool(self.session_key)

    def require_session_key(self) -> str:
        if not self.session_key:
            raise ConfigurationError(
                "PRINTOSKY_SESSION_KEY is not set — v2 sessions are disabled"
            )
        return self.session_key


_settings: Settings | None = None


def load_settings(*, refresh: bool = False) -> Settings:
    global _settings
    if _settings is not None and not refresh:
        return _settings
    _settings = Settings(
        session_key=os.environ.get("PRINTOSKY_SESSION_KEY", ""),
        agent_token=os.environ.get("PRINTOSKY_AGENT_TOKEN", ""),
        session_ttl_seconds=int(os.environ.get("PRINTOSKY_SESSION_TTL", "43200") or 43200),
        supabase_url=os.environ.get("SUPABASE_URL", ""),
        supabase_service_key=os.environ.get("SUPABASE_SERVICE_KEY", "")
        or os.environ.get("SUPABASE_KEY", ""),
        environment=os.environ.get("VERCEL_ENV", "development"),
    )
    if not _settings.session_key:
        # Loud at boot, not at the first login: a deploy that forgot the key
        # should be obvious in the function log before a customer finds it.
        logger.warning("PRINTOSKY_SESSION_KEY unset — /v2/auth will answer 503")
    return _settings


@dataclass
class Context:
    """What a handler is handed besides the request."""

    settings: Settings
    uow: object                                  # core.ports.UnitOfWork
    principal: Principal | None = None
    request_id: str = ""
    extras: dict = field(default_factory=dict)

    def require(self, permission: str) -> Principal:
        require_permission(self.principal, permission)
        assert self.principal is not None  # narrowed by require_permission  # noqa: S101
        return self.principal


def authenticate(request: Request, settings: Settings, uow) -> Principal | None:
    """Resolve the caller, or return None. Never raises for *absent* credentials.

    A present-but-invalid credential does raise: silently downgrading a bad
    token to "anonymous" would turn an expired session into a confusing 403 on
    a route that allows anonymous access, and hide revocation from the logs.
    """
    token = request.bearer_token()
    if not token:
        return None

    # Store-agent shared token. Constant-time compared, and only usable when the
    # deployment actually configured one.
    if settings.agent_token and hmac.compare_digest(token, settings.agent_token):
        device_id = request.header("x-device-id", "")
        store_id = request.header("x-store-id", "") or device_id.split(":")[0]
        if not store_id:
            raise AuthenticationError("agent token requires X-Store-Id")
        return Principal(
            identity_id=f"agent:{device_id or store_id}",
            display_name=f"store agent {store_id}",
            role=Role.AGENT,
            store_ids=(store_id,),
            device_id=device_id,
        )

    revoked = getattr(getattr(uow, "identities", None), "is_revoked", None)
    return verify_session(token, settings.require_session_key(), is_revoked=revoked)
