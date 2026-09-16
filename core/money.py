"""Money as integer paise, and allocation that always sums exactly.

Two rules, both learned from real defects:

1. **Money is an int of paise, never a float of rupees.** ``rate_card`` returns
   rupees as floats and will keep doing so; the boundary converts once, at the
   edge, with explicit rounding. Inside the core, ``2550`` means ₹25.50 and
   ``a + b`` is exact.

2. **A payment is allocated, not copied.** F02 in the 2026-09-10 review: the
   batch branch of ``_process_razorpay_payment`` called
   ``update_job_paid(jid, amount, ...)`` once per job with the *whole* batch
   amount, so one ₹100 payment across two jobs recorded ₹200 collected.
   ``allocate()`` below is the one routine every payment path must use, and its
   post-condition — ``sum(parts) == total`` — is asserted, not hoped for.

The allocation rule is **largest remainder** (Hamilton): give each line its
exact proportional share floored to paise, then hand the leftover paise out one
at a time to the lines with the largest fractional remainder, ties broken by
position. It is deterministic, order-stable, and every paise lands somewhere.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "allocate",
    "format_rupees",
    "paise_from_rupees",
    "rupees_from_paise",
    "split_evenly",
]


def paise_from_rupees(rupees) -> int:
    """Convert a rupee amount (float/int/str/Decimal) to integer paise.

    Rounds half-up at the paise, which is what a counter does with a printed
    receipt. Raises on a value that is not a finite number, because a NaN total
    that silently becomes 0 is exactly the class of bug F04 is about.
    """
    try:
        d = Decimal(str(rupees))
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
        raise ValueError(f"not a money amount: {rupees!r}") from exc
    if not d.is_finite():
        raise ValueError(f"not a finite money amount: {rupees!r}")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def rupees_from_paise(paise: int) -> Decimal:
    """Exact rupee value of an integer paise amount, for display/serialisation."""
    return (Decimal(int(paise)) / 100).quantize(Decimal("0.01"))


def format_rupees(paise: int) -> str:
    """``₹1,234.50`` — for WhatsApp messages, slips and the console."""
    value = rupees_from_paise(paise)
    whole, _, frac = f"{abs(value):.2f}".partition(".")
    # Indian digit grouping: last three, then pairs (12,34,567).
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])
    sign = "-" if value < 0 else ""
    return f"{sign}₹{whole}.{frac}"


def allocate(total_paise: int, weights: list[int]) -> list[int]:
    """Split ``total_paise`` across ``weights``; the parts sum to the total exactly.

    ``weights`` are the accepted line values in paise (what each line is worth),
    not percentages. A payment smaller than the order total is allocated
    proportionally — a ₹40 deposit on a ₹100 two-line order lands as the
    deposit's proportional share, so the per-line balances stay meaningful.

    All-zero weights (a fully discounted order that still took a delivery fee,
    say) split evenly rather than dividing by zero — the review's WP02 step 1
    calls this out explicitly.

    >>> allocate(10000, [4000, 3000, 3000])
    [4000, 3000, 3000]
    >>> allocate(10000, [1, 1, 1])          # 3333.33 each, remainder to the first
    [3334, 3333, 3333]
    >>> sum(allocate(9999, [7, 11, 13]))
    9999
    """
    if not isinstance(total_paise, int) or isinstance(total_paise, bool):
        raise TypeError("total_paise must be an int of paise")
    if total_paise < 0:
        raise ValueError("total_paise must not be negative; model a refund as its own event")
    if not weights:
        raise ValueError("cannot allocate across zero lines")
    if any((not isinstance(w, int)) or isinstance(w, bool) or w < 0 for w in weights):
        raise ValueError("weights must be non-negative ints of paise")

    weight_sum = sum(weights)
    if weight_sum == 0:
        return split_evenly(total_paise, len(weights))

    floors: list[int] = []
    remainders: list[tuple[int, int]] = []  # (remainder numerator, index)
    for i, w in enumerate(weights):
        exact = total_paise * w
        floors.append(exact // weight_sum)
        remainders.append((exact % weight_sum, i))

    leftover = total_paise - sum(floors)
    # Largest remainder first; ties go to the earlier line so the result is
    # stable for a given input and reproducible in a reconciliation report.
    remainders.sort(key=lambda pair: (-pair[0], pair[1]))
    for _, idx in remainders[:leftover]:
        floors[idx] += 1

    assert sum(floors) == total_paise, "allocation must be exact"  # noqa: S101
    return floors


def split_evenly(total_paise: int, n: int) -> list[int]:
    """Even split with the odd paise going to the earliest lines."""
    if n <= 0:
        raise ValueError("cannot split across zero lines")
    base, extra = divmod(total_paise, n)
    parts = [base + (1 if i < extra else 0) for i in range(n)]
    assert sum(parts) == total_paise, "even split must be exact"  # noqa: S101
    return parts
