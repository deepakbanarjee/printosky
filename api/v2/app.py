"""Wiring and the single entry point ``api/index.py`` calls.

``dispatch(handler)`` returns **True** if it answered the request and **False**
if the path is not a v2 path. ``api/index.py`` calls it as the first statement
of ``do_GET``/``do_POST``/``do_OPTIONS``:

    if v2_dispatch(self, body):
        return
    # …the existing chain, unchanged…

That one line is the entire integration. Every legacy route keeps its exact
behaviour, because ``owns()`` only claims ``/v2/…``. If this module fails to
import at all — a syntax error, a missing dependency — ``api/index.py`` catches
it and binds a stub that always returns False, so a broken v2 can never take
the live API down with it.

Middleware order, outermost first:

    1. CORS + request id           (every response, including errors)
    2. route match                 (404 / 405 with Allow)
    3. authentication              (bearer session or agent token)
    4. authorisation               (the route's declared permission)
    5. handler
    6. error mapping               (DomainError → its status; anything else → 500 + log)
"""

from __future__ import annotations

import logging
import os

from core.errors import DomainError
from api.v2 import routes_auth, routes_ops, routes_orders, routes_payments
from api.v2.deps import Context, authenticate, load_settings
from api.v2.http import Request, Response, cors_headers, error_response, json_error, write_response
from api.v2.router import MethodNotAllowed, Router, bind

logger = logging.getLogger("api.v2")

_router: Router | None = None
_uow = None


def build_router() -> Router:
    """The route table. One place, declarative, with permissions attached."""
    global _router
    if _router is not None:
        return _router

    r = Router("/v2")

    # ── ops ──────────────────────────────────────────────────────────────────
    r.get("", routes_ops.index, auth="none", name="index")
    r.get("/", routes_ops.index, auth="none", name="index_slash")
    r.get("/health", routes_ops.health, auth="none", name="health")

    # ── auth ─────────────────────────────────────────────────────────────────
    r.post("/auth/login", routes_auth.login, auth="none", name="login")
    r.post("/auth/logout", routes_auth.logout, name="logout")
    r.get("/auth/me", routes_auth.me, name="me")
    r.post("/auth/agent", routes_auth.agent_hello, name="agent_hello")

    # ── orders ───────────────────────────────────────────────────────────────
    r.post("/quotes", routes_orders.quote, permission="order:price", name="quote")
    r.post("/orders", routes_orders.create_order, permission="order:create", name="create_order")
    r.get("/orders", routes_orders.list_orders, permission="order:read", name="list_orders")
    r.get("/orders/{order_id}", routes_orders.get_order, permission="order:read", name="get_order")
    r.post("/orders/{order_id}/accept", routes_orders.accept_order,
           permission="order:accept", name="accept_order")
    r.post("/orders/{order_id}/transition", routes_orders.transition_order,
           permission="production:write", name="transition_order")
    r.get("/queue", routes_orders.work_queue, permission="order:read", name="work_queue")

    # ── production ───────────────────────────────────────────────────────────
    r.get("/agent/queue", routes_orders.agent_queue,
          permission="production:claim", name="agent_queue")
    r.post("/orders/{order_id}/claim", routes_orders.claim_order,
           permission="production:claim", name="claim_order")
    r.post("/attempts", routes_orders.open_attempt,
           permission="production:write", name="open_attempt")
    r.post("/attempts/{attempt_id}/close", routes_orders.close_attempt,
           permission="production:write", name="close_attempt")

    # ── payments ─────────────────────────────────────────────────────────────
    # The webhook authenticates by HMAC over the body, not by a bearer token,
    # so its route declares auth="none" and verifies inside the handler.
    r.post("/payments/webhook/razorpay", routes_payments.razorpay_webhook,
           auth="none", name="razorpay_webhook")
    r.post("/payments/replay", routes_payments.replay_inbox,
           permission="payment:record", name="replay_inbox")
    r.post("/orders/{order_id}/payments", routes_payments.record_counter_payment,
           permission="payment:record", name="counter_payment")
    r.get("/orders/{order_id}/payments", routes_payments.list_payments,
          permission="payment:read", name="list_payments")
    r.post("/orders/{order_id}/refunds", routes_payments.refund,
           permission="payment:refund", name="refund")

    _router = r
    return r


def build_uow(settings=None):
    """Pick the adapter set for this process, once.

    Supabase when the deployment is configured for it, in-memory otherwise. The
    in-memory fallback is what makes ``/v2/health`` answer usefully on a box
    with no credentials instead of 500-ing, and it is what the tests use.
    """
    global _uow
    if _uow is not None:
        return _uow
    settings = settings or load_settings()
    if settings.supabase_url and settings.supabase_service_key:
        try:
            from api.v2.repos_supabase import SupabaseUnitOfWork

            _uow = SupabaseUnitOfWork()
            return _uow
        except Exception as exc:  # noqa: BLE001 - reported, not hidden
            logger.error(
                "Supabase adapters unavailable (%s: %s) — v2 is running on in-memory "
                "storage and WILL NOT persist. Fix the configuration.",
                type(exc).__name__, exc,
            )
    from api.v2.repos import InMemoryUnitOfWork

    _uow = InMemoryUnitOfWork()
    return _uow


def reset_state() -> None:
    """Drop cached router/uow. Tests only; never called in production."""
    global _router, _uow
    _router, _uow = None, None
    load_settings(refresh=True)


def handle(request: Request, *, uow=None, settings=None) -> Response:
    """Run one request through the middleware chain. Pure-ish: no socket here.

    Exposed separately from :func:`dispatch` so tests can drive the whole API
    with a constructed :class:`Request` and assert on a :class:`Response`.
    """
    settings = settings or load_settings()
    uow = uow if uow is not None else build_uow(settings)
    ctx = Context(settings=settings, uow=uow, request_id=request.request_id)
    router = build_router()

    try:
        route, params = router.match(request.method, request.path)
    except MethodNotAllowed as exc:
        allow = ", ".join(sorted(exc.allowed))
        response = json_error("method_not_allowed", f"try one of: {allow}",
                              status=405, request_id=request.request_id)
        response.headers["Allow"] = allow
        return response
    except DomainError as exc:
        return error_response(exc, request_id=request.request_id)

    try:
        if route.auth != "none":
            ctx.principal = authenticate(request, settings, uow)
        elif request.bearer_token():
            # An open route still identifies a caller when it can — the webhook
            # does not, but /v2/health from a logged-in console should log who.
            try:
                ctx.principal = authenticate(request, settings, uow)
            except DomainError:
                ctx.principal = None
        if route.permission:
            ctx.require(route.permission)
        return bind(route, request, params, ctx)
    except Exception as exc:  # noqa: BLE001 - mapped and logged in one place
        return error_response(exc, request_id=request.request_id)


def dispatch(h, body: bytes | None = None) -> bool:
    """Answer the request if it is a v2 request. Returns True when it did.

    Never raises: ``api/index.py`` must be able to call this unguarded, and a
    defect in v2 must not stop a WhatsApp webhook from being processed.
    """
    try:
        request = Request.from_handler(h, body=body)
    except Exception as exc:  # noqa: BLE001 - a malformed request is not the caller's problem
        logger.error("v2 could not parse the request: %s", exc)
        return False

    router = build_router()
    if not router.owns(request.path):
        return False

    if request.method == "OPTIONS":
        h.send_response(204)
        for key, value in cors_headers().items():
            h.send_header(key, value)
        h.end_headers()
        return True

    response = handle(request)
    try:
        write_response(h, response, request_id=request.request_id)
    except Exception as exc:  # noqa: BLE001 - the socket died; nothing left to say
        logger.error("v2 could not write the response: %s", exc)
    logger.info(
        "v2 %s %s -> %s (%s)",
        request.method, request.path, response.status, request.request_id,
    )
    return True


# Set PRINTOSKY_V2_DISABLED=1 to make every v2 path fall through to the legacy
# chain (which will 404 it). A kill switch that needs no deploy, per the
# review's rollback guidance in WP01.
def enabled() -> bool:
    return os.environ.get("PRINTOSKY_V2_DISABLED", "") not in ("1", "true", "yes")
