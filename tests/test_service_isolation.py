"""
B-3 guard test — a service job never reaches a printer.

This is the file that should fail loudly if someone later "unifies" the service
path with the print path. Four claims, each checked against the code that would
have to break for it to stop being true:

  1. /new-service writes no print_items row.
  2. /print refuses a service job instead of auto-creating a print item for it.
  3. store_puller cannot pull one (no file_url, and it only pulls status Paid).
  4. /create-job with a service_kind skips the print_items insert it otherwise does.

Plus the other half of the contract: a job WITHOUT service_kind must behave
exactly as it did before B-3 existed.
"""

import os
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import print_server
import store_puller
from test_service_jobs import JOBS_DDL, PRINT_ITEMS_DDL, counter    # noqa: F401

REPO = os.path.join(os.path.dirname(__file__), "..")


# ── 1 + 4. No print_items row, ever ───────────────────────────────────────────

@pytest.mark.parametrize("kind,meta", [
    ("copy",     {"sheets": 10}),
    ("scan",     {"sheets": 30}),
    ("laminate", {"sheets": 6, "lam_type": "pouch"}),
    ("foil",     {"sheets": 12}),
    ("bind",     {"sheets": 50, "binding": "spiral"}),
    ("cut",      {"passes": 3}),
    ("punch",    {"passes": 3}),
    ("photo",    {"unit": "set5", "qty": 2}),
    ("dtp",      {"language": "english", "pages": 4}),
    ("other",    {"description": "odd job", "manual_price": 90}),
])
def test_no_service_kind_ever_gets_a_print_item(counter, kind, meta):
    r = print_server.handle_new_service({"kind": kind, "meta": meta})
    assert r["ok"] is True
    assert counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"]) == []


def test_create_job_skips_the_print_item_for_a_service(counter):
    r = print_server.handle_create_job({
        "pages": 12, "amount_collected": 200,
        "service_kind": "laminate", "service_meta": {"sheets": 12, "lam_type": "pouch"},
    })
    assert r["ok"] is True
    assert counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"]) == []
    job = counter.job(r["job_id"])
    assert job["service_kind"] == "laminate"


def test_create_job_still_makes_a_print_item_for_a_print_job(counter):
    """The other half of the guard: absent service_kind, nothing changed."""
    r = print_server.handle_create_job({"pages": 12, "amount_collected": 200})
    items = counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"])
    assert len(items) == 1
    assert items[0]["paper_type"] == "A4_BW"
    assert items[0]["status"] == "Pending"
    # A print job does not even trigger the v38 migration — the print path is
    # not asked to carry the service work's schema.
    cols = {c["name"] for c in counter.rows("SELECT name FROM pragma_table_info('jobs')")}
    assert "service_kind" not in cols


def test_create_job_refuses_an_unknown_service_kind(counter):
    r = print_server.handle_create_job({
        "pages": 3, "amount_collected": 30, "service_kind": "engraving"})
    assert r["ok"] is False
    assert counter.rows("SELECT * FROM jobs") == []
    assert any(name == "service.unknown_kind" for name, _, _ in counter.alerts)


def test_create_job_rejects_a_non_object_service_meta(counter):
    r = print_server.handle_create_job({
        "pages": 3, "amount_collected": 30,
        "service_kind": "scan", "service_meta": "sheets=30"})
    assert r["ok"] is False
    assert counter.rows("SELECT * FROM jobs") == []


# ── 2. /print refuses it ──────────────────────────────────────────────────────

def test_print_refuses_a_service_job(counter, monkeypatch):
    fired = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fired.append(a))

    r = print_server.handle_new_service({"kind": "laminate", "meta": {"sheets": 6}})
    out = print_server.handle_print_item(r["job_id"], 1, staff_id="S1")

    assert out["ok"] is False
    assert "nothing to print" in out["error"]
    assert "Lamination" in out["error"]
    assert fired == [], "a service job reached the printer"
    # and the refusal did not invent a print item on the way out
    assert counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"]) == []


def test_print_still_auto_creates_an_item_for_a_print_job(counter, monkeypatch):
    """Absent service_kind, handle_print_item's auto-create path is untouched."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    job = print_server.handle_create_job({"pages": 3, "amount_collected": 30})
    conn = sqlite3.connect(counter.path)
    conn.execute("DELETE FROM print_items WHERE job_id=?", (job["job_id"],))
    conn.commit()
    conn.close()

    print_server.handle_print_item(job["job_id"], 1, staff_id="S1")
    assert len(counter.rows("SELECT * FROM print_items WHERE job_id=?", job["job_id"])) == 1


# ── 3. store_puller cannot see one ────────────────────────────────────────────

def test_store_puller_pulls_only_paid_jobs_with_a_file():
    """A service job has neither, so it is structurally unpullable.

    Read from the source rather than mocked, because the property that matters
    is what the query says — a future edit that drops the file_url filter is
    exactly what this should catch.
    """
    src = open(store_puller.__file__, encoding="utf-8").read()
    assert '.eq("status", "Paid")' in src
    assert "file_url" in src
    assert "PULLABLE_STATUSES" in src
    assert "Paid" in store_puller.PULLABLE_STATUSES


def test_store_puller_does_not_ask_for_the_service_columns():
    """It selects an explicit column list; service columns are not in it."""
    for col in ("service_kind", "service_meta"):
        assert col not in store_puller._JOB_COLUMNS


def test_a_service_job_has_no_file_to_pull(counter):
    r = print_server.handle_new_service({"kind": "scan", "meta": {"sheets": 30}})
    job = counter.job(r["job_id"])
    assert job["filepath"] is None
    assert job["status"] == "Queued"          # never "Paid", which is what pulls


# ── The print path is byte-identical without service_kind ─────────────────────

def test_a_print_job_is_indistinguishable_from_before(counter):
    r = print_server.handle_create_job({
        "pages": 34, "colour": "bw", "sides": "ds", "copies": 2,
        "paper_size": "A4", "finishing": "spiral", "amount_collected": 200,
        "customer_name": "Anu", "phone": "919000000000",
    })
    job = counter.job(r["job_id"])
    assert job["status"] == "Queued"
    assert job["page_count"] == 34
    items = counter.rows("SELECT * FROM print_items WHERE job_id=?", r["job_id"])
    assert len(items) == 1 and items[0]["copies"] == 2 and items[0]["sides"] == "ds"


def test_service_kind_has_exactly_two_writers():
    """One door in: /new-service inserts it, /create-job updates it. Nothing else.

    A third writer would mean a path that can file a service job without going
    through the kind validation both of these do.
    """
    src = open(os.path.join(REPO, "print_server.py"), encoding="utf-8").read()
    assert src.count("SET service_kind") == 1
    assert src.count("           service_kind, service_meta)") == 1
    # Counting ensure_job_service_columns() calls used to stand in for this, and
    # stopped meaning it in B-8: the inter-store handlers call it to add the
    # transfer columns without writing service_kind at all. Assert the writers
    # themselves instead of a proxy that drifts.
    import inspect
    import types
    writers = set()
    for name, obj in vars(print_server).items():
        if not isinstance(obj, types.FunctionType):
            continue
        if getattr(obj, "__module__", "") != "print_server":
            continue
        try:
            body = inspect.getsource(obj)
        except OSError:                       # pragma: no cover
            continue
        if "SET service_kind" in body or "service_kind, service_meta)" in body:
            writers.add(name)
    assert writers == {"handle_create_job", "handle_new_service"}, \
        f"unexpected writers of service_kind: {sorted(writers)}"
