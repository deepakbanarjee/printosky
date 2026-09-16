"""core.pricing — a price or a refusal, never a silent zero (review finding F04)."""
import pytest

from core.errors import PricingUnavailableError, ValidationError
from core.pricing import PrintItem, Quote, QuoteLine, price_order, spec_hash


def fake_rate_card(items, finishing="none", urgent=False, is_student=False, paper_size="A4"):
    pages = sum(i["pages"] * i["copies"] for i in items)
    finishing_cost = 30.0 if finishing != "none" else 0.0
    return {
        "total": pages * 1.5 + finishing_cost,
        "print_cost": pages * 1.5,
        "finishing_cost": finishing_cost,
        "total_sheets": pages,
        "breakdown": ["fake"],
    }


def test_prices_an_order_and_lines_sum_to_the_total():
    quote = price_order(
        [PrintItem(10, "A4_BW"), PrintItem(3, "A4_col")],
        finishing="spiral", calculator=fake_rate_card,
    )
    assert quote.total_paise == 4950
    assert sum(quote.weights()) == quote.total_paise
    assert [line.kind for line in quote.lines] == ["print", "print", "finishing"]


def test_a_rate_card_exception_is_a_refusal_not_a_zero():
    def broken(*args, **kwargs):
        raise RuntimeError("rate table missing")

    with pytest.raises(PricingUnavailableError) as exc:
        price_order([PrintItem(5, "A4_BW")], calculator=broken)
    assert exc.value.status == 503
    assert exc.value.code == "pricing_unavailable"


def test_a_zero_total_is_refused_unless_it_was_asked_for():
    zero = lambda *a, **k: {"total": 0, "print_cost": 0, "finishing_cost": 0}
    with pytest.raises(PricingUnavailableError):
        price_order([PrintItem(5, "A4_BW")], calculator=zero)

    # …but a deliberate comp is allowed, loudly, at the call site.
    quote = price_order([PrintItem(5, "A4_BW")], calculator=zero, allow_zero=True)
    assert quote.total_paise == 0


def test_a_nonsense_total_is_refused():
    for bad in ({"total": "free"}, {"total": None}, {"total": float("nan")}, {"nope": 1}, "not a dict"):
        with pytest.raises(PricingUnavailableError):
            price_order([PrintItem(5, "A4_BW")], calculator=lambda *a, **k: bad)


def test_a_negative_total_is_refused():
    with pytest.raises(PricingUnavailableError):
        price_order([PrintItem(5, "A4_BW")],
                    calculator=lambda *a, **k: {"total": -10, "print_cost": -10, "finishing_cost": 0})


# ── Input validation (review: "validate copies, page ranges and colour") ─────

@pytest.mark.parametrize("item", [
    PrintItem(-1, "A4_BW"),
    PrintItem(10, "A4_BW", copies=0),
    PrintItem(10, "A4_BW", copies=100000),
    PrintItem(10, "A4_BW", sides="both"),
    PrintItem(10, "A4_BW", layout="3-up"),
    PrintItem(10, "nonsense"),
    PrintItem(999999, "A4_BW"),
])
def test_invalid_items_are_rejected_before_pricing(item):
    with pytest.raises(ValidationError):
        price_order([item], calculator=fake_rate_card)


def test_an_empty_order_is_rejected():
    with pytest.raises(ValidationError):
        price_order([], calculator=fake_rate_card)


# ── The immutable accepted specification (contract #2) ───────────────────────

def test_spec_hash_is_stable_and_sensitive():
    items = [PrintItem(10, "A4_BW")]
    assert spec_hash(items, "none", "A4") == spec_hash(items, "none", "A4")
    assert spec_hash(items, "none", "A4") != spec_hash(items, "spiral", "A4")
    assert spec_hash(items, "none", "A4") != spec_hash(items, "none", "A3")
    assert spec_hash(items, "none", "A4") != spec_hash([PrintItem(11, "A4_BW")], "none", "A4")


def test_a_quote_whose_lines_do_not_sum_is_rejected_at_construction():
    with pytest.raises(ValidationError):
        Quote(
            total_paise=1000,
            lines=(QuoteLine("print", "x", 900),),
            total_sheets=1, breakdown=(), spec_hash="x",
        )


# ── The real rate card, to lock the token contract ───────────────────────────

def test_real_rate_card_prices_a_simple_bw_job():
    """Locks the 'A4_BW' / 'A4_col' token contract against the live rate card.

    The lowercase "col" is load-bearing: "A4_COL" silently falls through to the
    flat-rate lookup and bills colour at the B&W rate.
    """
    bw = price_order([PrintItem(10, "A4_BW", copies=1)])
    colour = price_order([PrintItem(10, "A4_col", copies=1)])
    assert bw.total_paise > 0
    assert colour.total_paise > bw.total_paise
