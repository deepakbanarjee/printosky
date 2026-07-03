"""Unit tests for book_catalog pure logic (selection/qty parsing, totals)."""

import pytest

import book_catalog as bc


# ── parse_selection ───────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text,expected", [
    ("1", ["malayalam"]),
    ("2", ["hindi"]),
    ("3", ["english"]),
    ("1,3", ["malayalam", "english"]),
    ("1 3", ["malayalam", "english"]),
    ("3, 1", ["malayalam", "english"]),      # canonical order regardless of input order
    ("1,2,3", ["malayalam", "hindi", "english"]),
    ("4", ["malayalam", "hindi", "english"]),
    ("all", ["malayalam", "hindi", "english"]),
    ("ALL", ["malayalam", "hindi", "english"]),
    ("set", ["malayalam", "hindi", "english"]),
    ("1,1", ["malayalam"]),                  # de-duplicated
])
def test_parse_selection_valid(text, expected):
    assert bc.parse_selection(text) == expected


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "  ", "9", "0", "abc", "5", "1,9", "banana"])
def test_parse_selection_invalid(text):
    assert bc.parse_selection(text) is None


# ── parse_qty ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("text,expected", [
    ("1", 1),
    ("2", 2),
    ("10", 10),
    ("2 books", 2),
    (" 3 ", 3),
    ("99", 99),
])
def test_parse_qty_valid(text, expected):
    assert bc.parse_qty(text) == expected


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "0", "-1", "abc", "100", "1000"])
def test_parse_qty_invalid(text):
    assert bc.parse_qty(text) is None


# ── compute_totals ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_totals_single_book():
    t = bc.compute_totals({"malayalam": 1})
    assert t["books_total"] == 200.0
    assert t["courier"] == 75.0
    assert t["grand_total"] == 275.0
    assert t["is_set"] is False


@pytest.mark.unit
def test_totals_two_books_no_set_price():
    # Any 2 books → individual offer prices, no set discount.
    t = bc.compute_totals({"malayalam": 1, "english": 1})
    assert t["books_total"] == 400.0      # 200 + 200
    assert t["grand_total"] == 475.0
    assert t["is_set"] is False


@pytest.mark.unit
def test_totals_all_three_uses_set_price():
    t = bc.compute_totals({"malayalam": 1, "hindi": 1, "english": 1})
    assert t["books_total"] == 549.0      # set price, not 550
    assert t["grand_total"] == 664.0   # 549 books + 115 courier (1250g, 1 extra slab)
    assert t["is_set"] is True


@pytest.mark.unit
def test_totals_two_complete_sets():
    t = bc.compute_totals({"malayalam": 2, "hindi": 2, "english": 2})
    assert t["books_total"] == 1098.0     # 549 * 2
    # 6 books -> 7% off courier: 195 * 0.93 = 181.35 -> 181
    assert t["courier"] == 181.0
    assert t["grand_total"] == 1279.0     # 1098 books + 181 discounted courier
    assert t["is_set"] is True


@pytest.mark.unit
def test_courier_bulk_discount_applies_at_5_books():
    # 4 books: standard courier, no discount.
    four = bc.compute_totals({"malayalam": 4})
    assert four["courier"] == 155.0       # 2000g -> 75 + 2*40

    # 5 books: 7% off courier.
    five = bc.compute_totals({"malayalam": 5})
    assert five["courier"] == 181.0       # 2500g -> 195, minus 7% -> 181


@pytest.mark.unit
def test_totals_all_three_uneven_qty_partial_set():
    # All three present, unequal qty → extract min() sets, price remainder individually.
    # 2ML+1HI+1EN: 1 complete set (549) + 1 extra ML (200) = 749
    t = bc.compute_totals({"malayalam": 2, "hindi": 1, "english": 1})
    assert t["books_total"] == 749.0
    assert t["is_set"] is True

    # 3ML+2HI+2EN: 2 sets (1098) + 1 extra ML (200) = 1298
    t2 = bc.compute_totals({"malayalam": 3, "hindi": 2, "english": 2})
    assert t2["books_total"] == 1298.0
    assert t2["is_set"] is True


@pytest.mark.unit
def test_totals_empty_cart_has_no_courier():
    t = bc.compute_totals({})
    assert t["books_total"] == 0.0
    assert t["courier"] == 0.0
    assert t["grand_total"] == 0.0


@pytest.mark.unit
def test_totals_ignores_zero_qty():
    t = bc.compute_totals({"malayalam": 2, "hindi": 0, "english": 0})
    assert t["books_total"] == 400.0
    assert t["is_set"] is False


# ── line_items ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_line_items_ordered_and_priced():
    lines = bc.line_items({"english": 2, "malayalam": 1})
    # Canonical order: malayalam before english.
    assert [l["key"] for l in lines] == ["malayalam", "english"]
    assert lines[0]["line_total"] == 200.0
    assert lines[1]["qty"] == 2
    assert lines[1]["line_total"] == 400.0


@pytest.mark.unit
def test_line_items_skips_zero():
    lines = bc.line_items({"malayalam": 1, "hindi": 0})
    assert len(lines) == 1
    assert lines[0]["key"] == "malayalam"


# ── parse_payment_text ────────────────────────────────────────────────────────

@pytest.mark.unit
def test_parse_payment_text_canara_bank_sms():
    # The exact message Rasmi pasted instead of a screenshot.
    sms = ("Rs.275.00 paid thru A/C XX7606 on 13-6-26 13:21:36 to OXYGEN "
           "STUDENTS, UPI Ref 616443327414. If not done, SMS BLOCKUPI to "
           "9901771222.-Canara Bank")
    r = bc.parse_payment_text(sms)
    assert r is not None
    assert r["ref"] == "616443327414"
    assert r["amount"] == 275.0


@pytest.mark.unit
@pytest.mark.parametrize("text,ref", [
    ("Paid ₹664 to Oxygen, UPI transaction ID 410112233445", "410112233445"),
    ("₹275 sent. UTR: ABCD12345678", "ABCD12345678"),
    ("Transaction successful Ref no 998877665544 amount Rs 225", "998877665544"),
    ("credited Rs.150 txn 123456789012", "123456789012"),
    ("Payment received. UPI Reference 555000111222", "555000111222"),
])
def test_parse_payment_text_recognises_references(text, ref):
    r = bc.parse_payment_text(text)
    assert r is not None
    assert r["ref"] == ref


@pytest.mark.unit
def test_parse_payment_text_paid_claim_without_reference():
    # A 'paid' claim with an amount but no reference still routes (ref None).
    r = bc.parse_payment_text("I have paid Rs 275 just now")
    assert r is not None
    assert r["amount"] == 275.0


@pytest.mark.unit
@pytest.mark.parametrize("text", [
    "", "  ", "ok", "1", "books", "Aksharamrutham", "how many days will it take",
    "9876543210", "thanks", "no preference", "yes confirm", "275",
])
def test_parse_payment_text_ignores_ordinary_chat(text):
    assert bc.parse_payment_text(text) is None


# ── Divya rule: is_divya_phone / divya_order_terms ────────────────────────────

@pytest.mark.unit
@pytest.mark.parametrize("phone", [
    "919526738641",        # canonical stored form (with country code)
    "9526738641",          # bare 10-digit
    "+91 95267 38641",     # +91 prefix and spacing (how the owner typed it)
    "09526738641",         # leading 0
    "91-9526738641",       # punctuation
])
def test_is_divya_phone_matches_her_number(phone):
    assert bc.is_divya_phone(phone) is True


@pytest.mark.unit
@pytest.mark.parametrize("phone", [
    "919947184088",        # a real customer (Rajeena)
    "9947184088",
    "", "   ", None, "12345", "9526738642",   # off-by-one digit must NOT match
])
def test_is_divya_phone_rejects_everyone_else(phone):
    assert bc.is_divya_phone(phone) is False


@pytest.mark.unit
def test_divya_order_terms_customer_pays_courier_and_earns_commission():
    # Any normal customer, any channel: courier billed, Divya earns ₹50/book.
    t = bc.divya_order_terms("919947184088", {"malayalam": 1, "english": 1})
    assert t["books_total"] == 400.0
    assert t["courier"] == 75.0
    assert t["grand_total"] == 475.0
    assert t["commission"] == 50.0        # 1 Malayalam book * ₹50
    assert t["pradeep_commission"] == 50.0 # 1 English book * ₹50
    assert t["via_divya"] is True


@pytest.mark.unit
def test_divya_order_terms_divya_self_order_is_free_of_courier_and_commission():
    # Divya orders for herself: pays the book cost alone — no courier, no commission.
    t = bc.divya_order_terms("919526738641", {"malayalam": 1, "english": 1})
    assert t["books_total"] == 400.0
    assert t["courier"] == 0.0
    assert t["grand_total"] == 400.0       # books only
    assert t["commission"] == 0.0
    assert t["via_divya"] is False


@pytest.mark.unit
def test_divya_order_terms_office_pickup_no_courier_but_commission_stands():
    # xtraa_office pickup → no courier, but a customer order still earns commission.
    t = bc.divya_order_terms("919947184088", {"malayalam": 1},
                             delivery_method="xtraa_office")
    assert t["courier"] == 0.0
    assert t["grand_total"] == 200.0
    assert t["commission"] == 50.0
    assert t["via_divya"] is True


# ── parse_anu_order: multi-line address must not be truncated ────────────────

@pytest.mark.unit
def test_parse_anu_order_preserves_multiline_address():
    # Real addresses often span several lines (house name / PO / district / PIN)
    # with no "key:" prefix on the continuation lines — those must be folded
    # into the address, not silently dropped.
    text = (
        "ORDER\n"
        "Name: Jaise Abraham\n"
        "Phone: 9847012345\n"
        "Address: Near Temple\n"
        "Nedumkunnam PO\n"
        "Kottayam 686542\n"
        "Aksharamrutham: 1\n"
        "Payment: pending\n"
        "Delivery: courier"
    )
    parsed = bc.parse_anu_order(text)
    assert parsed["ok"] is True
    assert parsed["address"] == "Near Temple\nNedumkunnam PO\nKottayam 686542"


@pytest.mark.unit
def test_parse_anu_order_single_line_address_unaffected():
    text = (
        "ORDER\n"
        "Name: Priya\n"
        "Phone: 9947184088\n"
        "Address: House name, Thrissur - 680001\n"
        "Aksharamrutham: 1\n"
        "Payment: oxygen\n"
        "Delivery: courier"
    )
    parsed = bc.parse_anu_order(text)
    assert parsed["ok"] is True
    assert parsed["address"] == "House name, Thrissur - 680001"


@pytest.mark.unit
def test_parse_anu_order_continuation_after_qty_line_is_ignored():
    # A stray non-colon line after a book-quantity line (not an address) must
    # not silently attach itself to anything.
    text = (
        "ORDER\n"
        "Name: Priya\n"
        "Phone: 9947184088\n"
        "Address: Thrissur - 680001\n"
        "Aksharamrutham: 1\n"
        "some stray note\n"
        "Payment: oxygen\n"
        "Delivery: courier"
    )
    parsed = bc.parse_anu_order(text)
    assert parsed["ok"] is True
    assert parsed["address"] == "Thrissur - 680001"
    assert parsed["items"] == {"malayalam": 1}
