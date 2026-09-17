"""In-memory adapters — the reference implementation of :mod:`core.ports`.

Two jobs:

1. **Tests.** The whole v2 suite runs against these, with no Supabase, no
   network and no fixtures to reset. A contract test that passes here and fails
   against Supabase is an adapter bug, which is exactly the distinction that
   was missing when the allocation tests covered a receiver that is no longer
   deployed (F02).
2. **A worked example.** The store agent's SQLite adapter and the Supabase
   adapter are both easier to get right with a correct, readable version of the
   same semantics to compare against — in particular the version check in
   ``save_order`` and the all-or-nothing ``apply_payment_commit``.

Deliberately not thread-safe. It backs a test suite and a single-threaded
serverless handler; adding a lock would imply a concurrency guarantee that the
real adapters get from the database, not from Python.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field

from core.errors import ConflictError, NotFoundError
from core.identity import IdentityRecord, Principal
from core.orders import Order, PaymentState, PrintAttempt
from core.payments import InboxEvent, PaymentCommit

__all__ = ["InMemoryUnitOfWork"]


class InMemoryOrders:
    def __init__(self):
        self._rows: dict[str, Order] = {}

    def get_order(self, order_id: str) -> Order | None:
        row = self._rows.get(order_id)
        return copy.deepcopy(row) if row else None

    def save_order(self, order: Order, *, expected_version: int | None = None) -> Order:
        existing = self._rows.get(order.order_id)
        if expected_version is not None:
            current = existing.version if existing else 0
            if current != expected_version:
                raise ConflictError(
                    "this order changed while you were editing it",
                    details={"expected_version": expected_version, "actual_version": current},
                )
        order.version = (existing.version + 1) if existing else 1
        self._rows[order.order_id] = copy.deepcopy(order)
        return copy.deepcopy(order)

    def list_orders(self, *, store_id=None, states=None, limit=50, cursor=None):
        rows = sorted(self._rows.values(), key=lambda o: o.created_at, reverse=True)
        if store_id:
            rows = [o for o in rows if o.store_id == store_id]
        if states:
            wanted = set(states)
            rows = [o for o in rows if o.production_state.value in wanted]
        start = int(cursor) if cursor and cursor.isdigit() else 0
        page = rows[start:start + limit]
        nxt = str(start + limit) if start + limit < len(rows) else None
        return [copy.deepcopy(o) for o in page], nxt


class InMemoryPayments:
    def __init__(self, orders: InMemoryOrders):
        self._orders = orders
        self.ledger: list[dict] = []
        self.allocations: list[dict] = []
        self.outbox: list[dict] = []

    def recorded_payment_ids(self, order_id: str) -> list[str]:
        return [p["provider_payment_id"] for p in self.ledger if p["order_id"] == order_id]

    def apply_payment_commit(self, commit: PaymentCommit, *, expected_version: int) -> Order:
        """All-or-nothing: staged first, published only once every step succeeded."""
        order = self._orders.get_order(commit.order_id)
        if order is None:
            raise NotFoundError(f"unknown order {commit.order_id}")
        if commit.duplicate or commit.payment is None:
            return order
        if order.version != expected_version:
            raise ConflictError(
                "order changed while the payment was being applied",
                details={"expected_version": expected_version, "actual_version": order.version},
            )

        staged_ledger = list(self.ledger)
        staged_alloc = list(self.allocations)
        staged_outbox = list(self.outbox)
        try:
            staged_ledger.append(commit.payment.to_dict())
            staged_alloc.extend(a.to_dict() for a in commit.allocations)
            staged_outbox.extend(
                {
                    "outbox_id": uuid.uuid4().hex,
                    "topic": e.topic,
                    "payload": e.payload,
                    "dedup_key": e.dedup_key,
                    "state": "pending",
                    "attempts": 0,
                    "due_at": time.time(),
                }
                for e in commit.outbox
            )
            order.paid_paise = commit.new_paid_paise
            if order.payment_state is not commit.new_payment_state:
                order.move_payment(commit.new_payment_state)
            saved = self._orders.save_order(order, expected_version=expected_version)
        except Exception:
            # Nothing was published, so nothing needs undoing — that is the
            # point of staging. Re-raise so the transport answers 503 and the
            # provider retries against an unchanged world.
            raise
        self.ledger, self.allocations, self.outbox = staged_ledger, staged_alloc, staged_outbox
        return saved

    def list_payments(self, order_id: str) -> list[dict]:
        return [dict(p) for p in self.ledger if p["order_id"] == order_id]


class InMemoryInbox:
    def __init__(self):
        self._rows: dict[str, dict] = {}

    def accept(self, event: InboxEvent) -> bool:
        key = f"{event.provider}:{event.event_id}"
        if key in self._rows:
            return False
        self._rows[key] = {"event": event, "state": "received", "result": "", "error": ""}
        return True

    def mark_processed(self, event_id: str, *, result: str = "ok", error: str = "") -> None:
        for row in self._rows.values():
            if row["event"].event_id == event_id:
                row.update(state="processed", result=result, error=error)

    def pending(self, limit: int = 50) -> list[InboxEvent]:
        return [r["event"] for r in self._rows.values() if r["state"] == "received"][:limit]


class InMemoryOutbox:
    def __init__(self, payments: InMemoryPayments):
        self._payments = payments

    def due(self, limit: int = 50) -> list[dict]:
        now = time.time()
        return [
            row for row in self._payments.outbox
            if row["state"] == "pending" and row["due_at"] <= now
        ][:limit]

    def mark_sent(self, outbox_id: str) -> None:
        for row in self._payments.outbox:
            if row["outbox_id"] == outbox_id:
                row["state"] = "sent"

    def mark_failed(self, outbox_id: str, error: str, *, retry_after_seconds: int) -> None:
        for row in self._payments.outbox:
            if row["outbox_id"] == outbox_id:
                row["attempts"] += 1
                row["error"] = error
                row["due_at"] = time.time() + retry_after_seconds
                row["state"] = "pending" if row["attempts"] < 8 else "dead"


class InMemoryIdentities:
    def __init__(self):
        self.records: list[IdentityRecord] = []
        self.sessions: dict[str, dict] = {}
        self.failures: dict[str, list[float]] = {}

    def candidates_for_secret(self, *, kind: str = "staff") -> list[IdentityRecord]:
        return [r for r in self.records if r.active and r.kind == kind]

    def record_failure(self, identity_hint: str, ip: str) -> None:
        self.failures.setdefault(f"{identity_hint}|{ip}", []).append(time.time())

    def recent_failures(self, identity_hint: str, ip: str) -> list[float]:
        return list(self.failures.get(f"{identity_hint}|{ip}", ()))

    def start_session(self, principal: Principal) -> None:
        self.sessions[principal.session_id] = {
            "identity_id": principal.identity_id,
            "expires_at": principal.expires_at,
            "revoked": False,
        }

    def is_revoked(self, session_id: str) -> bool:
        row = self.sessions.get(session_id)
        # Unknown session id: not revoked. Revocation is a positive fact, and
        # treating "we have no record" as revoked would log every store PC out
        # the moment the session table is unreachable.
        return bool(row and row["revoked"])

    def revoke(self, session_id: str) -> None:
        if session_id in self.sessions:
            self.sessions[session_id]["revoked"] = True


class InMemoryAttempts:
    def __init__(self):
        self._rows: dict[str, PrintAttempt] = {}

    def open_attempt(self, attempt: PrintAttempt) -> PrintAttempt:
        self._rows[attempt.attempt_id] = copy.deepcopy(attempt)
        return copy.deepcopy(attempt)

    def close_attempt(self, attempt_id, *, state, spool_id="", sheets_observed=0,
                      failure_reason="") -> None:
        row = self._rows.get(attempt_id)
        if row is None:
            raise NotFoundError(f"unknown attempt {attempt_id}")
        from core.orders import AttemptState

        row.state = AttemptState(state)
        row.spool_id = spool_id or row.spool_id
        row.sheets_observed = sheets_observed or row.sheets_observed
        row.failure_reason = failure_reason

    def attempts_for_task(self, task_id: str) -> list[PrintAttempt]:
        return [copy.deepcopy(r) for r in self._rows.values() if r.task_id == task_id]


@dataclass
class InMemoryUnitOfWork:
    orders: InMemoryOrders = field(default_factory=InMemoryOrders)
    identities: InMemoryIdentities = field(default_factory=InMemoryIdentities)
    attempts: InMemoryAttempts = field(default_factory=InMemoryAttempts)
    inbox: InMemoryInbox = field(default_factory=InMemoryInbox)
    payments: InMemoryPayments = field(init=False)
    outbox: InMemoryOutbox = field(init=False)

    def __post_init__(self):
        self.payments = InMemoryPayments(self.orders)
        self.outbox = InMemoryOutbox(self.payments)
