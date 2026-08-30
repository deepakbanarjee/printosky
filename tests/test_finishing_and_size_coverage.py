"""Guard tests — every option the UI offers must resolve to a real price.

These exist because of what a 2026-08-30 audit found: five finishing keys and
two paper sizes were orderable and quoted at zero or at the wrong rate, for
months, in silence.

  * `calculate_finishing_cost` had no branch for `lam_roll`, `lam_cover` or
    `id_card`, and `rate_card` had no key at all for `perfect` or `thesis` —
    which the live order page offers. All five fell through to the function's
    zero initialiser, so a job finished with roll lamination was quoted the
    printing and nothing for the lamination.
  * `PRINT_RATES` had no `A5_*` or `Letter_*` entries while `_VALID_SIZE` and
    the order-v2 paper dropdown offered both, so `get_print_rate`'s fallback
    billed them — colour included — at A4 B&W Rs.3/sheet.

Both have the same shape: a value the UI offers that the rate card does not
know, failing to the cheapest thing instead of failing loud. These tests read
the real UI and the real API whitelist, so adding an option without a rate
fails the build rather than the till.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import rate_card as rc

ROOT = os.path.join(os.path.dirname(__file__), "..")
ORDER_V2 = os.path.join(ROOT, "website", "order-v2.html")
CONSOLES = [os.path.join(ROOT, "website", f) for f in ("jobs.html", "admin.html")]


def _valid_from_handler(name: str) -> set[str]:
    """Read a _VALID_* literal out of api/handlers_order.py without importing
    it — the module pulls in the Supabase client and the whole API surface."""
    src = open(os.path.join(ROOT, "api", "handlers_order.py"), encoding="utf-8").read()
    m = re.search(rf"{name}\s*=\s*\{{(.*?)\}}", src, re.S)
    assert m, f"{name} not found in api/handlers_order.py"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def _order_v2_bindings() -> set[str]:
    html = open(ORDER_V2, encoding="utf-8").read()
    return set(re.findall(r'data-binding="([a-z_]+)"', html))


def _console_finishings() -> set[str]:
    keys = set()
    for path in CONSOLES:
        html = open(path, encoding="utf-8").read()
        for block in re.findall(r'id="(?:nj|jp)-finishing".*?</select>', html, re.S):
            keys |= set(re.findall(r'<option value="([a-z_]+)"', block))
    return keys


# ─────────────────────────────────────────────────────────────────────────────
# Finishing coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestNoUnpricedFinishing:
    """Nothing orderable may quote zero unless it is deliberately free."""

    @pytest.mark.parametrize("finishing", sorted(_valid_from_handler("_VALID_FINISHING")))
    def test_api_whitelist_is_priced(self, finishing):
        r = rc.calculate_finishing_cost(finishing, sheets=50)
        assert not r["unpriced"], (
            f"{finishing!r} is accepted by _VALID_FINISHING but has no rate — "
            f"it would be quoted at zero. Add it to rate_card, or remove it "
            f"from the whitelist and the order page."
        )
        if finishing not in rc.ZERO_PRICED_FINISHINGS:
            assert r["finishing_cost"] > 0

    @pytest.mark.parametrize("binding", sorted(_order_v2_bindings()))
    def test_order_page_button_is_priced(self, binding):
        r = rc.calculate_finishing_cost(binding, sheets=50)
        assert not r["unpriced"], (
            f"order-v2.html offers {binding!r} but rate_card cannot price it — "
            f"a customer can order it and be charged nothing for it."
        )

    @pytest.mark.parametrize("finishing", sorted(_console_finishings()))
    def test_console_dropdown_is_priced(self, finishing):
        r = rc.calculate_finishing_cost(finishing, sheets=50)
        assert not r["unpriced"], (
            f"a store console offers {finishing!r} but rate_card cannot price it."
        )

    def test_every_binding_rates_key_is_priced(self):
        unpriced = [k for k in rc.BINDING_RATES
                    if rc.calculate_finishing_cost(k, sheets=50)["unpriced"]]
        assert unpriced == [], f"BINDING_RATES keys with no branch: {unpriced}"

    def test_an_unknown_key_is_flagged_not_free(self):
        r = rc.calculate_finishing_cost("no_such_finishing", sheets=50)
        assert r["unpriced"] is True
        assert "NO RATE" in r["breakdown_line"]

    def test_quote_surfaces_the_flag(self):
        q = rc.calculate_quote(
            [{"pages": 10, "paper_type": "A4_BW", "sides": "ss",
              "layout": "1-up", "copies": 1}],
            finishing="no_such_finishing")
        assert q["unpriced_finishing"] is True

    def test_thermal_is_gone_everywhere(self):
        """Withdrawn 2026-08-30 — must not be orderable from anywhere."""
        assert "thermal" not in rc.BINDING_RATES
        assert "thermal" not in rc.FINISHING_DISPLAY
        assert "thermal" not in _valid_from_handler("_VALID_FINISHING")
        assert "thermal" not in _order_v2_bindings()
        assert "thermal" not in _console_finishings()


# ─────────────────────────────────────────────────────────────────────────────
# Paper size coverage
# ─────────────────────────────────────────────────────────────────────────────

class TestEverySizeHasARate:
    """Every size the API accepts must have its own rate, B&W and colour."""

    SIZES = sorted(_valid_from_handler("_VALID_SIZE"))

    @pytest.mark.parametrize("size", SIZES)
    def test_bw_rate_exists(self, size):
        assert f"{size}_BW" in rc.PRINT_RATES, (
            f"{size} is accepted by _VALID_SIZE but has no B&W rate — "
            f"get_print_rate would fall back to A4_BW and bill it at Rs.3."
        )

    @pytest.mark.parametrize("size", SIZES)
    def test_colour_rate_exists_and_differs_from_bw(self, size):
        colour = rc.get_print_rate(f"{size}_col", "ss", 10)
        bw = rc.get_print_rate(f"{size}_BW", "ss", 10)
        assert colour > bw, (
            f"{size} colour ({colour}) is not dearer than its B&W ({bw}) — "
            f"the colour rate is almost certainly falling back to A4_BW."
        )

    def test_a5_is_half_of_a4(self):
        """Owner, 2026-08-30: A5 bills at half the A4 rate."""
        assert rc.get_print_rate("A5_BW", "ss", 10) == rc.get_print_rate("A4_BW", "ss", 10) / 2
        for sheets in (10, 40, 60):
            assert (rc.get_print_rate("A5_col", "ss", sheets)
                    == rc.get_print_rate("A4_col", "ss", sheets) / 2)

    def test_letter_matches_a4(self):
        """Owner, 2026-08-30: Letter bills at the A4 rate."""
        assert rc.get_print_rate("Letter_BW", "ss", 10) == rc.get_print_rate("A4_BW", "ss", 10)
        for sheets in (10, 40, 60):
            assert (rc.get_print_rate("Letter_col", "ss", sheets)
                    == rc.get_print_rate("A4_col", "ss", sheets))

    def test_no_student_discount_outside_a4_bw(self):
        """Owner, 2026-08-30: "no discounts" on A5 or Letter."""
        for pt in ("A5_BW", "Letter_BW", "A3_BW", "Legal_BW"):
            assert (rc.get_print_rate(pt, "ss", 10, is_student=True)
                    == rc.get_print_rate(pt, "ss", 10, is_student=False)), pt
        assert (rc.get_print_rate("A4_BW", "ss", 10, is_student=True)
                < rc.get_print_rate("A4_BW", "ss", 10, is_student=False))

    def test_the_a5_colour_job_that_billed_as_a4_bw(self):
        """Regression: OSKY-20260821-6517-c724 — one A5 colour page, quoted
        Rs.3 (the A4 B&W rate) instead of Rs.5."""
        q = rc.calculate_quote(
            [{"pages": 1, "paper_type": "A5_col", "sides": "ss",
              "layout": "1-up", "copies": 1}],
            finishing="perfect")
        assert q["print_cost"] == 5.0
