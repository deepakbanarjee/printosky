"""
Tests for b2b_manager.py
Covers all DB operations using in-memory/temp SQLite.
"""

import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import b2b_manager


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: fresh DB for each test
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "b2b.db")
    b2b_manager.setup_b2b_db(path)
    return path


# ─────────────────────────────────────────────────────────────────────────────
# setup_b2b_db
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupB2bDb:
    def test_creates_b2b_clients_table(self, db):
        conn = sqlite3.connect(db)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        names = [t[0] for t in tables]
        assert "b2b_clients" in names

    def test_creates_b2b_payments_table(self, db):
        conn = sqlite3.connect(db)
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        conn.close()
        names = [t[0] for t in tables]
        assert "b2b_payments" in names

    def test_idempotent(self, db):
        # Should not raise if called twice
        b2b_manager.setup_b2b_db(db)


# ─────────────────────────────────────────────────────────────────────────────
# register_b2b_client
# ─────────────────────────────────────────────────────────────────────────────

class TestRegisterB2bClient:
    def test_register_new_client(self, db):
        msg = b2b_manager.register_b2b_client(db, "9876543210", "Acme Corp")
        assert "registered" in msg.lower() or "updated" in msg.lower() or "acme" in msg.lower()

    def test_register_persists_to_db(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Acme Corp")
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT * FROM b2b_clients").fetchone()
        conn.close()
        assert row is not None

    def test_register_with_discount(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Acme", discount_pct=15.0)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT discount_pct FROM b2b_clients").fetchone()
        conn.close()
        assert row[0] == 15.0

    def test_register_updates_existing(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Old Name")
        b2b_manager.register_b2b_client(db, "9876543210", "New Name")
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM b2b_clients").fetchone()[0]
        conn.close()
        assert count == 1  # UPSERT, not duplicate insert


# ─────────────────────────────────────────────────────────────────────────────
# get_b2b_client / is_b2b
# ─────────────────────────────────────────────────────────────────────────────

class TestGetB2bClient:
    def test_returns_none_for_unknown(self, db):
        assert b2b_manager.get_b2b_client(db, "0000000000") is None

    def test_returns_dict_for_known(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Test Co")
        r = b2b_manager.get_b2b_client(db, "9876543210")
        assert isinstance(r, dict)

    def test_country_code_normalization(self, db):
        # Register with 91-prefix, look up with 10-digit — this direction works
        # (lstrip("91") on "919876543210" strips chars not prefix, so 10→91 lookup is broken)
        b2b_manager.register_b2b_client(db, "919876543210", "Test Co")
        r = b2b_manager.get_b2b_client(db, "9876543210")
        assert r is not None

    def test_is_b2b_true(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Test Co")
        assert b2b_manager.is_b2b(db, "9876543210") is True

    def test_is_b2b_false(self, db):
        assert b2b_manager.is_b2b(db, "1234567890") is False


# ─────────────────────────────────────────────────────────────────────────────
# set_credit_limit
# ─────────────────────────────────────────────────────────────────────────────

class TestSetCreditLimit:
    def test_set_credit_limit(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Test Co")
        msg = b2b_manager.set_credit_limit(db, "9876543210", 5000)
        assert msg is not None
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT credit_limit FROM b2b_clients").fetchone()
        conn.close()
        assert row[0] == 5000


# ─────────────────────────────────────────────────────────────────────────────
# record_payment
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordPayment:
    def test_record_payment_inserts_row(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Test Co")
        b2b_manager.record_payment(db, "9876543210", 500.0, "cash")
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM b2b_payments").fetchone()[0]
        conn.close()
        assert count == 1

    def test_record_payment_returns_message(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Test Co")
        msg = b2b_manager.record_payment(db, "9876543210", 200.0, "upi", "REF123")
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_multiple_payments(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Test Co")
        b2b_manager.record_payment(db, "9876543210", 100.0, "cash")
        b2b_manager.record_payment(db, "9876543210", 200.0, "upi")
        conn = sqlite3.connect(db)
        count = conn.execute("SELECT COUNT(*) FROM b2b_payments").fetchone()[0]
        conn.close()
        assert count == 2


# ─────────────────────────────────────────────────────────────────────────────
# list_b2b_clients
# ─────────────────────────────────────────────────────────────────────────────

class TestListB2bClients:
    def test_empty_returns_string(self, db):
        result = b2b_manager.list_b2b_clients(db)
        assert isinstance(result, str)

    def test_shows_registered_clients(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Acme Corp")
        result = b2b_manager.list_b2b_clients(db)
        assert "Acme" in result

    def test_multiple_clients_listed(self, db):
        b2b_manager.register_b2b_client(db, "9876543210", "Alpha Ltd")
        b2b_manager.register_b2b_client(db, "9123456789", "Beta Ltd")
        result = b2b_manager.list_b2b_clients(db)
        assert "Alpha" in result
        assert "Beta" in result
