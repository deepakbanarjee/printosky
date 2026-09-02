"""
Part payment on a booking (N1) — half up front, online.

A service over ₹500 takes 50% before the work starts. Until now that could only
be paid at the counter, which meant an abandoned online booking cost the shop a
slot and the customer nothing. Paying it at booking time is what reverses that
(plan §4.8).

Two things make this unlike every other payment in the system, and both are why
the arithmetic lives in `service_jobs` rather than in the webhook:

  * **Payments accumulate.** A deposit then a balance is two payments against
    one job. `db_cloud.update_job_paid()` overwrites `amount_collected` — right
    for a print job, and it would silently lose the deposit here.
  * **Paying does not make it `Paid`.** For a print job that status means "pull
    it and print it". A booking whose item has not arrived is not printable.
"""

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import service_jobs

HANDLERS = ROOT / "api" / "handlers_order.py"
INDEX = ROOT / "api" / "index.py"


def _src(p):
    return Path(p).read_text(encoding="utf-8-sig")


def _fn(source, name):
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    raise AssertionError(f"{name} not found")


def booking(quoted=1000.0, collected=None, **kw):
    row = {"job_id": "OSKY-1", "service_kind": "bind",
           "service_type": "Binding only", "status": "Draft",
           "amount_quoted": quoted, "amount_collected": collected}
    row.update(kw)
    return row


# ── The arithmetic ────────────────────────────────────────────────────────────

def test_a_deposit_is_half_above_the_threshold():
    assert service_jobs.payable_now(booking(1000)) == 500.0
    assert service_jobs.deposit_for(1000) == 500.0


def test_below_the_threshold_the_whole_amount_is_payable():
    """There is no deposit under ₹500 — the payable amount is the bill."""
    assert service_jobs.deposit_for(400) == 0.0
    assert service_jobs.payable_now(booking(400)) == 400.0


def test_payments_accumulate_rather_than_replace():
    """The bug this whole design avoids: a balance payment overwriting the
    deposit and losing ₹500."""
    after_deposit = service_jobs.apply_payment(booking(1000), 500)
    assert after_deposit["amount_collected"] == 500.0
    after_balance = service_jobs.apply_payment(
        booking(1000, collected=500), 500)
    assert after_balance["amount_collected"] == 1000.0
    assert after_balance["fully_paid"] is True


def test_the_deposit_unblocks_the_work():
    assert booking(1000)["status"] == "Draft"
    assert service_jobs.apply_payment(booking(1000), 500)["status"] == "Queued"


def test_a_short_payment_does_not():
    assert service_jobs.apply_payment(booking(1000), 499)["status"] == "Draft"


def test_the_balance_is_what_is_still_owed():
    assert service_jobs.apply_payment(booking(1000), 500)["balance"] == 500.0
    assert service_jobs.apply_payment(booking(1000, 500), 500)["balance"] == 0.0


def test_paying_more_than_the_quote_never_goes_negative():
    r = service_jobs.apply_payment(booking(1000), 1500)
    assert r["balance"] == 0.0 and r["fully_paid"] is True


def test_a_junk_amount_changes_nothing():
    """A webhook with an unusable amount must not zero a booking that was paid."""
    for bad in (None, "", "lots", -50, 0):
        r = service_jobs.apply_payment(booking(1000, collected=500), bad)
        assert r["amount_collected"] == 500.0, bad
        assert r["added"] == 0.0, bad


def test_nothing_left_to_pay_is_zero_not_a_link():
    """A zero-rupee payment link is a dead end the customer has to be talked
    out of."""
    assert service_jobs.payable_now(booking(1000, collected=1000)) == 0.0
    assert service_jobs.payable_now(booking(400, collected=400)) == 0.0


def test_after_the_deposit_the_payable_amount_is_the_balance():
    assert service_jobs.payable_now(booking(1000, collected=500)) == 500.0


def test_a_part_paid_deposit_still_asks_only_for_the_rest_of_it():
    assert service_jobs.payable_now(booking(1000, collected=200)) == 300.0


# ── The link endpoint ─────────────────────────────────────────────────────────

def test_the_endpoint_is_public_like_the_rest_of_the_order_path():
    """A customer paying their own booking is not a staff action."""
    body = _fn(_src(HANDLERS), "_handle_order_booking_payment")
    assert "_acad_auth_staff" not in body


def test_the_endpoint_records_nothing():
    """Money is recorded when it arrives, by the webhook — never on the
    optimistic assumption that a link was opened."""
    body = _fn(_src(HANDLERS), "_handle_order_booking_payment")
    for verb in (".insert(", ".update(", ".upsert("):
        assert verb not in body, verb


def test_a_print_job_cannot_be_routed_through_it():
    """It would offer a 'deposit' on something that has never had one."""
    body = _fn(_src(HANDLERS), "_handle_order_booking_payment")
    assert "is not a service booking" in body


def test_a_finished_or_cancelled_booking_is_refused():
    body = _fn(_src(HANDLERS), "_handle_order_booking_payment")
    assert '("cancelled", "completed")' in body


def test_the_amount_comes_from_the_booking_not_the_request():
    """A stale page must not be able to ask for the wrong number, and a
    customer must not be able to name their own price."""
    body = _fn(_src(HANDLERS), "_handle_order_booking_payment")
    assert "service_jobs.payable_now(row)" in body
    assert 'data.get("amount")' not in body


def test_the_customer_may_choose_to_pay_in_full():
    body = _fn(_src(HANDLERS), "_handle_order_booking_payment")
    assert 'data.get("full")' in body


def test_a_provider_failure_says_pay_at_the_counter():
    """Never hand back a broken link dressed as a working one."""
    body = _fn(_src(HANDLERS), "_handle_order_booking_payment")
    assert "pay at the counter" in body
    assert "502" in body


# ── The webhook ───────────────────────────────────────────────────────────────

def test_a_booking_takes_a_different_path_from_a_print_job():
    body = _fn(_src(INDEX), "_process_razorpay_payment")
    assert "_process_service_payment(booking, payment)" in body
    branch = body.index("_process_service_payment")
    assert branch < body.index("update_job_paid(ref_id"), (
        "the branch must be taken BEFORE update_job_paid overwrites the total")


def test_the_print_path_is_untouched():
    """Rule 1: a print payment must behave exactly as it did."""
    body = _fn(_src(INDEX), "_process_razorpay_payment")
    assert "update_job_paid(ref_id, amount, method, pay_id)" in body
    assert "send_payment_confirmed(job[\"sender\"], ref_id, amount)" in body


def test_the_service_path_never_writes_status_paid():
    """`Paid` is the puller's vocabulary. A booking with no file has no business
    in it, and its item may not even be in the shop yet."""
    body = _fn(_src(INDEX), "_process_service_payment")
    assert '"Paid"' not in body
    assert '"status":              result["status"]' in body


def test_the_service_path_deduplicates_on_the_payment_not_the_event():
    """Razorpay fires BOTH payment_link.paid and payment.captured for one
    payment-link payment. They are separate events with separate ids, so the
    caller's event-level guard lets both through — harmless when the write is
    an overwrite, and a double count when it accumulates."""
    body = _fn(_src(INDEX), "_process_service_payment")
    assert '_mark_webhook_processed(pay_id, "razorpay_service_payment")' in body


def test_a_failed_write_alerts_because_the_money_already_arrived():
    """The silence that ends with a customer being asked to pay twice."""
    body = _fn(_src(INDEX), "_process_service_payment")
    assert "_alert_ops(" in body
    assert "NOT recorded" in body
    assert "Record it by hand" in body


def test_the_customer_is_told_what_is_still_due():
    body = _fn(_src(INDEX), "_process_service_payment")
    assert "Deposit received" in body and "due when you collect" in body
    assert "Paid in full" in body


def test_staff_are_told_the_item_is_not_here_yet():
    """A payment is not a reason to start looking for work that has not arrived."""
    body = _fn(_src(INDEX), "_process_service_payment")
    assert "Item not yet received." in body


def test_the_webhook_uses_the_shared_arithmetic():
    body = _fn(_src(INDEX), "_process_service_payment")
    assert "service_jobs.apply_payment(job, payment.get(\"amount\"))" in body


def test_the_payment_is_recorded_in_the_notes_with_its_id():
    """A payment nobody can trace back to Razorpay is one nobody can refund."""
    body = _fn(_src(INDEX), "_process_service_payment")
    assert "Paid Rs." in body and "pay_id" in body


# ── The interaction with the drop-off sweep ───────────────────────────────────

def test_a_paid_booking_is_never_auto_cancelled():
    """Once money is on it, abandonment is a person's decision — which is also
    what makes taking the deposit worth doing."""
    import dropoff
    from datetime import datetime, timedelta
    now = datetime(2026, 9, 10, 10, 0)
    old = (now - timedelta(hours=100)).strftime("%Y-%m-%d %H:%M:%S")
    reminded = (now - timedelta(hours=76)).strftime("%Y-%m-%d %H:%M:%S")
    paid = booking(1000, collected=500, status="Queued",
                   received_at=old, dropoff_reminded_at=reminded)
    assert dropoff.classify(paid, now) == dropoff.NEEDS_HUMAN


# ── The customer's step ───────────────────────────────────────────────────────

def _order_ui():
    return (ROOT / "website" / "order" / "order-ui.js").read_text(encoding="utf-8")


def test_the_pay_step_is_offered_only_when_a_deposit_is_due():
    js = _order_ui()
    assert "function bookingPayHTML(d)" in js
    assert "if (STAFF || !(d.deposit_due > 0)) return '';" in js


def test_the_page_does_not_compute_the_amount_it_asks_for():
    js = _order_ui()
    fn = js[js.index("function wireBookingPay("):js.index("function setServiceMode(")]
    assert "/order/booking-payment" in fn
    assert "deposit_for" not in fn and "* 0.5" not in fn


def test_an_already_paid_booking_is_not_offered_a_second_link():
    js = _order_ui()
    assert "d.nothing_to_pay" in js
    assert "already paid — nothing due" in js


def test_a_payment_failure_points_at_the_counter():
    js = _order_ui()
    assert "you can pay at the" in js and "counter" in js


def test_the_link_opens_in_a_new_tab():
    """The booking id on this page is the only record the customer has until
    the WhatsApp confirmation arrives."""
    js = _order_ui()
    assert "window.open(d.url, '_blank', 'noopener')" in js


def test_staff_are_not_shown_the_online_pay_step():
    """They take the money at the till."""
    js = _order_ui()
    assert "if (!STAFF && d.deposit_due > 0) wireBookingPay(d.job_id);" in js
