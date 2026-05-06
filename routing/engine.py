"""
Routing engine v1 — deliberately stupid (block 3 of plan v2).

The engine takes a paid job and the list of currently-eligible partner
stores, and decides which store should fulfil the job. Every decision is
logged to ``routing_decisions`` for fairness audits and v2 ML training.

Design rules (from plan v2):

- Filter eligible stores: ``kyc_status='active'``, capability matches the
  job spec, hours-of-day match.
- Score = ``(capacity_remaining_today * w1) - (queue_depth * w2) - (distance_km * w3)``.
- Pick the highest score. On exact tie, round-robin (the partner who has
  fulfilled the *fewest* jobs in the last 24h wins the tie).
- 60-second ack timeout in the dispatcher (block 5) re-routes via this
  engine with ``reroute_count`` incremented.

What this module does NOT do:

- Send the dispatch message. That's block 5 (`whatsapp_notify` /
  store-side WhatsApp bot).
- Decide what counts as "queue depth" or "capacity remaining". For v1
  these are simple counts of jobs assigned to the store with status in
  certain values; the engine takes them as inputs from the caller so the
  module is unit-testable.

Don't build a v2 (ML on historical SLA, dynamic take-rate, surge) until
v1 has run for 200+ jobs across 2+ stores.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from typing import Iterable, Protocol

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JobSpec:
    """Routing-relevant subset of a paid job. Decoupled from the DB row
    so the engine is easy to test and stable across schema additions."""
    job_id: str
    needs_colour: bool = False
    paper_size: str = "A4"               # "A4" | "A3" | ...
    finishing: tuple[str, ...] = ()      # e.g. ("spiral",) — empty = none
    pickup_lat: float | None = None
    pickup_lng: float | None = None


@dataclass(frozen=True)
class Candidate:
    """A partner store the engine may pick. Hydrated from ``partners`` +
    live counters (queue depth, jobs-today). Caller is responsible for
    populating ``queue_depth`` and ``jobs_today`` from the DB at
    decide-time."""
    store_id: str
    name: str
    kyc_status: str
    capabilities: dict
    capacity_jobs_per_day: int           # 0 = unlimited
    pickup_hours: dict                   # {"mon":[9,21], ...}
    geo_lat: float | None
    geo_lng: float | None
    queue_depth: int = 0                 # currently-printing or accepted-not-printed
    jobs_today: int = 0                  # for capacity + tie-break round-robin


@dataclass(frozen=True)
class RoutingDecision:
    job_id: str
    eligible_store_ids: tuple[str, ...]
    scores: dict[str, float]
    chosen_store_id: str | None
    reason: str
    reroute_count: int = 0
    notes: str | None = None


# ---------------------------------------------------------------------------
# Tunable weights (intentionally simple)
# ---------------------------------------------------------------------------

W_CAPACITY = 1.0
W_QUEUE = 2.0
W_DISTANCE = 0.5

# Walking-distance-pickup MVP: 8 km is generous for Thrissur city.
MAX_DISTANCE_KM = 8.0


# ---------------------------------------------------------------------------
# Eligibility filter
# ---------------------------------------------------------------------------


def _hour_now(now: datetime) -> tuple[str, int]:
    weekday_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    return weekday_key, now.hour


def _is_open(candidate: Candidate, now: datetime) -> bool:
    if not candidate.pickup_hours:
        # Default: always open. Onboarding will populate this; missing
        # hours during cutover should not exclude a store.
        return True
    weekday, hour = _hour_now(now)
    window = candidate.pickup_hours.get(weekday)
    if not window or len(window) < 2:
        return False
    open_h, close_h = int(window[0]), int(window[1])
    return open_h <= hour < close_h


def _capability_matches(candidate: Candidate, job: JobSpec) -> bool:
    caps = candidate.capabilities or {}
    if job.needs_colour and not caps.get("colour", False):
        return False
    max_size = caps.get("max_paper_size", "A4")
    # paper-size ordering: A6 < A5 < A4 < A3 < ...
    size_rank = {"A6": 0, "A5": 1, "A4": 2, "A3": 3, "A2": 4, "A1": 5, "A0": 6}
    if size_rank.get(job.paper_size, 99) > size_rank.get(max_size, 99):
        return False
    if job.finishing:
        store_finishing = set(caps.get("finishing", []) or [])
        if not set(job.finishing).issubset(store_finishing):
            return False
    return True


def _capacity_remaining(candidate: Candidate) -> int | None:
    """``None`` means unlimited capacity (cap == 0)."""
    if candidate.capacity_jobs_per_day == 0:
        return None
    return max(0, candidate.capacity_jobs_per_day - candidate.jobs_today)


def _has_capacity(candidate: Candidate) -> bool:
    remaining = _capacity_remaining(candidate)
    return remaining is None or remaining > 0


def _haversine_km(lat1, lng1, lat2, lng2) -> float:
    if None in (lat1, lng1, lat2, lng2):
        return 0.0  # unknown distance treated as adjacent (don't penalise)
    r = 6371.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lng2 - lng1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _distance_km(candidate: Candidate, job: JobSpec) -> float:
    return _haversine_km(
        candidate.geo_lat, candidate.geo_lng,
        job.pickup_lat, job.pickup_lng,
    )


def _eligible(candidate: Candidate, job: JobSpec, now: datetime) -> tuple[bool, str | None]:
    if candidate.kyc_status != "active":
        return False, f"kyc_status={candidate.kyc_status!r}"
    if not _is_open(candidate, now):
        return False, "closed_now"
    if not _capability_matches(candidate, job):
        return False, "capability_mismatch"
    if not _has_capacity(candidate):
        return False, "capacity_exhausted"
    if _distance_km(candidate, job) > MAX_DISTANCE_KM:
        return False, "out_of_range"
    return True, None


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score(candidate: Candidate, job: JobSpec) -> float:
    capacity_left = _capacity_remaining(candidate)
    # Unlimited capacity is treated as equivalent to a 20-cap store with no
    # jobs done yet. Picking a large sentinel here would let unlimited-cap
    # stores swamp the queue/distance penalties, defeating the point of v1.
    capacity_term = float(capacity_left) if capacity_left is not None else 20.0
    queue_term = float(candidate.queue_depth)
    distance_term = _distance_km(candidate, job)
    return (
        W_CAPACITY * capacity_term
        - W_QUEUE * queue_term
        - W_DISTANCE * distance_term
    )


# ---------------------------------------------------------------------------
# Public decision API
# ---------------------------------------------------------------------------


def decide(
    job: JobSpec,
    candidates: Iterable[Candidate],
    *,
    now: datetime | None = None,
    reroute_count: int = 0,
    excluded_store_ids: Iterable[str] = (),
) -> RoutingDecision:
    """Return the routing decision for a job.

    Pure function; logs are emitted via the standard logger but no DB
    write happens here. Use ``record_decision`` to persist.
    """
    now = now or datetime.now(timezone.utc)
    excluded = set(excluded_store_ids)

    eligible: list[Candidate] = []
    rejection_notes: list[str] = []
    for c in candidates:
        if c.store_id in excluded:
            rejection_notes.append(f"{c.store_id}: excluded_by_caller")
            continue
        ok, why = _eligible(c, job, now)
        if not ok:
            rejection_notes.append(f"{c.store_id}: {why}")
            continue
        eligible.append(c)

    if not eligible:
        return RoutingDecision(
            job_id=job.job_id,
            eligible_store_ids=(),
            scores={},
            chosen_store_id=None,
            reason="no_eligible_store",
            reroute_count=reroute_count,
            notes="; ".join(rejection_notes) or None,
        )

    scores = {c.store_id: _score(c, job) for c in eligible}
    top_score = max(scores.values())
    top_ids = [c.store_id for c in eligible if scores[c.store_id] == top_score]

    if len(top_ids) == 1:
        chosen = top_ids[0]
        reason = "highest_score"
    else:
        # Round-robin on tie: candidate with fewest jobs today wins.
        # Stable secondary tie-break: store_id lexicographic.
        top_candidates = [c for c in eligible if c.store_id in top_ids]
        top_candidates.sort(key=lambda c: (c.jobs_today, c.store_id))
        chosen = top_candidates[0].store_id
        reason = "round_robin_tiebreak"

    if reroute_count > 0:
        reason = f"reroute_after_failure:{reason}"

    return RoutingDecision(
        job_id=job.job_id,
        eligible_store_ids=tuple(c.store_id for c in eligible),
        scores=scores,
        chosen_store_id=chosen,
        reason=reason,
        reroute_count=reroute_count,
        notes="; ".join(rejection_notes) or None,
    )


# ---------------------------------------------------------------------------
# DB hydration + persistence (thin wrappers; isolated for test mockability)
# ---------------------------------------------------------------------------


class _SupabaseLike(Protocol):
    def table(self, name: str): ...  # noqa: D401, ANN201


def load_eligible_partners(client: _SupabaseLike) -> list[Candidate]:
    """Load all active partners from Supabase.

    Returns ``Candidate`` instances with ``queue_depth`` and ``jobs_today``
    set to 0 — the caller is expected to enrich these from the live jobs
    table (e.g. ``count(*) where assigned_store_id=? and status in (...)``)
    before passing into ``decide``.
    """
    try:
        rows = (
            client.table("partners")
            .select("store_id,name,kyc_status,capabilities_json,"
                    "capacity_jobs_per_day,pickup_hours_json,geo_lat,geo_lng")
            .eq("kyc_status", "active")
            .execute()
        )
    except Exception as e:
        logger.error(f"load_eligible_partners failed: {e}")
        return []

    out: list[Candidate] = []
    for r in getattr(rows, "data", None) or []:
        out.append(Candidate(
            store_id=r["store_id"],
            name=r.get("name") or r["store_id"],
            kyc_status=r.get("kyc_status", ""),
            capabilities=r.get("capabilities_json") or {},
            capacity_jobs_per_day=int(r.get("capacity_jobs_per_day") or 0),
            pickup_hours=r.get("pickup_hours_json") or {},
            geo_lat=r.get("geo_lat"),
            geo_lng=r.get("geo_lng"),
            queue_depth=0,
            jobs_today=0,
        ))
    return out


def record_decision(client: _SupabaseLike, decision: RoutingDecision) -> None:
    """Append the decision to ``routing_decisions``. Best-effort:
    logging-only on failure so a routing-log write never blocks a customer
    payment from being recorded."""
    try:
        client.table("routing_decisions").insert({
            "job_id":          decision.job_id,
            "decided_at":      datetime.now(timezone.utc).isoformat(),
            "eligible_stores": list(decision.eligible_store_ids),
            "scores_json":     decision.scores,
            "chosen_store_id": decision.chosen_store_id or "_NONE_",
            "reason":          decision.reason,
            "reroute_count":   decision.reroute_count,
            "notes":           decision.notes,
        }).execute()
    except Exception as e:
        logger.error(f"record_decision failed for {decision.job_id}: {e}")
