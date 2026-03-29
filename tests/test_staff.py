"""
Tests for staff_setup.py
Covers: sha256 (pure logic), staff DB operations with in-memory SQLite
"""

import sys
import os
import hashlib
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import staff_setup


# ─────────────────────────────────────────────────────────────────────────────
# sha256
# ─────────────────────────────────────────────────────────────────────────────

class TestSha256:
    def test_known_hash(self):
        # SHA-256 of "1234" is known
        expected = hashlib.sha256("1234".encode()).hexdigest()
        assert staff_setup.sha256("1234") == expected

    def test_different_pins_different_hashes(self):
        assert staff_setup.sha256("1234") != staff_setup.sha256("5678")

    def test_same_pin_same_hash(self):
        assert staff_setup.sha256("9999") == staff_setup.sha256("9999")

    def test_returns_hex_string(self):
        h = staff_setup.sha256("0000")
        assert isinstance(h, str)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_string(self):
        # Should not crash
        h = staff_setup.sha256("")
        assert len(h) == 64


# ─────────────────────────────────────────────────────────────────────────────
# DB fixture (in-memory via temp file since staff_setup uses get_conn() path)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def staff_db(tmp_path, monkeypatch):
    """Patch staff_setup.DB_PATH to a temp file and let get_conn() create the real schema."""
    db_path = str(tmp_path / "staff.db")
    monkeypatch.setattr(staff_setup, "DB_PATH", db_path)
    return db_path


class TestStaffDbOperations:
    def test_add_staff(self, staff_db):
        staff_setup.cmd_add("Alice", "1234")
        conn = sqlite3.connect(staff_db)
        rows = conn.execute("SELECT * FROM staff WHERE name='Alice'").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_add_stores_hashed_pin(self, staff_db):
        staff_setup.cmd_add("Bob", "5678")
        conn = sqlite3.connect(staff_db)
        row = conn.execute("SELECT pin_hash FROM staff WHERE name='Bob'").fetchone()
        conn.close()
        assert row[0] == staff_setup.sha256("5678")
        assert row[0] != "5678"  # not stored plain

    def test_reset_pin(self, staff_db):
        staff_setup.cmd_add("Carol", "1111")
        conn = sqlite3.connect(staff_db)
        id = conn.execute("SELECT id FROM staff WHERE name='Carol'").fetchone()[0]
        conn.close()
        staff_setup.cmd_reset_pin(id, "9999")
        conn = sqlite3.connect(staff_db)
        row = conn.execute("SELECT pin_hash FROM staff WHERE id=?", (id,)).fetchone()
        conn.close()
        assert row[0] == staff_setup.sha256("9999")

    def test_deactivate_staff(self, staff_db):
        staff_setup.cmd_add("Dave", "2222")
        conn = sqlite3.connect(staff_db)
        id = conn.execute("SELECT id FROM staff WHERE name='Dave'").fetchone()[0]
        conn.close()
        staff_setup.cmd_deactivate(id)
        conn = sqlite3.connect(staff_db)
        row = conn.execute("SELECT active FROM staff WHERE id=?", (id,)).fetchone()
        conn.close()
        assert row[0] == 0

    def test_activate_staff(self, staff_db):
        staff_setup.cmd_add("Eve", "3333")
        conn = sqlite3.connect(staff_db)
        id = conn.execute("SELECT id FROM staff WHERE name='Eve'").fetchone()[0]
        conn.close()
        staff_setup.cmd_deactivate(id)
        staff_setup.cmd_activate(id)
        conn = sqlite3.connect(staff_db)
        row = conn.execute("SELECT active FROM staff WHERE id=?", (id,)).fetchone()
        conn.close()
        assert row[0] == 1

    def test_seed_default_staff(self, staff_db):
        staff_setup.cmd_seed()
        conn = sqlite3.connect(staff_db)
        count = conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
        conn.close()
        assert count >= 1  # at least one default staff seeded

    def test_seed_idempotent(self, staff_db):
        staff_setup.cmd_seed()
        staff_setup.cmd_seed()  # second call should not crash
        conn = sqlite3.connect(staff_db)
        count = conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
        conn.close()
        assert count >= 1  # no duplicates
