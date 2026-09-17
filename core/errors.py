"""Domain errors with stable codes, and their HTTP mapping.

Why codes and not messages: the console, the store agent and the tests all
branch on failure. A string like "could not create order" cannot be branched
on without matching prose, so it gets matched on prose, and then a reworded
message silently changes behaviour somewhere else. ``code`` is the contract;
``message`` is for humans.

Why one exception type per family and not one per call site: the HTTP layer
maps a ``DomainError`` to a response envelope in exactly one place
(``api/v2/http.py``). Anything that is not a ``DomainError`` escaping into that
layer is a bug, is logged at error level, and becomes a 500 — which is the
fail-loud rule (docs/FAIL_LOUD.md) applied to the request path: an unexpected
failure is never quietly turned into a plausible-looking success.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every expected, reportable failure in the core.

    ``status`` is the HTTP status the transport should use. It lives here
    rather than in the router because the meaning ("the caller asked for
    something impossible" vs "we are temporarily unable") belongs to the rule,
    not to the wire format.
    """

    code = "domain_error"
    status = 400

    def __init__(self, message: str, *, details: dict | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class ValidationError(DomainError):
    """The request is self-inconsistent or outside the supported range."""

    code = "validation_error"
    status = 400


class AuthenticationError(DomainError):
    """No credential, or a credential that did not verify.

    Deliberately says nothing about *which* part was wrong: F01 in the review
    was a fall-through that returned success for any non-empty password, and
    the replacement must not leak an oracle in the other direction either.
    """

    code = "authentication_required"
    status = 401


class AuthorizationError(DomainError):
    """Verified identity, insufficient role or wrong store scope."""

    code = "forbidden"
    status = 403


class NotFoundError(DomainError):
    code = "not_found"
    status = 404


class ConflictError(DomainError):
    """The write lost a race, or would break an invariant that already holds.

    Used for optimistic-concurrency failures (``version`` mismatch) and for a
    state transition the state machine forbids.
    """

    code = "conflict"
    status = 409


class PricingUnavailableError(DomainError):
    """A price could not be computed.

    F04: the old path caught the rate-card exception and used ``total = 0.0``,
    so a broken rate card looked exactly like a free order. A missing price is
    a 503, never a number.
    """

    code = "pricing_unavailable"
    status = 503


class DependencyError(DomainError):
    """A dependency we need (database, storage, gateway) is unavailable.

    Retryable by the caller. Distinct from a 500 so that a queue or a webhook
    sender knows to try again rather than dropping the event.
    """

    code = "dependency_unavailable"
    status = 503


class ConfigurationError(DomainError):
    """A required secret or setting is missing.

    503, not 500, and never a silent default: a store that boots without its
    signing key must refuse to mint sessions rather than mint unverifiable
    ones.
    """

    code = "not_configured"
    status = 503
