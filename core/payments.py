"""Durable payment handling: inbox → ledger → allocations → outbox.

F03 in the 2026-09-10 review: the Razorpay route wrote ``200 OK`` before the
business update ran, and the dedup marker was written *before* the money was
recorded — so a crash in between made a real payment look already-handled, and
a failure of the marker store let duplicates through. F02 is the other half:
one payment was copied onto every job in a batch instead of being allocated.

The contract this module encodes (§7 contracts 4, 5 and 3):

1. **Inbox first.** The transport verifies the signature, writes the raw event
   to ``payment_inbox``, and only then acknowledges the provider. If the inbox
   write fails, the response is a retryable 503 — the provider re-sends, which
   is exactly what we want.
2. **One payment identity.** ``(provider, provider_payment_id)`` is unique.
   Two deliveries of the same payment produce one ledger row; a *different*
   payment for the same order produces a second row, and both are kept.
3. **Allocation, not duplication.** :func:`core.money.allocate` splits the
   payment across the order's accepted line values, exactly.
4. **Payment never moves production.** :meth:`core.orders.Order.move_payment`
   is the only thing a payment commit touches. A late capture on a delivered
   order updates the ledger and leaves the shelf alone.
5. **Outbox with the business write.** The customer's "payment received" and the
   staff alert are rows committed in the same transaction as the money, then
   delivered by a worker with retries. A WhatsApp outage can no longer decide
   whether a payment was recorded.

Everything here is a pure function returning a :class:`PaymentCommit` — a
description of the single transaction the adapter must apply. Nothing writes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

from core.errors import ValidationError
from core.money import allocate
from core.orders import Order, PaymentState

__all__ = [
    "Allocation",
    "InboxEvent",
    "OutboxEvent",
    "Payment",
    "PaymentCommit",
    "plan_payment",
    "plan_refund",
]

SUPPORTED_CURRENCIES = ("INR",)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class InboxEvent:
    """A verified provider event, as stored before anything is decided.

    ``raw`` is kept verbatim. Reconciliation against the provider's own
    statement is impossible if we only keep our interpretation of the payload.
    """

    provider: str                    # razorpay | cash | upi_counter | cashfree | …
    event_id: str                    # provider's event id — dedup key for delivery
    payment_id: str                  # provider's payment id — dedup key for money
    order_ref: str                   # our order id as the provider knows it
    amount_paise: int
    currency: str = "INR"
    method: str = ""
    captured_at: str = field(default_factory=_now)
    raw: dict = field(default_factory=dict)

    def validate(self) -> None:
        if not self.provider or not self.payment_id:
            raise ValidationError("provider and payment_id are required")
        if not self.order_ref:
            raise ValidationError("order_ref is required")
        if not isinstance(self.amount_paise, int) or isinstance(self.amount_paise, bool):
            raise ValidationError("amount_paise must be an int of paise")
        if self.amount_paise <= 0:
            raise ValidationError("a payment must be positive; model a refund with plan_refund")
        if self.currency not in SUPPORTED_CURRENCIES:
            raise ValidationError(
                f"unsupported currency {self.currency!r}",
                details={"supported": list(SUPPORTED_CURRENCIES)},
            )


@dataclass(frozen=True)
class Payment:
    """A ledger row. One real movement of money, recorded once."""

    payment_id: str
    provider: str
    provider_payment_id: str
    order_id: str
    amount_paise: int
    method: str
    currency: str = "INR"
    kind: str = "capture"            # capture | refund
    captured_at: str = field(default_factory=_now)
    event_id: str = ""

    def to_dict(self) -> dict:
        return {
            "payment_id": self.payment_id,
            "provider": self.provider,
            "provider_payment_id": self.provider_payment_id,
            "order_id": self.order_id,
            "amount_paise": self.amount_paise,
            "method": self.method,
            "currency": self.currency,
            "kind": self.kind,
            "captured_at": self.captured_at,
            "event_id": self.event_id,
        }


@dataclass(frozen=True)
class Allocation:
    """How much of one payment belongs to one order line."""

    payment_id: str
    order_id: str
    item_id: str
    amount_paise: int

    def to_dict(self) -> dict:
        return {
            "payment_id": self.payment_id,
            "order_id": self.order_id,
            "item_id": self.item_id,
            "amount_paise": self.amount_paise,
        }


@dataclass(frozen=True)
class OutboxEvent:
    """A side effect to perform *after* the money is committed, with retries."""

    topic: str                       # payment.received | staff.alert | order.ready | …
    payload: dict
    dedup_key: str = ""

    def to_dict(self) -> dict:
        return {"topic": self.topic, "payload": self.payload, "dedup_key": self.dedup_key}


@dataclass
class PaymentCommit:
    """Everything one payment changes — to be applied in a single transaction.

    ``duplicate`` means the payment was already in the ledger: the adapter
    writes nothing and the transport still answers 200, because the provider is
    entitled to re-deliver and must not be told to keep trying.
    """

    order_id: str
    payment: Payment | None
    allocations: tuple[Allocation, ...] = ()
    new_paid_paise: int = 0
    new_payment_state: PaymentState = PaymentState.UNPAID
    outbox: tuple[OutboxEvent, ...] = ()
    duplicate: bool = False
    warnings: tuple[str, ...] = ()

    def is_noop(self) -> bool:
        return self.payment is None

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "duplicate": self.duplicate,
            "payment": self.payment.to_dict() if self.payment else None,
            "allocations": [a.to_dict() for a in self.allocations],
            "new_paid_paise": self.new_paid_paise,
            "new_payment_state": self.new_payment_state.value,
            "outbox": [e.to_dict() for e in self.outbox],
            "warnings": list(self.warnings),
        }


def _allocation_weights(order: Order) -> tuple[list[str], list[int]]:
    """Line ids and their accepted values. Falls back to the order as one line."""
    if order.items:
        return ([i.item_id for i in order.items], [max(0, i.amount_paise) for i in order.items])
    return ([order.order_id], [max(0, order.total_paise)])


def plan_payment(
    order: Order,
    event: InboxEvent,
    *,
    already_recorded_payment_ids: Sequence[str] = (),
    payment_id: str | None = None,
) -> PaymentCommit:
    """Plan the transaction for one captured payment against one order.

    Concurrency: the caller must have read ``order`` inside the same
    transaction that applies this commit, and must check ``order.version``
    on write. ``new_paid_paise`` is computed from the order as read — a
    read-modify-write, made safe by the version check, not by hoping two
    deposits never arrive at once (the review's lost-update note on F03).
    """
    event.validate()

    if event.payment_id in set(already_recorded_payment_ids):
        # Same money, seen again. Nothing to write, nothing to send: the
        # original commit already produced the customer's message.
        return PaymentCommit(
            order_id=order.order_id,
            payment=None,
            new_paid_paise=order.paid_paise,
            new_payment_state=order.payment_state,
            duplicate=True,
        )

    ledger_id = payment_id or uuid.uuid4().hex
    payment = Payment(
        payment_id=ledger_id,
        provider=event.provider,
        provider_payment_id=event.payment_id,
        order_id=order.order_id,
        amount_paise=event.amount_paise,
        method=event.method,
        currency=event.currency,
        captured_at=event.captured_at,
        event_id=event.event_id,
    )

    item_ids, weights = _allocation_weights(order)
    parts = allocate(event.amount_paise, weights)
    allocations = tuple(
        Allocation(ledger_id, order.order_id, item_id, amount)
        for item_id, amount in zip(item_ids, parts)
        if amount  # a zero allocation is noise in the ledger, not information
    )

    new_paid = order.paid_paise + event.amount_paise
    warnings: list[str] = []
    if order.total_paise and new_paid > order.total_paise:
        warnings.append(
            f"overpaid by {new_paid - order.total_paise} paise — needs a refund or a credit note"
        )
    if order.total_paise == 0:
        warnings.append("payment against an order with no accepted total — check the quote")

    probe = Order(
        order_id=order.order_id, store_id=order.store_id,
        customer_phone=order.customer_phone,
        total_paise=order.total_paise, paid_paise=new_paid,
    )
    target_state = probe.derived_payment_state()

    outbox = [
        OutboxEvent(
            topic="payment.received",
            payload={
                "order_id": order.order_id,
                "amount_paise": event.amount_paise,
                "paid_paise": new_paid,
                "balance_paise": order.total_paise - new_paid,
                "phone": order.customer_phone,
                "method": event.method,
            },
            dedup_key=f"payment.received:{ledger_id}",
        ),
        OutboxEvent(
            topic="staff.payment_alert",
            payload={
                "order_id": order.order_id,
                "store_id": order.store_id,
                "amount_paise": event.amount_paise,
                "pickup_code": order.pickup_code,
                "customer_name": order.customer_name,
            },
            dedup_key=f"staff.payment_alert:{ledger_id}",
        ),
    ]
    if target_state in (PaymentState.PAID, PaymentState.OVERPAID):
        # Dispatch is a *consequence* of payment, published as an event — the
        # payment transaction itself never moves production state.
        outbox.append(
            OutboxEvent(
                topic="order.fully_paid",
                payload={"order_id": order.order_id, "store_id": order.store_id},
                dedup_key=f"order.fully_paid:{order.order_id}",
            )
        )

    return PaymentCommit(
        order_id=order.order_id,
        payment=payment,
        allocations=allocations,
        new_paid_paise=new_paid,
        new_payment_state=target_state,
        outbox=tuple(outbox),
        warnings=tuple(warnings),
    )


def plan_refund(
    order: Order,
    event: InboxEvent,
    *,
    already_recorded_payment_ids: Sequence[str] = (),
    payment_id: str | None = None,
) -> PaymentCommit:
    """Plan a refund as its own auditable ledger row, never a negative edit.

    A refund reduces ``paid_paise`` and moves the order to ``refunded``; it does
    not delete or rewrite the original capture, because the provider's statement
    still shows both and a reconciliation that cannot see both is useless.
    """
    event.validate()
    if event.payment_id in set(already_recorded_payment_ids):
        return PaymentCommit(
            order_id=order.order_id, payment=None,
            new_paid_paise=order.paid_paise, new_payment_state=order.payment_state,
            duplicate=True,
        )

    ledger_id = payment_id or uuid.uuid4().hex
    payment = Payment(
        payment_id=ledger_id, provider=event.provider,
        provider_payment_id=event.payment_id, order_id=order.order_id,
        amount_paise=-event.amount_paise, method=event.method,
        currency=event.currency, kind="refund",
        captured_at=event.captured_at, event_id=event.event_id,
    )
    item_ids, weights = _allocation_weights(order)
    parts = allocate(event.amount_paise, weights)
    allocations = tuple(
        Allocation(ledger_id, order.order_id, item_id, -amount)
        for item_id, amount in zip(item_ids, parts) if amount
    )
    new_paid = order.paid_paise - event.amount_paise
    warnings = ()
    if new_paid < 0:
        warnings = (f"refund exceeds collections by {-new_paid} paise — reconcile before closing",)

    return PaymentCommit(
        order_id=order.order_id,
        payment=payment,
        allocations=allocations,
        new_paid_paise=new_paid,
        new_payment_state=PaymentState.REFUNDED,
        outbox=(
            OutboxEvent(
                topic="payment.refunded",
                payload={
                    "order_id": order.order_id,
                    "amount_paise": event.amount_paise,
                    "phone": order.customer_phone,
                },
                dedup_key=f"payment.refunded:{ledger_id}",
            ),
        ),
        warnings=warnings,
    )
