"""
Tests for the bot relay's concurrency model (watcher.py).

The relay on :3003 is what Node posts every inbound WhatsApp message to. It ran
on a single-threaded HTTPServer, so each customer's turn — a Supabase session
read, the bot's own work, the reply, then a conversation_log write per message —
blocked every other customer's message from even being parsed.

It is now threaded, which raises the opposite risk: two messages from the SAME
customer overlapping on that phone's bot session, which a turn reads, decides
from, and writes back. _lock_for_phone keeps those serial while letting
different customers run at once.

These tests pin both halves — the parallelism and the per-phone ordering —
because either one alone is a bug.
"""

import os
import sys
import threading
import time
import types

import pytest

# ── stub heavy deps so watcher.py imports (mirrors test_watcher_transcript_skip) ──
for _mod in ("requests", "dotenv", "db_cloud", "whatsapp_bot",
             "whatsapp_notify", "razorpay_integration"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

if not hasattr(sys.modules["dotenv"], "load_dotenv"):
    sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None

if "watchdog" not in sys.modules:
    _wd = types.ModuleType("watchdog")
    _wd_obs = types.ModuleType("watchdog.observers")
    _wd_obs.Observer = object
    _wd_ev = types.ModuleType("watchdog.events")
    _wd_ev.FileSystemEventHandler = object
    sys.modules["watchdog"] = _wd
    sys.modules["watchdog.observers"] = _wd_obs
    sys.modules["watchdog.events"] = _wd_ev

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import watcher  # noqa: E402


@pytest.fixture
def w():
    return watcher


# ─────────────────────────────────────────────────────────────────────────────
# Per-phone locks
# ─────────────────────────────────────────────────────────────────────────────

class TestPhoneLocks:
    def test_same_phone_gets_the_same_lock(self, w):
        assert w._lock_for_phone("+919999000011") is w._lock_for_phone("+919999000011")

    def test_different_phones_get_different_locks(self, w):
        assert w._lock_for_phone("+919999000011") is not w._lock_for_phone("+919999000022")

    def test_lock_creation_is_itself_thread_safe(self, w):
        """Two threads asking for one phone's lock at the same instant must not
        each mint their own — that would leave the phone unserialised."""
        phone = "+919999000033"
        w._phone_locks.pop(phone, None)
        seen, seen_guard = [], threading.Lock()
        start = threading.Barrier(24)

        def grab():
            start.wait()
            lock = w._lock_for_phone(phone)
            with seen_guard:
                seen.append(id(lock))

        threads = [threading.Thread(target=grab) for _ in range(24)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(set(seen)) == 1, "concurrent callers got different locks"

    def test_one_phone_is_serialised(self, w):
        """Two turns for one customer must not overlap."""
        phone = "+919999000044"
        w._phone_locks.pop(phone, None)
        overlap = []
        inside = []
        inside_guard = threading.Lock()

        def turn():
            with w._lock_for_phone(phone):
                with inside_guard:
                    inside.append(1)
                    if len(inside) > 1:
                        overlap.append(True)
                time.sleep(0.02)          # the window a race would land in
                with inside_guard:
                    inside.pop()

        threads = [threading.Thread(target=turn) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not overlap, "two turns for the same phone ran concurrently"

    def test_different_phones_run_concurrently(self, w):
        """The point of threading the relay: customer B is not stuck behind
        customer A. Asserted with a barrier, not a stopwatch — if these were
        serialised the first would wait for siblings that never arrive."""
        phones = [f"+91999900{i:04d}" for i in range(3)]
        for p in phones:
            w._phone_locks.pop(p, None)
        barrier = threading.Barrier(3, timeout=10)
        reached = []
        reached_guard = threading.Lock()
        errors = []

        def turn(phone):
            try:
                with w._lock_for_phone(phone):
                    idx = barrier.wait()
                    with reached_guard:
                        reached.append(idx)
            except Exception as exc:        # BrokenBarrierError if serialised
                errors.append(exc)

        threads = [threading.Thread(target=turn, args=(p,)) for p in phones]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"phones did not run concurrently: {errors}"
        assert sorted(reached) == [0, 1, 2]

    def test_lock_is_released_when_a_turn_raises(self, w):
        """A handler that throws must not wedge that customer forever."""
        phone = "+919999000055"
        w._phone_locks.pop(phone, None)

        with pytest.raises(ValueError):
            with w._lock_for_phone(phone):
                raise ValueError("bot blew up")

        lock = w._lock_for_phone(phone)
        assert lock.acquire(timeout=1), "lock still held after an exception"
        lock.release()


# ─────────────────────────────────────────────────────────────────────────────
# Server wiring
# ─────────────────────────────────────────────────────────────────────────────

class TestRelayServer:
    def test_handler_splits_dispatch_from_the_bot_turn(self, w):
        """do_POST parses and locks; _handle_bot does the work under that lock.
        If these ever merge back, the lock stops covering the session read."""
        import inspect
        src = inspect.getsource(w.start_bot_relay_server)
        assert "def _handle_bot" in src
        assert "_lock_for_phone(phone)" in src
        assert "self._handle_bot(phone, text)" in src

    def test_relay_server_is_threaded(self, w):
        import inspect
        src = inspect.getsource(w.start_bot_relay_server)
        assert "ThreadingHTTPServer" in src
        assert "daemon_threads = True" in src

    def test_reply_is_sent_before_conversation_logging(self, w):
        """The customer's reply only goes out once this response lands, and the
        logging below it is one Supabase round trip per message. Logging first
        delayed every reply by (1 + replies) network calls."""
        import inspect
        src = inspect.getsource(w.start_bot_relay_server)
        body = src[src.index("def _handle_bot"):]
        send = body.index('_json.dumps({"replies": reply_list})')
        log = body.index('from db_cloud import log_message')
        assert send < log, "conversation logging must not gate the reply"
