"""
Store-side WhatsApp dispatch (block 5 of plan v2).

Two halves:

1. **Outbound dispatch** — when a job is paid and the routing engine
   picks a store, ``dispatch_job`` sends a WhatsApp message to the
   store owner's number with the file URL, spec summary, due-by and
   pickup code. The owner replies with ACCEPT / REJECT / QUERY to
   confirm they will fulfil the job.

2. **Inbound parser** — ``parse_store_reply`` takes a free-form
   WhatsApp message from a store owner and extracts a structured
   action (ACCEPT / REJECT / READY / DELIVERED / QUERY) plus an
   optional pickup code. ``apply_store_reply`` then transitions the
   matching job's state.

Why WhatsApp and not an HTTP agent: for the Thrissur 1-3 stores MVP,
WhatsApp messages with text replies are enough. No software install at
the store; the store owner just replies on their phone.

Status transitions driven from store replies:

    Paid     --ACCEPT--> Accepted      (pickup_ready_at unchanged)
    Accepted --READY---> Ready         (pickup_ready_at = now)
    Ready    --DELIVER-> Delivered     (delivered_at = now)
    *        --REJECT--> (caller re-routes via routing.engine)

Privacy note: the customer's tracker page only reveals the store name +
address once status='Ready' (see api.index._handle_track). This module
is what flips status from Accepted to Ready, so timing of that
transition is the source of truth for what the customer sees.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parser types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedReply:
    """A structured action extracted from a store-owner WhatsApp reply.

    ``pickup_code`` is the code the store owner referred to (e.g. they
    typed "READY P-7K2N"). It can be None if the message didn't include
    a code — the apply step then falls back to the most recently
    dispatched job for that store owner's phone.
    """
    action: str                       # ACCEPT | REJECT | READY | DELIVERED | QUERY
    pickup_code: str | None
    raw_text: str


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of applying a parsed store reply."""
    ok: bool
    job_id: str | None
    new_status: str | None
    message: str                      # human-readable summary for logs / replies


# ---------------------------------------------------------------------------
# DB protocol
# ---------------------------------------------------------------------------


class _SupabaseLike(Protocol):
    def table(self, name: str): ...  # noqa: D401, ANN201


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


_PICKUP_CODE_RE = r"P-[A-Z2-9]{4}"

_VERB_PATTERNS = [
    ("DELIVERED", re.compile(r"\bDELIVER(?:ED|Y)?\b", re.IGNORECASE)),
    ("READY",     re.compile(r"\bREADY\b",            re.IGNORECASE)),
    ("ACCEPT",    re.compile(r"\bACCEPT(?:ED)?\b",    re.IGNORECASE)),
    ("REJECT",    re.compile(r"\bREJECT(?:ED)?\b",    re.IGNORECASE)),
    ("QUERY",     re.compile(r"\bQUER(?:Y|IES)\b|\?", re.IGNORECASE)),
]


def parse_store_reply(text: str) -> ParsedReply | None:
    """Try to extract a structured action from a store-owner reply.

    Returns ``None`` if no recognised verb appears. Verb precedence
    when multiple match: DELIVERED > READY > ACCEPT > REJECT > QUERY.
    Covers messages like "ready and delivered P-7K2N" by treating as
    the further-along state.
    """
    if not isinstance(text, str) or not text.strip():
        return None

    action: str | None = None
    for verb, pat in _VERB_PATTERNS:
        if pat.search(text):
            action = verb
            break

    if action is None:
        return None

    code_match = re.search(_PICKUP_CODE_RE, text.upper())
    pickup_code = code_match.group(0) if code_match else None

    return ParsedReply(action=action, pickup_code=pickup_code, raw_text=text)


# ---------------------------------------------------------------------------
# Outbound dispatcher
# ---------------------------------------------------------------------------


def build_dispatch_message(
    pickup_code: str,
    customer_first_name: str | None,
    spec_summary: str,
    due_by: str,
    file_url: str,
) -> str:
    """Compose the WhatsApp body sent to the store owner."""
    name = customer_first_name or "Customer"
    return (
        "🖨️ *New Printosky job*\n\n"
        f"🎫 {pickup_code}\n"
        f"📦 {spec_summary}\n"
        f"🕐 Due {due_by}\n"
        f"👤 {name}\n\n"
        f"📎 {file_url}\n\n"
        "Reply *ACCEPT*, *REJECT* or *QUERY*."
    )


def dispatch_job(
    job_row: dict,
    partner_row: dict,
    file_url: str,
) -> bool:
    """Send the dispatch WhatsApp message to the store owner.

    Returns True on success, False if the partner has no
    ``dispatch_whatsapp`` configured or the send fails. Failure is
    logged but not raised — the routing decision is already recorded
    so retry/reroute is a separate concern.
    """
    dispatch_to = partner_row.get("dispatch_whatsapp")
    if not dispatch_to:
        logger.error(
            "dispatch_job: partner %s has no dispatch_whatsapp; cannot dispatch %s",
            partner_row.get("store_id"), job_row.get("job_id"),
        )
        return False

    body = build_dispatch_message(
        pickup_code=job_row.get("pickup_code") or "(no code)",
        customer_first_name=(job_row.get("customer_name") or "").split()[0] or None,
        spec_summary=_summarise_spec(job_row),
        due_by=job_row.get("due_by") or "today",
        file_url=file_url,
    )

    try:
        # Lazy import: avoid pulling whatsapp_notify at module load time
        # so a missing module here cannot break callers of store_dispatch.
        from whatsapp_notify import _send
        return bool(_send(dispatch_to, body))
    except Exception as e:
        logger.error("dispatch_job: send failed for %s: %s",
                     job_row.get("job_id"), e)
        return False


def _summarise_spec(job_row: dict) -> str:
    """Cheap human-readable summary, e.g. '12 pages × 1, colour, A4, spiral'."""
    parts: list[str] = []
    pages = job_row.get("page_count")
    copies = job_row.get("copies") or 1
    colour = (job_row.get("colour") or "").strip().lower()
    size = (job_row.get("size") or "A4").strip()
    finishing = (job_row.get("finishing") or "").strip()

    if pages:
        parts.append(f"{pages} pages × {copies}")
    elif copies and copies > 1:
        parts.append(f"{copies} copies")

    if colour in ("colour", "color"):
        parts.append("colour")
    elif colour in ("bw", "mono", "black"):
        parts.append("B&W")

    parts.append(size)
    if finishing and finishing.lower() not in ("none", ""):
        parts.append(finishing)
    return ", ".join(parts) if parts else "(no spec)"


# ---------------------------------------------------------------------------
# Apply parsed reply to a job
# ---------------------------------------------------------------------------


def _is_known_dispatch_phone(client: _SupabaseLike, sender_phone: str) -> dict | None:
    """Return the partner row whose ``dispatch_whatsapp`` matches sender,
    or None if no match. Phones compared after stripping non-digits."""
    digits = re.sub(r"\D", "", sender_phone or "")
    if not digits:
        return None
    try:
        rows = (
            client.table("partners")
            .select("store_id,dispatch_whatsapp,kyc_status,name")
            .execute()
        )
    except Exception as e:
        logger.error("_is_known_dispatch_phone: query failed: %s", e)
        return None

    for r in getattr(rows, "data", None) or []:
        candidate = re.sub(r"\D", "", r.get("dispatch_whatsapp") or "")
        if candidate and candidate == digits:
            return r
    return None


def _find_job_for_reply(
    client: _SupabaseLike,
    partner_row: dict,
    pickup_code: str | None,
) -> dict | None:
    """Look up the job a reply is acting on.

    With pickup_code: exact lookup, must match the partner's store_id.
    Without: fall back to the most recently dispatched job for the
    partner that hasn't been delivered yet.
    """
    store_id = partner_row.get("store_id")
    try:
        if pickup_code:
            rows = (
                client.table("jobs")
                .select("job_id,pickup_code,status,assigned_store_id,store_id,sender")
                .eq("pickup_code", pickup_code)
                .limit(1)
                .execute()
            )
            data = getattr(rows, "data", None) or []
            if not data:
                return None
            job = data[0]
            assigned = job.get("assigned_store_id") or job.get("store_id")
            if assigned and assigned != store_id:
                logger.warning(
                    "store_dispatch: %s tried to act on job %s assigned to %s",
                    store_id, pickup_code, assigned,
                )
                return None
            return job

        rows = (
            client.table("jobs")
            .select("job_id,pickup_code,status,assigned_store_id,store_id,sender")
            .eq("assigned_store_id", store_id)
            .neq("status", "Delivered")
            .order("received_at", desc=True)
            .limit(1)
            .execute()
        )
        data = getattr(rows, "data", None) or []
        return data[0] if data else None
    except Exception as e:
        logger.error("_find_job_for_reply: query failed: %s", e)
        return None


_TRANSITIONS = {
    # current_status, action  ->  new_status (None means idempotent no-op)
    ("Paid",      "ACCEPT"):    "Accepted",
    ("Printed",   "ACCEPT"):    "Accepted",
    ("Accepted",  "ACCEPT"):    None,
    ("Accepted",  "READY"):     "Ready",
    ("Paid",      "READY"):     "Ready",
    ("Printed",   "READY"):     "Ready",
    ("Ready",     "READY"):     None,
    ("Ready",     "DELIVERED"): "Delivered",
    ("Delivered", "DELIVERED"): None,
}


def apply_store_reply(
    client: _SupabaseLike,
    sender_phone: str,
    parsed: ParsedReply,
) -> ApplyResult:
    """Apply a parsed store-owner reply.

    Returns an ``ApplyResult`` with ``ok=False`` when the sender is not
    a known partner, the referenced job can't be found, or the
    transition is not allowed.
    """
    partner = _is_known_dispatch_phone(client, sender_phone)
    if not partner:
        return ApplyResult(False, None, None,
                           f"sender {sender_phone} not a known dispatch number")

    if parsed.action == "QUERY":
        return ApplyResult(True, None, None,
                           f"query from {partner.get('store_id')}: {parsed.raw_text!r}")

    if parsed.action == "REJECT":
        # State change is the caller's job (re-route via routing.engine).
        job = _find_job_for_reply(client, partner, parsed.pickup_code)
        if not job:
            return ApplyResult(False, None, None, "REJECT but no matching job")
        return ApplyResult(True, job.get("job_id"), "Rejected",
                           f"{partner.get('store_id')} rejected {job.get('pickup_code')}")

    job = _find_job_for_reply(client, partner, parsed.pickup_code)
    if not job:
        return ApplyResult(
            False, None, None,
            f"{parsed.action} but no matching job for {partner.get('store_id')}",
        )

    current = (job.get("status") or "").strip()
    transition_key = (current, parsed.action)
    new_status = _TRANSITIONS.get(transition_key)
    if new_status is None:
        if transition_key in _TRANSITIONS:
            return ApplyResult(True, job.get("job_id"), current,
                               f"{parsed.action} idempotent (already {current})")
        return ApplyResult(False, job.get("job_id"), current,
                           f"transition {current!r} --{parsed.action}--> not allowed")

    update_payload: dict = {"status": new_status}
    if new_status == "Ready":
        update_payload["pickup_ready_at"] = datetime.now(timezone.utc).isoformat()
    elif new_status == "Delivered":
        update_payload["delivered_at"] = datetime.now(timezone.utc).isoformat()

    try:
        client.table("jobs").update(update_payload).eq(
            "job_id", job["job_id"]
        ).execute()
    except Exception as e:
        logger.error("apply_store_reply: db update failed: %s", e)
        return ApplyResult(False, job.get("job_id"), current,
                           f"db update failed: {e}")

    return ApplyResult(True, job.get("job_id"), new_status,
                       f"{partner.get('store_id')}: {current} -> {new_status}")
