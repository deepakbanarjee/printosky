"""Authoritative quoting: a price, or an explicit refusal. Never a silent ₹0.

F04 in the 2026-09-10 review: ``_handle_order_create`` wrapped the rate-card
call in ``except Exception: total = 0.0`` and then created the order and sent
the customer a confirmation. A broken rate card was indistinguishable from a
free order. This module exists so that path has somewhere correct to go.

The contract:

* ``price_order`` returns a :class:`Quote` or raises
  :class:`core.errors.PricingUnavailableError`. There is no third outcome, and
  no code path in which an exception becomes a number.
* A quote is **priced per line**, and the line values sum to the total exactly
  (:func:`core.money.allocate` does the reconciling), so a later payment can be
  allocated against the same line values the customer accepted.
* A quote carries ``spec_hash`` — a stable digest of the exact specification it
  priced. Contract #2 of the review's target architecture: one immutable
  accepted specification. If the spec changes after acceptance, the hash
  changes, and the order needs a new quote instead of quietly printing
  something the customer never agreed to pay for.

``rate_card.calculate_quote`` stays the single source of prices; this is a
boundary around it, not a replacement. It is injected so the core keeps its
no-I/O promise and tests do not need the real rate card.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

from core.errors import PricingUnavailableError, ValidationError
from core.money import allocate, paise_from_rupees

__all__ = ["PrintItem", "Quote", "QuoteLine", "price_order", "spec_hash"]

# Mirrors rate_card's accepted tokens. The lowercase "col" in "A4_col" is
# load-bearing — "A4_COL" falls through to the flat-rate lookup and bills a
# colour job at the B&W rate (see api/handlers_order.py's module docstring).
_VALID_SIDES = ("ss", "ds")
_VALID_LAYOUTS = ("1-up", "2-up", "4-up", "9-up")
_MAX_COPIES = 2000
_MAX_PAGES = 20000


@dataclass(frozen=True)
class PrintItem:
    """One priced line of a print order, in rate-card vocabulary."""

    pages: int
    paper_type: str            # "A4_BW" | "A4_col" | "A3_BW" | …
    sides: str = "ss"          # ss | ds
    layout: str = "1-up"       # 1-up | 2-up | 4-up | 9-up
    copies: int = 1

    def as_rate_card_item(self) -> dict:
        return {
            "pages": self.pages,
            "paper_type": self.paper_type,
            "sides": self.sides,
            "layout": self.layout,
            "copies": self.copies,
        }

    def validate(self) -> None:
        if self.pages < 0 or self.pages > _MAX_PAGES:
            raise ValidationError(
                f"pages out of range: {self.pages}",
                details={"field": "pages", "max": _MAX_PAGES},
            )
        if self.copies < 1 or self.copies > _MAX_COPIES:
            raise ValidationError(
                f"copies out of range: {self.copies}",
                details={"field": "copies", "max": _MAX_COPIES},
            )
        if self.sides not in _VALID_SIDES:
            raise ValidationError(f"unsupported sides: {self.sides!r}", details={"field": "sides"})
        if self.layout not in _VALID_LAYOUTS:
            raise ValidationError(f"unsupported layout: {self.layout!r}", details={"field": "layout"})
        if not self.paper_type or "_" not in self.paper_type:
            raise ValidationError(
                f"unsupported paper_type: {self.paper_type!r}", details={"field": "paper_type"}
            )


@dataclass(frozen=True)
class QuoteLine:
    """A line of the accepted quote. ``amount_paise`` is what this line owes."""

    kind: str                  # "print" | "finishing"
    label: str
    amount_paise: int
    item: PrintItem | None = None


@dataclass(frozen=True)
class Quote:
    total_paise: int
    lines: tuple[QuoteLine, ...]
    total_sheets: int
    breakdown: tuple[str, ...]
    spec_hash: str
    outsourced_finishing: bool = False
    priced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        line_sum = sum(line.amount_paise for line in self.lines)
        if line_sum != self.total_paise:
            # Not reachable through price_order (it allocates), so if it ever
            # fires it is a caller constructing a Quote by hand and getting the
            # invariant wrong — which must be loud, not rounded away.
            raise ValidationError(
                "quote lines do not sum to the total",
                details={"line_sum": line_sum, "total_paise": self.total_paise},
            )

    def weights(self) -> list[int]:
        """Allocation weights for a payment against this quote."""
        return [line.amount_paise for line in self.lines]

    def to_dict(self) -> dict:
        return {
            "total_paise": self.total_paise,
            "total_sheets": self.total_sheets,
            "spec_hash": self.spec_hash,
            "priced_at": self.priced_at,
            "outsourced_finishing": self.outsourced_finishing,
            "breakdown": list(self.breakdown),
            "lines": [
                {
                    "kind": line.kind,
                    "label": line.label,
                    "amount_paise": line.amount_paise,
                    "item": line.item.as_rate_card_item() if line.item else None,
                }
                for line in self.lines
            ],
        }


def spec_hash(
    items: Sequence[PrintItem],
    finishing: str,
    paper_size: str,
    *,
    urgent: bool = False,
    is_student: bool = False,
    extra: dict | None = None,
) -> str:
    """Stable digest of everything that can change the price or the output.

    Sorted keys and a canonical separator, so the same specification hashes the
    same on the store PC and in the cloud. Truncated to 32 hex chars: enough to
    make an accidental collision irrelevant, short enough to read in a log.
    """
    payload = {
        "items": [i.as_rate_card_item() for i in items],
        "finishing": finishing,
        "paper_size": paper_size,
        "urgent": bool(urgent),
        "is_student": bool(is_student),
        "extra": extra or {},
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


def _default_calculator():
    """Import ``rate_card.calculate_quote`` lazily.

    Lazy because the core must stay importable where rate_card's dependencies
    are not installed (a bare test runner, a lambda that only serves health).
    An import failure here is a configuration problem and surfaces as
    PricingUnavailableError at the call site, not as an ImportError at boot.
    """
    from rate_card import calculate_quote  # noqa: PLC0415 - deliberate lazy import

    return calculate_quote


def price_order(
    items: Sequence[PrintItem],
    *,
    finishing: str = "none",
    paper_size: str = "A4",
    urgent: bool = False,
    is_student: bool = False,
    allow_zero: bool = False,
    calculator: Callable[..., dict] | None = None,
) -> Quote:
    """Price a complete order. Raises rather than guessing.

    ``allow_zero`` must be passed explicitly for a genuinely free order (a
    reprint of our own mistake, a staff comp). Without it, a zero total from a
    non-empty specification is treated as a pricing failure, because that is
    what it has always actually been.
    """
    items = tuple(items)
    if not items:
        raise ValidationError("an order needs at least one print item", details={"field": "items"})
    for item in items:
        item.validate()

    calc = calculator or _default_calculator()
    kwargs = {
        "finishing": finishing,
        "urgent": urgent,
        "is_student": is_student,
        "paper_size": paper_size,
    }

    try:
        whole = calc([i.as_rate_card_item() for i in items], **kwargs)
    except Exception as exc:  # noqa: BLE001 - every failure is the same answer: no price
        raise PricingUnavailableError(
            "could not price this order",
            details={"reason": type(exc).__name__},
        ) from exc
    if not isinstance(whole, dict) or "total" not in whole:
        raise PricingUnavailableError(
            "rate card returned no total", details={"got": type(whole).__name__}
        )

    try:
        total_paise = paise_from_rupees(whole["total"])
        finishing_paise = paise_from_rupees(whole.get("finishing_cost", 0))
    except ValueError as exc:
        raise PricingUnavailableError("rate card returned a non-numeric total") from exc

    if total_paise < 0:
        raise PricingUnavailableError(
            "rate card returned a negative total", details={"total_paise": total_paise}
        )
    if total_paise == 0 and not allow_zero:
        raise PricingUnavailableError(
            "priced at zero — refusing to treat a pricing failure as a free order",
            details={"hint": "pass allow_zero=True for a deliberate comp"},
        )

    # Per-item weights come from pricing each item alone. They are only weights:
    # the authoritative number is the single whole-order call above (tiered
    # rates mean the parts need not add to the whole), and allocate() makes the
    # published line values sum to it exactly.
    weights: list[int] = []
    for item in items:
        try:
            one = calc([item.as_rate_card_item()], **{**kwargs, "finishing": "none"})
            weights.append(max(0, paise_from_rupees(one.get("total", 0))))
        except Exception:  # noqa: BLE001 - a weight is recoverable; fall back to sheets
            weights.append(max(1, item.pages * max(1, item.copies)))

    if finishing_paise > 0 and total_paise > finishing_paise:
        print_share = total_paise - finishing_paise
    else:
        # The parts disagree with the whole (or finishing swallowed the total).
        # `total` is what the customer is shown and charged, so it wins and the
        # split collapses to one print share. `print_cost` is only ever used to
        # derive this split, never as the amount owed.
        print_share, finishing_paise = total_paise, 0

    per_item = allocate(print_share, weights)
    lines = [
        QuoteLine(
            kind="print",
            label=f"{item.copies}× {item.pages}p {item.paper_type} {item.sides} {item.layout}",
            amount_paise=amount,
            item=item,
        )
        for item, amount in zip(items, per_item)
    ]
    if finishing_paise > 0:
        lines.append(QuoteLine(kind="finishing", label=finishing, amount_paise=finishing_paise))

    return Quote(
        total_paise=total_paise,
        lines=tuple(lines),
        total_sheets=int(whole.get("total_sheets", 0) or 0),
        breakdown=tuple(str(b) for b in (whole.get("breakdown") or [])),
        spec_hash=spec_hash(
            items, finishing, paper_size, urgent=urgent, is_student=is_student
        ),
        outsourced_finishing=bool(whole.get("outsourced_finishing", False)),
    )
