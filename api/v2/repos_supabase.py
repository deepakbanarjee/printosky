"""Supabase adapters for the v2 ports.

Written against the tables in ``api/migrations/SCHEMA_v44_core_v2.sql``. Three
things here are load-bearing and easy to get wrong:

**Optimistic concurrency.** ``save_order`` updates with ``eq("version",
expected_version)`` and treats "0 rows updated" as a
:class:`core.errors.ConflictError`. PostgREST returns the updated rows, so an
empty list is the lost race — it is not "nothing to do", and must never be
swallowed. This is the same class of mistake as F07, where an empty read was
indistinguishable from a failed one.

**One transaction for a payment.** PostgREST has no multi-statement
transaction, so ``apply_payment_commit`` calls the ``v2_apply_payment``
Postgres function created by the migration, which does the whole commit inside
the database. The Python here is a thin call; the atomicity lives where it can
actually be guaranteed.

**Nothing is best-effort.** Every method either returns data or raises. There is
no ``except Exception: return []`` in this file, by design — see
docs/FAIL_LOUD.md.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.errors import ConflictError, DependencyError, NotFoundError
from core.identity import IdentityRecord, Principal, Role
from core.orders import (
    AttemptState, Lane, Order, OrderItem, PaymentState, PrintAttempt, ProductionState, Task, TaskKind,
)
from core.payments import InboxEvent, PaymentCommit

logger = logging.getLogger("api.v2.repos_supabase")

ORDERS = "orders"
ORDER_ITEMS = "order_items"
ORDER_TASKS = "order_tasks"
PAYMENTS = "payments"
PAYMENT_INBOX = "payment_inbox"
OUTBOX = "outbox_events"
IDENTITIES = "identities"
IDENTITY_SESSIONS = "identity_sessions"
LOGIN_FAILURES = "login_failures"
PRINT_ATTEMPTS = "print_attempts"


def _client():
    """Service-role Supabase client, reusing db_cloud's single construction.

    Imported lazily so this module is importable (and unit-testable) without
    the ``supabase`` package installed.
    """
    try:
        from db_cloud import _client as db_client
    except ImportError as exc:  # pragma: no cover - deployment problem
        raise DependencyError("supabase client unavailable") from exc
    return db_client()


def _rows(response) -> list[dict]:
    data = getattr(response, "data", None)
    if data is None:
        raise DependencyError("supabase returned no data attribute")
    return list(data)


def _order_from_rows(row: dict, items: list[dict], tasks: list[dict]) -> Order:
    order = Order(
        order_id=row["order_id"],
        store_id=row["store_id"],
        customer_phone=row.get("customer_phone") or "",
        customer_name=row.get("customer_name") or "",
        channel=row.get("channel") or "web",
        lane=Lane(row.get("lane") or Lane.ASSISTED.value),
        payment_state=PaymentState(row.get("payment_state") or PaymentState.UNPAID.value),
        production_state=ProductionState(row.get("production_state") or ProductionState.DRAFT.value),
        total_paise=int(row.get("total_paise") or 0),
        paid_paise=int(row.get("paid_paise") or 0),
        quote_hash=row.get("quote_hash") or "",
        accepted_at=row.get("accepted_at") or "",
        promised_at=row.get("promised_at") or "",
        pickup_code=row.get("pickup_code") or "",
        note=row.get("note") or "",
        version=int(row.get("version") or 1),
        created_at=row.get("created_at") or "",
        updated_at=row.get("updated_at") or "",
    )
    for item in sorted(items, key=lambda r: r.get("item_id", "")):
        order.items.append(
            OrderItem(
                item_id=item["item_id"],
                source_object=item.get("source_object") or "",
                file_name=item.get("file_name") or "",
                page_count=int(item.get("page_count") or 0),
                spec=item.get("spec") or {},
                amount_paise=int(item.get("amount_paise") or 0),
                document_hash=item.get("document_hash") or "",
                preflight_ok=item.get("preflight_ok"),
                preflight_note=item.get("preflight_note") or "",
            )
        )
    for task in tasks:
        order.tasks.append(
            Task(
                task_id=task["task_id"],
                kind=TaskKind(task.get("kind") or TaskKind.PRINT.value),
                store_id=task.get("store_id") or order.store_id,
                state=task.get("state") or "open",
                item_id=task.get("item_id") or "",
                assigned_to=task.get("assigned_to") or "",
                due_at=task.get("due_at") or "",
                note=task.get("note") or "",
            )
        )
    return order


class SupabaseOrders:
    def get_order(self, order_id: str) -> Order | None:
        client = _client()
        rows = _rows(client.table(ORDERS).select("*").eq("order_id", order_id).limit(1).execute())
        if not rows:
            return None
        items = _rows(client.table(ORDER_ITEMS).select("*").eq("order_id", order_id).execute())
        tasks = _rows(client.table(ORDER_TASKS).select("*").eq("order_id", order_id).execute())
        return _order_from_rows(rows[0], items, tasks)

    def save_order(self, order: Order, *, expected_version: int | None = None) -> Order:
        client = _client()
        payload = {
            "order_id": order.order_id,
            "store_id": order.store_id,
            "customer_phone": order.customer_phone,
            "customer_name": order.customer_name,
            "channel": order.channel,
            "lane": order.lane.value,
            "payment_state": order.payment_state.value,
            "production_state": order.production_state.value,
            "total_paise": order.total_paise,
            "paid_paise": order.paid_paise,
            "quote_hash": order.quote_hash,
            "accepted_at": order.accepted_at or None,
            "promised_at": order.promised_at or None,
            "pickup_code": order.pickup_code,
            "note": order.note,
        }

        if expected_version in (None, 0):
            payload["version"] = 1
            _rows(client.table(ORDERS).insert(payload).execute())
            new_version = 1
        else:
            payload["version"] = expected_version + 1
            updated = _rows(
                client.table(ORDERS)
                .update(payload)
                .eq("order_id", order.order_id)
                .eq("version", expected_version)
                .execute()
            )
            if not updated:
                # Zero rows means the version moved under us. Reporting this as
                # success is how two consoles silently overwrite each other.
                raise ConflictError(
                    "this order changed while you were editing it",
                    details={"order_id": order.order_id, "expected_version": expected_version},
                )
            new_version = payload["version"]

        if order.items:
            client.table(ORDER_ITEMS).upsert(
                [
                    {
                        "item_id": i.item_id,
                        "order_id": order.order_id,
                        "source_object": i.source_object,
                        "file_name": i.file_name,
                        "page_count": i.page_count,
                        "spec": i.spec,
                        "amount_paise": i.amount_paise,
                        "document_hash": i.document_hash,
                        "preflight_ok": i.preflight_ok,
                        "preflight_note": i.preflight_note,
                    }
                    for i in order.items
                ],
                on_conflict="item_id",
            ).execute()

        order.version = new_version
        return order

    def list_orders(self, *, store_id=None, states=None, limit=50, cursor=None):
        client = _client()
        query = client.table(ORDERS).select("*").order("created_at", desc=True)
        if store_id:
            query = query.eq("store_id", store_id)
        if states:
            query = query.in_("production_state", list(states))
        offset = int(cursor) if cursor and str(cursor).isdigit() else 0
        rows = _rows(query.range(offset, offset + limit - 1).execute())
        orders = [_order_from_rows(row, [], []) for row in rows]
        nxt = str(offset + limit) if len(rows) == limit else None
        return orders, nxt


class SupabasePayments:
    def recorded_payment_ids(self, order_id: str) -> list[str]:
        rows = _rows(
            _client().table(PAYMENTS).select("provider_payment_id").eq("order_id", order_id).execute()
        )
        return [r["provider_payment_id"] for r in rows]

    def apply_payment_commit(self, commit: PaymentCommit, *, expected_version: int) -> Order:
        """One RPC, one transaction. See ``v2_apply_payment`` in SCHEMA_v44."""
        if commit.duplicate or commit.payment is None:
            order = SupabaseOrders().get_order(commit.order_id)
            if order is None:
                raise NotFoundError(f"unknown order {commit.order_id}")
            return order

        response = _client().rpc(
            "v2_apply_payment",
            {
                "p_order_id": commit.order_id,
                "p_expected_version": expected_version,
                "p_payment": commit.payment.to_dict(),
                "p_allocations": [a.to_dict() for a in commit.allocations],
                "p_new_paid_paise": commit.new_paid_paise,
                "p_new_payment_state": commit.new_payment_state.value,
                "p_outbox": [e.to_dict() for e in commit.outbox],
            },
        ).execute()
        result: Any = getattr(response, "data", None)
        if isinstance(result, str):
            result = json.loads(result)
        if not result or not result.get("ok"):
            reason = (result or {}).get("error", "unknown")
            if reason == "version_conflict":
                raise ConflictError(
                    "order changed while the payment was being applied",
                    details={"order_id": commit.order_id},
                )
            raise DependencyError(f"payment commit failed: {reason}")

        order = SupabaseOrders().get_order(commit.order_id)
        if order is None:  # pragma: no cover - the RPC just wrote it
            raise NotFoundError(f"unknown order {commit.order_id}")
        return order

    def list_payments(self, order_id: str) -> list[dict]:
        return _rows(
            _client().table(PAYMENTS).select("*").eq("order_id", order_id)
            .order("captured_at", desc=True).execute()
        )


class SupabaseInbox:
    def accept(self, event: InboxEvent) -> bool:
        client = _client()
        existing = _rows(
            client.table(PAYMENT_INBOX).select("event_id")
            .eq("provider", event.provider).eq("event_id", event.event_id).limit(1).execute()
        )
        if existing:
            return False
        _rows(
            client.table(PAYMENT_INBOX).insert({
                "provider": event.provider,
                "event_id": event.event_id,
                "payment_id": event.payment_id,
                "order_ref": event.order_ref,
                "amount_paise": event.amount_paise,
                "currency": event.currency,
                "method": event.method,
                "state": "received",
                "raw": event.raw,
            }).execute()
        )
        return True

    def mark_processed(self, event_id: str, *, result: str = "ok", error: str = "") -> None:
        _client().table(PAYMENT_INBOX).update(
            {"state": "processed", "result": result, "error": error or None}
        ).eq("event_id", event_id).execute()

    def pending(self, limit: int = 50) -> list[InboxEvent]:
        rows = _rows(
            _client().table(PAYMENT_INBOX).select("*").eq("state", "received")
            .order("created_at").limit(limit).execute()
        )
        return [
            InboxEvent(
                provider=r["provider"], event_id=r["event_id"], payment_id=r["payment_id"],
                order_ref=r["order_ref"], amount_paise=int(r["amount_paise"]),
                currency=r.get("currency") or "INR", method=r.get("method") or "",
                raw=r.get("raw") or {},
            )
            for r in rows
        ]


class SupabaseOutbox:
    def due(self, limit: int = 50) -> list[dict]:
        return _rows(
            _client().table(OUTBOX).select("*").eq("state", "pending")
            .lte("due_at", "now()").order("created_at").limit(limit).execute()
        )

    def mark_sent(self, outbox_id: str) -> None:
        _client().table(OUTBOX).update({"state": "sent"}).eq("outbox_id", outbox_id).execute()

    def mark_failed(self, outbox_id: str, error: str, *, retry_after_seconds: int) -> None:
        _client().rpc(
            "v2_outbox_fail",
            {"p_outbox_id": outbox_id, "p_error": error[:500],
             "p_retry_after_seconds": retry_after_seconds},
        ).execute()


class SupabaseIdentities:
    def candidates_for_secret(self, *, kind: str = "staff") -> list[IdentityRecord]:
        rows = _rows(
            _client().table(IDENTITIES).select("*").eq("active", True).eq("kind", kind).execute()
        )
        return [
            IdentityRecord(
                identity_id=r["identity_id"],
                display_name=r.get("display_name") or r["identity_id"],
                role=Role(r.get("role") or Role.COUNTER.value),
                store_ids=tuple(r.get("store_ids") or ()),
                secret_hash=r.get("secret_hash") or "",
                secret_salt=r.get("secret_salt"),
                active=bool(r.get("active")),
                kind=r.get("kind") or "staff",
                metadata=r.get("metadata") or {},
            )
            for r in rows
        ]

    def record_failure(self, identity_hint: str, ip: str) -> None:
        _client().table(LOGIN_FAILURES).insert(
            {"identity_hint": identity_hint[:64], "ip": ip[:64]}
        ).execute()

    def recent_failures(self, identity_hint: str, ip: str) -> list[float]:
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
        rows = _rows(
            _client().table(LOGIN_FAILURES).select("created_at")
            .eq("ip", ip[:64]).gte("created_at", since).limit(50).execute()
        )
        out = []
        for row in rows:
            stamp = str(row.get("created_at") or "").replace("Z", "+00:00")
            try:
                out.append(datetime.fromisoformat(stamp).timestamp())
            except ValueError:
                logger.warning("unparseable login_failures.created_at: %r", stamp)
        return out

    def start_session(self, principal: Principal) -> None:
        from datetime import datetime, timezone

        _client().table(IDENTITY_SESSIONS).insert({
            "session_id": principal.session_id,
            "identity_id": principal.identity_id,
            "role": principal.role.value,
            "store_ids": list(principal.store_ids),
            "device_id": principal.device_id or None,
            "expires_at": datetime.fromtimestamp(principal.expires_at, timezone.utc).isoformat(),
        }).execute()

    def is_revoked(self, session_id: str) -> bool:
        rows = _rows(
            _client().table(IDENTITY_SESSIONS).select("revoked_at")
            .eq("session_id", session_id).limit(1).execute()
        )
        # No row: not revoked. A session table that has lost a row must not log
        # out every store PC; expiry still bounds the exposure.
        return bool(rows and rows[0].get("revoked_at"))

    def revoke(self, session_id: str) -> None:
        _client().table(IDENTITY_SESSIONS).update({"revoked_at": "now()"}).eq(
            "session_id", session_id
        ).execute()


class SupabaseAttempts:
    def open_attempt(self, attempt: PrintAttempt) -> PrintAttempt:
        _rows(_client().table(PRINT_ATTEMPTS).insert(attempt.to_dict()).execute())
        return attempt

    def close_attempt(self, attempt_id: str, *, state: str, spool_id: str = "",
                      sheets_observed: int = 0, failure_reason: str = "") -> None:
        updated = _rows(
            _client().table(PRINT_ATTEMPTS).update({
                "state": AttemptState(state).value,
                "spool_id": spool_id or None,
                "sheets_observed": sheets_observed or None,
                "failure_reason": failure_reason or None,
                "finished_at": "now()",
            }).eq("attempt_id", attempt_id).execute()
        )
        if not updated:
            raise NotFoundError(f"unknown attempt {attempt_id}")

    def attempts_for_task(self, task_id: str) -> list[PrintAttempt]:
        rows = _rows(
            _client().table(PRINT_ATTEMPTS).select("*").eq("task_id", task_id)
            .order("started_at").execute()
        )
        return [
            PrintAttempt(
                attempt_id=r["attempt_id"], task_id=r["task_id"], device_id=r.get("device_id") or "",
                printer_queue=r.get("printer_queue") or "", attempt_token=r.get("attempt_token") or "",
                state=AttemptState(r.get("state") or AttemptState.CREATED.value),
                spool_id=r.get("spool_id") or "", sheets_expected=int(r.get("sheets_expected") or 0),
                sheets_observed=int(r.get("sheets_observed") or 0),
                started_at=r.get("started_at") or "", finished_at=r.get("finished_at") or "",
                failure_reason=r.get("failure_reason") or "",
            )
            for r in rows
        ]


class SupabaseUnitOfWork:
    def __init__(self):
        self.orders = SupabaseOrders()
        self.payments = SupabasePayments()
        self.inbox = SupabaseInbox()
        self.outbox = SupabaseOutbox()
        self.identities = SupabaseIdentities()
        self.attempts = SupabaseAttempts()
