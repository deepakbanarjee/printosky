"""Supabase Realtime for the sync pollers — the piece supabase-py does not give
a sync client.

WHY THIS MODULE EXISTS
----------------------
``store_puller``, ``academic_pipeline_worker`` and
``tools/cloud_transcription_worker`` each did this against a *sync* supabase-py
client (``create_client``)::

    channel = client.channel("...")          # <-- raises here, every time
    channel.on_postgres_changes(...)
    channel.subscribe()

and every one of them raised on the first line::

    NotImplementedError: This feature isn't available in the sync client.
    You can use the realtime feature in the async client only.

Each caller caught it, alerted, and fell back to its 15-minute poll — so the
event-driven pickup that those 900s fallbacks were sized around never once
worked: a paid print job could sit up to 15 minutes before the store PC pulled
it. The alert was doing its job (``transcription_worker.realtime`` had been red
for nine days); the subscription never was.

Realtime itself is fine — supabase-py just only wires it into the *async*
client. So this module runs the supported ``AsyncRealtimeClient`` on its own
event loop in a daemon thread and hands the wake-up back to the caller's
``threading.Event``. The pollers stay sync; nothing else about them changes.

Usage (mirrors what the callers already did)::

    import realtime_wake

    sub = realtime_wake.subscribe(
        topic=f"store-{store_id}-jobs",
        table="jobs",
        filter=f"assigned_store_id=eq.{store_id}",
        on_wake=_wake_event.set,
        on_status=lambda ok, detail: report("store_puller.realtime", ok, detail),
    )

``subscribe`` blocks until the channel is actually SUBSCRIBED (or raises), so a
caller's existing "subscription failed -> alert -> keep polling" branch keeps
working unchanged. After that first success the connection is supervised on the
background thread: a dropped socket is rebuilt with backoff, and every
transition is pushed to ``on_status`` so a subscription that dies at 3am is an
alert, not a silent slowdown back to poll-only (docs/FAIL_LOUD.md).
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Callable, Optional

log = logging.getLogger("realtime_wake")

# How long ``subscribe()`` waits for the channel to reach SUBSCRIBED before
# giving up and letting the caller fall back to polling. Generous: a store PC
# on a bad connection is normal, and the cost of waiting is one slow startup.
CONNECT_TIMEOUT = float(os.environ.get("REALTIME_CONNECT_TIMEOUT", "20"))

# How often the supervisor checks that the websocket is still alive. This is a
# local check, not a round trip — it costs nothing.
SUPERVISE_SECONDS = float(os.environ.get("REALTIME_SUPERVISE_SECONDS", "30"))

# Reconnect backoff bounds after the first successful subscribe.
_BACKOFF_START = 5.0
_BACKOFF_MAX = 300.0


def credentials() -> tuple[str, str]:
    """(url, key) from the environment, same precedence as ``db_cloud._client``."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ["SUPABASE_KEY"]
    return url, key


def _async_client(url: str, key: str):
    """Build the realtime client. Split out so tests can swap it."""
    from realtime import AsyncRealtimeClient

    # auto_reconnect stays on: the library recovers from the common case (a
    # server-side close) on its own, and our supervisor below is the backstop
    # for the cases it does not notice.
    return AsyncRealtimeClient(f"{url}/realtime/v1", key, auto_reconnect=True)


def _socket_alive(socket) -> bool:
    """Best-effort liveness check on the underlying websocket.

    ``AsyncRealtimeClient.is_connected`` alone is not enough: when the listen
    task dies from anything other than a ConnectionClosedError the client logs
    it and leaves ``_ws_connection`` set, so it keeps reporting "connected"
    over a dead socket. We look at the websocket's own close state too, across
    both the legacy and the current websockets client shapes. Anything we
    cannot interrogate is treated as alive — the delivery watchdog in each
    poller is the second line of defence for "connected but not delivering".
    """
    if not socket.is_connected:
        return False
    ws = getattr(socket, "_ws_connection", None)
    if ws is None:
        return False
    if getattr(ws, "close_code", None) is not None:
        return False
    if getattr(ws, "closed", False) is True:
        return False
    return True


class RealtimeWake:
    """One supervised postgres_changes subscription on a background thread."""

    def __init__(
        self,
        url: str,
        key: str,
        *,
        topic: str,
        table: str,
        on_wake: Callable[[], None],
        schema: str = "public",
        filter: Optional[str] = None,
        event: str = "*",
        on_status: Optional[Callable[[bool, str], None]] = None,
    ) -> None:
        self._url = url
        self._key = key
        self._topic = topic
        self._table = table
        self._schema = schema
        self._filter = filter
        self._event = event
        self._on_wake = on_wake
        self._on_status = on_status

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None
        self._ready = threading.Event()      # first subscribe settled (ok or not)
        self._error: Optional[BaseException] = None
        self._subscribed = False

    # ── public surface ───────────────────────────────────────────────────────

    @property
    def subscribed(self) -> bool:
        return self._subscribed

    def start(self, timeout: Optional[float] = None) -> "RealtimeWake":
        """Start the thread and block until the first subscribe settles.

        Raises whatever went wrong (or TimeoutError) so the caller's existing
        try/except can alert and fall back to polling.
        """
        timeout = CONNECT_TIMEOUT if timeout is None else timeout
        self._thread = threading.Thread(
            target=self._run, name=f"realtime-{self._topic}", daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError(
                f"realtime channel {self._topic!r} did not subscribe within {timeout}s"
            )
        if self._error is not None:
            raise self._error
        return self

    def close(self, timeout: float = 5.0) -> None:
        """Stop the supervisor and close the socket. Safe to call twice."""
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stop.set)
        if self._thread is not None:
            self._thread.join(timeout)
        self._subscribed = False

    # ── background thread ────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            asyncio.run(self._main())
        except Exception as exc:            # pragma: no cover - defensive
            log.warning("realtime_wake[%s]: loop died (%s)", self._topic, exc)
            self._settle(exc)
        finally:
            self._subscribed = False
            self._settle(None)              # never leave start() hanging

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        first = True
        backoff = _BACKOFF_START

        while not self._stop.is_set():
            socket = None
            try:
                socket = await self._connect_and_subscribe()
            except Exception as exc:
                await self._safe_close(socket)
                if first:
                    # Nothing has ever worked — hand the failure to the caller,
                    # which alerts and keeps polling. Retrying here would leave
                    # it blocked on start().
                    self._settle(exc)
                    return
                log.warning("realtime_wake[%s]: resubscribe failed (%s)", self._topic, exc)
                self._status(False, f"reconnect failed: {type(exc).__name__}: {exc}")
                await self._sleep_or_stop(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
                continue

            self._subscribed = True
            if first:
                first = False
                self._settle(None)
            else:
                log.info("realtime_wake[%s]: resubscribed", self._topic)
                self._status(True, "resubscribed after a dropped connection")
            backoff = _BACKOFF_START

            await self._supervise(socket)

            self._subscribed = False
            await self._safe_close(socket)
            if not self._stop.is_set():
                log.warning("realtime_wake[%s]: connection lost — rebuilding", self._topic)
                self._status(False, "websocket dropped — reconnecting")

    async def _connect_and_subscribe(self):
        socket = _async_client(self._url, self._key)
        await socket.connect()
        channel = socket.channel(self._topic)
        channel.on_postgres_changes(
            self._event,
            callback=self._fire,
            table=self._table,
            schema=self._schema,
            filter=self._filter,
        )

        settled: asyncio.Future = self._loop.create_future()

        def _on_subscribe(state, err=None):
            if settled.done():
                return
            name = getattr(state, "value", str(state))
            if name == "SUBSCRIBED":
                settled.set_result(True)
            else:
                settled.set_exception(
                    RuntimeError(f"channel {self._topic!r} state {name}: {err or 'no detail'}")
                )

        await channel.subscribe(_on_subscribe)
        await asyncio.wait_for(settled, CONNECT_TIMEOUT)
        return socket

    async def _supervise(self, socket) -> None:
        """Hold until the socket dies or ``close()`` is called."""
        while not self._stop.is_set():
            await self._sleep_or_stop(SUPERVISE_SECONDS)
            if self._stop.is_set():
                return
            if not _socket_alive(socket):
                return

    async def _sleep_or_stop(self, seconds: float) -> bool:
        """Wait up to ``seconds``. True if ``close()`` was called meanwhile.

        The TimeoutError is the normal path — the wait simply elapsed — which
        is why this returns a flag instead of swallowing an exception
        (tests/test_fail_loud_rule.py budgets silent handlers at zero here).
        """
        try:
            await asyncio.wait_for(self._stop.wait(), seconds)
            return True
        except asyncio.TimeoutError:
            return False

    async def _safe_close(self, socket) -> None:
        if socket is None:
            return
        try:
            await socket.close()
        except Exception as exc:
            log.debug("realtime_wake[%s]: close failed (%s)", self._topic, exc)

    # ── plumbing ─────────────────────────────────────────────────────────────

    def _fire(self, _payload=None) -> None:
        """postgres_changes callback. Runs on the loop thread, so it must not
        block — callers pass ``Event.set``. A raising callback must never take
        the subscription down with it."""
        try:
            self._on_wake()
        except Exception as exc:            # pragma: no cover - defensive
            log.warning("realtime_wake[%s]: on_wake raised (%s)", self._topic, exc)

    def _settle(self, error: Optional[BaseException]) -> None:
        if self._ready.is_set():
            return
        self._error = error
        self._ready.set()

    def _status(self, ok: bool, detail: str) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(ok, detail)
        except Exception as exc:            # pragma: no cover - defensive
            log.warning("realtime_wake[%s]: on_status raised (%s)", self._topic, exc)


def subscribe(
    *,
    topic: str,
    table: str,
    on_wake: Callable[[], None],
    schema: str = "public",
    filter: Optional[str] = None,
    event: str = "*",
    url: Optional[str] = None,
    key: Optional[str] = None,
    on_status: Optional[Callable[[bool, str], None]] = None,
    timeout: Optional[float] = None,
) -> RealtimeWake:
    """Subscribe to postgres_changes and wake ``on_wake`` on every match.

    Blocks until the channel is SUBSCRIBED; raises on failure so the caller can
    alert and fall back to polling. Credentials default to the environment.
    """
    if url is None or key is None:
        env_url, env_key = credentials()
        url = url or env_url
        key = key or env_key
    return RealtimeWake(
        url, key,
        topic=topic, table=table, schema=schema, filter=filter, event=event,
        on_wake=on_wake, on_status=on_status,
    ).start(timeout)
