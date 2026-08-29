"""
OPS WATCHDOG — the fail-loud rule.

THE RULE: when something that is expected to be working stops working, a human
is told. A log line is not "told".

Why this module exists. Nattika (PRINTK) went dark for a week in August 2026 and
nobody found out until the owner happened to look at the console:

  * the Epson's IP changed, so the poller's TCP probe failed;
  * an unreachable printer out of hours is normal, so `poll_once` skipped the
    cycle quietly — and kept skipping it for seven days;
  * `epson_jobs_fetcher` gave up after three failures, by design, and said so
    only at DEBUG;
  * `supabase_sync.get_sync_status()` existed but nothing ever called it;
  * `print_server /health` reported printer reachability but no console read it;
  * the cloud store-PC monitor watches exactly one store id (OSP), so a dead
    Nattika PC was structurally invisible;
  * the admin console's green "Live" dot only means "Supabase answered", so it
    stayed green over empty tables that read "No records for this period".

Every one of those was locally reasonable. Together they hid a dead store. This
module is the shared place where "not working" becomes an alert, so a new check
is three lines instead of a new invention each time.

Usage
-----
    from ops_watchdog import report, guard, health

    report("printer.epson", ok, detail=f"unreachable at {ip}")   # explicit state

    with guard("epson.weblog"):        # an exception reports + alerts, then raises
        rows = fetch_weblog()

    health()   # -> {"healthy": bool, "failing": [...], "checks": {...}}

Alerting policy
---------------
* The first failing check alerts **immediately** — no sustain window and no
  store-hours gate (owner's decision, 2026-08-18).
* A check that stays broken re-alerts every ``OPS_ALERT_REPEAT_HOURS`` (default
  6) so a long outage is not forgotten after a single message. It does not
  re-send on every cycle: at a five-minute poll that would be 288 WhatsApps a
  day, and outbound messages are metered (`wa_message_costs`).
* Recovery is announced once, so "is it back?" never needs asking.

State lives in SQLite (`ops_health`), so a store-PC restart neither forgets a
firing alert nor re-sends one that is already out.

This module must never break its caller: every path swallows its own errors and
falls back to in-memory state. A watchdog that raises is worse than no watchdog.
"""
from __future__ import annotations

import logging
import ntpath
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any

log = logging.getLogger("ops_watchdog")

# Re-alert cadence for a check that is still broken. 0 disables repeats (one
# alert per outage, plus the recovery).
REPEAT_HOURS = float(os.environ.get("OPS_ALERT_REPEAT_HOURS", "6"))

# Set OPS_ALERTS_ENABLED=0 on a dev box to keep the bookkeeping but send nothing.
ALERTS_ENABLED = os.environ.get("OPS_ALERTS_ENABLED", "1") not in ("0", "false", "False")

# Optional quiet window, e.g. "21-8" holds failure alerts between 21:00 and
# 08:00. DISABLED by default: the owner chose immediate alerts on 2026-08-18,
# accepting a nightly 🔴 as printers power off. Turn it on if that gets old —
# a held alert is not lost, it fires on the first check after the window, and
# /health and the console show the check red the whole time either way.
# Recoveries are never held.
QUIET_HOURS = os.environ.get("OPS_ALERT_QUIET_HOURS", "").strip()

_lock = threading.RLock()
_memory: dict[str, dict[str, Any]] = {}   # fallback when SQLite is unavailable
_db_path_override: str | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ops_health (
    check_name     TEXT PRIMARY KEY,
    store_id       TEXT,
    ok             INTEGER NOT NULL,
    detail         TEXT,
    since          TEXT,      -- when the check entered its current state
    last_alert_at  TEXT,      -- when we last told a human about a failure
    fail_count     INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT
)
"""


# ── Wiring ────────────────────────────────────────────────────────────────────

def set_db_path(path: str | None) -> None:
    """Point the watchdog at a specific SQLite file (tests, or a process whose
    DB path is not the store default)."""
    global _db_path_override
    with _lock:
        _db_path_override = path


def _db_path() -> str | None:
    """The SQLite file to keep health state in, or None to stay in memory.

    A path whose parent directory does not exist is rejected rather than
    created: on a dev box or in CI the store default is a Windows path
    (C:\\Printosky\\Data\\jobs.db) which SQLite would happily create as a
    single oddly-named file in the working directory.
    """
    for candidate in (_db_path_override,
                      os.environ.get("PRINTOSKY_DB"),
                      _configured_db_path()):
        if not candidate or candidate == ":memory:":
            continue
        if _usable_db_path(candidate):
            return candidate
    return None


def _usable_db_path(path: str) -> bool:
    """True if SQLite can open `path` without inventing a directory.

    On POSIX, os.path.dirname(r"C:\\Printosky\\Data\\jobs.db") is "" — so the
    store's Windows default would be created in the working directory as one
    file with backslashes in its name. Reject foreign-looking paths outright.
    """
    try:
        if os.sep != "\\" and ("\\" in path or ntpath.splitdrive(path)[0]):
            return False
        return os.path.isdir(os.path.dirname(os.path.abspath(path)) or ".")
    except OSError:
        return False


def _configured_db_path() -> str | None:
    try:
        from store_config import get_store_config
        return get_store_config().db_path
    except Exception:
        return None


# This machine's store id, resolved once and reused. See _store_id().
_live_store_id: str | None = None


def _store_id() -> str:
    """This machine's store id, resolved once per process.

    Cached because report() runs on every poll cycle of every poller, while
    get_store_config() re-reads the JSON from disk and re-applies the LAN rule
    on each call — and the store id it returns changes only when someone edits
    store_config.json and restarts the box, which is when this refreshes.

    (An earlier version of this comment claimed live resolution would recurse,
    because store_config's missing-config path calls report() itself. Measured:
    it does not — that nested report never re-enters _store_id, so the nesting
    depth stays at 1. The caching is a cost decision, not a safety one.)

    A failed resolution is never cached — "?" now must not become "?" forever
    once the config file comes back.
    """
    global _live_store_id
    if _live_store_id is None:
        try:
            from store_config import get_store_config
            _live_store_id = get_store_config().store_id
        except Exception:
            return "?"
    return _live_store_id


@contextmanager
def _conn():
    """Yield a connection to the health table, or None if SQLite is unusable —
    callers fall back to in-memory state rather than failing."""
    path = _db_path()
    if not path:
        yield None
        return
    c = None
    try:
        c = sqlite3.connect(path, timeout=5)
        c.execute(_SCHEMA)
        yield c
        c.commit()
    except Exception as exc:          # disk full, locked, path gone — keep going
        log.warning("ops_watchdog: SQLite unavailable (%s); using memory state", exc)
        yield None
    finally:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def _load(check: str) -> dict[str, Any] | None:
    with _conn() as c:
        if c is None:
            return _memory.get(check)
        row = c.execute(
            "SELECT check_name, store_id, ok, detail, since, last_alert_at, fail_count"
            " FROM ops_health WHERE check_name = ?", (check,)).fetchone()
    if not row:
        return _memory.get(check)
    return {"check": row[0], "store_id": row[1], "ok": bool(row[2]), "detail": row[3],
            "since": row[4], "last_alert_at": row[5], "fail_count": row[6]}


def _save(state: dict[str, Any]) -> None:
    _memory[state["check"]] = dict(state)
    with _conn() as c:
        if c is None:
            return
        c.execute(
            "INSERT INTO ops_health"
            " (check_name, store_id, ok, detail, since, last_alert_at, fail_count, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(check_name) DO UPDATE SET"
            "   store_id=excluded.store_id, ok=excluded.ok, detail=excluded.detail,"
            "   since=excluded.since, last_alert_at=excluded.last_alert_at,"
            "   fail_count=excluded.fail_count, updated_at=excluded.updated_at",
            (state["check"], state.get("store_id"), int(bool(state["ok"])),
             state.get("detail"), state.get("since"), state.get("last_alert_at"),
             int(state.get("fail_count") or 0), _now()))


def _now() -> str:
    return datetime.now().isoformat(sep=" ", timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except (TypeError, ValueError):
        return None


def _age(ts: str | None) -> str:
    """'7 days', '35 min' — how long a state has held, for the alert text."""
    started = _parse(ts)
    if not started:
        return "unknown"
    secs = (datetime.now() - started).total_seconds()
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 5400:
        return f"{int(secs // 60)} min"
    if secs < 172800:
        return f"{secs / 3600:.1f} h"
    return f"{int(secs // 86400)} days"


def _in_quiet_hours(now: datetime | None = None) -> bool:
    """True inside the configured quiet window (empty setting = never)."""
    if not QUIET_HOURS:
        return False
    try:
        start_s, end_s = QUIET_HOURS.split("-", 1)
        start, end = int(start_s), int(end_s)
    except ValueError:
        log.warning("ops_watchdog: bad OPS_ALERT_QUIET_HOURS=%r (want '21-8')", QUIET_HOURS)
        return False
    hour = (now or datetime.now()).hour
    return start <= hour or hour < end if start > end else start <= hour < end


def _notify(message: str) -> bool:
    """Send an ops alert. Never raises — a failed alert must not take down the
    thing that was trying to report a failure."""
    if not ALERTS_ENABLED:
        log.warning("ops_watchdog (alerts disabled): %s", message)
        return False
    try:
        from whatsapp_notify import send_staff_alert
        return bool(send_staff_alert(message))
    except Exception as exc:
        log.error("ops_watchdog: could not send alert (%s). Message was: %s", exc, message)
        return False


# ── The API ───────────────────────────────────────────────────────────────────

def _resolve_store_id(explicit: str | None, prev: dict[str, Any] | None) -> str:
    """Which store does this alert belong to?

    Live identity wins over what the health row happens to remember. The row
    used to win, which froze whatever the box resolved to the first time a
    check was ever recorded: PRIOFF (the Nattika back-office box) sat on the
    192.168.1.* LAN, resolved to PRINTK before it declared store_id in its
    store_config.json, and every alert from it named the shop for the next
    nine days. Alerts that name the wrong store send someone to look at a
    machine that is fine.

    The recorded value is the fallback for when live resolution fails, so a
    momentarily unreadable config does not turn a named store into "?".
    Nothing to migrate: each stale row is corrected by the next report() call
    on that machine.
    """
    live = explicit or _store_id()
    if live and live != "?":
        return live
    return (prev or {}).get("store_id") or "?"


def report(check: str, ok: bool, detail: str = "", *, store_id: str | None = None) -> bool:
    """Record the state of a named check and alert on the interesting edges.

    Alerts when a healthy check starts failing (immediately, first failure),
    again every REPEAT_HOURS while it stays broken, and once when it recovers.
    Returns True if a message was sent this call.

    `check` is a stable dotted name — "printer.epson", "sync.supabase" — because
    it is the dedup key and what the console shows.
    """
    try:
        with _lock:
            prev = _load(check)
            was_ok = prev["ok"] if prev else True     # first sight of a healthy check is not news
            first_seen = prev is None
            now = _now()

            state = {
                "check": check,
                "store_id": _resolve_store_id(store_id, prev),
                "ok": bool(ok),
                "detail": detail,
                "since": now if (first_seen or was_ok != bool(ok)) else prev.get("since"),
                "last_alert_at": (prev or {}).get("last_alert_at"),
                "fail_count": 0 if ok else int((prev or {}).get("fail_count") or 0) + 1,
            }

            # Has a human actually been told about this outage yet? A failure
            # held over quiet hours has state but no alert, and its release must
            # read as the failure, not as a reminder of one nobody received.
            told_before = bool((prev or {}).get("last_alert_at"))

            send = None
            if not ok and (was_ok or not told_before):
                # First failure — tell someone now.
                held = "" if was_ok else f" — {_age(state['since'])}"
                send = (f"🔴 *{check} FAILED*{held}\n\n{detail or 'check failed'}\n\n"
                        f"Store: {state['store_id']}")
            elif not ok and _due_for_repeat(prev):
                send = (f"🔴 *{check} STILL FAILING* — {_age(state['since'])}\n\n"
                        f"{detail or 'check failed'}\n\nStore: {state['store_id']}")
            elif ok and not was_ok:
                send = (f"🟢 *{check} recovered* — was down {_age((prev or {}).get('since'))}\n\n"
                        f"{detail or 'back to normal'}\n\nStore: {state['store_id']}")

            if send and not ok and _in_quiet_hours():
                # Held, not dropped: last_alert_at stays put, so the first check
                # after the quiet window reports it.
                log.warning("ops_watchdog: %s failing (%s) — alert held, quiet hours %s",
                            check, detail, QUIET_HOURS)
                _save(state)
                return False

            if send:
                log.warning("ops_watchdog: %s", send.replace("\n", " | "))
                # The send is isolated: a dead alert channel must not stop the
                # state below from being persisted, or every cycle would look
                # like a fresh first failure and re-alert once WhatsApp returns.
                try:
                    _notify(send)
                except Exception as exc:
                    log.error("ops_watchdog: alert send raised (%s)", exc)
                if not ok:
                    state["last_alert_at"] = now
            elif not ok:
                log.warning("ops_watchdog: %s still failing (%s) — alert already sent",
                            check, detail)

            _save(state)
            return bool(send)
    except Exception as exc:
        # Never let the watchdog break the caller. Log loudly and carry on.
        log.error("ops_watchdog.report(%s) itself failed: %s", check, exc)
        return False


def _due_for_repeat(prev: dict[str, Any] | None) -> bool:
    if REPEAT_HOURS <= 0:
        return False
    last = _parse((prev or {}).get("last_alert_at"))
    if last is None:
        return True
    return datetime.now() - last >= timedelta(hours=REPEAT_HOURS)


@contextmanager
def guard(check: str, detail: str = "", *, reraise: bool = True,
          store_id: str | None = None):
    """Report the wrapped block as a check: an exception is a failure (alerted),
    a clean exit is a success (recovery alerted if it had been failing).

    Use this in place of `except Exception: pass`, which is how the outage this
    module exists for stayed invisible. `reraise=False` keeps the old
    swallow-and-continue behaviour where a caller genuinely must not crash —
    the difference being that now someone is told.
    """
    try:
        yield
    except Exception as exc:
        report(check, False, detail or f"{type(exc).__name__}: {exc}", store_id=store_id)
        if reraise:
            raise
    else:
        report(check, True, detail, store_id=store_id)


def health(include_ok: bool = True) -> dict[str, Any]:
    """Every known check, for /health and the consoles. `healthy` is false if any
    check is failing — that is what a console should surface instead of a green
    dot over empty tables."""
    checks: dict[str, Any] = {}
    try:
        with _lock:
            with _conn() as c:
                rows = c.execute(
                    "SELECT check_name, store_id, ok, detail, since, last_alert_at, fail_count"
                    " FROM ops_health ORDER BY check_name").fetchall() if c else []
            if not rows:
                rows = [(s["check"], s.get("store_id"), int(s["ok"]), s.get("detail"),
                         s.get("since"), s.get("last_alert_at"), s.get("fail_count") or 0)
                        for s in _memory.values()]
            for name, store, ok, detail, since, last_alert, fails in rows:
                if not include_ok and ok:
                    continue
                checks[name] = {
                    "ok": bool(ok), "store_id": store, "detail": detail,
                    "since": since, "for": _age(since) if not ok else None,
                    "last_alert_at": last_alert, "fail_count": fails,
                }
    except Exception as exc:
        log.error("ops_watchdog.health() failed: %s", exc)
        return {"healthy": None, "error": str(exc), "checks": {}, "failing": []}

    failing = sorted(n for n, c in checks.items() if not c["ok"])
    return {
        "healthy": not failing,
        "failing": failing,
        "checks": checks,
        "as_of": _now(),
    }


def reset() -> None:
    """Forget all state. Tests only."""
    global _live_store_id
    with _lock:
        _live_store_id = None
        _memory.clear()
        with _conn() as c:
            if c is not None:
                c.execute("DELETE FROM ops_health")
