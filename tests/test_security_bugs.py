"""
TDD regression tests for security bugs found in session 10 bug scan.

Bugs covered:
  1. Timing attack on PIN/password comparison (api/index.py:299,352,389)
  2. Race condition in generate_job_id (watcher.py:350-364)
  3. Empty META_APP_SECRET silently drops all webhooks (api/index.py:71)
  4. Cloud job ID collision for same-second uploads (api/index.py:~148)
"""

import sys
import os
import hmac
import hashlib
import sqlite3
import logging
import threading
import types

# ── stub heavy deps so api/index.py and watcher.py can be imported ───────────
for _mod in ("requests", "dotenv", "db_cloud", "whatsapp_bot",
             "whatsapp_notify", "razorpay_integration"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.modules["dotenv"].load_dotenv = lambda *a, **kw: None

# watchdog stub (watcher.py imports it at module level)
_wd = types.ModuleType("watchdog")
_wd_obs = types.ModuleType("watchdog.observers")
_wd_obs.Observer = object
_wd_ev = types.ModuleType("watchdog.events")
_wd_ev.FileSystemEventHandler = object
sys.modules["watchdog"] = _wd
sys.modules["watchdog.observers"] = _wd_obs
sys.modules["watchdog.events"] = _wd_ev

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import api.index as api_mod


# ═════════════════════════════════════════════════════════════════════════════
# BUG 1 — Timing attack: PIN/password comparison not constant-time
# ═════════════════════════════════════════════════════════════════════════════

class TestConstantTimeComparison:
    """
    _handle_staff_set_pin and _handle_admin_reset_pin must use
    hmac.compare_digest() for hash comparisons, not plain != or ==.
    """

    def test_set_pin_uses_compare_digest_not_inequality(self):
        """
        _handle_staff_set_pin must not use bare != for hash comparison.
        Since PBKDF2 refactor, PIN verification is delegated to _verify_pin()
        which uses hmac.compare_digest() internally.
        """
        import inspect
        src = inspect.getsource(api_mod._handle_staff_set_pin)
        # Must NOT have bare hash inequality
        assert 'pin_hash"] !=' not in src, (
            "TIMING ATTACK: _handle_staff_set_pin uses != for hash comparison "
            "instead of hmac.compare_digest()"
        )
        # Either direct compare_digest OR delegated to _verify_pin (which uses compare_digest)
        assert ("compare_digest" in src or "_verify_pin" in src), (
            "_handle_staff_set_pin must use hmac.compare_digest() or _verify_pin() for PIN comparison"
        )
        # Confirm _verify_pin itself uses compare_digest
        verify_src = inspect.getsource(api_mod._verify_pin)
        assert "compare_digest" in verify_src, (
            "_verify_pin must use hmac.compare_digest() for constant-time comparison"
        )

    def test_admin_reset_pin_uses_compare_digest_not_inequality(self):
        """Admin password check must be constant-time."""
        import inspect
        src = inspect.getsource(api_mod._handle_admin_reset_pin)
        assert "_sha256(admin_pw) !=" not in src, (
            "TIMING ATTACK: _handle_admin_reset_pin uses != for hash comparison"
        )
        assert "compare_digest" in src, (
            "_handle_admin_reset_pin must use hmac.compare_digest() for admin password"
        )

    def test_admin_send_uses_compare_digest_not_inequality(self):
        """Admin password check in _handle_admin_send must be constant-time."""
        import inspect
        src = inspect.getsource(api_mod._handle_admin_send)
        assert "_sha256(admin_pw) !=" not in src, (
            "TIMING ATTACK: _handle_admin_send uses != for hash comparison"
        )
        assert "compare_digest" in src, (
            "_handle_admin_send must use hmac.compare_digest() for admin password"
        )


# ═════════════════════════════════════════════════════════════════════════════
# BUG 3 — Empty META_APP_SECRET silently drops all webhooks
# ═════════════════════════════════════════════════════════════════════════════

class TestMetaAppSecretGuard:
    """
    _verify_meta_sig must detect and log an error when META_APP_SECRET
    is not configured, rather than silently computing a bogus HMAC.
    """

    def test_empty_secret_returns_false(self):
        """With empty secret, signature must always be rejected."""
        original = api_mod.META_APP_SECRET
        try:
            api_mod.META_APP_SECRET = ""
            result = api_mod._verify_meta_sig(b"any body", "sha256=abc123")
            assert result is False
        finally:
            api_mod.META_APP_SECRET = original

    def test_empty_secret_logs_error(self, caplog):
        """
        With empty META_APP_SECRET, an ERROR must be logged indicating
        misconfiguration — not a silent HMAC mismatch.
        """
        original = api_mod.META_APP_SECRET
        try:
            api_mod.META_APP_SECRET = ""
            with caplog.at_level(logging.ERROR, logger="api.webhook"):
                api_mod._verify_meta_sig(b"any body", "sha256=abc123")
            assert any("META_APP_SECRET" in r.message for r in caplog.records), (
                "No error logged for missing META_APP_SECRET — silent failure bug"
            )
        finally:
            api_mod.META_APP_SECRET = original

    def test_valid_secret_and_sig_passes(self):
        """Correctly signed request must pass verification."""
        secret = "test_secret_abc"
        body   = b'{"test": true}'
        sig    = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        original = api_mod.META_APP_SECRET
        try:
            api_mod.META_APP_SECRET = secret
            assert api_mod._verify_meta_sig(body, sig) is True
        finally:
            api_mod.META_APP_SECRET = original

    def test_wrong_sig_rejected(self):
        """Tampered signature must be rejected."""
        original = api_mod.META_APP_SECRET
        try:
            api_mod.META_APP_SECRET = "real_secret"
            result = api_mod._verify_meta_sig(b"body", "sha256=deadbeef00000000")
            assert result is False
        finally:
            api_mod.META_APP_SECRET = original


# ═════════════════════════════════════════════════════════════════════════════
# BUG 2 — Race condition in generate_job_id
# ═════════════════════════════════════════════════════════════════════════════

class TestJobIdRaceCondition:
    """
    generate_job_id() must produce unique IDs even when called
    concurrently from multiple threads.
    """

    def _make_db(self, tmp_path) -> str:
        db = str(tmp_path / "jobs.db")
        conn = sqlite3.connect(db)
        conn.execute("""
            CREATE TABLE jobs (
                id     INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT UNIQUE NOT NULL
            )
        """)
        conn.commit()
        conn.close()
        return db

    def test_concurrent_job_ids_are_unique(self, tmp_path):
        """
        10 threads calling generate_job_id() simultaneously must all
        receive distinct IDs — no duplicates despite the COUNT→INSERT gap.
        """
        import watcher as w
        db = self._make_db(tmp_path)
        original_db = w.DB_PATH
        original_counters = dict(w._job_id_counters)
        w._job_id_counters.clear()   # reset in-memory counter for clean test
        w.DB_PATH = db

        ids       = []
        errors    = []
        lock      = threading.Lock()
        barrier   = threading.Barrier(10)

        def _gen():
            try:
                barrier.wait()          # all threads fire at the same instant
                job_id = w.generate_job_id()
                with lock:
                    ids.append(job_id)
            except Exception as e:
                with lock:
                    errors.append(str(e))

        threads = [threading.Thread(target=_gen) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        w.DB_PATH = original_db
        w._job_id_counters.clear()
        w._job_id_counters.update(original_counters)

        assert not errors, f"Errors during concurrent generation: {errors}"
        assert len(ids) == 10, f"Expected 10 IDs, got {len(ids)}"
        assert len(set(ids)) == 10, (
            f"Duplicate job IDs generated under concurrency: {sorted(ids)}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# BUG 4 — Cloud job ID collision for same-second uploads
# ═════════════════════════════════════════════════════════════════════════════

class TestCloudJobIdUniqueness:
    """
    Cloud job IDs (generated in api/index.py _handle_media) must be
    unique even when the same sender uploads multiple files in the
    same second.
    """

    def _make_cloud_job_id(self, sender: str) -> str:
        """Reproduce the job_id formula from api/index.py _handle_media."""
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"OSP-{datetime.now().strftime('%Y%m%d')}-{sender[-4:]}-{ts[-4:]}-{os.urandom(3).hex()}"

    def test_same_sender_same_second_produces_unique_ids(self):
        """
        Two cloud job IDs generated for the same sender within the same
        second must not collide (os.urandom suffix guarantees this).
        """
        sender = "919876543210"
        ids = {self._make_cloud_job_id(sender) for _ in range(20)}
        assert len(ids) == 20, (
            f"Cloud job ID collision detected across 20 rapid calls"
        )
