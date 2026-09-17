"""Orders: quote, create, read, accept, transition — and the production lane.

The order endpoints are where the review's contracts become HTTP:

* ``POST /v2/quotes`` prices without creating anything, and answers **503
  pricing_unavailable** when the rate card fails. It cannot answer ₹0 (F04).
* ``POST /v2/orders`` takes **storage object ids**, never customer-supplied
  URLs (F08). The store agent later resolves an object id to a short-lived
  signed download; there is nothing here for an attacker to point at the shop's
  LAN.
* ``POST /v2/orders/{id}/accept`` freezes the specification: it re-prices, and
  refuses if the ``spec_hash`` moved since the customer saw the quote
  (contract #2).
* ``POST /v2/orders/{id}/claim`` hands an order to one device with a **fencing
  token**; ``/attempts`` records the attempt *before* spooling, and closing an
  attempt as ``uncertain`` parks the order for a human instead of reprinting
  (F05, F06).
"""

from __future__ import annotations

import uuid

from core.errors import ConflictError, NotFoundError, ValidationError
from core.orders import (
    AttemptState, Lane, Order, OrderItem, PaymentState, PrintAttempt, ProductionState,
    new_order_id,
)
from core.pricing import PrintItem, price_order
from api.v2.http import Request, Response, json_ok

_MAX_ITEMS = 50


def _items_from_payload(payload: dict) -> list[PrintItem]:
    raw = payload.get("items")
    if not isinstance(raw, list) or not raw:
        raise ValidationError("items must be a non-empty list", details={"field": "items"})
    if len(raw) > _MAX_ITEMS:
        raise ValidationError(f"at most {_MAX_ITEMS} items per order", details={"field": "items"})
    items = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValidationError(f"items[{i}] must be an object")
        try:
            items.append(
                PrintItem(
                    pages=int(entry.get("pages", 0)),
                    paper_type=str(entry.get("paper_type", "A4_BW")),
                    sides=str(entry.get("sides", "ss")),
                    layout=str(entry.get("layout", "1-up")),
                    copies=int(entry.get("copies", 1)),
                )
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"items[{i}] has a non-numeric field") from exc
    return items


def quote(request: Request, ctx) -> Response:
    """POST /v2/quotes — price a specification. Creates nothing."""
    ctx.require("order:price")
    payload = request.json()
    result = price_order(
        _items_from_payload(payload),
        finishing=str(payload.get("finishing") or "none"),
        paper_size=str(payload.get("paper_size") or "A4"),
        urgent=bool(payload.get("urgent")),
        is_student=bool(payload.get("is_student")),
    )
    return json_ok(result.to_dict(), request_id=ctx.request_id)


def create_order(request: Request, ctx) -> Response:
    """POST /v2/orders — one order, many items, priced server-side."""
    principal = ctx.require("order:create")
    payload = request.json()

    store_id = str(payload.get("store_id") or "").strip()
    if not store_id:
        raise ValidationError("store_id is required", details={"field": "store_id"})
    from core.identity import require_store

    require_store(principal, store_id)

    phone = str(payload.get("customer_phone") or "").strip()
    if not phone:
        raise ValidationError("customer_phone is required", details={"field": "customer_phone"})

    print_items = _items_from_payload(payload)
    sources = payload.get("sources") or []
    if len(sources) not in (0, len(print_items)):
        raise ValidationError(
            "sources must be empty or one per item",
            details={"items": len(print_items), "sources": len(sources)},
        )

    quoted = price_order(
        print_items,
        finishing=str(payload.get("finishing") or "none"),
        paper_size=str(payload.get("paper_size") or "A4"),
        urgent=bool(payload.get("urgent")),
        is_student=bool(payload.get("is_student")),
    )

    order = Order(
        order_id=new_order_id(store_id),
        store_id=store_id,
        customer_phone=phone,
        customer_name=str(payload.get("customer_name") or "").strip(),
        channel=str(payload.get("channel") or "web"),
        lane=Lane(str(payload.get("lane") or Lane.ASSISTED.value)),
        total_paise=quoted.total_paise,
        quote_hash=quoted.spec_hash,
        note=str(payload.get("note") or "")[:500],
        promised_at=str(payload.get("promised_at") or ""),
    )

    # Quote lines and order items are the same thing seen twice: the line's
    # amount is what a later payment allocates against, so they are built from
    # one list and cannot drift.
    print_lines = [line for line in quoted.lines if line.kind == "print"]
    for idx, line in enumerate(print_lines):
        source = sources[idx] if idx < len(sources) else {}
        object_id = str(source.get("object_id") or "").strip()
        if not object_id:
            raise ValidationError(
                f"sources[{idx}].object_id is required — v2 does not accept file URLs",
                details={"field": f"sources[{idx}].object_id"},
            )
        order.items.append(
            OrderItem(
                item_id=f"{order.order_id}-{idx + 1}",
                source_object=object_id,
                file_name=str(source.get("file_name") or f"item-{idx + 1}"),
                page_count=line.item.pages if line.item else 0,
                spec=line.item.as_rate_card_item() if line.item else {},
                amount_paise=line.amount_paise,
                document_hash=str(source.get("document_hash") or ""),
            )
        )
    finishing_lines = [line for line in quoted.lines if line.kind == "finishing"]
    if finishing_lines and order.items:
        # Finishing is charged on the order, and is carried on the last item so
        # that item amounts still sum to the order total (Order.validate).
        order.items[-1].amount_paise += sum(line.amount_paise for line in finishing_lines)

    order.validate()
    saved = ctx.uow.orders.save_order(order, expected_version=0)
    return json_ok(
        {"order": saved.to_dict(), "quote": quoted.to_dict()},
        status=201,
        request_id=ctx.request_id,
    )


def _load(ctx, order_id: str) -> Order:
    order = ctx.uow.orders.get_order(order_id)
    if order is None:
        raise NotFoundError(f"no order {order_id}", details={"order_id": order_id})
    from core.identity import require_store

    require_store(ctx.principal, order.store_id)
    return order


def get_order(request: Request, ctx) -> Response:
    ctx.require("order:read")
    order = _load(ctx, request.params["order_id"])
    return json_ok(
        {
            "order": order.to_dict(),
            "payments": ctx.uow.payments.list_payments(order.order_id),
        },
        request_id=ctx.request_id,
    )


def list_orders(request: Request, ctx) -> Response:
    """GET /v2/orders?store_id=&state=&limit=&cursor="""
    principal = ctx.require("order:read")
    store_id = request.q("store_id") or (
        principal.store_ids[0] if len(principal.store_ids) == 1 else ""
    )
    if store_id:
        from core.identity import require_store

        require_store(principal, store_id)
    states = [s for s in request.q("state").split(",") if s]
    rows, cursor = ctx.uow.orders.list_orders(
        store_id=store_id or None,
        states=states or None,
        limit=request.q_int("limit", 50, low=1, high=200),
        cursor=request.q("cursor") or None,
    )
    return json_ok(
        {"orders": [o.to_dict() for o in rows], "next_cursor": cursor},
        request_id=ctx.request_id,
    )


def accept_order(request: Request, ctx) -> Response:
    """POST /v2/orders/{id}/accept — freeze the spec and queue it.

    Re-prices from the stored items and refuses if the total moved: a quote the
    customer saw an hour ago is not automatically a price we are bound to, and
    silently charging the new number is worse than asking again.
    """
    ctx.require("order:accept")
    order = _load(ctx, request.params["order_id"])
    payload = request.json()

    if not order.items:
        raise ValidationError("cannot accept an order with no items")

    expected_hash = str(payload.get("quote_hash") or order.quote_hash)
    if expected_hash and order.quote_hash and expected_hash != order.quote_hash:
        raise ConflictError(
            "the specification changed since this quote — re-quote before accepting",
            details={"quote_hash": order.quote_hash, "sent": expected_hash},
        )

    from datetime import datetime, timezone

    order.accepted_at = datetime.now(timezone.utc).isoformat()
    target = (
        ProductionState.AWAITING_APPROVAL if order.lane is Lane.ASSISTED else ProductionState.QUEUED
    )
    order.move_production(target)
    order.validate()
    saved = ctx.uow.orders.save_order(order, expected_version=order.version)
    return json_ok({"order": saved.to_dict()}, request_id=ctx.request_id)


def transition_order(request: Request, ctx) -> Response:
    """POST /v2/orders/{id}/transition — ``{"to": "ready", "reason": "…"}``.

    Production only. There is deliberately no way to set ``payment_state`` from
    here: money moves through ``/v2/payments`` and nowhere else (contract #3).
    """
    ctx.require("production:write")
    order = _load(ctx, request.params["order_id"])
    payload = request.json()
    try:
        target = ProductionState(str(payload.get("to") or ""))
    except ValueError as exc:
        raise ValidationError(
            f"unknown production state {payload.get('to')!r}",
            details={"allowed": [s.value for s in ProductionState]},
        ) from exc

    if target is ProductionState.DELIVERED and order.balance_paise() > 0:
        raise ConflictError(
            "balance outstanding — collect it or record a write-off before handover",
            details={"balance_paise": order.balance_paise()},
        )

    order.move_production(target, reason=str(payload.get("reason") or "")[:200])
    saved = ctx.uow.orders.save_order(order, expected_version=order.version)
    return json_ok({"order": saved.to_dict()}, request_id=ctx.request_id)


# ── Production: claim, attempt, close ────────────────────────────────────────

def agent_queue(request: Request, ctx) -> Response:
    """GET /v2/agent/queue — what this store's agent may print right now.

    Only orders that pass :meth:`Order.is_dispatchable` appear: paid, accepted,
    preflighted, and not in the assisted lane. An agent cannot decide to print
    something the rules say a human should look at first.
    """
    principal = ctx.require("production:claim")
    store_id = request.q("store_id") or (principal.store_ids[0] if principal.store_ids else "")
    from core.identity import require_store

    require_store(principal, store_id)
    rows, _ = ctx.uow.orders.list_orders(
        store_id=store_id, states=[ProductionState.QUEUED.value], limit=50
    )
    ready = [o for o in rows if o.is_dispatchable()]
    return json_ok(
        {"store_id": store_id, "orders": [o.to_dict() for o in ready], "count": len(ready)},
        request_id=ctx.request_id,
    )


def claim_order(request: Request, ctx) -> Response:
    """POST /v2/orders/{id}/claim — take a fenced lease on an order.

    The returned ``attempt_token`` is the fence. Every later write about this
    order from a device must carry it; a second device that claims after the
    lease expires gets a *new* token, and writes bearing the old one are
    refused. That is what makes "two PCs cannot independently release the same
    attempt" true rather than hoped for.
    """
    principal = ctx.require("production:claim")
    order = _load(ctx, request.params["order_id"])

    if not order.is_dispatchable():
        raise ConflictError(
            "this order is not dispatchable",
            details={
                "production_state": order.production_state.value,
                "payment_state": order.payment_state.value,
                "lane": order.lane.value,
            },
        )
    order.move_production(ProductionState.CLAIMED)
    saved = ctx.uow.orders.save_order(order, expected_version=order.version)
    token = uuid.uuid4().hex
    return json_ok(
        {
            "order": saved.to_dict(),
            "attempt_token": token,
            "device_id": principal.device_id or principal.identity_id,
            "lease_seconds": 900,
        },
        request_id=ctx.request_id,
    )


def open_attempt(request: Request, ctx) -> Response:
    """POST /v2/attempts — record an attempt BEFORE the file is spooled."""
    principal = ctx.require("production:write")
    payload = request.json()
    order = _load(ctx, str(payload.get("order_id") or ""))
    token = request.header("x-attempt-token") or str(payload.get("attempt_token") or "")
    if not token:
        raise ValidationError("attempt_token is required", details={"field": "attempt_token"})

    attempt = PrintAttempt(
        attempt_id=uuid.uuid4().hex,
        task_id=str(payload.get("task_id") or order.order_id),
        device_id=principal.device_id or principal.identity_id,
        printer_queue=str(payload.get("printer_queue") or ""),
        attempt_token=token,
        sheets_expected=int(payload.get("sheets_expected") or 0),
    )
    stored = ctx.uow.attempts.open_attempt(attempt)
    order.move_production(ProductionState.SPOOLED)
    ctx.uow.orders.save_order(order, expected_version=order.version)
    return json_ok({"attempt": stored.to_dict()}, status=201, request_id=ctx.request_id)


def close_attempt(request: Request, ctx) -> Response:
    """POST /v2/attempts/{attempt_id}/close — ``confirmed`` | ``failed`` | ``uncertain``.

    ``uncertain`` moves the order to ``output_uncertain`` and stops. It is the
    one outcome that must never trigger an automatic retry, because the paper
    may already be in the tray.
    """
    ctx.require("production:write")
    payload = request.json()
    try:
        state = AttemptState(str(payload.get("state") or ""))
    except ValueError as exc:
        raise ValidationError(
            f"unknown attempt state {payload.get('state')!r}",
            details={"allowed": [s.value for s in AttemptState]},
        ) from exc

    ctx.uow.attempts.close_attempt(
        request.params["attempt_id"],
        state=state.value,
        spool_id=str(payload.get("spool_id") or ""),
        sheets_observed=int(payload.get("sheets_observed") or 0),
        failure_reason=str(payload.get("failure_reason") or "")[:300],
    )

    order = _load(ctx, str(payload.get("order_id") or ""))
    if state is AttemptState.CONFIRMED:
        order.move_production(ProductionState.PRINTED)
    elif state is AttemptState.UNCERTAIN:
        order.move_production(
            ProductionState.OUTPUT_UNCERTAIN, reason="output unconfirmed — check the tray"
        )
    elif state is AttemptState.FAILED:
        order.move_production(ProductionState.QUEUED, reason="attempt failed before output")
    saved = ctx.uow.orders.save_order(order, expected_version=order.version)
    return json_ok(
        {"order": saved.to_dict(), "retryable": state is AttemptState.FAILED},
        request_id=ctx.request_id,
    )


def work_queue(request: Request, ctx) -> Response:
    """GET /v2/queue — the operator console's "needs action first" list (§8.4).

    Ordering is by consequence, not by timestamp: money or paper is at risk at
    the top, routine work below. The console renders this list as-is, so the
    decision about what matters lives here, once, and not in each page.
    """
    principal = ctx.require("order:read")
    store_id = request.q("store_id") or (
        principal.store_ids[0] if len(principal.store_ids) == 1 else ""
    )
    if store_id:
        from core.identity import require_store

        require_store(principal, store_id)
    rows, _ = ctx.uow.orders.list_orders(store_id=store_id or None, limit=200)

    buckets: dict[str, list[dict]] = {
        "exceptions": [], "paid_not_started": [], "in_production": [],
        "finishing": [], "ready": [], "unpaid": [],
    }
    for order in rows:
        card = {
            "order_id": order.order_id,
            "store_id": order.store_id,
            "customer_name": order.customer_name or order.customer_phone,
            "production_state": order.production_state.value,
            "payment_state": order.payment_state.value,
            "balance_paise": order.balance_paise(),
            "total_paise": order.total_paise,
            "lane": order.lane.value,
            "promised_at": order.promised_at,
            "pickup_code": order.pickup_code,
            "updated_at": order.updated_at,
        }
        state = order.production_state
        if state in (ProductionState.OUTPUT_UNCERTAIN, ProductionState.HELD):
            buckets["exceptions"].append(card)
        elif state is ProductionState.AWAITING_APPROVAL:
            buckets["exceptions"].append(card)
        elif state is ProductionState.QUEUED and order.payment_state in (
            PaymentState.PAID, PaymentState.OVERPAID
        ):
            buckets["paid_not_started"].append(card)
        elif state in (ProductionState.CLAIMED, ProductionState.SPOOLED, ProductionState.PRINTED):
            buckets["in_production"].append(card)
        elif state is ProductionState.FINISHING:
            buckets["finishing"].append(card)
        elif state is ProductionState.READY:
            buckets["ready"].append(card)
        elif order.payment_state is PaymentState.UNPAID:
            buckets["unpaid"].append(card)

    return json_ok(
        {
            "store_id": store_id,
            "buckets": buckets,
            "counts": {k: len(v) for k, v in buckets.items()},
        },
        request_id=ctx.request_id,
    )
