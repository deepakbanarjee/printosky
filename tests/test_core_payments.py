"""core.payments + core.orders — the review's payment regression scenarios.

From §10 "Required regression scenarios":
  * A ₹100 batch across three jobs totals exactly ₹100 after allocation.
  * Duplicate, delayed and concurrent payment events neither add money twice
    nor regress production status.
  * A crash after inbox receipt but before payment commit is recoverable.
"""
import pytest

from core.errors import ConflictError, ValidationError
from core.orders import (
    AttemptState, Lane, Order, OrderItem, PaymentState, PrintAttempt, ProductionState,
    can_transition, new_order_id,
)
from core.payments import InboxEvent, plan_payment, plan_refund


def order(total=10000, items=(4000, 6000), **kw):
    o = Order("OSP-20260916-TEST", "OSP", "919495706405", total_paise=total, **kw)
    for idx, amount in enumerate(items, 1):
        o.items.append(OrderItem(f"i{idx}", f"obj/{idx}", f"f{idx}.pdf", amount_paise=amount))
    return o


def event(amount=10000, payment_id="pay_1", event_id="evt_1"):
    return InboxEvent("razorpay", event_id, payment_id, "OSP-20260916-TEST", amount, method="upi")


# ── Allocation ───────────────────────────────────────────────────────────────

def test_one_payment_allocates_across_lines_and_sums_exactly():
    commit = plan_payment(order(), event())
    assert [a.amount_paise for a in commit.allocations] == [4000, 6000]
    assert sum(a.amount_paise for a in commit.allocations) == 10000
    assert commit.new_payment_state is PaymentState.PAID


def test_hundred_rupees_across_three_lines():
    commit = plan_payment(order(10000, (3333, 3333, 3334)), event())
    assert sum(a.amount_paise for a in commit.allocations) == 10000


def test_an_order_with_no_items_allocates_to_itself():
    o = Order("OSP-1", "OSP", "91", total_paise=5000)
    commit = plan_payment(o, event(5000))
    assert len(commit.allocations) == 1
    assert commit.allocations[0].amount_paise == 5000


# ── Idempotency ──────────────────────────────────────────────────────────────

def test_the_same_payment_twice_is_recorded_once():
    commit = plan_payment(order(), event(), already_recorded_payment_ids=["pay_1"])
    assert commit.duplicate is True
    assert commit.is_noop()
    assert commit.outbox == ()          # no second "payment received" message


def test_a_different_payment_for_the_same_order_still_lands():
    paid = order(paid_paise=4000)
    paid.move_payment(PaymentState.PART_PAID)
    commit = plan_payment(paid, event(6000, payment_id="pay_2"),
                          already_recorded_payment_ids=["pay_1"])
    assert commit.duplicate is False
    assert commit.new_paid_paise == 10000
    assert commit.new_payment_state is PaymentState.PAID


# ── Payment never moves production (contract #3) ─────────────────────────────

def test_a_late_payment_does_not_pull_a_delivered_order_backwards():
    delivered = order()
    for state in (ProductionState.QUEUED, ProductionState.CLAIMED, ProductionState.SPOOLED,
                  ProductionState.PRINTED, ProductionState.READY, ProductionState.DELIVERED):
        delivered.move_production(state)
    commit = plan_payment(delivered, event())
    # The commit describes payment fields only. There is no production field on it.
    assert not hasattr(commit, "new_production_state")
    assert delivered.production_state is ProductionState.DELIVERED


def test_full_payment_publishes_a_dispatch_event_rather_than_dispatching():
    commit = plan_payment(order(), event())
    topics = [e.topic for e in commit.outbox]
    assert "order.fully_paid" in topics
    assert "payment.received" in topics


# ── Overpayment and refunds are visible, not absorbed ────────────────────────

def test_overpayment_is_flagged():
    commit = plan_payment(order(), event(12000))
    assert commit.new_payment_state is PaymentState.OVERPAID
    assert any("overpaid" in w for w in commit.warnings)


def test_a_refund_is_its_own_row_and_does_not_rewrite_the_capture():
    paid = order(paid_paise=10000)
    paid.move_payment(PaymentState.PAID)
    commit = plan_refund(paid, event(4000, payment_id="rfnd_1", event_id="evt_2"))
    assert commit.payment.kind == "refund"
    assert commit.payment.amount_paise == -4000
    assert commit.new_paid_paise == 6000
    assert commit.new_payment_state is PaymentState.REFUNDED


# ── Event validation ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [
    InboxEvent("", "e", "p", "o", 100),
    InboxEvent("razorpay", "e", "", "o", 100),
    InboxEvent("razorpay", "e", "p", "", 100),
    InboxEvent("razorpay", "e", "p", "o", 0),
    InboxEvent("razorpay", "e", "p", "o", -100),
    InboxEvent("razorpay", "e", "p", "o", 100.5),
    InboxEvent("razorpay", "e", "p", "o", 100, currency="USD"),
])
def test_a_malformed_event_is_rejected_before_anything_is_written(bad):
    with pytest.raises(ValidationError):
        plan_payment(order(), bad)


# ── Production state machine (F05, F06) ──────────────────────────────────────

def test_an_uncertain_outcome_cannot_jump_to_delivered():
    o = order()
    o.move_production(ProductionState.QUEUED)
    o.move_production(ProductionState.CLAIMED)
    o.move_production(ProductionState.SPOOLED)
    o.move_production(ProductionState.OUTPUT_UNCERTAIN)
    with pytest.raises(ConflictError):
        o.move_production(ProductionState.DELIVERED)
    # A person resolves it, in one of exactly three directions.
    assert can_transition(ProductionState.OUTPUT_UNCERTAIN, ProductionState.PRINTED)
    assert can_transition(ProductionState.OUTPUT_UNCERTAIN, ProductionState.QUEUED)
    assert can_transition(ProductionState.OUTPUT_UNCERTAIN, ProductionState.HELD)


def test_only_a_known_failure_is_safe_to_retry():
    def attempt(state):
        return PrintAttempt("a1", "t1", "pc1", "Konica", "tok", state=state)

    assert attempt(AttemptState.FAILED).is_safe_to_retry() is True
    assert attempt(AttemptState.UNCERTAIN).is_safe_to_retry() is False
    assert attempt(AttemptState.SUBMITTED).is_safe_to_retry() is False


def test_re_asserting_the_same_state_is_not_an_error():
    """A retried webhook or a double-tapped button must be harmless."""
    o = order()
    o.move_production(ProductionState.QUEUED)
    o.move_production(ProductionState.QUEUED)
    assert o.production_state is ProductionState.QUEUED


def test_delivered_is_terminal():
    assert can_transition(ProductionState.DELIVERED, ProductionState.READY) is False


# ── Dispatch eligibility ─────────────────────────────────────────────────────

def test_an_order_is_only_dispatchable_when_every_condition_holds():
    o = order(paid_paise=10000, lane=Lane.EXPRESS)
    o.move_payment(PaymentState.PAID)
    o.accepted_at = "2026-09-16T00:00:00Z"
    o.move_production(ProductionState.QUEUED)
    assert o.is_dispatchable() is False          # preflight has not run
    for item in o.items:
        item.preflight_ok = True
    assert o.is_dispatchable() is True

    o.lane = Lane.ASSISTED                       # a human must look first
    assert o.is_dispatchable() is False


def test_an_unpaid_order_is_never_dispatchable():
    o = order(lane=Lane.EXPRESS)
    o.accepted_at = "2026-09-16T00:00:00Z"
    o.move_production(ProductionState.QUEUED)
    for item in o.items:
        item.preflight_ok = True
    assert o.is_dispatchable() is False


def test_order_ids_keep_the_existing_shape():
    oid = new_order_id("OSP")
    assert oid.startswith("OSP-")
    assert len(oid.split("-")) == 3
