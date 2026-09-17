"""Request/response plumbing for v2: one envelope, one error mapping, one CORS.

Two things that were spread across ~90 branches of the legacy chain live in
exactly one place here:

* **The envelope.** Every v2 response is
  ``{"ok": bool, "data": …}`` or ``{"ok": false, "error": {"code", "message",
  "details"}}``, plus a ``request_id``. A client can therefore handle failure
  once instead of guessing whether this endpoint returns ``{"error": "..."}``
  or ``{"ok": false}`` or a bare 500 page.
* **The error mapping.** A :class:`core.errors.DomainError` becomes its own
  status and code. Anything else becomes a 500 *and is logged at error level
  with its traceback* — the fail-loud rule on the request path. An unexpected
  exception must never be reshaped into a plausible 200.

CORS stays permissive (``*``) to match the legacy endpoints, because the
consoles are served from Netlify and the API from Vercel and every route is
individually authenticated. The allowed-header list is a superset of the legacy
one so a page can use both APIs during the migration.
"""

from __future__ import annotations

import json
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from core.errors import DomainError, ValidationError

logger = logging.getLogger("api.v2")

__all__ = ["Request", "Response", "error_response", "json_error", "json_ok", "write_response"]

MAX_BODY_BYTES = 6 * 1024 * 1024        # Vercel's practical request ceiling
_ALLOWED_HEADERS = (
    "Content-Type, Authorization, X-Request-Id, X-Hub-Signature-256, "
    "X-Razorpay-Signature, X-Staff-Pin, X-Student-Phone, X-Admin-Password, "
    "X-Whatsapp-Phone, X-Device-Id, X-Attempt-Token, X-Idempotency-Key"
)


@dataclass
class Request:
    """A parsed HTTP request, independent of ``BaseHTTPRequestHandler``.

    Decoupled on purpose: the router and every handler can then be unit-tested
    by constructing a Request, with no socket, no Vercel and no mock handler.
    """

    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    params: dict[str, str] = field(default_factory=dict)
    client_ip: str = ""
    request_id: str = ""

    @classmethod
    def from_handler(cls, h, *, body: bytes | None = None) -> "Request":
        parsed = urlparse(h.path)
        headers = {k.lower(): v for k, v in h.headers.items()}
        if body is None:
            length = int(headers.get("content-length") or 0)
            body = h.rfile.read(min(length, MAX_BODY_BYTES)) if length else b""
        forwarded = headers.get("x-forwarded-for", "")
        return cls(
            method=h.command.upper(),
            path=parsed.path,
            query=parse_qs(parsed.query),
            headers=headers,
            body=body or b"",
            client_ip=(forwarded.split(",")[0].strip() if forwarded else ""),
            request_id=headers.get("x-request-id") or uuid.uuid4().hex[:16],
        )

    # ── accessors ────────────────────────────────────────────────────────────

    def json(self) -> dict:
        """Parse the body as a JSON object, or raise a 400.

        Returns ``{}`` for an empty body so a handler can treat "no fields" and
        "no body" the same. A JSON array or scalar is rejected: every v2
        endpoint takes an object, and accepting a list here would make field
        access fail later with an obscure TypeError.
        """
        if not self.body:
            return {}
        if len(self.body) > MAX_BODY_BYTES:
            raise ValidationError("request body too large", details={"max_bytes": MAX_BODY_BYTES})
        try:
            parsed = json.loads(self.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValidationError("body is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValidationError("body must be a JSON object")
        return parsed

    def q(self, name: str, default: str = "") -> str:
        values = self.query.get(name) or []
        return values[0] if values else default

    def q_int(self, name: str, default: int, *, low: int = 0, high: int = 1000) -> int:
        raw = self.q(name)
        if not raw:
            return default
        try:
            return max(low, min(high, int(raw)))
        except ValueError as exc:
            raise ValidationError(f"{name} must be a whole number") from exc

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def bearer_token(self) -> str:
        raw = self.header("authorization")
        return raw[7:].strip() if raw[:7].lower() == "bearer " else ""


@dataclass
class Response:
    status: int = 200
    body: dict | None = None
    headers: dict[str, str] = field(default_factory=dict)
    raw: bytes | None = None          # for the rare non-JSON reply

    def encoded(self) -> bytes:
        if self.raw is not None:
            return self.raw
        return json.dumps(self.body if self.body is not None else {}).encode()


def json_ok(data, *, status: int = 200, request_id: str = "", **extra) -> Response:
    # `ok` is about the request, not the payload: /v2/health answers 503 with a
    # perfectly well-formed body, and a client that only reads `ok` must not
    # read that as healthy.
    payload = {"ok": status < 400, "data": data}
    if request_id:
        payload["request_id"] = request_id
    payload.update(extra)
    return Response(status=status, body=payload)


def json_error(code: str, message: str, *, status: int = 400, details: dict | None = None,
               request_id: str = "") -> Response:
    payload = {"ok": False, "error": {"code": code, "message": message, "details": details or {}}}
    if request_id:
        payload["request_id"] = request_id
    return Response(status=status, body=payload)


def error_response(exc: BaseException, *, request_id: str = "") -> Response:
    """Map any exception to a response. The only place that decides this.

    A ``DomainError`` is an expected outcome and carries its own status. Every
    other exception is a defect: it is logged with a traceback (fail loud) and
    returned as an opaque 500, because the internals of a crash are not a
    customer-facing message.
    """
    if isinstance(exc, DomainError):
        if exc.status >= 500:
            logger.error("v2 %s: %s %s", exc.code, exc.message, exc.details)
        else:
            logger.info("v2 %s: %s", exc.code, exc.message)
        return json_error(exc.code, exc.message, status=exc.status,
                          details=exc.details, request_id=request_id)

    logger.error(
        "v2 unhandled %s: %s\n%s", type(exc).__name__, exc, traceback.format_exc()
    )
    return json_error(
        "internal_error",
        "something went wrong on our side",
        status=500,
        details={"request_id": request_id},
        request_id=request_id,
    )


def cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": _ALLOWED_HEADERS,
        "Access-Control-Expose-Headers": "X-Request-Id",
        "Access-Control-Max-Age": "86400",
    }


def write_response(h, response: Response, *, request_id: str = "") -> None:
    """Write a :class:`Response` onto a ``BaseHTTPRequestHandler``."""
    payload = response.encoded()
    h.send_response(response.status)
    h.send_header("Content-Type", response.headers.pop("Content-Type", "application/json"))
    h.send_header("Content-Length", str(len(payload)))
    if request_id:
        h.send_header("X-Request-Id", request_id)
    for key, value in {**cors_headers(), **response.headers}.items():
        h.send_header(key, value)
    h.end_headers()
    if h.command.upper() != "HEAD":
        h.wfile.write(payload)
