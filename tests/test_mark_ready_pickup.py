"""Regression tests: handle_mark_ready uses claim_unique_pickup_code (not bare generator)."""
import sqlite3
import pytest
import print_server


class _FakeSupabase:
    """Minimal _SupabaseLike stub — no taken codes in the collision table."""

    class _Q:
        def select(self, *a): return self
        def eq(self, *a): return self
        def limit(self, *a): return self
        def execute(self):
            class _R:
                data = []
            return _R()

    def table(self, name):
        return self._Q()


def _seed_db(path, pickup_code=None):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE jobs (
        job_id TEXT PRIMARY KEY,
        sender TEXT,
        filename TEXT,
        customer_name TEXT,
        pickup_code TEXT,
        pickup_ready_at TEXT,
        notes TEXT,
        status TEXT
    )""")
    conn.execute(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)",
        ("J-001", "919000000000", "essay.pdf", "Alice", pickup_code, None, None, "Printing"),
    )
    conn.commit()
    conn.close()


def test_handle_mark_ready_uses_claim_unique(monkeypatch, tmp_path):
    db = str(tmp_path / "jobs.db")
    _seed_db(db)

    claimed = []

    def _fake_claim(client):
        claimed.append(client)
        return "P-TEST"

    monkeypatch.setattr(print_server, "DB_PATH", db)
    monkeypatch.setattr(print_server, "_supabase_client", lambda: _FakeSupabase())
    monkeypatch.setattr(print_server, "_send_whatsapp", lambda *a, **kw: True)
    monkeypatch.setattr("pickup_code.claim_unique_pickup_code", _fake_claim)

    result = print_server.handle_mark_ready({"job_id": "J-001", "staff_id": "S1"})

    assert result["ok"] is True
    assert result["pickup_code"] == "P-TEST"
    assert len(claimed) == 1, "claim_unique_pickup_code should be called exactly once"


def test_handle_mark_ready_idempotent_existing_code(monkeypatch, tmp_path):
    """If job already has a pickup_code, claim_unique_pickup_code is NOT called again."""
    db = str(tmp_path / "jobs.db")
    _seed_db(db, pickup_code="P-ZZZZ")

    claim_calls = []

    def _fake_claim(client):
        claim_calls.append(1)
        return "P-NEWW"

    monkeypatch.setattr(print_server, "DB_PATH", db)
    monkeypatch.setattr(print_server, "_supabase_client", lambda: _FakeSupabase())
    monkeypatch.setattr(print_server, "_send_whatsapp", lambda *a, **kw: True)
    monkeypatch.setattr("pickup_code.claim_unique_pickup_code", _fake_claim)

    result = print_server.handle_mark_ready({"job_id": "J-001", "staff_id": "S1"})

    assert result["pickup_code"] == "P-ZZZZ"
    assert len(claim_calls) == 0, "claim should not be called when code already exists"


def test_handle_mark_ready_claim_failure_does_not_crash(monkeypatch, tmp_path):
    """If claim_unique_pickup_code raises, handler returns ok=True with code=None (graceful)."""
    db = str(tmp_path / "jobs.db")
    _seed_db(db)

    def _fail(client):
        raise RuntimeError("supabase down")

    monkeypatch.setattr(print_server, "DB_PATH", db)
    monkeypatch.setattr(print_server, "_supabase_client", lambda: _FakeSupabase())
    monkeypatch.setattr(print_server, "_send_whatsapp", lambda *a, **kw: True)
    monkeypatch.setattr("pickup_code.claim_unique_pickup_code", _fail)

    result = print_server.handle_mark_ready({"job_id": "J-001", "staff_id": "S1"})

    assert result["ok"] is True
    assert result["pickup_code"] is None
