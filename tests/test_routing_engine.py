"""Unit tests for routing.engine (block 3 of plan v2)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from routing.engine import (
    Candidate,
    JobSpec,
    RoutingDecision,
    decide,
    load_eligible_partners,
    record_decision,
)


# --- candidate factories --------------------------------------------------


def _osp(**overrides) -> Candidate:
    base = dict(
        store_id="OSP",
        name="Oxygen Students Paradise",
        kyc_status="active",
        capabilities={"colour": True, "max_paper_size": "A3",
                      "finishing": ["spiral", "wiro", "strip"]},
        capacity_jobs_per_day=0,
        pickup_hours={"mon": [9, 21], "tue": [9, 21], "wed": [9, 21],
                      "thu": [9, 21], "fri": [9, 21], "sat": [9, 21],
                      "sun": [10, 18]},
        geo_lat=10.5276, geo_lng=76.2144,
        queue_depth=0,
        jobs_today=0,
    )
    base.update(overrides)
    return Candidate(**base)


def _store2(**overrides) -> Candidate:
    base = dict(
        store_id="STORE2",
        name="Print Pro",
        kyc_status="active",
        capabilities={"colour": True, "max_paper_size": "A4",
                      "finishing": ["spiral"]},
        capacity_jobs_per_day=20,
        pickup_hours={"mon": [9, 21], "tue": [9, 21], "wed": [9, 21],
                      "thu": [9, 21], "fri": [9, 21], "sat": [9, 21],
                      "sun": [10, 18]},
        geo_lat=10.5300, geo_lng=76.2160,
        queue_depth=0,
        jobs_today=0,
    )
    base.update(overrides)
    return Candidate(**base)


def _job(**overrides) -> JobSpec:
    base = dict(
        job_id="J1",
        needs_colour=False,
        paper_size="A4",
        finishing=(),
        pickup_lat=10.5290,
        pickup_lng=76.2150,
    )
    base.update(overrides)
    return JobSpec(**base)


# A weekday afternoon when both stores' default pickup_hours include this hour.
NOW = datetime(2026, 5, 4, 15, 0, tzinfo=timezone.utc)


# --- decide() -------------------------------------------------------------


class TestDecide:
    def test_single_eligible_store_is_picked(self):
        d = decide(_job(), [_osp()], now=NOW)
        assert d.chosen_store_id == "OSP"
        assert d.reason == "highest_score"
        assert d.reroute_count == 0
        assert d.eligible_store_ids == ("OSP",)

    def test_no_eligible_returns_none(self):
        d = decide(_job(), [_osp(kyc_status="pending")], now=NOW)
        assert d.chosen_store_id is None
        assert d.reason == "no_eligible_store"
        assert "kyc_status='pending'" in (d.notes or "")

    def test_capability_mismatch_excludes_store(self):
        d = decide(_job(paper_size="A3"), [_store2()], now=NOW)
        assert d.chosen_store_id is None
        assert "capability_mismatch" in (d.notes or "")

    def test_finishing_must_be_subset(self):
        d = decide(_job(finishing=("wiro",)), [_store2()], now=NOW)
        assert d.chosen_store_id is None
        assert "capability_mismatch" in (d.notes or "")

    def test_colour_filter_excludes_mono_only(self):
        mono_only = _osp(capabilities={"colour": False, "max_paper_size": "A3",
                                       "finishing": ["spiral"]})
        d = decide(_job(needs_colour=True), [mono_only], now=NOW)
        assert d.chosen_store_id is None

    def test_queue_depth_penalises(self):
        d = decide(_job(), [_osp(queue_depth=10), _store2()], now=NOW)
        assert d.chosen_store_id == "STORE2"

    def test_round_robin_tiebreak_lex_when_jobs_today_equal(self):
        a = _osp(store_id="A", capacity_jobs_per_day=10, jobs_today=5,
                 queue_depth=0, geo_lat=10.529, geo_lng=76.215)
        b = _osp(store_id="B", capacity_jobs_per_day=10, jobs_today=5,
                 queue_depth=0, geo_lat=10.529, geo_lng=76.215)
        d = decide(_job(), [a, b], now=NOW)
        assert d.chosen_store_id == "A"
        assert d.reason == "round_robin_tiebreak"

    def test_round_robin_breaks_in_favour_of_fewer_jobs_today(self):
        a = _osp(store_id="A", capacity_jobs_per_day=0, jobs_today=10,
                 queue_depth=0)
        b = _osp(store_id="B", capacity_jobs_per_day=0, jobs_today=2,
                 queue_depth=0)
        d = decide(_job(), [a, b], now=NOW)
        assert d.chosen_store_id == "B"
        assert d.reason == "round_robin_tiebreak"

    def test_excluded_store_ids_skipped(self):
        d = decide(_job(), [_osp(), _store2()], now=NOW,
                   excluded_store_ids=("OSP",))
        assert d.chosen_store_id == "STORE2"
        assert "excluded_by_caller" in (d.notes or "")

    def test_reroute_increments_decoration(self):
        d = decide(_job(), [_osp()], now=NOW, reroute_count=2)
        assert d.reroute_count == 2
        assert d.reason.startswith("reroute_after_failure:")

    def test_closed_store_excluded(self):
        sunday_4am = datetime(2026, 5, 3, 4, 0, tzinfo=timezone.utc)
        d = decide(_job(), [_osp()], now=sunday_4am)
        assert d.chosen_store_id is None
        assert "closed_now" in (d.notes or "")

    def test_capacity_exhausted_excluded(self):
        full = _osp(capacity_jobs_per_day=5, jobs_today=5)
        d = decide(_job(), [full], now=NOW)
        assert d.chosen_store_id is None
        assert "capacity_exhausted" in (d.notes or "")

    def test_distance_beyond_max_excluded(self):
        far = _osp(geo_lat=12.9716, geo_lng=77.5946)  # Bangalore
        d = decide(_job(), [far], now=NOW)
        assert d.chosen_store_id is None
        assert "out_of_range" in (d.notes or "")


# --- DB hydration / persistence (FakeClient) ------------------------------


class _FakeQuery:
    def __init__(self, parent, table):
        self.parent = parent
        self.table_name = table
        self.mode = "select"
        self.payload = None
        self.filters = {}

    def select(self, *cols):
        self.mode = "select"
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = payload
        return self

    def eq(self, col, value):
        self.filters[col] = value
        return self

    def execute(self):
        class R:
            pass
        r = R()
        if self.mode == "select":
            rows = self.parent.tables.get(self.table_name, [])
            for col, val in self.filters.items():
                rows = [row for row in rows if row.get(col) == val]
            r.data = rows
        else:
            self.parent.tables.setdefault(self.table_name, []).append(self.payload)
            r.data = [self.payload]
        return r


class _FakeClient:
    def __init__(self, **tables):
        self.tables = {k: list(v) for k, v in tables.items()}

    def table(self, name):
        return _FakeQuery(self, name)


def test_load_eligible_partners_filters_inactive():
    client = _FakeClient(partners=[
        {"store_id": "OSP", "name": "Oxygen", "kyc_status": "active",
         "capabilities_json": {"colour": True, "max_paper_size": "A3"},
         "capacity_jobs_per_day": 0,
         "pickup_hours_json": {"mon": [9, 21]},
         "geo_lat": 10.5, "geo_lng": 76.2},
        {"store_id": "S2", "name": "Pending Store", "kyc_status": "pending",
         "capabilities_json": {}, "capacity_jobs_per_day": 0,
         "pickup_hours_json": {}, "geo_lat": None, "geo_lng": None},
    ])
    out = load_eligible_partners(client)
    assert [c.store_id for c in out] == ["OSP"]
    assert out[0].capabilities == {"colour": True, "max_paper_size": "A3"}


def test_record_decision_writes_row():
    client = _FakeClient(routing_decisions=[])
    d = RoutingDecision(
        job_id="J1",
        eligible_store_ids=("OSP",),
        scores={"OSP": 12.4},
        chosen_store_id="OSP",
        reason="highest_score",
        reroute_count=0,
        notes=None,
    )
    record_decision(client, d)
    rows = client.tables["routing_decisions"]
    assert len(rows) == 1
    assert rows[0]["job_id"] == "J1"
    assert rows[0]["chosen_store_id"] == "OSP"
    assert rows[0]["reason"] == "highest_score"
    assert rows[0]["scores_json"] == {"OSP": 12.4}


def test_record_decision_handles_no_chosen_store():
    client = _FakeClient(routing_decisions=[])
    d = RoutingDecision(
        job_id="J2",
        eligible_store_ids=(),
        scores={},
        chosen_store_id=None,
        reason="no_eligible_store",
    )
    record_decision(client, d)
    assert client.tables["routing_decisions"][0]["chosen_store_id"] == "_NONE_"
