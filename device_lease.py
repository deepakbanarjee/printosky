"""
DEVICE COORDINATION — many PCs per store, one owner per job of work.

The problem. Every store will run several boxes: a counter PC, a second
counter, an office machine. Each one runs the same agent, so each one polls the
printers, imports the printer's job log, and pulls paid jobs down to print. The
only guards today are a per-machine config flag and a *local* `pulled_jobs`
table, neither of which one box can see in another. That is how Nattika stored
388 printer jobs twice, and it is how two boxes would eventually print the same
paid job twice — on paper, in front of the customer.

Configuring each box by hand does not scale and drifts the moment a PC is
reimaged. So boxes coordinate through the one thing they already share:
Supabase.

Two primitives
--------------
**Role lease** — for singleton work (polling a printer, importing its job log).
Every box tries to hold the lease; exactly one wins, the rest stand by. The
lease has a short TTL and is renewed while the holder works, so if that box is
switched off, another takes over within a TTL without anyone touching a config
file.

    if device_lease.hold("poll_printers"):
        poll()          # I am this store's poller right now

**Job claim** — for work that must happen exactly once (printing a job). The
claim is an atomic conditional update: the first box to set `print_claimed_at`
wins, everyone else sees a claimed row and skips.

    if device_lease.claim_job(job_id):
        print_it()

Failure behaviour, deliberately different for each:

* A lease that cannot be checked (Supabase down) falls back to the local
  `poll_printers` config flag. Polling twice is a data annoyance; not polling at
  all is a blind store.
* A claim that cannot be taken **fails closed** — the job is not printed this
  cycle and is retried on the next one. Paper is unrecoverable; a delayed print
  is not. A store that cannot reach Supabase cannot see paid jobs in the first
  place, so this costs nothing offline.

Everything here degrades quietly to "single box behaves exactly as before", and
every failure is reported to ops_watchdog rather than swallowed.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("device_lease")

# How long a lease is good for, and how often the holder renews it. A box that
# dies is replaced after at most LEASE_TTL_SECONDS.
LEASE_TTL_SECONDS = int(os.environ.get("DEVICE_LEASE_TTL_SECONDS", "180"))

# Roles. One holder per (store, role).
ROLE_POLL_PRINTERS = "poll_printers"     # SNMP/web counters + supply levels
ROLE_FETCH_EPSON   = "fetch_epson_log"   # the Epson's own job history
ROLE_PRINT_JOBS    = "print_jobs"        # pulling paid jobs down and printing

_lock = threading.RLock()
_device_id: str | None = None


# ── Identity ─────────────────────────────────────────────────────────────────

def device_id() -> str:
    """A stable id for THIS machine, surviving restarts and rename.

    Order: PRINTOSKY_DEVICE_ID, then a uuid persisted next to the local DB, then
    a hostname-derived fallback if the disk is not writable.
    """
    global _device_id
    with _lock:
        if _device_id:
            return _device_id
        env = (os.environ.get("PRINTOSKY_DEVICE_ID") or "").strip()
        if env:
            _device_id = env
            return _device_id
        try:
            path = _id_file()
            if path and path.exists():
                stored = path.read_text(encoding="utf-8").strip()
                if stored:
                    _device_id = stored
                    return _device_id
            generated = f"{_hostname()}-{uuid.uuid4().hex[:8]}"
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(generated, encoding="utf-8")
            _device_id = generated
        except OSError as exc:
            # Read-only disk: fall back to the hostname. Two boxes with the same
            # hostname would collide, which is why this is the last resort.
            log.warning("device_lease: could not persist a device id (%s); using hostname", exc)
            _device_id = _hostname()
        return _device_id


def _hostname() -> str:
    try:
        return socket.gethostname() or "unknown-host"
    except OSError:
        return "unknown-host"


def _id_file() -> Path | None:
    try:
        from store_config import get_store_config
        db = get_store_config().db_path
        parent = Path(db).parent
        if str(parent) in ("", "."):
            return None
        return parent / "device_id.txt"
    except Exception:
        return None


def _store_id() -> str:
    try:
        from store_config import get_store_config
        return get_store_config().store_id
    except Exception:
        return "?"


# ── Supabase seam ────────────────────────────────────────────────────────────

def _client():
    """Supabase client, or None when the cloud is unreachable/unconfigured."""
    try:
        from db_cloud import _client as _c
        return _c()
    except Exception as exc:
        log.debug("device_lease: no Supabase client (%s)", exc)
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── Presence ─────────────────────────────────────────────────────────────────

def heartbeat(app_version: str | None = None) -> bool:
    """Record that this box is alive. Cheap; call it on each agent cycle."""
    c = _client()
    if c is None:
        return False
    try:
        c.table("store_devices").upsert({
            "store_id":  _store_id(),
            "device_id": device_id(),
            "hostname":  _hostname(),
            "app_version": app_version,
            "last_seen": _iso(_now()),
        }).execute()
        return True
    except Exception as exc:
        log.warning("device_lease: heartbeat failed (%s)", exc)
        return False


# ── Role leases ──────────────────────────────────────────────────────────────

def hold(role: str, ttl: int = LEASE_TTL_SECONDS) -> bool:
    """True if THIS box owns `role` for its store right now.

    Acquires the lease if it is free or expired, renews it if we already hold
    it, and returns False if another live box holds it. Call it every cycle: it
    is the renew as well as the acquire.

    When Supabase cannot be reached we cannot know who else is out there, so we
    fall back to the machine's own `poll_printers` setting (default true) —
    i.e. exactly the behaviour that existed before leases.
    """
    c = _client()
    if c is None:
        return _offline_fallback(role)

    me, store, now = device_id(), _store_id(), _now()
    expires = now + timedelta(seconds=max(30, ttl))

    try:
        rows = (c.table("store_role_leases").select("*")
                 .eq("store_id", store).eq("role", role).limit(1).execute()).data or []
        current = rows[0] if rows else None

        if current:
            owner = current.get("owner_device")
            expiry = _parse(current.get("expires_at"))
            mine = owner == me
            free = owner is None or expiry is None or expiry <= now
            if not mine and not free:
                log.debug("device_lease: %s held by %s until %s", role, owner, expiry)
                return False

            # Conditional take-over: only if the row still looks the way we just
            # read it. Two boxes racing on an expired lease cannot both win —
            # the second one's eq(owner_device) no longer matches.
            q = (c.table("store_role_leases").update({
                    "owner_device": me,
                    "acquired_at": _iso(now) if not mine else current.get("acquired_at"),
                    "expires_at": _iso(expires),
                    "updated_at": _iso(now),
                 })
                 .eq("store_id", store).eq("role", role))
            q = q.eq("owner_device", owner) if owner is not None else q.is_("owner_device", "null")
            updated = (q.execute()).data or []
            won = bool(updated)
        else:
            c.table("store_role_leases").insert({
                "store_id": store, "role": role, "owner_device": me,
                "acquired_at": _iso(now), "expires_at": _iso(expires),
                "updated_at": _iso(now),
            }).execute()
            won = True

        if won:
            log.debug("device_lease: holding %s until %s", role, expires)
        else:
            log.info("device_lease: lost the race for %s — another box has it", role)
        return won

    except Exception as exc:
        # A duplicate-key error here is a lost race, not a fault: another box
        # inserted first. Anything else is worth reporting.
        if "duplicate key" in str(exc).lower():
            return False
        log.warning("device_lease: could not evaluate %s (%s)", role, exc)
        _report(f"lease.{role}", False, f"lease check failed: {exc}")
        return _offline_fallback(role)


def release(role: str) -> None:
    """Give up a lease on a clean shutdown so a peer takes over immediately
    instead of waiting out the TTL."""
    c = _client()
    if c is None:
        return
    try:
        (c.table("store_role_leases")
          .update({"owner_device": None, "expires_at": _iso(_now()), "updated_at": _iso(_now())})
          .eq("store_id", _store_id()).eq("role", role)
          .eq("owner_device", device_id()).execute())
    except Exception as exc:
        log.debug("device_lease: release(%s) failed (%s)", role, exc)


def owner(role: str) -> str | None:
    """Which device currently holds `role`, or None. For /health and consoles."""
    c = _client()
    if c is None:
        return None
    try:
        rows = (c.table("store_role_leases").select("owner_device,expires_at")
                 .eq("store_id", _store_id()).eq("role", role).limit(1).execute()).data or []
        if not rows:
            return None
        expiry = _parse(rows[0].get("expires_at"))
        if expiry is not None and expiry <= _now():
            return None
        return rows[0].get("owner_device")
    except Exception:
        return None


def _offline_fallback(role: str) -> bool:
    """No cloud: behave like a single-box store, honouring the explicit config
    switch. Polling twice is a data annoyance; polling never is a blind store."""
    try:
        from store_config import get_store_config
        allowed = bool(getattr(get_store_config(), "poll_printers", True))
    except Exception:
        allowed = True
    log.info("device_lease: cloud unreachable — falling back to poll_printers=%s for %s",
             allowed, role)
    return allowed


def _parse(ts) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _report(check: str, ok: bool, detail: str) -> None:
    try:
        from ops_watchdog import report
        report(check, ok, detail)
    except Exception as exc:
        # The reporter itself failing must not break the caller, but it still
        # gets said out loud — that is the whole rule (docs/FAIL_LOUD.md).
        log.error("device_lease: could not report %s (%s); detail was: %s",
                  check, exc, detail)


# ── Exactly-once job claims ──────────────────────────────────────────────────

def claim_job(job_id: str) -> bool:
    """Claim `job_id` for printing by this box. True == it is ours to print.

    The claim is an atomic conditional update — `print_claimed_at IS NULL` — so
    of any number of boxes trying at the same moment, exactly one gets a row
    back. Everyone else skips the job.

    Fails CLOSED: if the claim cannot be written, we do not print. Wasted paper
    cannot be undone; a print delayed to the next cycle can.
    """
    c = _client()
    if c is None:
        _report("print.claim", False,
                "cannot reach Supabase to claim jobs — auto-print is paused rather "
                "than risk printing a job twice")
        return False
    try:
        updated = (c.table("jobs")
                    .update({"print_claimed_at": _iso(_now()), "print_claimed_by": device_id()})
                    .eq("job_id", job_id)
                    .is_("print_claimed_at", "null")
                    .execute()).data or []
        if updated:
            _report("print.claim", True, "claims working")
            return True
        log.info("store_puller: %s is already claimed by another box — skipping", job_id)
        return False
    except Exception as exc:
        log.warning("device_lease: claim for %s failed (%s)", job_id, exc)
        _report("print.claim", False, f"claim failed for {job_id}: {exc}")
        return False


def release_job(job_id: str) -> None:
    """Hand a job back after a failed print so another box (or a retry) can take
    it. Only clears OUR claim."""
    c = _client()
    if c is None:
        return
    try:
        (c.table("jobs")
          .update({"print_claimed_at": None, "print_claimed_by": None})
          .eq("job_id", job_id).eq("print_claimed_by", device_id()).execute())
    except Exception as exc:
        log.debug("device_lease: release_job(%s) failed (%s)", job_id, exc)
