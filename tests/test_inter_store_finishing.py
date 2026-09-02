"""
B-8 — a job one store sells and another finishes.

When OSP sells a record binding and Nattika binds it, no third party is
involved: one customer, one payment, two shops with one owner. The existing
/vendor-send path books that as an outside cost, which is wrong on both the
money and the tracking — so this is a **parallel** path and the vendor path is
untouched.

Two properties carry the weight:

  * **The split never invents money.** print_amount + finishing_amount is the
    quote; finishing_internal_amount is a slice of the finishing charge, never
    an addition to it.
  * **The status only walks forward.** A job cannot be marked returned before it
    was received, because the queue at the other shop is the only record that
    the work is physically there.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import print_server
import rate_card
from rate_card import (FINISHING_STATUSES, get_internal_rate,
                       is_valid_finishing_move, next_finishing_status,
                       split_amounts)
from test_service_jobs import JOBS_DDL, PRINT_ITEMS_DDL, counter    # noqa: F401


@pytest.fixture
def store(counter, monkeypatch):
    """The counter, plus a store identity for this box."""
    class Cfg:
        store_id = "OSP"
        store_name = "Oxygen"
        capabilities: dict = {}
    monkeypatch.setattr(print_server, "get_store_config", lambda: Cfg())
    return counter


def _job(store, finishing="record", quoted=420.0):
    r = print_server.handle_create_job(
        {"pages": 40, "amount_collected": quoted, "finishing": finishing,
         "amount_quoted": quoted})
    return r["job_id"]


# ── The split ─────────────────────────────────────────────────────────────────

def test_the_split_adds_up_to_the_quote():
    s = split_amounts(300, 120, "record")
    assert s["print_amount"] + s["finishing_amount"] == 420


def test_the_internal_share_is_a_slice_not_an_addition():
    s = split_amounts(300, 120, "record")
    assert s["finishing_internal_amount"] <= s["finishing_amount"]


def test_the_internal_rate_is_seeded_at_one_hundred_percent():
    """Owner has not set real numbers, and nothing blocks on numbers nobody
    has decided (plan §4.7)."""
    assert get_internal_rate("record") == 1.0
    assert get_internal_rate("anything-at-all") == 1.0
    s = split_amounts(300, 120, "record")
    assert s["finishing_internal_amount"] == s["finishing_amount"]


def test_a_rate_outside_zero_to_one_is_clamped(monkeypatch):
    monkeypatch.setitem(rate_card.FINISHING_INTERNAL_RATES, "record", 1.8)
    assert get_internal_rate("record") == 1.0
    monkeypatch.setitem(rate_card.FINISHING_INTERNAL_RATES, "record", -0.5)
    assert get_internal_rate("record") == 0.0


def test_a_rate_that_is_not_a_number_falls_back(monkeypatch):
    monkeypatch.setitem(rate_card.FINISHING_INTERNAL_RATES, "record", "half")
    assert get_internal_rate("record") == 1.0


def test_negative_costs_cannot_reduce_the_other_side():
    s = split_amounts(-100, 120, "record")
    assert s["print_amount"] == 0
    assert s["finishing_amount"] == 120


# ── The walk ──────────────────────────────────────────────────────────────────

def test_the_walk_is_forward_only():
    assert next_finishing_status(None) == "sent"
    assert next_finishing_status("sent") == "at_finisher"
    assert next_finishing_status("at_finisher") == "returned"
    assert next_finishing_status("returned") is None


def test_a_status_cannot_jump():
    assert is_valid_finishing_move(None, "sent") is True
    assert is_valid_finishing_move(None, "returned") is False
    assert is_valid_finishing_move("sent", "returned") is False
    assert is_valid_finishing_move("sent", "at_finisher") is True


def test_an_unknown_status_has_no_next_step():
    assert next_finishing_status("lost-in-transit") is None
    assert is_valid_finishing_move("lost-in-transit", "returned") is False


# ── Sending ───────────────────────────────────────────────────────────────────

def test_sending_records_the_store_the_status_and_the_split(store):
    job_id = _job(store)
    r = print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "printk", "staff_id": "S1",
         "print_cost": 300, "finishing_cost": 120})
    assert r["ok"] is True
    assert r["finishing_status"] == "sent"
    row = store.job(job_id)
    assert row["finishing_store_id"] == "PRINTK"     # normalised
    assert row["finishing_status"] == "sent"
    assert row["print_amount"] == 300
    assert row["finishing_amount"] == 120
    assert row["finishing_internal_amount"] == 120


def test_sending_writes_an_audit_event(store):
    job_id = _job(store)
    print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "PRINTK", "staff_id": "S1"})
    events = store.rows("SELECT * FROM job_events WHERE job_id=?", job_id)
    assert any(e["action"] == "finishing_sent" for e in events)


def test_without_costs_the_whole_quote_books_as_printing(store):
    """Better than inventing a finishing charge nobody quoted."""
    job_id = _job(store, quoted=420.0)
    r = print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "PRINTK"})
    assert r["print_amount"] == 420
    assert r["finishing_amount"] == 0
    assert r["finishing_internal_amount"] == 0


def test_a_store_cannot_send_work_to_itself(store):
    job_id = _job(store)
    r = print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "OSP"})
    assert r["ok"] is False
    assert "this store" in r["error"]
    assert store.job(job_id)["finishing_status"] is None


def test_sending_twice_is_refused_and_alerts(store):
    job_id = _job(store)
    print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "PRINTK"})
    r = print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "PRINTK"})
    assert r["ok"] is False
    assert any(n == "finishing.bad_transition" for n, _, _ in store.alerts)


def test_an_unknown_job_is_refused(store):
    r = print_server.handle_finishing_send(
        {"job_id": "OSP-NOPE", "finishing_store_id": "PRINTK"})
    assert r["ok"] is False
    assert "not found" in r["error"]


# ── Walking it ────────────────────────────────────────────────────────────────

def test_the_full_walk(store):
    job_id = _job(store)
    print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "PRINTK"})
    for target in ("at_finisher", "returned"):
        r = print_server.handle_finishing_advance(
            {"job_id": job_id, "to": target, "staff_id": "S2"})
        assert r["ok"] is True
        assert store.job(job_id)["finishing_status"] == target


def test_returning_before_receiving_is_refused_and_says_what_is_next(store):
    job_id = _job(store)
    print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "PRINTK"})
    r = print_server.handle_finishing_advance({"job_id": job_id, "to": "returned"})
    assert r["ok"] is False
    assert r["expected"] == "at_finisher"
    assert store.job(job_id)["finishing_status"] == "sent"
    assert any(n == "finishing.bad_transition" for n, _, _ in store.alerts)


def test_a_job_never_sent_cannot_be_received(store):
    job_id = _job(store)
    r = print_server.handle_finishing_advance({"job_id": job_id, "to": "at_finisher"})
    assert r["ok"] is False
    assert r["expected"] == "sent"


# ── The queue at the other shop ───────────────────────────────────────────────

def test_the_incoming_queue_shows_work_sent_here_and_not_returned(store):
    a, b, c = _job(store), _job(store), _job(store)
    for job_id in (a, b, c):
        print_server.handle_finishing_send(
            {"job_id": job_id, "finishing_store_id": "PRINTK"})
    print_server.handle_finishing_advance({"job_id": b, "to": "at_finisher"})
    print_server.handle_finishing_advance({"job_id": c, "to": "at_finisher"})
    print_server.handle_finishing_advance({"job_id": c, "to": "returned"})

    q = print_server.handle_finishing_incoming({"store_id": ["PRINTK"]})
    assert q["ok"] is True
    ids = {j["job_id"] for j in q["jobs"]}
    assert ids == {a, b}          # c has gone back
    assert q["count"] == 2


def test_the_queue_defaults_to_this_machines_store(store):
    job_id = _job(store)
    print_server.handle_finishing_send(
        {"job_id": job_id, "finishing_store_id": "PRINTK"})
    assert print_server.handle_finishing_incoming({})["store_id"] == "OSP"
    assert print_server.handle_finishing_incoming({})["jobs"] == []


def test_a_box_that_cannot_migrate_says_so_rather_than_showing_an_empty_queue(store, monkeypatch):
    """"No work waiting" and "this box cannot answer" are different statements.

    The columns are added on demand, so reaching this branch means the ALTER
    itself failed — db_migrations has already alerted, and the queue must not
    paper over it with a reassuring empty list.
    """
    monkeypatch.setattr(print_server, "ensure_job_service_columns", lambda conn: [])
    q = print_server.handle_finishing_incoming({"store_id": ["PRINTK"]})
    assert q["ok"] is False
    assert "migration" in q["error"]


def test_an_unmigrated_box_does_not_tell_the_counter_its_job_vanished(store, monkeypatch):
    job_id = _job(store)
    monkeypatch.setattr(print_server, "ensure_job_service_columns", lambda conn: [])
    r = print_server.handle_finishing_advance({"job_id": job_id, "to": "at_finisher"})
    assert r["ok"] is False
    assert "migration" in r["error"]
    assert "not found" not in r["error"]


# ── The vendor path is untouched ──────────────────────────────────────────────

def test_the_vendor_path_is_a_separate_path():
    """An internal transfer must not book as an outside cost."""
    import inspect
    send = inspect.getsource(print_server.handle_finishing_send)
    assert "job_vendor_steps" not in send
    assert "vendor_name" not in send
    assert "At Vendor" not in send


# ── The queue at the counter ──────────────────────────────────────────────────
#
# The panel is deliberately hidden when the queue is empty. A panel that always
# reads "0 jobs" is one people stop looking at, and this one only earns
# attention by appearing.

ROOT = os.path.join(os.path.dirname(__file__), "..")
CONSOLES = ("jobs.html", "admin.html")


def _html(name):
    return open(os.path.join(ROOT, "website", name), encoding="utf-8").read()


@pytest.mark.parametrize("name", CONSOLES)
def test_the_queue_panel_starts_hidden(name):
    src = _html(name)
    panel = src[src.index('id="finishing-queue"'):][:200]
    assert 'style="display:none"' in panel


@pytest.mark.parametrize("name", CONSOLES)
def test_an_empty_queue_hides_the_panel_rather_than_showing_zero(name):
    src = _html(name)
    fn = src[src.index("async function refreshFinishingQueue()"):
             src.index("function _fqHours(")]
    assert 'if (!data.ok || !(data.jobs || []).length) { panel.style.display = "none"; return; }' in fn


@pytest.mark.parametrize("name", CONSOLES)
def test_the_button_offers_only_the_next_legal_step(name):
    """The store PC enforces forward-only; the console must not offer a jump."""
    src = _html(name)
    fn = src[src.index("async function refreshFinishingQueue()"):
             src.index("function _fqHours(")]
    assert 'j.finishing_status === "sent" ? "receive" : "return"' in fn


@pytest.mark.parametrize("name", CONSOLES)
def test_a_refused_step_shows_the_reason_the_store_pc_gave(name):
    """That reason names the step it expected — the actionable half."""
    src = _html(name)
    fn = src[src.index("async function advanceFinishing("):]
    assert 'alert("Failed: " + (data.error || "Unknown error"));' in fn


@pytest.mark.parametrize("name", CONSOLES)
def test_an_unparseable_age_shows_nothing_rather_than_a_wrong_number(name):
    src = _html(name)
    fn = src[src.index("function _fqHours("):src.index("async function advanceFinishing(")]
    assert "if (isNaN(t)) return null;" in fn


@pytest.mark.parametrize("name", CONSOLES)
def test_the_overdue_threshold_matches_the_digest(name):
    """One number, two places it is shown — they must agree."""
    from store_digest import FINISHING_OVERDUE_HOURS
    src = _html(name)
    assert f"const FINISHING_OVERDUE_HOURS = {FINISHING_OVERDUE_HOURS};" in src


@pytest.mark.parametrize("name", CONSOLES)
def test_the_queue_refreshes_with_the_job_list(name):
    assert "if (!isDemo) refreshFinishingQueue();" in _html(name)


def test_the_queue_is_identical_in_both_consoles():
    def block(name):
        s = _html(name)
        i = s.index("// ── Incoming finishing queue (B-8)")
        return s[i:s.index("// ── Service Modal — post-press work", i)]
    assert block("jobs.html") == block("admin.html")
