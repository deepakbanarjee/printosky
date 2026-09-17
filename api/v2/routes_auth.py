"""Login, logout, whoami — fail-closed, individual, store-scoped.

This is the endpoint F01 describes the absence of. The legacy handler's last
branch was:

    if password:
        _json_response(h, 200, {"ok": True, "supabase_jwt": _mint_supabase_jwt()})

There is no equivalent here, and the tests in ``tests/test_v2_auth.py`` assert
that: wrong PIN, empty PIN, unknown identity, inactive identity and
unconfigured deployment all answer 401 or 503 and never carry a token.

What a successful login returns is a **v2 session token** (``pk2.…``) bound to
one identity, one role and an explicit store list — not a JWT for one shared
Supabase Auth user (F09). Legacy pages keep using the legacy endpoint; nothing
here changes their behaviour.
"""

from __future__ import annotations

import logging

from core.errors import AuthenticationError, ValidationError
from core.identity import Role, authenticate as authenticate_secret, mint_session, throttle_check
from api.v2.http import Request, Response, json_ok

logger = logging.getLogger("api.v2.auth")

_MIN_SECRET = 4
_MAX_SECRET = 128


def login(request: Request, ctx) -> Response:
    """POST /v2/auth/login — ``{"secret": "1234"}`` → a session token.

    The identity is discovered from the credential rather than supplied by the
    caller, matching how staff actually sign in at a counter (they type a PIN,
    not a username). ``identity_hint`` is accepted only to scope the throttle.
    """
    body = request.json()
    secret = str(body.get("secret") or body.get("pin") or body.get("password") or "")
    hint = str(body.get("identity_hint") or "")[:64]

    # Refuse to *verify* a credential that could not possibly be one. Cheap, and
    # it keeps a flood of empty posts off the PBKDF2 path.
    if not (_MIN_SECRET <= len(secret) <= _MAX_SECRET):
        raise ValidationError("credential missing or malformed", details={"field": "secret"})

    key = ctx.settings.require_session_key()   # 503 before anything else if unset

    allowed, retry_after = throttle_check(
        ctx.uow.identities.recent_failures(hint, request.client_ip)
    )
    if not allowed:
        raise AuthenticationError(
            "too many attempts — wait and try again",
            details={"retry_after_seconds": retry_after},
            code="too_many_attempts",
        )

    try:
        principal = authenticate_secret(
            secret, ctx.uow.identities.candidates_for_secret(kind="staff")
        )
    except AuthenticationError:
        ctx.uow.identities.record_failure(hint, request.client_ip)
        logger.warning("v2 login rejected from %s (hint=%r)", request.client_ip or "?", hint)
        raise

    token, signed = mint_session(principal, key, ttl_seconds=ctx.settings.session_ttl_seconds)
    ctx.uow.identities.start_session(signed)
    logger.info("v2 login ok: %s (%s)", signed.identity_id, signed.role.value)
    return json_ok(
        {
            "token": token,
            "expires_at": signed.expires_at,
            "identity": signed.to_dict(),
        },
        request_id=ctx.request_id,
    )


def me(request: Request, ctx) -> Response:
    """GET /v2/auth/me — who this token says you are, and what you may do."""
    principal = ctx.require("order:read")
    from core.identity import PERMISSIONS

    return json_ok(
        {
            "identity": principal.to_dict(),
            "permissions": sorted(
                name for name, roles in PERMISSIONS.items() if principal.role in roles
            ),
        },
        request_id=ctx.request_id,
    )


def logout(request: Request, ctx) -> Response:
    """POST /v2/auth/logout — revoke this session id now, not at expiry."""
    principal = ctx.require("order:read")
    if principal.session_id:
        ctx.uow.identities.revoke(principal.session_id)
    return json_ok({"revoked": bool(principal.session_id)}, request_id=ctx.request_id)


def agent_hello(request: Request, ctx) -> Response:
    """POST /v2/auth/agent — a store PC announces itself and gets its scope.

    Agents authenticate with the shared ``PRINTOSKY_AGENT_TOKEN`` (see
    ``deps.authenticate``); this endpoint exists so a box can verify at boot
    that its token, store id and clock all agree with the cloud, rather than
    discovering a mismatch on the first paid job.
    """
    principal = ctx.require("production:claim")
    if principal.role is not Role.AGENT:
        raise AuthenticationError("agent token required")
    return json_ok(
        {
            "identity": principal.to_dict(),
            "server_time": __import__("time").time(),
            "stores": list(principal.store_ids),
        },
        request_id=ctx.request_id,
    )
