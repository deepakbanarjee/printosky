"""
Device coordination: many boxes per store, one owner per job of work.

Nattika proved the data half of this (388 printer jobs imported twice by two
boxes). The expensive half is paper: two boxes auto-printing the same paid job.
These tests pin both — the role lease that elects a single poller without any
per-box configuration, and the atomic claim that makes printing exactly-once.
"""

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import device_lease as dl


NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    """Records the filters applied, then serves whatever the table decides."""

    def __init__(self, table, op, payload=None):
        self.t, self.op, self.payload = table, op, payload
        self.filters = {}

    def select(self, *a, **k):  return self
    def limit(self, *a, **k):   return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def is_(self, col, val):
        self.filters[col] = None if str(val).lower() in ("null", "none") else val
        return self

    def execute(self):
        return self.t._execute(self)


class _Table:
    def __init__(self, name, store):
        self.name, self.store = name, store

    def select(self, *a, **k): return _Query(self, "select")
    def update(self, payload):  return _Query(self, "update", payload)
    def insert(self, payload):  return _Query(self, "insert", payload)
    def upsert(self, payload):  return _Query(self, "upsert", payload)

    def _execute(self, q):
        return self.store.execute(self.name, q)


class FakeSupabase:
    """Row store with the one semantic that matters: a conditional UPDATE only
    touches rows whose current values match every filter."""

    def __init__(self, rows=None, fail=False):
        self.rows = rows or {"store_role_leases": [], "jobs": [], "store_devices": []}
        self.fail = fail
        self.calls = []

    def table(self, name):
        if self.fail:
            raise RuntimeError("supabase unreachable")
        return _Table(name, self)

    def _match(self, row, filters):
        return all(row.get(k) == v for k, v in filters.items())

    def execute(self, name, q):
        rows = self.rows.setdefault(name, [])
        self.calls.append((name, q.op, dict(q.filters)))
        if q.op == "select":
            return _Result([r for r in rows if self._match(r, q.filters)])
        if q.op in ("insert", "upsert"):
            rows.append(dict(q.payload))
            return _Result([dict(q.payload)])
        if q.op == "update":
            hit = [r for r in rows if self._match(r, q.filters)]
            for r in hit:
                r.update(q.payload)
            return _Result([dict(r) for r in hit])
        raise AssertionError(q.op)


@pytest.fixture
def lease(monkeypatch):
    monkeypatch.setattr(dl, "_store_id", lambda: "PRINTK")
    monkeypatch.setattr(dl, "_now", lambda: NOW)
    monkeypatch.setattr(dl, "_device_id", "box-A", raising=False)
    monkeypatch.setattr(dl, "device_id", lambda: "box-A")
    monkeypatch.setattr(dl, "_report", lambda *a, **k: None)
    return dl


def _as(monkeypatch, name):
    monkeypatch.setattr(dl, "device_id", lambda: name)


# ── Role leases ───────────────────────────────────────────────────────────────

def test_first_box_takes_the_lease(lease, monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(dl, "_client", lambda: db)
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is True
    row = db.rows["store_role_leases"][0]
    assert row["owner_device"] == "box-A"
    assert row["role"] == "poll_printers"


def test_second_box_stands_by(lease, monkeypatch):
    db = FakeSupabase({"store_role_leases": [{
        "store_id": "PRINTK", "role": "poll_printers", "owner_device": "box-A",
        "expires_at": (NOW + timedelta(minutes=2)).isoformat(),
    }]})
    monkeypatch.setattr(dl, "_client", lambda: db)
    _as(monkeypatch, "box-B")
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is False
    assert db.rows["store_role_leases"][0]["owner_device"] == "box-A"


def test_the_holder_renews_without_losing_the_lease(lease, monkeypatch):
    db = FakeSupabase({"store_role_leases": [{
        "store_id": "PRINTK", "role": "poll_printers", "owner_device": "box-A",
        "acquired_at": NOW.isoformat(),
        "expires_at": (NOW + timedelta(seconds=30)).isoformat(),
    }]})
    monkeypatch.setattr(dl, "_client", lambda: db)
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is True
    row = db.rows["store_role_leases"][0]
    assert row["owner_device"] == "box-A"
    assert dl._parse(row["expires_at"]) > NOW + timedelta(seconds=30)   # extended
    assert row["acquired_at"] == NOW.isoformat()                        # not reset


def test_a_dead_holders_lease_is_taken_over(lease, monkeypatch):
    """The whole point: a box is switched off and another takes the work up
    within a TTL, with nobody editing a config file."""
    db = FakeSupabase({"store_role_leases": [{
        "store_id": "PRINTK", "role": "poll_printers", "owner_device": "box-A",
        "expires_at": (NOW - timedelta(minutes=5)).isoformat(),
    }]})
    monkeypatch.setattr(dl, "_client", lambda: db)
    _as(monkeypatch, "box-B")
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is True
    assert db.rows["store_role_leases"][0]["owner_device"] == "box-B"


def test_only_one_box_wins_a_race_for_an_expired_lease(lease, monkeypatch):
    db = FakeSupabase({"store_role_leases": [{
        "store_id": "PRINTK", "role": "poll_printers", "owner_device": "box-A",
        "expires_at": (NOW - timedelta(minutes=5)).isoformat(),
    }]})
    monkeypatch.setattr(dl, "_client", lambda: db)

    _as(monkeypatch, "box-B")
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is True     # B takes over from expired A
    _as(monkeypatch, "box-C")
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is False    # C's compare-and-set misses


def test_roles_are_independent(lease, monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(dl, "_client", lambda: db)
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is True
    _as(monkeypatch, "box-B")
    assert dl.hold(dl.ROLE_PRINT_JOBS) is True        # a different job of work
    owners = {r["role"]: r["owner_device"] for r in db.rows["store_role_leases"]}
    assert owners == {"poll_printers": "box-A", "print_jobs": "box-B"}


def test_release_lets_a_peer_take_over_immediately(lease, monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(dl, "_client", lambda: db)
    dl.hold(dl.ROLE_POLL_PRINTERS)
    dl.release(dl.ROLE_POLL_PRINTERS)
    assert db.rows["store_role_leases"][0]["owner_device"] is None
    _as(monkeypatch, "box-B")
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is True


def test_owner_reports_the_current_holder_and_ignores_a_stale_one(lease, monkeypatch):
    db = FakeSupabase({"store_role_leases": [{
        "store_id": "PRINTK", "role": "poll_printers", "owner_device": "box-A",
        "expires_at": (NOW + timedelta(minutes=1)).isoformat(),
    }]})
    monkeypatch.setattr(dl, "_client", lambda: db)
    assert dl.owner(dl.ROLE_POLL_PRINTERS) == "box-A"

    db.rows["store_role_leases"][0]["expires_at"] = (NOW - timedelta(minutes=1)).isoformat()
    assert dl.owner(dl.ROLE_POLL_PRINTERS) is None


# ── Offline behaviour ─────────────────────────────────────────────────────────

def test_without_the_cloud_a_lease_falls_back_to_the_config_flag(lease, monkeypatch):
    """Polling twice is a data annoyance; polling never is a blind store."""
    monkeypatch.setattr(dl, "_client", lambda: None)
    import types
    monkeypatch.setattr(dl, "_offline_fallback", dl._offline_fallback)

    import store_config
    monkeypatch.setattr(store_config, "get_store_config",
                        lambda: types.SimpleNamespace(poll_printers=True))
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is True

    monkeypatch.setattr(store_config, "get_store_config",
                        lambda: types.SimpleNamespace(poll_printers=False))
    assert dl.hold(dl.ROLE_POLL_PRINTERS) is False


# ── Exactly-once printing ─────────────────────────────────────────────────────

def test_the_first_box_to_claim_a_job_prints_it(lease, monkeypatch):
    db = FakeSupabase({"jobs": [{"job_id": "OSP-1", "print_claimed_at": None}]})
    monkeypatch.setattr(dl, "_client", lambda: db)
    assert dl.claim_job("OSP-1") is True
    assert db.rows["jobs"][0]["print_claimed_by"] == "box-A"


def test_a_second_box_does_not_print_the_same_job(lease, monkeypatch):
    """This is the one that costs paper."""
    db = FakeSupabase({"jobs": [{"job_id": "OSP-1", "print_claimed_at": None}]})
    monkeypatch.setattr(dl, "_client", lambda: db)
    assert dl.claim_job("OSP-1") is True

    _as(monkeypatch, "box-B")
    assert dl.claim_job("OSP-1") is False
    assert db.rows["jobs"][0]["print_claimed_by"] == "box-A"


def test_a_claim_that_cannot_be_written_fails_closed(lease, monkeypatch):
    """No claim, no print: wasted paper cannot be undone, a delayed print can."""
    monkeypatch.setattr(dl, "_client", lambda: FakeSupabase(fail=True))
    assert dl.claim_job("OSP-1") is False

    monkeypatch.setattr(dl, "_client", lambda: None)
    assert dl.claim_job("OSP-1") is False


def test_a_failed_print_hands_the_job_back(lease, monkeypatch):
    db = FakeSupabase({"jobs": [{"job_id": "OSP-1", "print_claimed_at": None}]})
    monkeypatch.setattr(dl, "_client", lambda: db)
    dl.claim_job("OSP-1")
    dl.release_job("OSP-1")
    assert db.rows["jobs"][0]["print_claimed_at"] is None

    _as(monkeypatch, "box-B")
    assert dl.claim_job("OSP-1") is True          # now another box can take it


def test_release_job_only_clears_our_own_claim(lease, monkeypatch):
    db = FakeSupabase({"jobs": [{"job_id": "OSP-1", "print_claimed_at": None}]})
    monkeypatch.setattr(dl, "_client", lambda: db)
    dl.claim_job("OSP-1")                          # box-A owns it
    _as(monkeypatch, "box-B")
    dl.release_job("OSP-1")                        # B must not free A's claim
    assert db.rows["jobs"][0]["print_claimed_by"] == "box-A"


# ── Identity ──────────────────────────────────────────────────────────────────

def test_device_id_is_stable_across_restarts(tmp_path, monkeypatch):
    monkeypatch.delenv("PRINTOSKY_DEVICE_ID", raising=False)
    monkeypatch.setattr(dl, "_id_file", lambda: tmp_path / "device_id.txt")
    monkeypatch.setattr(dl, "_device_id", None, raising=False)
    first = dl.device_id()

    monkeypatch.setattr(dl, "_device_id", None, raising=False)   # restart
    assert dl.device_id() == first


def test_device_id_can_be_pinned_by_env(monkeypatch):
    monkeypatch.setenv("PRINTOSKY_DEVICE_ID", "counter-1")
    monkeypatch.setattr(dl, "_device_id", None, raising=False)
    assert dl.device_id() == "counter-1"
