"""
B-3 — /service-quote and /new-service.

A post-press service job is an ordinary `jobs` row with `service_kind` set. It
is quoted from the rate card, booked at the counter, marked Ready when the work
is done, and paid through the payment path everything else already uses.

What these tests are really pinning is the negative space: no print_items row,
no printer, no file, and a price the shop can stand behind or an alert saying it
cannot.
"""

import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import print_server
import rate_card

# Every column the three handlers under test write, minus the v38 ones — those
# are added by the self-migration, which is part of what is being tested.
JOBS_DDL = """
    CREATE TABLE jobs (
        job_id           TEXT PRIMARY KEY,
        received_at      TEXT,
        filename         TEXT,
        file_extension   TEXT,
        source           TEXT,
        sender           TEXT,
        customer_name    TEXT,
        service_type     TEXT,
        colour           TEXT,
        sides            TEXT,
        copies           INTEGER,
        finishing        TEXT,
        paper_size       TEXT,
        page_count       INTEGER,
        amount_quoted    REAL,
        amount_collected REAL,
        amount_partial   REAL,
        payment_mode     TEXT,
        override_reason  TEXT,
        status           TEXT,
        queued_at        TEXT,
        completed_at     TEXT,
        delivered_at     TEXT,
        filepath         TEXT,
        printed_by       TEXT,
        pickup_code      TEXT,
        notes            TEXT,
        staff_notes      TEXT
    )
"""

PRINT_ITEMS_DDL = """
    CREATE TABLE print_items (
        job_id      TEXT, item_number INTEGER, page_list TEXT, paper_type TEXT,
        colour      TEXT, sides TEXT, layout TEXT, copies INTEGER,
        paper_gsm   INTEGER, printer TEXT, status TEXT,
        printed_at  TEXT, printed_by TEXT
    )
"""


@pytest.fixture
def counter(monkeypatch, tmp_path):
    """A store PC database with no jobs yet, and every alert captured."""
    db = tmp_path / "jobs.db"
    conn = sqlite3.connect(db)
    conn.executescript(JOBS_DDL + ";" + PRINT_ITEMS_DDL)
    conn.commit()
    conn.close()

    alerts = []
    monkeypatch.setattr(print_server, "DB_PATH", str(db))
    monkeypatch.setattr(print_server, "_report_health",
                        lambda name, ok, detail="", **kw: alerts.append((name, ok, detail)))
    monkeypatch.setattr(print_server, "_send_whatsapp", lambda *a, **k: False)

    class Counter:
        path = str(db)
        alerts = None

        def rows(self, sql, *args):
            c = sqlite3.connect(db)
            c.row_factory = sqlite3.Row
            try:
                return [dict(r) for r in c.execute(sql, args)]
            finally:
                c.close()

        def job(self, job_id):
            found = self.rows("SELECT * FROM jobs WHERE job_id=?", job_id)
            return found[0] if found else None

    c = Counter()
    c.alerts = alerts
    return c


# ── /service-quote ────────────────────────────────────────────────────────────

def test_quote_prices_a_lamination_job(counter):
    r = print_server.handle_service_quote(
        {"kind": ["laminate"], "sheets": ["6"], "lam_type": ["pouch"], "paper_size": ["A4"]}
    )
    assert r["ok"] is True
    assert r["total"] == 420          # 6 x Rs.70
    assert r["label"] == "Lamination"
    assert r["needs_manual_price"] is False
    assert any("Pouch lamination" in line for line in r["breakdown"])


def test_quote_says_out_loud_when_a_minimum_did_the_billing(counter):
    r = print_server.handle_service_quote(
        {"kind": ["foil"], "sheets": ["3"], "paper_size": ["A4"]}
    )
    assert r["total"] == 300          # minimum 10 x Rs.30
    assert any("minimum 10 sheets applied" in line for line in r["breakdown"])


def test_quote_reads_every_supported_meta_type(counter):
    r = print_server.handle_service_quote(
        {"kind": ["cut"], "passes": ["4"], "with_our_job": ["false"]}
    )
    assert r["total"] == 100          # 4 x Rs.20 = Rs.80, floored to Rs.100
    free = print_server.handle_service_quote(
        {"kind": ["cut"], "passes": ["4"], "with_our_job": ["true"]}
    )
    assert free["total"] == 0


def test_quote_ignores_keys_it_does_not_know(counter):
    """A typo must not become a meta key the rate card then defaults away."""
    r = print_server.handle_service_quote(
        {"kind": ["scan"], "sheets": ["30"], "sheetz": ["9999"], "paper_size": ["A4"]}
    )
    assert r["total"] == 300          # 30 x Rs.10, the 9999 ignored


def test_unknown_kind_is_refused_and_alerts(counter):
    r = print_server.handle_service_quote({"kind": ["engraving"], "sheets": ["4"]})
    assert r["ok"] is False
    assert r["needs_manual_price"] is True
    assert r["unpriced"] is True
    assert any(name == "service.unknown_kind" and ok is False
               for name, ok, _ in counter.alerts)


def test_a_quantity_that_is_not_a_number_alerts_instead_of_quoting(counter):
    r = print_server.handle_service_quote({"kind": ["scan"], "sheets": ["twenty"]})
    assert r["ok"] is False
    assert r["needs_manual_price"] is True
    assert any(name == "service.quote" and ok is False for name, ok, _ in counter.alerts)


def test_quote_never_raises_when_the_rate_card_blows_up(counter, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rate table corrupt")
    monkeypatch.setattr(rate_card, "calculate_service_quote", boom)
    r = print_server.handle_service_quote({"kind": ["scan"], "sheets": ["10"]})
    assert r["ok"] is False
    assert r["needs_manual_price"] is True
    assert "manually" in r["error"]
    assert any(name == "service.quote" and ok is False for name, ok, _ in counter.alerts)


def test_a_healthy_quote_is_announced_once_not_per_keystroke(counter, monkeypatch):
    """ops_watchdog writes to SQLite on every call; the modal quotes on every key."""
    monkeypatch.setattr(print_server, "_service_quote_healthy_announced", False)
    for _ in range(5):
        print_server.handle_service_quote({"kind": ["scan"], "sheets": ["30"]})
    assert [a for a in counter.alerts if a[0] == "service.quote"] == [
        ("service.quote", True, "pricing services")
    ]


def test_recovery_is_announced_again_after_a_failure(counter, monkeypatch):
    monkeypatch.setattr(print_server, "_service_quote_healthy_announced", False)
    print_server.handle_service_quote({"kind": ["scan"], "sheets": ["30"]})
    print_server.handle_service_quote({"kind": ["scan"], "sheets": ["lots"]})
    print_server.handle_service_quote({"kind": ["scan"], "sheets": ["30"]})
    states = [ok for name, ok, _ in counter.alerts if name == "service.quote"]
    assert states == [True, False, True]


def test_quote_reports_the_deposit_for_a_big_job(counter):
    small = print_server.handle_service_quote(
        {"kind": ["scan"], "sheets": ["30"], "paper_size": ["A4"]})
    assert small["total"] == 300 and small["deposit_required"] == 0

    big = print_server.handle_service_quote(
        {"kind": ["scan"], "sheets": ["100"], "paper_size": ["A3"]})
    assert big["total"] == 1400
    assert big["deposit_required"] == 700          # 50 % over the Rs.500 threshold


# ── /new-service ──────────────────────────────────────────────────────────────

def test_new_service_creates_a_job_and_no_print_item(counter):
    r = print_server.handle_new_service({
        "kind": "laminate", "meta": {"sheets": 6, "lam_type": "pouch", "paper_size": "A4"},
        "customer_name": "Anu", "phone": "919000000000", "staff_id": "S1",
    })
    assert r["ok"] is True
    assert r["amount_quoted"] == 420
    assert r["status"] == "Queued"

    job = counter.job(r["job_id"])
    assert job["service_kind"] == "laminate"
    assert json.loads(job["service_meta"])["sheets"] == 6
    assert job["service_type"] == "Lamination"
    assert job["amount_quoted"] == 420
    assert job["filepath"] is None
    assert counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"]) == []


def test_new_service_writes_an_audit_event(counter):
    r = print_server.handle_new_service({
        "kind": "scan", "meta": {"sheets": 30}, "staff_id": "S1"})
    events = counter.rows("SELECT * FROM job_events WHERE job_id=?", r["job_id"])
    assert [e["action"] for e in events] == ["service_created_queued"]
    assert events[0]["to_status"] == "Queued"
    assert "kind=scan" in events[0]["notes"]


def test_new_service_migrates_a_pre_b2_database(counter):
    """The counter must work on a PC that has not restarted since B-2."""
    cols = {r["name"] for r in counter.rows("SELECT name FROM pragma_table_info('jobs')")}
    assert "service_kind" not in cols          # fixture is deliberately pre-v38
    r = print_server.handle_new_service({"kind": "punch", "meta": {"passes": 2}})
    assert r["ok"] is True
    assert counter.job(r["job_id"])["service_kind"] == "punch"


def test_new_service_refuses_an_unknown_kind_and_alerts(counter):
    r = print_server.handle_new_service({"kind": "engraving", "meta": {}})
    assert r["ok"] is False
    assert counter.rows("SELECT * FROM jobs") == []
    assert any(name == "service.unknown_kind" for name, _, _ in counter.alerts)


def test_new_service_flags_a_price_it_cannot_compute(counter):
    r = print_server.handle_new_service({"kind": "other", "meta": {"description": "Rebind"}})
    assert r["ok"] is True
    assert r["needs_manual_price"] is True
    assert r["amount_quoted"] == 0


def test_a_typed_price_settles_an_unpriceable_service(counter):
    r = print_server.handle_new_service(
        {"kind": "other", "meta": {"description": "Rebind", "manual_price": 250}})
    assert r["needs_manual_price"] is False
    assert r["amount_quoted"] == 250


def test_a_big_job_waits_in_draft_until_the_deposit_is_paid(counter):
    body = {"kind": "scan", "meta": {"sheets": 100, "paper_size": "A3"}, "staff_id": "S1"}
    unpaid = print_server.handle_new_service(dict(body))
    assert unpaid["status"] == "Draft"
    assert unpaid["deposit_required"] == 700
    assert unpaid["deposit_met"] is False
    assert counter.job(unpaid["job_id"])["queued_at"] is None

    short = print_server.handle_new_service(dict(body, amount_partial=300))
    assert short["status"] == "Draft"          # a part payment under the deposit

    paid = print_server.handle_new_service(dict(body, amount_partial=700))
    assert paid["status"] == "Queued"
    assert paid["deposit_met"] is True
    assert counter.job(paid["job_id"])["amount_partial"] == 700


def test_an_override_reason_starts_the_work_without_a_deposit(counter):
    r = print_server.handle_new_service({
        "kind": "scan", "meta": {"sheets": 100, "paper_size": "A3"},
        "override_reason": "regular customer, pays monthly"})
    assert r["status"] == "Queued"
    assert counter.job(r["job_id"])["override_reason"].startswith("regular customer")


def test_a_small_job_is_paid_on_collection(counter):
    r = print_server.handle_new_service({"kind": "scan", "meta": {"sheets": 30}})
    assert r["deposit_required"] == 0
    assert r["status"] == "Queued"
    assert counter.job(r["job_id"])["amount_collected"] is None


def test_service_jobs_share_the_daily_job_id_sequence(counter):
    a = print_server.handle_new_service({"kind": "scan", "meta": {"sheets": 5}})
    b = print_server.handle_create_job({"pages": 3, "amount_collected": 30})
    c = print_server.handle_new_service({"kind": "punch", "meta": {"passes": 1}})
    seqs = [int(j["job_id"].split("-")[-1]) for j in (a, b, c)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3


def test_an_explicit_amount_overrides_the_computed_one(counter):
    r = print_server.handle_new_service(
        {"kind": "scan", "meta": {"sheets": 30}, "amount_quoted": 250})
    assert r["amount_quoted"] == 250


# ── The service lifecycle uses the paths that already exist ───────────────────

def test_a_service_job_completes_through_the_normal_payment_path(counter):
    r = print_server.handle_new_service({"kind": "scan", "meta": {"sheets": 30}})
    done = print_server.handle_complete_job(
        {"job_id": r["job_id"], "amount_collected": 300, "payment_mode": "UPI", "staff_id": "S1"})
    assert done["ok"] is True
    job = counter.job(r["job_id"])
    assert job["status"] == "Completed"
    assert job["amount_collected"] == 300
    assert job["service_kind"] == "scan"
    assert not [a for a in counter.alerts if a[0] == "service.unpriced"]


def test_a_service_collected_for_nothing_alerts(counter):
    r = print_server.handle_new_service({"kind": "scan", "meta": {"sheets": 30}})
    print_server.handle_complete_job(
        {"job_id": r["job_id"], "amount_collected": 0, "staff_id": "S1"})
    unpriced = [a for a in counter.alerts if a[0] == "service.unpriced"]
    assert unpriced and unpriced[0][1] is False
    assert r["job_id"] in unpriced[0][2]


def test_a_free_service_with_a_reason_does_not_alert(counter):
    r = print_server.handle_new_service({
        "kind": "punch", "meta": {"passes": 2, "with_our_job": True},
        "override_reason": "part of the binding job next door"})
    print_server.handle_complete_job({"job_id": r["job_id"], "amount_collected": 0})
    assert not [a for a in counter.alerts if a[0] == "service.unpriced"]


def test_a_print_job_collected_for_nothing_is_not_a_service_alert(counter):
    """/complete-job must behave exactly as it did for every print job."""
    job = print_server.handle_create_job({"pages": 3, "override_reason": "reprint"})
    print_server.handle_complete_job({"job_id": job["job_id"], "amount_collected": 0})
    assert not [a for a in counter.alerts if a[0] == "service.unpriced"]


def test_complete_job_survives_a_pre_b2_database(counter):
    """No service job can exist there, so the check must return silently."""
    job = print_server.handle_create_job({"pages": 3, "amount_collected": 30})
    r = print_server.handle_complete_job({"job_id": job["job_id"], "amount_collected": 0})
    assert r["ok"] is True
