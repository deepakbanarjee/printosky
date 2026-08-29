"""Is a Supabase Realtime socket still alive — and rebuild it when it is not.

WHY THIS EXISTS
---------------
All three pollers (``store_puller``, ``academic_pipeline_worker``,
``tools/cloud_transcription_worker``) subscribe on a daemon thread and then
hold the asyncio loop open so the realtime client's background listen and
heartbeat tasks keep pumping. They held it with::

    while not stop.is_set():
        await asyncio.sleep(1.0)

and trusted the client's own auto-reconnect for a dropped socket. It does not
always get there. ``AsyncRealtimeClient._listen`` routes every failure through
``_on_connect_error``, which only reconnects for a ``ConnectionClosedError``;
anything else is logged and the listen task simply ends, leaving
``_ws_connection`` set. And when it does reconnect, it gives up after 5
attempts and the exception dies inside that background task. Either way the
hold loop above keeps sleeping over a corpse, and the ``*.realtime`` check
stays green — the exact silent-degradation shape docs/FAIL_LOUD.md exists to
prevent. ``_check_realtime_delivery`` in each poller does eventually notice,
but only once a job actually arrives, so the first job after the socket died is
late by up to the fallback poll interval.

WHAT THIS DOES
--------------
``hold`` replaces that sleep loop. Same job — keep the loop alive, notice the
stop signal — plus: if the socket looks dead for ``GRACE_SECONDS``, close it,
resubscribe, and report both edges to ops_watchdog. A subscription that dies at
3am now comes back on its own, and if it cannot, a human is told.

Only the liveness predicate and the hold loop live here. Each poller still
builds its own client, channel and filter, self-contained, as
``_check_realtime_delivery`` is. This part is shared because it reaches into
realtime-client internals to answer "is it actually up?" — three hand-mirrored
copies of that would drift apart on the first library upgrade, and drift here
is invisible by construction.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger("realtime_liveness")

# How long the socket must look dead before we step in. The client retries a
# dropped connection itself — 5 attempts on a 1/2/4/8/16s backoff — and reads
# as not-connected the whole time it is trying. Rebuilding during that window
# would race its reconnect and leave two sockets, so we let it finish first.
GRACE_SECONDS = float(os.environ.get("REALTIME_DEAD_GRACE_SECONDS", "60"))

# How often the hold loop wakes. Kept at 1s so a stop signal is still noticed
# promptly (tests, clean shutdown); the liveness check is two attribute reads,
# no I/O, so running it every tick costs nothing.
TICK_SECONDS = float(os.environ.get("REALTIME_TICK_SECONDS", "1.0"))

# Backoff between failed rebuild attempts.
BACKOFF_START = 5.0
BACKOFF_MAX = 300.0


def socket_alive(realtime) -> bool:
    """Is this ``AsyncRealtimeClient``'s websocket actually up?

    ``is_connected`` alone is not enough. It is False while the client's own
    reconnect is in flight (fine — the grace period covers that), but it stays
    *True* over a dead socket whenever the listen task ended from anything
    other than a ConnectionClosedError, because nothing clears
    ``_ws_connection`` on that path. So we ask the websocket itself as well,
    across both the legacy and current websockets client shapes.

    Anything we cannot interrogate counts as alive: a false "dead" would tear
    down a working subscription every tick, which is worse than the gap this
    closes. The poller's delivery watchdog remains the backstop.
    """
    if not getattr(realtime, "is_connected", False):
        return False
    ws = getattr(realtime, "_ws_connection", None)
    if ws is None:
        return False
    if getattr(ws, "close_code", None) is not None:
        return False
    if getattr(ws, "closed", False) is True:
        return False
    return True


async def _close_quietly(realtime, label: str) -> None:
    """Drop the dead socket so the next connect() actually reconnects.

    ``connect()`` returns early when ``_ws_connection`` is still set, which is
    precisely the state we are recovering from, so this is not optional.
    """
    try:
        await realtime.close()
    except Exception as exc:
        log.debug("%s: closing the dead socket raised (%s)", label, exc)


async def hold(realtime, stop, *, resubscribe, on_status, label: str) -> None:
    """Keep the subscription's event loop alive until ``stop``, rebuilding the
    socket if it dies.

    ``resubscribe`` is an async callable that rebuilds the channel and
    subscribes it — each poller passes its own, so the topic, table and filter
    stay with the poller. ``on_status(ok, detail)`` reports to ops_watchdog.
    Returns when ``stop`` is set; never raises, because the fallback poll is
    the source of truth and this thread dying must not take the poller with it.
    """
    dead_since: float | None = None
    backoff = BACKOFF_START

    while not stop.is_set():
        await asyncio.sleep(TICK_SECONDS)
        if stop.is_set():
            return

        if socket_alive(realtime):
            dead_since = None
            backoff = BACKOFF_START
            continue

        if dead_since is None:
            dead_since = time.monotonic()      # the client's own retry goes first
            continue
        down_for = time.monotonic() - dead_since
        if down_for < GRACE_SECONDS:
            continue

        log.warning("%s: realtime socket dead for %.0fs — rebuilding", label, down_for)
        on_status(False, f"websocket dead for {down_for:.0f}s — rebuilding")

        await _close_quietly(realtime, label)
        try:
            await resubscribe()
        except Exception as exc:
            log.warning("%s: resubscribe failed (%s)", label, exc)
            on_status(False, f"reconnect failed: {type(exc).__name__}: {exc}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            continue

        log.info("%s: realtime resubscribed", label)
        on_status(True, "resubscribed after a dropped connection")
        dead_since = None
        backoff = BACKOFF_START
