"""realtime_wake — the fix for "realtime subscription failed (This feature
isn't available in the sync client)".

All three pollers called ``client.channel(...)`` on a *sync* supabase-py
client, which raises NotImplementedError, so every one of them alerted once and
then ran on its 15-minute fallback poll forever. These tests lock in the two
halves of the fix: the bridge itself (async client on a background thread,
supervised), and that no poller goes back to the sync client's ``.channel``.
"""
import ast
import pathlib
import sys
import threading
import types

import pytest

import realtime_wake

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _State:
    """Stand-in for realtime's RealtimeSubscribeStates enum member."""

    def __init__(self, value):
        self.value = value


class _FakeWs:
    close_code = None
    closed = False


class _FakeChannel:
    def __init__(self, socket, topic):
        self.socket = socket
        self.topic = topic
        self.registered = None
        self.callback = None

    def on_postgres_changes(self, event, callback=None, table="*", schema="public", filter=None):
        self.registered = {"event": event, "table": table, "schema": schema, "filter": filter}
        self.callback = callback
        return self

    async def subscribe(self, callback=None):
        self.socket.subscribes += 1
        if self.socket.fail_subscribe:
            callback(_State("CHANNEL_ERROR"), "realtime is not enabled on this table")
        else:
            callback(_State("SUBSCRIBED"), None)
        return self


class _FakeSocket:
    """Enough of AsyncRealtimeClient to drive RealtimeWake."""

    instances = []

    def __init__(self, url, key, fail_connect=False, fail_subscribe=False):
        self.url = url
        self.key = key
        self.fail_connect = fail_connect
        self.fail_subscribe = fail_subscribe
        self.subscribes = 0
        self.closed = False
        self.channels = []
        self._ws_connection = _FakeWs()
        self.is_connected = True
        _FakeSocket.instances.append(self)

    async def connect(self):
        if self.fail_connect:
            raise OSError("connection refused")

    def channel(self, topic):
        ch = _FakeChannel(self, topic)
        self.channels.append(ch)
        return ch

    async def close(self):
        self.closed = True
        self.is_connected = False


@pytest.fixture
def sockets(monkeypatch):
    _FakeSocket.instances = []
    monkeypatch.setattr(realtime_wake, "_async_client",
                        lambda url, key: _FakeSocket(url, key))
    yield _FakeSocket.instances


# ── the bridge ───────────────────────────────────────────────────────────────

def test_subscribe_registers_the_filter_and_wakes_the_event(sockets):
    woken = threading.Event()
    sub = realtime_wake.subscribe(
        topic="store-OSP-jobs", table="jobs", filter="assigned_store_id=eq.OSP",
        on_wake=woken.set, url="https://x.supabase.co", key="svc",
    )
    try:
        assert sub.subscribed
        channel = sockets[0].channels[0]
        assert channel.topic == "store-OSP-jobs"
        assert channel.registered == {
            "event": "*", "table": "jobs", "schema": "public",
            "filter": "assigned_store_id=eq.OSP",
        }
        # A row change on the loop thread must wake the poller's Event.
        channel.callback({"eventType": "UPDATE"})
        assert woken.is_set()
    finally:
        sub.close()


def test_the_async_client_targets_the_realtime_endpoint(monkeypatch):
    captured = {}

    class _Client:
        def __init__(self, url, token=None, auto_reconnect=True, **_kw):
            captured.update(url=url, token=token, auto_reconnect=auto_reconnect)

    module = types.ModuleType("realtime")
    module.AsyncRealtimeClient = _Client
    monkeypatch.setitem(sys.modules, "realtime", module)

    realtime_wake._async_client("https://x.supabase.co", "svc")

    assert captured == {
        "url": "https://x.supabase.co/realtime/v1",
        "token": "svc",
        "auto_reconnect": True,
    }


def test_a_channel_error_raises_so_the_caller_falls_back_to_polling(monkeypatch):
    monkeypatch.setattr(realtime_wake, "_async_client",
                        lambda url, key: _FakeSocket(url, key, fail_subscribe=True))
    with pytest.raises(RuntimeError, match="CHANNEL_ERROR"):
        realtime_wake.subscribe(topic="t", table="jobs", on_wake=lambda: None,
                                url="https://x.supabase.co", key="svc")


def test_a_dead_connection_raises_rather_than_hanging(monkeypatch):
    monkeypatch.setattr(realtime_wake, "_async_client",
                        lambda url, key: _FakeSocket(url, key, fail_connect=True))
    with pytest.raises(OSError):
        realtime_wake.subscribe(topic="t", table="jobs", on_wake=lambda: None,
                                url="https://x.supabase.co", key="svc")


def test_a_raising_on_wake_does_not_kill_the_subscription(sockets):
    def _boom():
        raise ValueError("poller blew up")

    sub = realtime_wake.subscribe(topic="t", table="jobs", on_wake=_boom,
                                  url="https://x.supabase.co", key="svc")
    try:
        sockets[0].channels[0].callback({})      # must not propagate
        assert sub.subscribed
    finally:
        sub.close()


def test_a_dropped_socket_is_rebuilt_and_both_transitions_are_reported(monkeypatch):
    """The silent-slowdown case: the subscription dies at 3am. It must come
    back, and a human must be told both times (docs/FAIL_LOUD.md)."""
    monkeypatch.setattr(realtime_wake, "SUPERVISE_SECONDS", 0.02)
    _FakeSocket.instances = []
    monkeypatch.setattr(realtime_wake, "_async_client",
                        lambda url, key: _FakeSocket(url, key))

    reported = []
    recovered = threading.Event()

    def _on_status(ok, detail):
        reported.append((ok, detail))
        if ok:
            recovered.set()

    sub = realtime_wake.subscribe(topic="t", table="jobs", on_wake=lambda: None,
                                  url="https://x.supabase.co", key="svc",
                                  on_status=_on_status)
    try:
        _FakeSocket.instances[0].is_connected = False        # the socket dies
        assert recovered.wait(5), f"never resubscribed (reports: {reported})"
        assert [ok for ok, _ in reported] == [False, True]
        assert len(_FakeSocket.instances) == 2               # a fresh socket
        assert sub.subscribed
    finally:
        sub.close()


def test_socket_alive_sees_a_closed_websocket_under_a_connected_client():
    """AsyncRealtimeClient keeps reporting is_connected over a socket whose
    listen task died from anything but a ConnectionClosedError."""
    socket = _FakeSocket("u", "k")
    assert realtime_wake._socket_alive(socket)
    socket._ws_connection.close_code = 1006
    assert not realtime_wake._socket_alive(socket)


def test_credentials_prefer_the_service_key(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service")
    assert realtime_wake.credentials() == ("https://x.supabase.co", "service")


# ── the pollers ──────────────────────────────────────────────────────────────

POLLERS = ["store_puller.py", "academic_pipeline_worker.py",
           "tools/cloud_transcription_worker.py"]


@pytest.mark.parametrize("rel", POLLERS)
def test_no_poller_subscribes_through_the_sync_client(rel):
    """`sb.channel(...)` / `client.channel(...)` raises NotImplementedError on
    every sync supabase-py client. Nine days of a red
    transcription_worker.realtime alert is the cost of getting this wrong."""
    src = (ROOT / rel).read_text(encoding="utf-8-sig")
    calls = [
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "channel"
    ]
    assert not calls, (
        f"{rel} calls .channel() on line {calls[0].lineno} — that is the sync "
        "client's realtime and it raises. Use realtime_wake.subscribe()."
    )
    assert "realtime_wake" in src


def test_store_puller_reports_a_healthy_subscription(monkeypatch):
    import store_puller

    calls = {}
    monkeypatch.setattr(realtime_wake, "subscribe",
                        lambda **kw: calls.update(kw) or object())
    reports = []
    monkeypatch.setattr(store_puller, "_report_health",
                        lambda name, ok, detail, **kw: reports.append((name, ok)))

    store_puller.start_realtime("OSP")

    assert calls["table"] == "jobs"
    assert calls["filter"] == "assigned_store_id=eq.OSP"
    assert reports == [("store_puller.realtime", True)]


def test_store_puller_falls_back_to_polling_when_realtime_is_unavailable(monkeypatch):
    import store_puller

    def _boom(**_kw):
        raise RuntimeError("realtime is down")

    monkeypatch.setattr(realtime_wake, "subscribe", _boom)
    reports = []
    monkeypatch.setattr(store_puller, "_report_health",
                        lambda name, ok, detail, **kw: reports.append((name, ok, detail)))

    store_puller.start_realtime("OSP")       # must not raise: polling continues

    assert reports == [("store_puller.realtime", False, "RuntimeError: realtime is down")]
