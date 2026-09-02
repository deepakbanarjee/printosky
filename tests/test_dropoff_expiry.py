"""
Drop-off bookings — a job that exists before the item does (plan §4.8).

A customer books lamination online and brings the paper in afterwards. Until it
arrives the job is real but not startable, and if it never arrives the booking
has to close itself — after a reminder, never before one.

The four rules under test, in the order of how badly breaking each would go:

  1. A reminder always comes before a cancellation.
  2. Money stops the sweep — a paid booking goes to a human.
  3. An arrived item is untouchable.
  4. Cancelling says why.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import dropoff

NOW = datetime(2026, 9, 10, 10, 0, 0)


def ago(hours):
    return (NOW - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def booking(**kw):
    row = {"job_id": "OSKY-1", "service_kind": "laminate",
           "service_type": "Lamination", "status": "Queued",
           "sender": "919495706405", "received_at": ago(2)}
    row.update(kw)
    return row


# ── What counts as a booking ──────────────────────────────────────────────────

def test_a_service_job_with_no_item_is_a_dropoff():
    assert dropoff.is_dropoff(booking())


def test_a_print_job_is_never_a_dropoff():
    """A print job's item is its file, and the file is already here."""
    assert not dropoff.is_dropoff(booking(service_kind=None))
    assert not dropoff.is_dropoff(booking(service_kind=""))


def test_an_arrived_item_is_no_longer_a_dropoff():
    assert not dropoff.is_dropoff(booking(item_received_at=ago(1)))


@pytest.mark.parametrize("status", ["Ready", "Completed", "Cancelled", "Printed"])
def test_a_booking_that_moved_on_is_not_swept(status):
    assert not dropoff.is_dropoff(booking(status=status))
    assert dropoff.classify(booking(status=status), NOW) == dropoff.NOT_DROPOFF


@pytest.mark.parametrize("status", ["Draft", "Queued", "Pending", "Received",
                                    "queued", "DRAFT"])
def test_every_open_status_is_swept(status):
    assert dropoff.is_dropoff(booking(status=status))


# ── Rule 1: the reminder always comes first ───────────────────────────────────

def test_a_fresh_booking_is_left_alone():
    assert dropoff.classify(booking(received_at=ago(2)), NOW) == dropoff.WAITING


def test_a_day_old_booking_earns_the_reminder():
    assert dropoff.classify(booking(received_at=ago(25)), NOW) == dropoff.REMIND


def test_the_reminder_is_not_sent_twice():
    row = booking(received_at=ago(50), dropoff_reminded_at=ago(26))
    assert dropoff.classify(row, NOW) == dropoff.REMINDED


def test_an_ancient_booking_that_was_never_reminded_is_reminded_not_cancelled():
    """The rule that matters most. A cron outage delays the cancellation; it
    does not skip the warning the customer was promised."""
    row = booking(received_at=ago(24 * 30))          # a month old
    assert dropoff.classify(row, NOW) == dropoff.REMIND


def test_only_a_reminded_booking_can_expire():
    reminded = booking(received_at=ago(100), dropoff_reminded_at=ago(76))
    assert dropoff.classify(reminded, NOW) == dropoff.EXPIRE
    not_reminded = booking(received_at=ago(100))
    assert dropoff.classify(not_reminded, NOW) != dropoff.EXPIRE


def test_the_expiry_boundary_is_the_owners_three_days():
    assert dropoff.DROPOFF_EXPIRY_DAYS == 3
    just_under = booking(received_at=ago(71), dropoff_reminded_at=ago(47))
    just_over  = booking(received_at=ago(73), dropoff_reminded_at=ago(49))
    assert dropoff.classify(just_under, NOW) == dropoff.REMINDED
    assert dropoff.classify(just_over, NOW) == dropoff.EXPIRE


def test_the_reminder_boundary_is_a_day():
    assert dropoff.DROPOFF_REMINDER_HOURS == 24
    assert dropoff.classify(booking(received_at=ago(23)), NOW) == dropoff.WAITING
    assert dropoff.classify(booking(received_at=ago(24)), NOW) == dropoff.REMIND


# ── Rule 2: money stops the sweep ─────────────────────────────────────────────

def test_a_paid_booking_is_never_auto_cancelled():
    """Refunds, disputes and part payments are not a nightly script's call."""
    row = booking(received_at=ago(100), dropoff_reminded_at=ago(76),
                  amount_collected=200)
    assert dropoff.classify(row, NOW) == dropoff.NEEDS_HUMAN


def test_a_part_payment_counts_as_money():
    row = booking(received_at=ago(100), dropoff_reminded_at=ago(76),
                  amount_partial=50)
    assert dropoff.classify(row, NOW) == dropoff.NEEDS_HUMAN


def test_a_zero_payment_is_not_money():
    row = booking(received_at=ago(100), dropoff_reminded_at=ago(76),
                  amount_collected=0, amount_partial=None)
    assert dropoff.classify(row, NOW) == dropoff.EXPIRE


def test_the_paid_alert_names_the_jobs_and_the_amounts():
    rows = [booking(job_id="OSKY-A", amount_collected=200),
            booking(job_id="OSKY-B", amount_partial=75)]
    text = dropoff.format_needs_human(rows)
    assert "2 paid drop-off bookings" in text
    assert "OSKY-A" in text and "Rs.200" in text
    assert "OSKY-B" in text and "Rs.75" in text
    assert "NOT auto-cancelled" in text


def test_the_paid_alert_is_silent_when_there_are_none():
    """A section that speaks every day is one people stop reading."""
    assert dropoff.format_needs_human([]) == ""
    assert dropoff.format_needs_human(None) == ""


# ── Rule 3: an arrived item is untouchable ────────────────────────────────────

def test_an_arrived_item_is_never_reminded_or_cancelled():
    for age in (2, 25, 100, 24 * 365):
        row = booking(received_at=ago(age), item_received_at=ago(1))
        assert dropoff.classify(row, NOW) == dropoff.ARRIVED


def test_an_arrived_item_appears_in_no_sweep_bucket():
    plan = dropoff.sweep([booking(received_at=ago(100), item_received_at=ago(1))], NOW)
    assert all(not v for v in plan.values())


# ── Rule 4: cancelling says why ───────────────────────────────────────────────

def test_the_expiry_reason_explains_itself_at_the_counter():
    reason = dropoff.expiry_reason(booking())
    assert "3 days" in reason
    assert "never reached the counter" in reason
    assert "Reminder was sent first" in reason


def test_the_cancelled_status_matches_the_one_already_in_use():
    """The live jobs table spells it `Cancelled`; nothing here invents a new
    status vocabulary for reports to miss."""
    assert dropoff.CANCELLED == "Cancelled"


def test_the_customer_messages_say_what_happens_next():
    remind = dropoff.reminder_message(booking(received_at=ago(25)), NOW)
    assert "OSKY-1" in remind and "Lamination" in remind
    assert "cancelled automatically" in remind

    expire = dropoff.expiry_message(booking())
    assert "cancelled" in expire.lower()
    assert "Nothing has been charged" in expire
    assert "Book again" in expire


def test_the_reminder_says_how_long_is_left():
    row = booking(received_at=ago(25))
    assert "in about 47 hours" in dropoff.reminder_message(row, NOW)


def test_hours_left_never_goes_negative():
    assert dropoff.hours_left(booking(received_at=ago(1000)), NOW) == 0


def test_hours_left_is_none_without_a_booking_time():
    assert dropoff.hours_left(booking(received_at=None), NOW) is None


# ── Failure modes ─────────────────────────────────────────────────────────────

def test_a_booking_with_no_time_is_left_alone_not_guessed_at():
    """A wrong age is worse than no age — the rule store_digest follows for a
    finishing transfer with no send time."""
    assert dropoff.classify(booking(received_at=None), NOW) == dropoff.WAITING
    assert dropoff.classify(booking(received_at="last tuesday"), NOW) == dropoff.WAITING


def test_the_sweep_never_raises_on_junk():
    junk = [{}, {"service_kind": "laminate"}, {"service_kind": None},
            booking(received_at=12345), booking(amount_collected="lots")]
    plan = dropoff.sweep(junk, NOW)
    assert isinstance(plan, dict)


def test_the_sweep_partitions_without_overlap():
    rows = [
        booking(job_id="fresh",    received_at=ago(2)),
        booking(job_id="remind",   received_at=ago(25)),
        booking(job_id="reminded", received_at=ago(50), dropoff_reminded_at=ago(26)),
        booking(job_id="expire",   received_at=ago(100), dropoff_reminded_at=ago(76)),
        booking(job_id="paid",     received_at=ago(100), dropoff_reminded_at=ago(76),
                amount_collected=200),
        booking(job_id="arrived",  received_at=ago(100), item_received_at=ago(1)),
        booking(job_id="print",    service_kind=None),
    ]
    plan = dropoff.sweep(rows, NOW)
    assert [r["job_id"] for r in plan[dropoff.REMIND]] == ["remind"]
    assert [r["job_id"] for r in plan[dropoff.EXPIRE]] == ["expire"]
    assert [r["job_id"] for r in plan[dropoff.NEEDS_HUMAN]] == ["paid"]
    assert [r["job_id"] for r in plan[dropoff.WAITING]] == ["fresh"]
    seen = [r["job_id"] for v in plan.values() for r in v]
    assert len(seen) == len(set(seen)), "a row landed in two buckets"


def test_the_sweep_does_not_mutate_its_input():
    row = booking(received_at=ago(25))
    before = dict(row)
    dropoff.sweep([row], NOW)
    assert row == before


@pytest.mark.parametrize("ts", [
    "2026-09-09 09:00:00", "2026-09-09T09:00:00", "2026-09-09T09:00:00Z",
    "2026-09-09T09:00:00+05:30", "2026-09-09 09:00", "2026-09-09",
])
def test_both_databases_timestamp_shapes_parse(ts):
    """SQLite stores space-separated; Supabase returns ISO with a zone."""
    assert dropoff._as_datetime(ts) is not None
