"""core.money — allocation must be exact, always.

This is the test that was missing when F02 shipped. The repository already had
allocation tests, but they covered ``webhook_receiver.py`` — the store-PC
receiver that is no longer deployed — while the live cloud path copied the whole
batch amount onto every job. These tests cover the *shared* routine that both
paths now call, which is the only way the coverage cannot drift again.
"""
import pytest

from core.money import allocate, format_rupees, paise_from_rupees, rupees_from_paise, split_evenly


# ── The review's own regression scenario ─────────────────────────────────────

def test_hundred_rupees_across_three_jobs_totals_exactly_hundred():
    """'A ₹100 batch across three jobs totals exactly ₹100 after allocation.'"""
    parts = allocate(10000, [3300, 3300, 3400])
    assert sum(parts) == 10000
    assert parts == [3300, 3300, 3400]


def test_indivisible_paise_are_handed_out_not_dropped():
    parts = allocate(10000, [1, 1, 1])
    assert sum(parts) == 10000
    assert parts == [3334, 3333, 3333]      # largest remainder, ties to the earlier line


@pytest.mark.parametrize("total", [1, 7, 99, 100, 9999, 123457])
@pytest.mark.parametrize("weights", [[1], [1, 2], [7, 11, 13], [0, 5, 5], [1] * 9])
def test_allocation_is_always_exact(total, weights):
    assert sum(allocate(total, weights)) == total


def test_allocation_is_deterministic():
    assert allocate(1000, [3, 3, 3]) == allocate(1000, [3, 3, 3])


def test_zero_weights_split_evenly_instead_of_dividing_by_zero():
    # A fully discounted order still has to allocate whatever was paid.
    assert allocate(1000, [0, 0, 0]) == [334, 333, 333]


def test_partial_payment_is_allocated_proportionally():
    """A ₹40 deposit on a ₹100 two-line order keeps the per-line balances real."""
    parts = allocate(4000, [4000, 6000])
    assert parts == [1600, 2400]
    assert sum(parts) == 4000


# ── Guardrails ───────────────────────────────────────────────────────────────

def test_negative_total_is_refused():
    with pytest.raises(ValueError, match="negative"):
        allocate(-1, [1])


def test_float_total_is_refused():
    # Rupee floats are the bug class this module exists to remove.
    with pytest.raises(TypeError):
        allocate(100.0, [1])


def test_bool_is_not_an_int_here():
    with pytest.raises(TypeError):
        allocate(True, [1])


def test_empty_weights_is_refused():
    with pytest.raises(ValueError):
        allocate(100, [])


def test_negative_weight_is_refused():
    with pytest.raises(ValueError):
        allocate(100, [5, -5])


# ── Conversion and formatting ────────────────────────────────────────────────

@pytest.mark.parametrize("rupees,paise", [
    (25.50, 2550), ("25.50", 2550), (0, 0), (1, 100), ("25.505", 2551), (0.1 + 0.2, 30),
])
def test_paise_from_rupees(rupees, paise):
    assert paise_from_rupees(rupees) == paise


def test_paise_from_rupees_refuses_nonsense():
    for bad in ("abc", None, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            paise_from_rupees(bad)


def test_round_trip_is_stable():
    for paise in (0, 1, 99, 100, 123456789):
        assert paise_from_rupees(rupees_from_paise(paise)) == paise


def test_indian_digit_grouping():
    assert format_rupees(123456789) == "₹12,34,567.89"
    assert format_rupees(100) == "₹1.00"
    assert format_rupees(-2550) == "-₹25.50"


def test_split_evenly_is_exact():
    assert split_evenly(10, 3) == [4, 3, 3]
    assert sum(split_evenly(9999, 7)) == 9999
