"""
SERVICE JOBS — the decisions, once, for both backends
=====================================================

A post-press service (lamination, binding, scanning, a photocopy) can now be
booked from two places:

  * the store PC — `print_server` `/new-service`, writing local SQLite;
  * order-v2 staff mode — the Vercel API `/order/staff-service`, writing
    Supabase, so staff can work off-site (owner, 2026-09-02).

The storage differs. **Nothing else may.** A price, a deposit, a status or a
job id that depends on which machine the counter happened to use is a bug that
would take months to notice — the exact shape of the konica_jobs split B-10
found. So every decision lives here, both callers import it, and
tests/test_service_parity.py asserts the two paths agree.

What stays with each caller: the INSERT itself, and the job-id sequence (SQLite
counts today's rows; the cloud cannot see a store's local ids, so it uses the
same id shape with a random suffix, exactly as /order/staff-create already does).
"""

from __future__ import annotations

import json
from typing import Any

#: Above this quote, money has to change hands before the work starts.
#: At or below it, services are paid on collection (owner decision, B8).
SERVICE_DEPOSIT_THRESHOLD = 500.0

#: The share of the quote taken as that deposit.
SERVICE_DEPOSIT_FRACTION = 0.5

#: A service job with the deposit met is queued for the shop to work on; one
#: without is a Draft, which is not work and not owed.
STATUS_QUEUED = "Queued"
STATUS_DRAFT  = "Draft"

#: The meta keys /service-quote understands, by type. Anything else is dropped
#: rather than passed through, so a typo cannot become a silent meta key that
#: the rate card then defaults away.
META_INTS  = ("sheets", "copies", "passes", "qty", "pages")
META_BOOLS = ("is_student", "is_colour", "with_our_job", "urgent")
META_TEXTS = ("paper_size", "colour", "sides", "lam_type", "binding",
              "project_cover", "unit", "language", "description",
              "manual_price")

#: Payment modes a counter may record. Anything else falls back to Cash rather
#: than being stored as a mode no report knows how to sum.
PAYMENT_MODES = ("Cash", "UPI", "Online")
DEFAULT_PAYMENT_MODE = "Cash"


def amount_or_none(value) -> float | None:
    """A rupee amount from whatever the request carried, or None.

    Blank, absent and unparseable all mean "the counter did not type one",
    which is different from zero — zero is a decision to charge nothing.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def payment_mode(raw) -> str:
    text = str(raw or "").strip().title()
    if text.upper() == "UPI":
        text = "UPI"
    return text if text in PAYMENT_MODES else DEFAULT_PAYMENT_MODE


def deposit_for(total) -> float:
    """Deposit due before the work starts, or 0 when payment is on collection."""
    try:
        total = float(total or 0)
    except (TypeError, ValueError):
        return 0.0
    if total <= SERVICE_DEPOSIT_THRESHOLD:
        return 0.0
    return round(total * SERVICE_DEPOSIT_FRACTION, 2)


def service_status(amount_quoted, paid_now, override_reason: str = "") -> str:
    """`Queued` once the deposit is met (or waived with a reason), else `Draft`.

    An override needs a *reason*, not a flag — a waiver nobody can explain later
    is how a job ends up worked on and unpaid with no one accountable.
    """
    try:
        paid = float(paid_now or 0)
    except (TypeError, ValueError):
        paid = 0.0
    due = deposit_for(amount_quoted)
    met = paid >= due or bool(str(override_reason or "").strip())
    return STATUS_QUEUED if met else STATUS_DRAFT


def meta_from_params(params: dict) -> dict:
    """The rate-card meta from a flat query-string/form mapping.

    Values arrive as strings from a URL and as real types from JSON; both are
    accepted.

    A quantity that is not a number **raises**. Quoting it as the default would
    bill the wrong number quietly, and a non-numeric sheet count is a UI bug,
    not a customer choice — the caller turns this into a visible error.
    """
    def one(key):
        v = params.get(key)
        return v[0] if isinstance(v, (list, tuple)) and v else v

    meta: dict[str, Any] = {}
    for key in META_INTS:
        raw = one(key)
        if raw in (None, ""):
            continue
        try:
            meta[key] = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"{key}={raw!r} is not a number") from None
    for key in META_BOOLS:
        raw = one(key)
        if raw in (None, ""):
            continue
        meta[key] = str(raw).strip().lower() in ("1", "true", "yes", "on")
    for key in META_TEXTS:
        raw = one(key)
        if raw in (None, ""):
            continue
        meta[key] = str(raw)
    return meta


def photocopy_meta(body: dict) -> dict:
    """The rate-card meta for a counter photocopy.

    A photocopy is priced as a print of the same sheets on the same machine —
    same paper, same toner — so it goes through the one rate card rather than a
    second table that would drift from it.
    """
    def _int(key, default):
        try:
            return max(1, int(body.get(key) or default))
        except (TypeError, ValueError):
            return default

    return {
        "sheets":     _int("pages", 1),
        "copies":     _int("copies", 1),
        "colour":     body.get("colour", "bw"),
        "sides":      body.get("sides", "ss"),
        "paper_size": (body.get("paper_size") or "A4").upper(),
        "is_student": bool(body.get("is_student", False)),
    }


def resolve_amount(quoted: float | None, typed: float | None) -> dict:
    """Which number gets billed, and whether that was an override.

    A typed amount always wins — a discount or a miscount has to be possible —
    but it is recorded against the quote so the two are visible side by side
    rather than one silently replacing the other. When neither exists the
    caller must refuse rather than file a ₹0 job.
    """
    if typed is not None and typed > 0:
        amount = typed
    elif quoted is not None:
        amount = quoted
    else:
        return {"amount": None, "quoted": None, "overridden": False,
                "billable": False}
    overridden = (quoted is not None and typed is not None
                  and typed > 0 and typed != quoted)
    return {"amount": amount,
            "quoted": quoted if quoted is not None else amount,
            "overridden": overridden,
            "billable": True}


# ── Part payment on a booking (N1) ────────────────────────────────────────────
#
# A service over SERVICE_DEPOSIT_THRESHOLD takes half up front, and from
# 2026-09-02 that half can be paid online at booking time rather than only at
# the counter. Two things make this different from every other payment in the
# system, and both are why the accounting lives here rather than in the webhook:
#
#   * **Payments accumulate.** A deposit then a balance is two payments against
#     one job. `db_cloud.update_job_paid()` OVERWRITES `amount_collected`, which
#     is correct for a print job (one payment, in full) and would silently lose
#     the deposit here.
#   * **Paying does not make it `Paid`.** For a print job `Paid` means "pull it
#     and print it". A booking whose item has not arrived is not printable, not
#     startable and not finished; its payment state lives in the amounts, and
#     its work state stays `Queued`. Setting `Paid` would put a job with no file
#     into the puller's own vocabulary.


def apply_payment(row: dict, incoming) -> dict:
    """What a booking looks like after `incoming` rupees arrive.

    Pure. Returns the fields to write plus what the customer should be told:

        amount_collected  the new running total, never a replacement
        status            Queued once the deposit is met, else unchanged
        balance           what is still owed on collection
        deposit_met       whether work can start
        fully_paid        nothing left to pay

    A payment that is not a positive number changes nothing — a webhook with a
    junk amount must not zero a booking that was already paid.
    """
    already = amount_or_none(row.get("amount_collected")) or 0.0
    quoted = amount_or_none(row.get("amount_quoted")) or 0.0
    add = amount_or_none(incoming) or 0.0
    if add <= 0:
        add = 0.0

    total = round(already + add, 2)
    due = deposit_for(quoted)
    status = service_status(quoted, total, row.get("override_reason") or "")

    return {
        "amount_collected": total,
        "status": status,
        "balance": round(max(0.0, quoted - total), 2),
        "deposit_due": due,
        "deposit_met": total >= due,
        "fully_paid": quoted > 0 and total >= quoted,
        "added": add,
    }


def payable_now(row: dict) -> float:
    """What a customer can usefully pay against this booking right now.

    The deposit if one is owed and unpaid, otherwise the outstanding balance.
    Zero means there is nothing to collect and no link should be made — an
    empty payment link is a dead end the customer has to be talked out of.
    """
    quoted = amount_or_none(row.get("amount_quoted")) or 0.0
    paid = amount_or_none(row.get("amount_collected")) or 0.0
    outstanding = round(max(0.0, quoted - paid), 2)
    if outstanding <= 0:
        return 0.0
    due = deposit_for(quoted)
    if due > 0 and paid < due:
        return round(due - paid, 2)
    return outstanding


def service_meta_json(meta: dict) -> str:
    """`service_meta` as stored. Sorted so two identical bookings compare equal."""
    return json.dumps(meta or {}, sort_keys=True)
