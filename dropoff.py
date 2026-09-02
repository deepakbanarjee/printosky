"""
DROP-OFF BOOKINGS — a job that exists before the item does
===========================================================

A customer books lamination, foiling or binding on the site and brings the
paper in afterwards (plan §4.8). That inverts the usual order: the job row
exists while the thing it describes is still in someone's bag.

`item_received_at` is the whole distinction:

    NULL      booked, not in hand. NOT work-ready. Counts as revenue owed,
              never as work queued, and expires if the item never arrives.
    a time    the item is on the counter. An ordinary service job from here on,
              and nothing in this module ever touches it again.

Staff booking a service at the counter set it immediately — the customer is
standing there holding the paper. Only an online booking starts NULL.

Four rules this module will not bend
------------------------------------

**A reminder always comes before a cancellation.** The plan promises one, and a
booking cancelled without it is a customer who was never told. `expire()` skips
anything `dropoff_reminded_at` does not cover, however old it is — a missed
cron run delays a cancellation, it does not skip the warning.

**Money stops the sweep.** A booking with anything collected is never
auto-cancelled; it is handed to a human. Refunds, disputes and part payments are
not decisions a nightly script should make on its own.

**An arrived item is untouchable.** Once `item_received_at` is set, this module
has no opinion about the job at all.

**Cancelling says why.** `Cancelled` with a reason in `notes`, never a delete
and never a bare status change — a job that vanishes is a job nobody can explain
to the customer who asks about it next week.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta

#: Hours after booking before the customer is reminded to bring the item in.
DROPOFF_REMINDER_HOURS = 24

#: Days after booking before an un-received booking is cancelled.
#: Owner decision N3 (2026-08-30): 3 days, WhatsApp reminder first.
DROPOFF_EXPIRY_DAYS = 3

#: The statuses a booking can still be waiting in. A booking that has moved on
#: (Ready, Completed, Cancelled) is not the sweep's business.
OPEN_STATUSES: frozenset[str] = frozenset({"draft", "queued", "pending", "received"})

#: What an expired booking becomes. Matches the spelling already in the live
#: `jobs` table; nothing here invents a new status vocabulary.
CANCELLED = "Cancelled"

#: Classifications returned by `classify`.
WAITING     = "waiting"       # too new to do anything about
REMIND      = "remind"        # old enough for the reminder, not yet sent
REMINDED    = "reminded"      # reminder sent, not yet old enough to cancel
EXPIRE      = "expire"        # reminded, and out of time
NEEDS_HUMAN = "needs_human"   # out of time but money is on it
ARRIVED     = "arrived"       # the item is here
NOT_DROPOFF = "not_a_dropoff"  # not a booking at all


def _as_datetime(value) -> datetime | None:
    """Parse the timestamp shapes this repo stores, or None.

    Deliberately the same permissive parse as store_digest._as_datetime: rows
    reach here from SQLite (space-separated) and from Supabase (ISO with a zone).
    """
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip().replace("T", " ")
    if text.endswith("Z"):
        text = text[:-1]
    text = text.split("+")[0].split(".")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _money(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def is_dropoff(row: Mapping) -> bool:
    """A service booking whose item has not arrived.

    A print job is never a drop-off: it has a file, and the file is the thing.
    """
    if not str(row.get("service_kind") or "").strip():
        return False
    if row.get("item_received_at"):
        return False
    return str(row.get("status") or "").strip().lower() in OPEN_STATUSES


def paid_amount(row: Mapping) -> float:
    """Money already taken against this booking, from either column."""
    return _money(row.get("amount_collected")) + _money(row.get("amount_partial"))


def classify(row: Mapping, now: datetime | None = None,
             reminder_hours: int = DROPOFF_REMINDER_HOURS,
             expiry_days: int = DROPOFF_EXPIRY_DAYS) -> str:
    """What, if anything, this row needs from the sweep today."""
    now = now or datetime.now()

    if str(row.get("service_kind") or "").strip() == "":
        return NOT_DROPOFF
    if row.get("item_received_at"):
        return ARRIVED
    if str(row.get("status") or "").strip().lower() not in OPEN_STATUSES:
        return NOT_DROPOFF

    booked = _as_datetime(row.get("received_at"))
    if booked is None:
        # No booking time means no age, and an age is what every decision below
        # rests on. Left alone rather than guessed at — the same rule
        # store_digest follows for a finishing transfer with no send time.
        return WAITING

    age_hours = (now - booked).total_seconds() / 3600.0
    reminded = _as_datetime(row.get("dropoff_reminded_at")) is not None

    if not reminded:
        return REMIND if age_hours >= reminder_hours else WAITING

    if age_hours < expiry_days * 24:
        return REMINDED

    # Out of time. Money changes what cancelling means, so it stops here.
    return NEEDS_HUMAN if paid_amount(row) > 0 else EXPIRE


def sweep(rows: Iterable[Mapping], now: datetime | None = None,
          reminder_hours: int = DROPOFF_REMINDER_HOURS,
          expiry_days: int = DROPOFF_EXPIRY_DAYS) -> dict:
    """Partition today's bookings into what to do about each.

    Pure: decides, touches nothing. The caller sends the messages and writes the
    rows, which is what makes every rule above testable without a database.
    """
    now = now or datetime.now()
    out: dict[str, list] = {REMIND: [], EXPIRE: [], NEEDS_HUMAN: [],
                            REMINDED: [], WAITING: []}
    for row in rows or []:
        verdict = classify(row, now, reminder_hours, expiry_days)
        if verdict in out:
            out[verdict].append(dict(row))
    return out


def hours_left(row: Mapping, now: datetime | None = None,
               expiry_days: int = DROPOFF_EXPIRY_DAYS) -> int | None:
    """Whole hours until this booking expires, or None if it has no age."""
    now = now or datetime.now()
    booked = _as_datetime(row.get("received_at"))
    if booked is None:
        return None
    deadline = booked + timedelta(days=expiry_days)
    return max(0, int((deadline - now).total_seconds() // 3600))


def reminder_message(row: Mapping, now: datetime | None = None,
                     expiry_days: int = DROPOFF_EXPIRY_DAYS) -> str:
    """The WhatsApp nudge. Says what was booked, and what happens if nothing does."""
    left = hours_left(row, now, expiry_days)
    when = (f"in about {left} hour{'s' if left != 1 else ''}"
            if left is not None else f"after {expiry_days} days")
    service = row.get("service_type") or row.get("service_kind") or "your booking"
    return (
        f"👋 Hi! You booked *{service}* with Printosky "
        f"({row.get('job_id')}), but we have not received your item yet.\n\n"
        f"Please drop it at the shop and we will start right away. "
        f"If it does not reach us {when}, the booking is cancelled automatically "
        f"— just book again whenever you are ready. 🙏"
    )


def expiry_reason(row: Mapping, expiry_days: int = DROPOFF_EXPIRY_DAYS) -> str:
    """Why this booking was cancelled, in the words the customer would get.

    Written into `notes`, because a status with no explanation is a job the
    counter cannot answer a question about.
    """
    return (f"Auto-cancelled after {expiry_days} days: booked online but the "
            f"item never reached the counter. Reminder was sent first.")


def expiry_message(row: Mapping, expiry_days: int = DROPOFF_EXPIRY_DAYS) -> str:
    """The cancellation the customer receives. Not an apology, not a scolding."""
    service = row.get("service_type") or row.get("service_kind") or "your booking"
    return (
        f"Your *{service}* booking ({row.get('job_id')}) has been cancelled — "
        f"we did not receive your item within {expiry_days} days.\n\n"
        "Nothing has been charged. Book again any time and we will be ready. 🙏"
    )


def format_needs_human(rows, expiry_days: int = DROPOFF_EXPIRY_DAYS) -> str:
    """The owner alert for paid bookings that ran out of time, or "".

    Silent when there are none — a section that speaks every day is one people
    stop reading (the rule store_digest's overdue line follows).
    """
    rows = list(rows or [])
    if not rows:
        return ""
    head = (f"⚠️ {len(rows)} paid drop-off booking"
            f"{'s' if len(rows) != 1 else ''} past {expiry_days} days with no "
            "item — NOT auto-cancelled, because money has been taken:")
    lines = [f"  {r.get('job_id')} — {r.get('service_type') or r.get('service_kind')}"
             f", Rs.{paid_amount(r):.0f} paid" for r in rows]
    return "\n".join([head, *lines])
