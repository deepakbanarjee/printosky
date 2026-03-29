"""
Tests for whatsapp_bot.py — session DB operations
Uses in-memory SQLite via temp path.
"""

import sys
import os
import sqlite3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import whatsapp_bot as bot


# ─────────────────────────────────────────────────────────────────────────────
# Fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "bot.db")
    bot.setup_bot_db(path)
    # Also need customer_profiles for save_customer_profile
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customer_profiles (
            phone        TEXT PRIMARY KEY,
            last_size    TEXT,
            last_colour  TEXT,
            last_layout  TEXT,
            last_copies  INTEGER,
            last_finishing TEXT,
            last_delivery  INTEGER DEFAULT 0,
            updated_at   TEXT
        )
    """)
    conn.commit()
    conn.close()
    return path


# ─────────────────────────────────────────────────────────────────────────────
# setup_bot_db
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupBotDb:
    def test_creates_bot_sessions_table(self, db):
        conn = sqlite3.connect(db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        assert "bot_sessions" in tables

    def test_idempotent(self, db):
        bot.setup_bot_db(db)  # should not raise


# ─────────────────────────────────────────────────────────────────────────────
# get_session / save_session / clear_session
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionCrud:
    def test_get_unknown_phone_returns_empty(self, db):
        assert bot.get_session(db, "91999") == {}

    def test_save_and_get(self, db):
        bot.save_session(db, "91111", step="size", job_id="OSP-001")
        s = bot.get_session(db, "91111")
        assert s["step"] == "size"
        assert s["job_id"] == "OSP-001"

    def test_save_updates_existing(self, db):
        bot.save_session(db, "91111", step="size")
        bot.save_session(db, "91111", step="colour")
        s = bot.get_session(db, "91111")
        assert s["step"] == "colour"

    def test_clear_session_removes_record(self, db):
        bot.save_session(db, "91111", step="size")
        bot.clear_session(db, "91111")
        assert bot.get_session(db, "91111") == {}

    def test_clear_nonexistent_no_error(self, db):
        bot.clear_session(db, "99999")  # should not raise

    def test_save_multiple_fields(self, db):
        bot.save_session(db, "91111",
                         step="copies", size="A4", colour="bw",
                         layout="single", copies=2, finishing="spiral")
        s = bot.get_session(db, "91111")
        assert s["size"] == "A4"
        assert s["colour"] == "bw"
        assert s["copies"] == 2
        assert s["finishing"] == "spiral"

    def test_updated_at_is_set(self, db):
        bot.save_session(db, "91111", step="size")
        s = bot.get_session(db, "91111")
        assert s.get("updated_at") is not None

    def test_multiple_phones_independent(self, db):
        bot.save_session(db, "91111", step="size")
        bot.save_session(db, "91222", step="colour")
        assert bot.get_session(db, "91111")["step"] == "size"
        assert bot.get_session(db, "91222")["step"] == "colour"

    def test_clear_one_leaves_other(self, db):
        bot.save_session(db, "91111", step="size")
        bot.save_session(db, "91222", step="colour")
        bot.clear_session(db, "91111")
        assert bot.get_session(db, "91111") == {}
        assert bot.get_session(db, "91222")["step"] == "colour"


# ─────────────────────────────────────────────────────────────────────────────
# save_customer_profile
# ─────────────────────────────────────────────────────────────────────────────

class TestSaveCustomerProfile:
    def _settings(self, **overrides):
        base = {"size": "A4", "colour": "bw", "layout": "single",
                "copies": 1, "finishing": "none", "delivery": 0}
        base.update(overrides)
        return base

    def test_saves_profile(self, db):
        bot.save_customer_profile("91111", self._settings(), db)
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT * FROM customer_profiles WHERE phone='91111'").fetchone()
        conn.close()
        assert row is not None

    def test_upsert_updates_existing(self, db):
        bot.save_customer_profile("91111", self._settings(colour="bw"), db)
        bot.save_customer_profile("91111", self._settings(colour="col"), db)
        conn = sqlite3.connect(db)
        rows = conn.execute("SELECT * FROM customer_profiles WHERE phone='91111'").fetchall()
        conn.close()
        assert len(rows) == 1

    def test_stores_correct_colour(self, db):
        bot.save_customer_profile("91111", self._settings(colour="col"), db)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT last_colour FROM customer_profiles WHERE phone='91111'"
        ).fetchone()
        conn.close()
        assert row[0] == "col"

    def test_stores_copies(self, db):
        bot.save_customer_profile("91111", self._settings(copies=5), db)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT last_copies FROM customer_profiles WHERE phone='91111'"
        ).fetchone()
        conn.close()
        assert row[0] == 5

    def test_delivery_flag_stored(self, db):
        bot.save_customer_profile("91111", self._settings(delivery=1), db)
        conn = sqlite3.connect(db)
        row = conn.execute(
            "SELECT last_delivery FROM customer_profiles WHERE phone='91111'"
        ).fetchone()
        conn.close()
        assert row[0] == 1
