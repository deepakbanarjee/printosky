"""The order model: one order, many items, many tasks — and two state machines.

Contract #1 and #3 of the review's target architecture (§7):

* **One order, many items and tasks.** A customer who sends four files gets one
  order with four items and however many production tasks that implies. Today
  each file is its own ``jobs`` row and a batch is a comma-joined string of job
  ids, which is why a batch payment had nowhere correct to land (F02).
* **Payment state and production state are separate.** A retried payment
  webhook must not drag a ``delivered`` order back to ``paid``. They are two
  independent machines over the same row and neither may write the other's
  column.

The third machine here is the **print attempt** (contract #6): a task can be
attempted more than once, each attempt has its own identity and evidence, and
an attempt whose outcome we do not know becomes ``output_uncertain`` — a state a
human resolves — rather than being retried into a second stack of paper (F05,
F06).

Lanes (§8.1) are a property of the order, decided from the file and the spec:
``express`` runs unattended, ``assisted`` waits for an operator, ``scheduled``
is planned against capacity. A job that fails preflight moves lane with a
reason; it never loops in express.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from core.errors import ConflictError, ValidationError

__all__ = [
    "AttemptState",
    "Lane",
    "Order",
    "OrderItem",
    "PaymentState",
    "PrintAttempt",
    "ProductionState",
    "Task",
    "TaskKind",
    "assert_transition",
    "can_transition",
    "new_order_id",
]


class PaymentState(str, Enum):
    UNPAID = "unpaid"
    PART_PAID = "part_paid"       # deposit taken, balance due at handover
    PAID = "paid"
    OVERPAID = "overpaid"         # visible, not silently absorbed
    REFUNDED = "refunded"
    WRITTEN_OFF = "written_off"   # an owner decision, always deliberate


class ProductionState(str, Enum):
    DRAFT = "draft"                        # being built; not yet promised
    AWAITING_APPROVAL = "awaiting_approval"  # assisted lane: operator must look
    QUEUED = "queued"                      # accepted spec, ready to dispatch
    CLAIMED = "claimed"                    # a device holds a fenced lease on it
    SPOOLED = "spooled"                    # handed to the printer
    OUTPUT_UNCERTAIN = "output_uncertain"  # we do not know if paper came out
    PRINTED = "printed"
    FINISHING = "finishing"
    READY = "ready"                        # on the shelf, pickup code live
    DELIVERED = "delivered"
    HELD = "held"                          # blocked on a human (file, price, stock)
    CANCELLED = "cancelled"


class Lane(str, Enum):
    EXPRESS = "express"
    ASSISTED = "assisted"
    SCHEDULED = "scheduled"


class TaskKind(str, Enum):
    PREFLIGHT = "preflight"
    PRINT = "print"
    FINISHING = "finishing"
    TRANSFER = "transfer"      # inter-store handoff, with a manifest
    HANDOVER = "handover"


class AttemptState(str, Enum):
    CREATED = "created"
    SUBMITTED = "submitted"    # spooler accepted it
    CONFIRMED = "confirmed"    # counters or a human agree paper came out
    FAILED = "failed"          # known not to have printed — safe to retry
    UNCERTAIN = "uncertain"    # unknown — a human decides, never auto-retried


# Allowed edges. Anything not listed is refused by assert_transition, which is
# the point: a new state cannot be reached by accident from six handlers.
_PRODUCTION_EDGES: dict[ProductionState, frozenset[ProductionState]] = {
    ProductionState.DRAFT: frozenset({
        ProductionState.AWAITING_APPROVAL, ProductionState.QUEUED,
        ProductionState.HELD, ProductionState.CANCELLED,
    }),
    ProductionState.AWAITING_APPROVAL: frozenset({
        ProductionState.QUEUED, ProductionState.HELD, ProductionState.CANCELLED,
    }),
    ProductionState.QUEUED: frozenset({
        ProductionState.CLAIMED, ProductionState.HELD, ProductionState.CANCELLED,
    }),
    ProductionState.CLAIMED: frozenset({
        ProductionState.SPOOLED, ProductionState.QUEUED,      # lease released cleanly
        ProductionState.OUTPUT_UNCERTAIN, ProductionState.HELD,
    }),
    ProductionState.SPOOLED: frozenset({
        ProductionState.PRINTED, ProductionState.OUTPUT_UNCERTAIN, ProductionState.HELD,
    }),
    # An uncertain outcome is resolved by a person, in one of three directions.
    ProductionState.OUTPUT_UNCERTAIN: frozenset({
        ProductionState.PRINTED, ProductionState.QUEUED, ProductionState.HELD,
    }),
    ProductionState.PRINTED: frozenset({
        ProductionState.FINISHING, ProductionState.READY, ProductionState.HELD,
    }),
    ProductionState.FINISHING: frozenset({ProductionState.READY, ProductionState.HELD}),
    ProductionState.READY: frozenset({ProductionState.DELIVERED, ProductionState.HELD}),
    ProductionState.DELIVERED: frozenset(),        # terminal; a reprint is a new order
    ProductionState.HELD: frozenset({
        ProductionState.QUEUED, ProductionState.AWAITING_APPROVAL, ProductionState.CANCELLED,
    }),
    ProductionState.CANCELLED: frozenset(),
}

_PAYMENT_EDGES: dict[PaymentState, frozenset[PaymentState]] = {
    PaymentState.UNPAID: frozenset({
        PaymentState.PART_PAID, PaymentState.PAID, PaymentState.OVERPAID,
        PaymentState.WRITTEN_OFF,
    }),
    PaymentState.PART_PAID: frozenset({
        PaymentState.PAID, PaymentState.OVERPAID, PaymentState.REFUNDED,
        PaymentState.WRITTEN_OFF,
    }),
    PaymentState.PAID: frozenset({PaymentState.OVERPAID, PaymentState.REFUNDED}),
    PaymentState.OVERPAID: frozenset({PaymentState.PAID, PaymentState.REFUNDED}),
    PaymentState.REFUNDED: frozenset({PaymentState.PAID, PaymentState.PART_PAID}),
    PaymentState.WRITTEN_OFF: frozenset({PaymentState.PAID}),
}


def can_transition(current, target) -> bool:
    """True if ``current -> target`` is a legal edge in that state's machine."""
    if isinstance(current, ProductionState):
        return target in _PRODUCTION_EDGES.get(current, frozenset())
    if isinstance(current, PaymentState):
        return target in _PAYMENT_EDGES.get(current, frozenset())
    raise TypeError(f"not a Printosky state: {current!r}")


def assert_transition(current, target) -> None:
    """Raise :class:`ConflictError` unless the edge is legal.

    A refused transition is a 409, not a 400: the request was reasonable, the
    world had moved. The console shows it as "this order is already delivered"
    rather than "bad request", which is what the operator needs to read.
    """
    if current == target:
        return  # idempotent re-assert; a retried webhook must not be an error
    if not can_transition(current, target):
        raise ConflictError(
            f"cannot go from {current.value} to {target.value}",
            details={"from": current.value, "to": target.value},
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_order_id(store_id: str, when: datetime | None = None) -> str:
    """``OSP-20260916-7F3A`` — sortable by eye, unique by suffix.

    Keeps the shape of the existing ``OSP-YYYYMMDD-XXXX`` ids so slips, pickup
    boards and WhatsApp messages read the same after the upgrade as before.
    """
    when = when or datetime.now(timezone.utc)
    return f"{store_id}-{when.strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"


@dataclass
class OrderItem:
    """One file + one accepted specification. Immutable once the order is accepted."""

    item_id: str
    source_object: str                 # storage object id, NOT a caller-supplied URL (F08)
    file_name: str
    page_count: int = 0
    spec: dict = field(default_factory=dict)
    amount_paise: int = 0
    document_hash: str = ""
    preflight_ok: bool | None = None   # None = not checked yet
    preflight_note: str = ""

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "source_object": self.source_object,
            "file_name": self.file_name,
            "page_count": self.page_count,
            "spec": self.spec,
            "amount_paise": self.amount_paise,
            "document_hash": self.document_hash,
            "preflight_ok": self.preflight_ok,
            "preflight_note": self.preflight_note,
        }


@dataclass
class Task:
    """A unit of work with an owner and a due time — what the console lists."""

    task_id: str
    kind: TaskKind
    store_id: str
    state: str = "open"                # open | in_progress | done | blocked
    item_id: str = ""
    assigned_to: str = ""
    due_at: str = ""
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "kind": self.kind.value,
            "store_id": self.store_id,
            "state": self.state,
            "item_id": self.item_id,
            "assigned_to": self.assigned_to,
            "due_at": self.due_at,
            "note": self.note,
        }


@dataclass
class PrintAttempt:
    """One submission of one task to one printer, with its own evidence.

    ``attempt_token`` is the fencing token: a device writes it when it claims,
    and a write from an older token is refused. That is what stops the "two PCs
    release the same attempt" scenario in the review's regression list.
    """

    attempt_id: str
    task_id: str
    device_id: str
    printer_queue: str
    attempt_token: str
    state: AttemptState = AttemptState.CREATED
    spool_id: str = ""
    sheets_expected: int = 0
    sheets_observed: int = 0
    started_at: str = field(default_factory=_now)
    finished_at: str = ""
    failure_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "attempt_id": self.attempt_id,
            "task_id": self.task_id,
            "device_id": self.device_id,
            "printer_queue": self.printer_queue,
            "attempt_token": self.attempt_token,
            "state": self.state.value,
            "spool_id": self.spool_id,
            "sheets_expected": self.sheets_expected,
            "sheets_observed": self.sheets_observed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure_reason": self.failure_reason,
        }

    def is_safe_to_retry(self) -> bool:
        """Only a *known* failure may be retried automatically.

        ``UNCERTAIN`` deliberately returns False. A timeout after spooling is
        the case where re-sending prints 200 pages twice; it goes to a person.
        """
        return self.state is AttemptState.FAILED


@dataclass
class Order:
    """The authoritative record. Everything else in the system points at this."""

    order_id: str
    store_id: str
    customer_phone: str
    channel: str = "web"               # web | whatsapp | counter
    lane: Lane = Lane.ASSISTED         # safe default: a human looks unless proven otherwise
    payment_state: PaymentState = PaymentState.UNPAID
    production_state: ProductionState = ProductionState.DRAFT
    items: list[OrderItem] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)
    total_paise: int = 0
    paid_paise: int = 0
    quote_hash: str = ""
    accepted_at: str = ""
    promised_at: str = ""
    customer_name: str = ""
    pickup_code: str = ""
    note: str = ""
    version: int = 1                   # optimistic concurrency; see repos
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    # ── invariants ───────────────────────────────────────────────────────────

    def balance_paise(self) -> int:
        """What the customer still owes. Negative means we owe them."""
        return self.total_paise - self.paid_paise

    def derived_payment_state(self) -> PaymentState:
        """Payment state implied by the money, ignoring manual overrides."""
        if self.paid_paise <= 0:
            return PaymentState.UNPAID
        if self.paid_paise < self.total_paise:
            return PaymentState.PART_PAID
        if self.paid_paise == self.total_paise:
            return PaymentState.PAID
        return PaymentState.OVERPAID

    def validate(self) -> None:
        if not self.order_id or not self.store_id:
            raise ValidationError("order_id and store_id are required")
        if self.total_paise < 0 or self.paid_paise < 0:
            raise ValidationError("money must not be negative")
        item_sum = sum(i.amount_paise for i in self.items)
        if self.items and self.accepted_at and item_sum != self.total_paise:
            raise ValidationError(
                "item amounts do not sum to the order total",
                details={"item_sum": item_sum, "total_paise": self.total_paise},
            )

    # ── transitions ──────────────────────────────────────────────────────────

    def move_production(self, target: ProductionState, *, reason: str = "") -> None:
        assert_transition(self.production_state, target)
        self.production_state = target
        if reason:
            self.note = f"{self.note} · {reason}".strip(" ·")
        self.updated_at = _now()

    def move_payment(self, target: PaymentState) -> None:
        """Move payment state only. Production state is never touched here.

        This is contract #3 made mechanical: a late ``payment.captured`` for an
        order that is already ``delivered`` updates the ledger and leaves the
        shelf alone.
        """
        assert_transition(self.payment_state, target)
        self.payment_state = target
        self.updated_at = _now()

    def is_dispatchable(self) -> bool:
        """May a store agent pull this order and print it right now?

        Deliberately strict. Unattended printing requires: money in, a lane that
        allows it, an accepted spec, and every item having passed preflight.
        """
        return (
            self.production_state is ProductionState.QUEUED
            and self.payment_state in (PaymentState.PAID, PaymentState.OVERPAID)
            and self.lane is not Lane.ASSISTED
            and bool(self.accepted_at)
            and bool(self.items)
            and all(i.preflight_ok for i in self.items)
        )

    def to_dict(self) -> dict:
        return {
            "order_id": self.order_id,
            "store_id": self.store_id,
            "customer_phone": self.customer_phone,
            "customer_name": self.customer_name,
            "channel": self.channel,
            "lane": self.lane.value,
            "payment_state": self.payment_state.value,
            "production_state": self.production_state.value,
            "total_paise": self.total_paise,
            "paid_paise": self.paid_paise,
            "balance_paise": self.balance_paise(),
            "quote_hash": self.quote_hash,
            "accepted_at": self.accepted_at,
            "promised_at": self.promised_at,
            "pickup_code": self.pickup_code,
            "note": self.note,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "items": [i.to_dict() for i in self.items],
            "tasks": [t.to_dict() for t in self.tasks],
        }
