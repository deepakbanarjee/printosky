"""realtime_liveness — the gap left open when realtime moved to the async client.

All three pollers hold their asyncio loop open so the realtime client's listen
and heartbeat tasks keep pumping, and trusted that client's auto-reconnect for
a dropped socket. It does not cover every case: ``_on_connect_error`` only
reconnects for a ConnectionClosedError, and when it does reconnect it gives up
after 5 attempts inside a background task nobody awaits. Either way the hold
loop kept sleeping over a dead socket with the ``*.realtime`` check still
green. These tests pin the recovery and both of its alerts.
"""
import ast
import asyncio
import pathlib
import threading

import pytest

import realtime_liveness as rl

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Ws:
    def __init__(self, close_code=None, closed=False):
        self.close_code = close_code
        self.closed = closed


class _Realtime:
    """Enough of AsyncRealtimeClient to drive hold()."""

    def __init__(self):
        self.is_connected = True
        self._ws_connection = _Ws()
        self.closes = 0

    async def close(self):
        self.closes += 1
        self._ws_connection = None
        self.is_connected = False

    # test helpers
    def die_silently(self):
        """The listen task ended without a ConnectionClosedError: nothing
        clears _ws_connection, so the client still claims to be connected."""
        self._ws_connection.close_code = 1006

    def die_disconnected(self):
        """The client's own reconnect ran out of attempts."""
        self.is_connected = False

    def revive(self):
        self.is_connected = True
        self._ws_connection = _Ws()


@pytest.fixture
def fast(monkeypatch):
    """Run the loop at test speed rather than 1s ticks / 60s grace."""
    monkeypatch.setattr(rl, "TICK_SECONDS", 0.001)
    monkeypatch.setattr(rl, "GRACE_SECONDS", 0.01)
    monkeypatch.setattr(rl, "BACKOFF_START", 0.001)


# ── the liveness predicate ───────────────────────────────────────────────────

def test_a_healthy_socket_is_alive():
    assert rl.socket_alive(_Realtime())


def test_a_closed_websocket_under_a_connected_client_is_dead():
    """The case the client never notices: is_connected still True."""
    rt = _Realtime()
    rt.die_silently()
    assert rt.is_connected is True
    assert not rl.socket_alive(rt)


def test_a_disconnected_client_is_dead():
    rt = _Realtime()
    rt.die_disconnected()
    assert not rl.socket_alive(rt)


def test_a_cleared_connection_is_dead():
    rt = _Realtime()
    rt._ws_connection = None
    assert not rl.socket_alive(rt)


def test_an_uninterrogable_client_counts_as_alive():
    """A false 'dead' would tear down a working subscription every tick."""
    class _Opaque:
        is_connected = True
        _ws_connection = object()

    assert rl.socket_alive(_Opaque())


# ── the hold loop ────────────────────────────────────────────────────────────

def _run(coro, timeout=5):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def test_hold_returns_when_stopped(fast):
    rt, stop = _Realtime(), threading.Event()
    stop.set()
    _run(rl.hold(rt, stop, resubscribe=_fail, on_status=lambda *_: None, label="t"))
    assert rt.closes == 0, "a healthy socket must never be torn down"


async def _fail():
    raise AssertionError("must not resubscribe")


def test_a_dead_socket_is_rebuilt_and_both_edges_are_reported(fast):
    rt, stop = _Realtime(), threading.Event()
    rt.die_silently()
    reported = []

    async def _resubscribe():
        rt.revive()
        stop.set()                     # one rebuild is enough for the test

    _run(rl.hold(rt, stop, resubscribe=_resubscribe,
                 on_status=lambda ok, detail: reported.append((ok, detail)),
                 label="t"), timeout=5)

    assert rt.closes == 1, "the dead socket must be closed before reconnecting"
    assert [ok for ok, _ in reported] == [False, True]
    assert "dead" in reported[0][1]
    assert "resubscribed" in reported[1][1]


def test_the_clients_own_reconnect_gets_the_grace_period_first(monkeypatch):
    """is_connected reads False while the client retries. Stepping in there
    would race it and leave two sockets, so a blip inside the grace window must
    not trigger a rebuild."""
    monkeypatch.setattr(rl, "TICK_SECONDS", 0.001)
    monkeypatch.setattr(rl, "GRACE_SECONDS", 30.0)      # never reached in this test
    rt, stop = _Realtime(), threading.Event()

    async def _watch():
        rt.die_disconnected()
        await asyncio.sleep(0.05)      # several ticks inside the grace window
        rt.revive()
        await asyncio.sleep(0.05)
        stop.set()

    async def _both():
        await asyncio.gather(
            rl.hold(rt, stop, resubscribe=_fail, on_status=lambda *_: None, label="t"),
            _watch(),
        )

    _run(_both())
    assert rt.closes == 0


def test_a_failed_rebuild_reports_and_keeps_trying(fast):
    rt, stop = _Realtime(), threading.Event()
    rt.die_silently()
    reported = []
    attempts = {"n": 0}

    async def _resubscribe():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("connection refused")
        rt.revive()
        stop.set()

    _run(rl.hold(rt, stop, resubscribe=_resubscribe,
                 on_status=lambda ok, detail: reported.append((ok, detail)),
                 label="t"), timeout=5)

    assert attempts["n"] == 3
    assert reported[-1][0] is True, "recovery must be announced after the failures"
    assert any("reconnect failed" in d for ok, d in reported if not ok)


def test_hold_survives_a_socket_it_cannot_close(fast):
    """close() raising must not strand the poller with no subscription."""
    rt, stop = _Realtime(), threading.Event()

    async def _boom():
        raise OSError("already gone")

    rt.close = _boom
    rt.die_silently()

    async def _resubscribe():
        rt.revive()
        stop.set()

    _run(rl.hold(rt, stop, resubscribe=_resubscribe,
                 on_status=lambda *_: None, label="t"), timeout=5)
    assert rl.socket_alive(rt)


# ── the wiring ───────────────────────────────────────────────────────────────

POLLERS = ["store_puller.py", "academic_pipeline_worker.py",
           "tools/cloud_transcription_worker.py"]


@pytest.mark.parametrize("rel", POLLERS)
def test_every_poller_holds_its_loop_with_the_liveness_check(rel):
    """The bare `while not stop.is_set(): await asyncio.sleep(1.0)` is what let
    a dead socket read as healthy. No poller may go back to it."""
    src = (ROOT / rel).read_text(encoding="utf-8-sig")
    assert "realtime_liveness.hold(" in src, f"{rel} must hold its loop via realtime_liveness"

    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.While):
            continue
        body = [n for n in node.body if not isinstance(n, ast.Pass)]
        is_bare_sleep = (
            len(body) == 1
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Await)
        )
        assert not is_bare_sleep, (
            f"{rel} line {node.lineno}: bare sleep loop — a dead socket would "
            "read as healthy again. Use realtime_liveness.hold()."
        )
