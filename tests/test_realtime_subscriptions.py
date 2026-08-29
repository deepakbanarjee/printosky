"""Every poller must subscribe through the *async* Supabase client.

supabase-py 2.15 wires Realtime into the async client only: `channel.subscribe()`
on a sync client raises

    NotImplementedError: This feature isn't available in the sync client.
    You can use the realtime feature in the async client only.

All three pollers did exactly that. Each caught it, alerted, and fell back to
its 15-minute poll — so the event-driven pickup those fallbacks were sized
around never once worked, and transcription_worker.realtime sat red for nine
days. This is the ratchet against going back.
"""
import ast
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

POLLERS = ["store_puller.py", "academic_pipeline_worker.py",
           "tools/cloud_transcription_worker.py"]

# The sync client handles in these modules. `.channel()` on any of them raises.
SYNC_HANDLES = {"sb", "_sb"}


@pytest.mark.parametrize("rel", POLLERS)
def test_poller_uses_the_async_client(rel):
    src = (ROOT / rel).read_text(encoding="utf-8-sig")
    assert "create_async_client" in src, (
        f"{rel} has a realtime subscription that does not go through "
        "create_async_client — the sync client's .channel() raises"
    )


@pytest.mark.parametrize("rel", POLLERS)
def test_poller_never_calls_channel_on_the_sync_client(rel):
    src = (ROOT / rel).read_text(encoding="utf-8-sig")
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "channel":
            continue
        recv = node.func.value
        # `sb.channel(...)` / `_client().channel(...)` — the sync handles.
        if isinstance(recv, ast.Name) and recv.id in SYNC_HANDLES:
            offenders.append(node.lineno)
        elif (isinstance(recv, ast.Call) and isinstance(recv.func, ast.Name)
              and recv.func.id == "_client"):
            offenders.append(node.lineno)
    assert not offenders, (
        f"{rel} calls .channel() on the sync client at line(s) {offenders} — "
        "that raises NotImplementedError and drops the poller back to polling"
    )


@pytest.mark.parametrize("rel", POLLERS)
def test_realtime_runs_on_its_own_thread(rel):
    """The subscription needs an asyncio loop; the poll loop stays blocking."""
    src = (ROOT / rel).read_text(encoding="utf-8-sig")
    assert "_realtime_thread" in src and "daemon=True" in src, (
        f"{rel} must run its subscription on a daemon thread so a realtime "
        "failure can never delay or block the poll loop"
    )
