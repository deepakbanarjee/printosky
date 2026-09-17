"""
Tests for print_server.py's concurrency and caching behaviour.

A store runs several PCs against one print server (docs/MULTI_BOX.md). These
tests pin the properties that keep one slow caller from freezing the others:

  * the server is threaded, so a blocking handler does not serialise the rest
  * the login rate limiter stays correct when attempts arrive simultaneously
  * the reachability probes run in parallel and are cached briefly
  * the probes never touch the process-wide socket default timeout

Each is a regression that was live before 2026-09-16: a single-threaded
HTTPServer, an unlocked read-modify-write limiter, three sequential socket
probes on every /health, and socket.setdefaulttimeout() leaking a timeout onto
every other socket in the process.
"""

import os
import socket
import sys
import threading
import time
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

import print_server


# ─────────────────────────────────────────────────────────────────────────────
# Threaded server
# ─────────────────────────────────────────────────────────────────────────────

class TestThreadedServer:
    def test_server_class_is_threaded(self):
        """A blocking handler must not serialise every other console."""
        assert issubclass(print_server._PrintServer, ThreadingHTTPServer)

    def test_daemon_threads_so_shutdown_is_immediate(self):
        # Without this, Ctrl-C hangs until every in-flight request finishes —
        # including a 90s colour-detect subprocess.
        assert print_server._PrintServer.daemon_threads is True

    def test_reuses_address_so_a_restart_can_rebind(self):
        # A store PC restart must not have to wait out TIME_WAIT on port 3005.
        assert print_server._PrintServer.allow_reuse_address is True


# ─────────────────────────────────────────────────────────────────────────────
# Rate limiter — atomic under concurrent attempts
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimiterUnderConcurrency:
    def setup_method(self):
        print_server._rate_limit.clear()

    def test_sequential_limit_still_holds(self):
        ip = "10.0.0.9"
        allowed = [print_server._check_rate_limit(ip) for _ in range(8)]
        assert allowed[: print_server._RATE_LIMIT_MAX] == [True] * print_server._RATE_LIMIT_MAX
        assert not any(allowed[print_server._RATE_LIMIT_MAX:])

    def test_simultaneous_attempts_cannot_exceed_the_cap(self):
        """The whole point of the limiter: a burst must not slip through.

        Unlocked, every thread read the same pre-append list, saw fewer than
        _RATE_LIMIT_MAX hits, and was let through — so 40 simultaneous guesses
        all passed a 5-per-minute limit.
        """
        ip = "10.0.0.10"
        results = []
        results_lock = threading.Lock()
        start = threading.Barrier(40)

        def attempt():
            start.wait()                      # maximise the overlap
            ok = print_server._check_rate_limit(ip)
            with results_lock:
                results.append(ok)

        threads = [threading.Thread(target=attempt) for _ in range(40)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert sum(results) == print_server._RATE_LIMIT_MAX


# ─────────────────────────────────────────────────────────────────────────────
# Reachability probes — parallel, cached, and no global socket state
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkProbes:
    def setup_method(self):
        print_server._probe_cache.update({"at": 0.0, "value": None})

    def test_probes_run_in_parallel_not_in_sequence(self, monkeypatch):
        """Three 0.2s probes should cost ~0.2s in total, not ~0.6s.

        In production these are up to 3s + 2s + 2s when a printer is powered
        off — the exact moment a console asks whether the printer is up.
        """
        delay = 0.2

        def slow_internet(*a, **kw):
            time.sleep(delay)
            return True

        def slow_printer(ip, *a, **kw):
            time.sleep(delay)
            return True

        monkeypatch.setattr(print_server, "check_internet", slow_internet)
        monkeypatch.setattr(print_server, "check_printer_reachable", slow_printer)

        t0 = time.perf_counter()
        assert print_server._probe_network() == (True, True, True)
        elapsed = time.perf_counter() - t0

        # Sequential would be >= 3*delay; allow generous headroom for slow CI.
        assert elapsed < delay * 2.5, f"probes appear sequential ({elapsed:.2f}s)"

    def test_result_is_cached_so_consoles_share_one_sweep(self, monkeypatch):
        calls = []
        monkeypatch.setattr(print_server, "check_internet",
                            lambda *a, **kw: calls.append("net") or True)
        monkeypatch.setattr(print_server, "check_printer_reachable",
                            lambda ip, *a, **kw: calls.append(ip) or True)
        monkeypatch.setattr(print_server, "_HEALTH_PROBE_TTL", 60.0)

        first = print_server._probe_network()
        after_first = len(calls)
        for _ in range(5):
            assert print_server._probe_network() == first
        assert len(calls) == after_first, "cached probes should not re-hit the network"

    def test_cache_expires_so_a_printer_coming_back_is_seen(self, monkeypatch):
        calls = []
        monkeypatch.setattr(print_server, "check_internet",
                            lambda *a, **kw: calls.append("net") or True)
        monkeypatch.setattr(print_server, "check_printer_reachable",
                            lambda ip, *a, **kw: True)
        monkeypatch.setattr(print_server, "_HEALTH_PROBE_TTL", 0.05)

        print_server._probe_network()
        time.sleep(0.1)
        print_server._probe_network()
        assert len(calls) == 2, "a stale cache must be re-probed, not served forever"

    def test_probes_do_not_change_the_process_wide_socket_timeout(self):
        """socket.setdefaulttimeout() is global: it raced between concurrent
        probes and left its value on every socket opened afterwards (Supabase,
        urllib, SNMP). Probes must scope their timeout to their own socket."""
        before = socket.getdefaulttimeout()
        # Port 9 (discard) on a doc-example address: refused or timed out, never
        # answered — either way the probe returns False without a real network.
        print_server.check_printer_reachable("192.0.2.1", timeout=0.05)
        print_server.check_internet(host="192.0.2.1", port=9, timeout=0.05)
        assert socket.getdefaulttimeout() == before

    def test_unreachable_probe_returns_false_rather_than_raising(self):
        assert print_server.check_printer_reachable("192.0.2.1", timeout=0.05) is False
        assert print_server.check_printer_reachable(None) is False


# ─────────────────────────────────────────────────────────────────────────────
# find_sumatra caching
# ─────────────────────────────────────────────────────────────────────────────

class TestActiveStaffSnapshot:
    def test_snapshot_is_a_copy_not_a_live_view(self):
        print_server._active_sessions.clear()
        print_server._active_sessions["PC1"] = {"session_id": 1}
        snap = print_server._active_staff_snapshot()
        print_server._active_sessions["PC2"] = {"session_id": 2}
        assert snap == ["PC1"], "snapshot must not change under the caller's feet"
        print_server._active_sessions.clear()

    def test_health_reports_a_self_consistent_staff_count(self, monkeypatch):
        """active_staff and staff_count must agree — they are read from one
        snapshot, not two separate looks at a dict another thread is mutating."""
        monkeypatch.setattr(print_server, "_probe_network", lambda: (True, True, True))
        print_server._active_sessions.clear()
        print_server._active_sessions.update(
            {"PC1": {"session_id": 1}, "PC2": {"session_id": 2}})
        health = print_server.get_system_health()
        assert health["staff_count"] == len(health["active_staff"]) == 2
        print_server._active_sessions.clear()


class TestFindSumatraCache:
    def test_repeated_calls_do_not_restat_the_disk(self, monkeypatch):
        """/status calls this on every console refresh."""
        print_server.find_sumatra.cache_clear()
        stats = []

        def counting_exists(p):
            stats.append(p)
            return False

        monkeypatch.setattr(print_server.os.path, "exists", counting_exists)
        print_server.find_sumatra()
        after_first = len(stats)
        assert after_first > 0

        for _ in range(10):
            print_server.find_sumatra()
        assert len(stats) == after_first

        print_server.find_sumatra.cache_clear()

    def test_cache_clear_is_exposed_for_a_live_recheck(self):
        assert callable(print_server.find_sumatra.cache_clear)
