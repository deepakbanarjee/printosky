"""
B-6 — the photocopy button quotes from the rate card.

The one-tap button had no test of any kind before this: it took whatever number
staff typed and filed it. That is the same shape as the five finishings that
billed Rs.0 — a price nobody computed — and it is the last place in the system
where the rate card is not the answer.

What changes: the amount is quoted. What does not: the button, the flow, and the
row it writes. A photocopy is still an immediate Completed job with no file and
no print item, and it deliberately does NOT get a `service_kind` — it ran on the
Konica, so it belongs in the printer counts (unlike a lamination, B-5).
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import print_server
import rate_card
from test_service_jobs import JOBS_DDL, PRINT_ITEMS_DDL, counter    # noqa: F401

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _job(counter, job_id):
    return counter.job(job_id)


# ── The price now comes from the rate card ────────────────────────────────────

def test_a_photocopy_with_no_typed_amount_is_priced(counter):
    """This is the whole point: staff stop inventing the number."""
    r = print_server.handle_new_photocopy({"pages": 10, "staff_id": "S1"})
    assert r["ok"] is True
    assert r["amount"] == 30                      # 10 A4 B&W sheets at Rs.3
    job = _job(counter, r["job_id"])
    assert job["amount_collected"] == 30
    assert job["amount_quoted"] == 30
    assert job["status"] == "Completed"


def test_it_agrees_with_the_rate_card_rather_than_reimplementing_it():
    """One rate card, one answer — the handler must not grow its own maths."""
    meta = {"sheets": 40, "copies": 2, "colour": "bw", "sides": "ss",
            "paper_size": "A4", "is_student": False}
    quoted, _ = print_server._photocopy_quote(meta)
    assert quoted == rate_card.calculate_service_quote("copy", meta)["total"]


def test_the_student_rate_reaches_photocopying(counter):
    full = print_server.handle_new_photocopy({"pages": 10})
    disc = print_server.handle_new_photocopy({"pages": 10, "is_student": True})
    assert disc["amount"] < full["amount"]
    assert disc["amount"] == 20                   # 10 x Rs.2 student


def test_colour_copies_cost_more_than_bw(counter):
    bw  = print_server.handle_new_photocopy({"pages": 10, "colour": "bw"})
    col = print_server.handle_new_photocopy({"pages": 10, "colour": "col"})
    assert col["amount"] > bw["amount"]


def test_paper_size_and_copies_reach_the_price(counter):
    a4 = print_server.handle_new_photocopy({"pages": 10, "paper_size": "A4"})
    a3 = print_server.handle_new_photocopy({"pages": 10, "paper_size": "A3"})
    assert a3["amount"] > a4["amount"]
    two = print_server.handle_new_photocopy({"pages": 10, "copies": 2})
    assert two["amount"] == a4["amount"] * 2


def test_the_breakdown_is_stored_so_the_number_can_be_explained(counter):
    r = print_server.handle_new_photocopy({"pages": 10, "staff_id": "S1"})
    notes = _job(counter, r["job_id"])["notes"]
    assert "Photocopy job created" in notes       # the old line survives
    assert r["breakdown"] and r["breakdown"][0][:4] in notes


# ── A typed amount still wins, and says so ────────────────────────────────────

def test_a_typed_amount_overrides_the_quote(counter):
    r = print_server.handle_new_photocopy({"pages": 10, "amount_collected": 25})
    assert r["amount"] == 25
    assert r["amount_quoted"] == 30
    assert r["overridden"] is True
    job = _job(counter, r["job_id"])
    assert job["amount_collected"] == 25
    assert job["amount_quoted"] == 30             # both visible, side by side
    assert "over the quoted" in job["notes"]


def test_a_typed_amount_equal_to_the_quote_is_not_an_override(counter):
    r = print_server.handle_new_photocopy({"pages": 10, "amount_collected": 30})
    assert r["overridden"] is False
    assert "over the quoted" not in _job(counter, r["job_id"])["notes"]


# ── Fail loud ─────────────────────────────────────────────────────────────────

def test_an_unpriceable_photocopy_is_refused_rather_than_filed_free(counter, monkeypatch):
    """Refusing costs one keystroke. Filing Rs.0 costs the sale, silently."""
    monkeypatch.setattr(rate_card, "calculate_service_quote",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate table gone")))
    r = print_server.handle_new_photocopy({"pages": 10, "staff_id": "S1"})
    assert r["ok"] is False
    assert r["needs_manual_price"] is True
    assert counter.rows("SELECT * FROM jobs") == []
    assert any(name == "photocopy.quote" and ok is False for name, ok, _ in counter.alerts)


def test_an_unpriceable_photocopy_still_completes_when_staff_type_a_price(counter, monkeypatch):
    monkeypatch.setattr(rate_card, "calculate_service_quote",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rate table gone")))
    r = print_server.handle_new_photocopy({"pages": 10, "amount_collected": 40})
    assert r["ok"] is True
    assert r["amount"] == 40
    assert r["amount_quoted"] is None


def test_a_rate_card_that_cannot_price_the_kind_asks_for_a_price(counter, monkeypatch):
    monkeypatch.setattr(rate_card, "calculate_service_quote",
                        lambda *a, **k: {"total": 0, "breakdown": ["no rate"],
                                         "label": "Photocopy",
                                         "needs_manual_price": True, "unpriced": False})
    r = print_server.handle_new_photocopy({"pages": 10})
    assert r["ok"] is False
    assert any(name == "photocopy.quote" and ok is False for name, ok, _ in counter.alerts)


def test_a_photocopy_completed_at_zero_alerts(counter):
    r = print_server.handle_new_photocopy({"pages": 10, "amount_collected": 0.0,
                                           "staff_id": "S1"})
    # 0 is not an override — the quote bills instead, so this is NOT free...
    assert r["amount"] == 30
    assert not [a for a in counter.alerts if a[0] == "photocopy.unpriced"]


def test_a_genuinely_free_photocopy_alerts(counter, monkeypatch):
    """A rate card that answers Rs.0 must not pass unnoticed."""
    monkeypatch.setattr(rate_card, "calculate_service_quote",
                        lambda *a, **k: {"total": 0, "breakdown": ["Photocopy: Rs.0"],
                                         "label": "Photocopy",
                                         "needs_manual_price": False, "unpriced": False})
    r = print_server.handle_new_photocopy({"pages": 10, "staff_id": "S1"})
    assert r["ok"] is True and r["amount"] == 0
    unpriced = [a for a in counter.alerts if a[0] == "photocopy.unpriced"]
    assert unpriced and r["job_id"] in unpriced[0][2]


# ── Nothing else about the button moves ───────────────────────────────────────

def test_a_photocopy_is_still_an_immediate_completed_job_with_no_print_item(counter):
    r = print_server.handle_new_photocopy({"pages": 10, "payment_mode": "UPI",
                                           "customer_name": "Anu", "staff_id": "S1"})
    job = _job(counter, r["job_id"])
    assert job["status"] == "Completed"
    assert job["completed_at"] is not None
    assert job["source"] == "Photocopy"
    assert job["service_type"] == "Photocopy"
    assert job["filename"] == "Photocopy Job"
    assert job["payment_mode"] == "UPI"
    assert job["customer_name"] == "Anu"
    assert counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"]) == []


def test_a_photocopy_is_not_a_service_job(counter):
    """It ran on the Konica, so it belongs in the printer counts.

    B-5 excludes service_kind rows from the printer breakdown. A photocopy must
    stay out of that exclusion or the machine's own pages stop being accounted
    for — which is the opposite of what B-10's reconciliation needs.
    """
    r = print_server.handle_new_photocopy({"pages": 10})
    cols = {c["name"] for c in counter.rows("SELECT name FROM pragma_table_info('jobs')")}
    if "service_kind" in cols:
        assert _job(counter, r["job_id"])["service_kind"] is None
    import inspect
    fn = inspect.getsource(print_server.handle_new_photocopy)
    assert "service_kind" not in fn


def test_bad_payment_modes_still_fall_back_to_cash(counter):
    r = print_server.handle_new_photocopy({"pages": 1, "payment_mode": "Bitcoin"})
    assert _job(counter, r["job_id"])["payment_mode"] == "Cash"


def test_photocopies_share_the_daily_job_id_sequence(counter):
    a = print_server.handle_new_photocopy({"pages": 1})
    b = print_server.handle_new_photocopy({"pages": 1})
    assert a["job_id"] != b["job_id"]
    assert int(b["job_id"].split("-")[-1]) == int(a["job_id"].split("-")[-1]) + 1
