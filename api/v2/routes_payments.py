"""Payments: webhook intake, counter collection, replay, ledger reads.

The order of operations is the fix for F03 and is worth stating plainly,
because it is the opposite of what the legacy route does:

    legacy:  200 OK  →  dedup marker  →  business update   (a crash loses money)
    v2:      verify  →  inbox commit  →  200 OK  →  ledger commit  →  mark done

Consequences of that order:

* If the **inbox write** fails, the caller gets **503** and Razorpay retries.
  We never acknowledge money we have not durably recorded.
* If the process dies **after** the inbox commit and **before** the ledger
  commit, the event is still sitting in the inbox marked ``received``, and
  ``POST /v2/payments/replay`` (or the next webhook retry) finishes it. That is
  the review's "crash after inbox receipt but before payment commit is
  recoverable" scenario, made routine.
* A re-delivery of the same ``payment_id`` produces a duplicate commit that
  writes nothing — the ledger, not a marker table, is the source of truth about
  whether money was already counted.

Signature verification is delegated to the existing
``razorpay_integration.verify_webhook`` so there is exactly one implementation
of it in the repository.
"""

from __future__ import annotations

import logging

from core.errors import AuthenticationError, DependencyError, NotFoundError, ValidationError
from core.money import paise_from_rupees
from core.payments import InboxEvent, plan_payment, plan_refund
from api.v2.http import Request, Response, json_ok

logger = logging.getLogger("api.v2.payments")

_COUNTER_METHODS = ("cash", "upi", "card", "credit")


def _apply(ctx, event: InboxEvent, *, refund: bool = False) -> dict:
    """Load, plan and commit one event. Returns a summary for the response."""
    order = ctx.uow.orders.get_order(event.order_ref)
    if order is None:
        # A payment for an order we do not have is not a 404 to be forgotten:
        # the money is real. It stays in the inbox, flagged, for a human.
        ctx.uow.inbox.mark_processed(event.event_id, result="unmatched",
                                     error=f"no order {event.order_ref}")
        raise NotFoundError(
            f"payment for unknown order {event.order_ref} — held for reconciliation",
            details={"order_ref": event.order_ref, "payment_id": event.payment_id},
        )

    planner = plan_refund if refund else plan_payment
    commit = planner(
        order, event,
        already_recorded_payment_ids=ctx.uow.payments.recorded_payment_ids(order.order_id),
    )
    if commit.duplicate:
        ctx.uow.inbox.mark_processed(event.event_id, result="duplicate")
        logger.info("payment %s already recorded for %s", event.payment_id, order.order_id)
        return {"duplicate": True, "order": order.to_dict()}

    saved = ctx.uow.payments.apply_payment_commit(commit, expected_version=order.version)
    ctx.uow.inbox.mark_processed(event.event_id, result="ok")
    if commit.warnings:
        # Fail loud: an overpayment or a payment against an unpriced order is a
        # thing a person must see, not a log line nobody reads.
        logger.error("payment %s on %s: %s", event.payment_id, order.order_id,
                     "; ".join(commit.warnings))
    return {
        "duplicate": False,
        "order": saved.to_dict(),
        "allocations": [a.to_dict() for a in commit.allocations],
        "warnings": list(commit.warnings),
    }


def razorpay_webhook(request: Request, ctx) -> Response:
    """POST /v2/payments/webhook/razorpay — verified, durable, idempotent."""
    body = request.body
    signature = request.header("x-razorpay-signature")
    try:
        from razorpay_integration import parse_payment_webhook, verify_webhook
    except ImportError as exc:  # pragma: no cover - deployment problem, not a request problem
        raise DependencyError("payment provider integration unavailable") from exc

    if not verify_webhook(body, signature):
        logger.warning("v2 razorpay webhook rejected: bad signature")
        raise AuthenticationError("invalid webhook signature")

    payload = request.json()
    parsed = parse_payment_webhook(payload)
    if not parsed:
        # A real event we do not act on (order.paid, payment.authorized, …).
        # Acknowledge it so the provider stops retrying; record nothing.
        return json_ok({"ignored": payload.get("event", "")}, request_id=ctx.request_id)

    event = InboxEvent(
        provider="razorpay",
        event_id=str(payload.get("id") or parsed["payment_id"]),
        payment_id=str(parsed["payment_id"]),
        order_ref=str(parsed["job_id"]),
        amount_paise=paise_from_rupees(parsed["amount"]),
        method=str(parsed.get("method") or ""),
        raw=payload,
    )
    event.validate()

    try:
        fresh = ctx.uow.inbox.accept(event)
    except Exception as exc:  # noqa: BLE001 - must become a retryable 503, never a 200
        logger.error("inbox write failed for %s: %s", event.payment_id, exc)
        raise DependencyError("could not durably record the event — retry") from exc

    if not fresh:
        logger.info("razorpay event %s already in the inbox", event.event_id)

    result = _apply(ctx, event)
    return json_ok(result, request_id=ctx.request_id)


def record_counter_payment(request: Request, ctx) -> Response:
    """POST /v2/orders/{order_id}/payments — cash or UPI taken at the counter.

    Goes through the same inbox → ledger → allocation path as a gateway
    payment. A counter collection that skipped the ledger is how a day's cash
    ends up reconciling against nothing.
    """
    ctx.require("payment:record")
    order_id = request.params["order_id"]
    payload = request.json()

    method = str(payload.get("method") or "").lower()
    if method not in _COUNTER_METHODS:
        raise ValidationError(
            f"method must be one of {', '.join(_COUNTER_METHODS)}",
            details={"field": "method"},
        )
    amount_paise = payload.get("amount_paise")
    if amount_paise is None and payload.get("amount_rupees") is not None:
        amount_paise = paise_from_rupees(payload["amount_rupees"])
    if not isinstance(amount_paise, int) or amount_paise <= 0:
        raise ValidationError("amount_paise must be a positive whole number of paise",
                              details={"field": "amount_paise"})

    reference = str(payload.get("reference") or "").strip()
    principal = ctx.principal
    # The identity plus the reference makes the counter payment id, so the same
    # collection submitted twice (a double tap, a retried request) is caught by
    # the ledger exactly like a duplicated webhook.
    payment_id = f"counter:{order_id}:{reference or payload.get('idempotency_key') or request.header('x-idempotency-key') or ''}"
    if payment_id.endswith(":"):
        raise ValidationError(
            "a counter payment needs a reference or X-Idempotency-Key so it cannot be double-counted",
            details={"field": "reference"},
        )

    event = InboxEvent(
        provider="counter",
        event_id=payment_id,
        payment_id=payment_id,
        order_ref=order_id,
        amount_paise=amount_paise,
        method=method,
        raw={"taken_by": principal.identity_id if principal else "", "reference": reference},
    )
    event.validate()
    try:
        ctx.uow.inbox.accept(event)
    except Exception as exc:  # noqa: BLE001
        raise DependencyError("could not record the collection — try again") from exc
    return json_ok(_apply(ctx, event), request_id=ctx.request_id)


def refund(request: Request, ctx) -> Response:
    """POST /v2/orders/{order_id}/refunds — owner-only, auditable, additive."""
    ctx.require("payment:refund")
    order_id = request.params["order_id"]
    payload = request.json()
    amount_paise = payload.get("amount_paise")
    if not isinstance(amount_paise, int) or amount_paise <= 0:
        raise ValidationError("amount_paise must be a positive whole number of paise")
    reference = str(payload.get("reference") or "").strip()
    if not reference:
        raise ValidationError("a refund needs a reference", details={"field": "reference"})

    event = InboxEvent(
        provider=str(payload.get("provider") or "counter"),
        event_id=f"refund:{order_id}:{reference}",
        payment_id=f"refund:{order_id}:{reference}",
        order_ref=order_id,
        amount_paise=amount_paise,
        method=str(payload.get("method") or "manual"),
        raw={"reason": str(payload.get("reason") or "")[:300]},
    )
    event.validate()
    ctx.uow.inbox.accept(event)
    return json_ok(_apply(ctx, event, refund=True), request_id=ctx.request_id)


def list_payments(request: Request, ctx) -> Response:
    ctx.require("payment:read")
    order_id = request.params["order_id"]
    order = ctx.uow.orders.get_order(order_id)
    if order is None:
        raise NotFoundError(f"no order {order_id}")
    return json_ok(
        {
            "order_id": order_id,
            "total_paise": order.total_paise,
            "paid_paise": order.paid_paise,
            "balance_paise": order.balance_paise(),
            "payments": ctx.uow.payments.list_payments(order_id),
        },
        request_id=ctx.request_id,
    )


def replay_inbox(request: Request, ctx) -> Response:
    """POST /v2/payments/replay — finish events accepted but never committed.

    Safe to run at any time and as often as you like: every event goes through
    the same idempotent planner, so an already-applied payment is a no-op. Run
    it from a cron; run it by hand after an incident.
    """
    ctx.require("payment:record")
    processed, failed = [], []
    for event in ctx.uow.inbox.pending(limit=request.q_int("limit", 25, low=1, high=200)):
        try:
            _apply(ctx, event)
            processed.append(event.payment_id)
        except Exception as exc:  # noqa: BLE001 - one bad event must not stop the sweep
            logger.error("replay failed for %s: %s", event.payment_id, exc)
            ctx.uow.inbox.mark_processed(event.event_id, result="error", error=str(exc)[:300])
            failed.append({"payment_id": event.payment_id, "error": str(exc)[:200]})
    return json_ok(
        {"processed": processed, "failed": failed, "count": len(processed)},
        request_id=ctx.request_id,
    )
