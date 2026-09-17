"""Ports: what the core needs from the outside world, as Protocols.

The core computes; adapters persist. These Protocols are the seam between
them, and they exist for three practical reasons:

* **Two backends, one rule set.** The cloud adapter talks to Supabase; the
  store agent's adapter will talk to SQLite while the internet is down. Both
  satisfy the same Protocol, so the rules cannot drift apart the way
  ``webhook_receiver.py`` and ``_process_razorpay_payment`` did (F02: the
  allocation tests protected the receiver that is no longer deployed).
* **Field ownership (F07).** ``apply_payment_commit`` is the *only* way money
  reaches storage, and it takes a whole commit, so a sync job cannot
  half-update a balance. A store agent publishes print events; it does not
  overwrite cloud-owned financial columns.
* **Testability.** ``api/v2/repos.py`` ships an in-memory implementation used by
  the whole v2 test suite, so the contract tests run with no network at all.

Runtime-checkable Protocols, not ABCs: adapters stay plain classes and nothing
in this package has to be imported to implement one.
"""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from core.identity import IdentityRecord, Principal
from core.orders import Order, PrintAttempt
from core.payments import InboxEvent, PaymentCommit

__all__ = [
    "IdentityRepository",
    "InboxRepository",
    "OrderRepository",
    "OutboxRepository",
    "PaymentRepository",
    "PrintAttemptRepository",
    "UnitOfWork",
]


@runtime_checkable
class OrderRepository(Protocol):
    def get_order(self, order_id: str) -> Order | None: ...

    def save_order(self, order: Order, *, expected_version: int | None = None) -> Order:
        """Persist ``order``.

        When ``expected_version`` is given, the write must fail with
        :class:`core.errors.ConflictError` if the stored version differs. That
        check is the whole defence against two consoles editing one order.
        """

    def list_orders(self, *, store_id: str | None = None, states: Sequence[str] | None = None,
                    limit: int = 50, cursor: str | None = None) -> tuple[list[Order], str | None]:
        """Newest first. Returns ``(orders, next_cursor)``; cursor is opaque."""


@runtime_checkable
class PaymentRepository(Protocol):
    def recorded_payment_ids(self, order_id: str) -> list[str]:
        """Provider payment ids already in the ledger for this order."""

    def apply_payment_commit(self, commit: PaymentCommit, *, expected_version: int) -> Order:
        """Apply payment, allocations, order balance and outbox in ONE transaction.

        All of it or none of it. A partial apply is the failure mode F03 is
        about, so an adapter that cannot do this atomically must say so by
        raising rather than doing its best.
        """

    def list_payments(self, order_id: str) -> list[dict]: ...


@runtime_checkable
class InboxRepository(Protocol):
    def accept(self, event: InboxEvent) -> bool:
        """Durably store a verified provider event *before* it is acknowledged.

        Returns False if this ``event_id`` was already stored (a re-delivery).
        Raises on a storage failure so the transport can answer 503 and let the
        provider retry — never returns True on a failed write.
        """

    def mark_processed(self, event_id: str, *, result: str = "ok", error: str = "") -> None: ...

    def pending(self, limit: int = 50) -> list[InboxEvent]:
        """Events accepted but not yet processed — the crash-recovery queue."""


@runtime_checkable
class OutboxRepository(Protocol):
    def due(self, limit: int = 50) -> list[dict]: ...

    def mark_sent(self, outbox_id: str) -> None: ...

    def mark_failed(self, outbox_id: str, error: str, *, retry_after_seconds: int) -> None: ...


@runtime_checkable
class IdentityRepository(Protocol):
    def candidates_for_secret(self, *, kind: str = "staff") -> list[IdentityRecord]:
        """Active identities whose credential could match. Never includes inactive rows."""

    def record_failure(self, identity_hint: str, ip: str) -> None: ...

    def recent_failures(self, identity_hint: str, ip: str) -> list[float]: ...

    def start_session(self, principal: Principal) -> None:
        """Persist the minted session so it can be revoked before it expires."""

    def is_revoked(self, session_id: str) -> bool: ...

    def revoke(self, session_id: str) -> None: ...


@runtime_checkable
class PrintAttemptRepository(Protocol):
    def open_attempt(self, attempt: PrintAttempt) -> PrintAttempt:
        """Record an attempt before the file is spooled, not after.

        Written first so that a crash between here and the printer leaves
        evidence that *something* may have come out (F05/F06). An attempt with
        no terminal state is resolved by a human, not by a retry.
        """

    def close_attempt(self, attempt_id: str, *, state: str, spool_id: str = "",
                      sheets_observed: int = 0, failure_reason: str = "") -> None: ...

    def attempts_for_task(self, task_id: str) -> list[PrintAttempt]: ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Everything a v2 request handler is allowed to touch."""

    orders: OrderRepository
    payments: PaymentRepository
    inbox: InboxRepository
    outbox: OutboxRepository
    identities: IdentityRepository
    attempts: PrintAttemptRepository
