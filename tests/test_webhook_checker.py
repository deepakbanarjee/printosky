"""Tests for webhook_checker.py — temp SQLite DB, mocked HTTP and notify calls."""
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import webhook_checker as wc


def _make_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            sender TEXT,
            amount_quoted REAL,
            razorpay_link_id TEXT,
            link_sent_at TEXT,
            status TEXT DEFAULT 'QuoteSent',
            payment_mode TEXT,
            batch_id TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE job_batches (
            batch_id TEXT PRIMARY KEY,
            phone TEXT,
            total_amount REAL,
            razorpay_link_id TEXT,
            link_sent_at TEXT,
            status TEXT DEFAULT 'awaiting_payment',
            job_ids TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _stale():
    return (datetime.now() - timedelta(minutes=90)).strftime("%Y-%m-%d %H:%M:%S")


def _fresh():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── get_stale_jobs ────────────────────────────────────────────────────────────

def test_get_stale_jobs_empty_db(tmp_path):
    assert wc.get_stale_jobs(_make_db(tmp_path)) == []


def test_get_stale_jobs_fresh_link_excluded(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('J1','91999',100,'lnk1',?,'QuoteSent',NULL,NULL)", (_fresh(),))
    conn.commit(); conn.close()
    assert wc.get_stale_jobs(db) == []


def test_get_stale_jobs_returns_stale(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('J2','91999',200,'lnk2',?,'QuoteSent',NULL,NULL)", (_stale(),))
    conn.commit(); conn.close()
    result = wc.get_stale_jobs(db)
    assert len(result) == 1
    assert result[0]["job_id"] == "J2"
    assert result[0]["is_batch"] is False


def test_get_stale_jobs_excludes_paid(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('J3','91999',200,'lnk3',?,'Paid',NULL,NULL)", (_stale(),))
    conn.commit(); conn.close()
    assert wc.get_stale_jobs(db) == []


def test_get_stale_jobs_excludes_batch_jobs(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('J4','91999',200,'lnk4',?,'QuoteSent',NULL,'BATCH-1')", (_stale(),))
    conn.commit(); conn.close()
    assert wc.get_stale_jobs(db) == []


def test_get_stale_jobs_bad_db_path():
    assert wc.get_stale_jobs("/nonexistent/path.db") == []


# ── get_stale_batch_jobs ──────────────────────────────────────────────────────

def test_get_stale_batch_jobs_empty(tmp_path):
    assert wc.get_stale_batch_jobs(_make_db(tmp_path)) == []


def test_get_stale_batch_jobs_fresh_excluded(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO job_batches VALUES ('B1','91999',300,'blnk1',?,'awaiting_payment','J1,J2')", (_fresh(),))
    conn.commit(); conn.close()
    assert wc.get_stale_batch_jobs(db) == []


def test_get_stale_batch_jobs_returns_stale(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO job_batches VALUES ('B2','91998',400,'blnk2',?,'awaiting_payment','J3,J4')", (_stale(),))
    conn.commit(); conn.close()
    result = wc.get_stale_batch_jobs(db)
    assert len(result) == 1
    assert result[0]["job_id"] == "B2"
    assert result[0]["is_batch"] is True


def test_get_stale_batch_jobs_bad_db():
    assert wc.get_stale_batch_jobs("/bad/path.db") == []


# ── _check_razorpay_link_status ───────────────────────────────────────────────

def test_check_razorpay_paid():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "paid"}
    with patch.object(wc.requests, "get", return_value=mock_resp, create=True), \
         patch("razorpay_integration.RAZORPAY_KEY_ID", "k"), \
         patch("razorpay_integration.RAZORPAY_KEY_SECRET", "s"):
        assert wc._check_razorpay_link_status("lnk1") == "paid"


def test_check_razorpay_http_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch.object(wc.requests, "get", return_value=mock_resp, create=True), \
         patch("razorpay_integration.RAZORPAY_KEY_ID", "k"), \
         patch("razorpay_integration.RAZORPAY_KEY_SECRET", "s"):
        assert wc._check_razorpay_link_status("lnk_bad") is None


def test_check_razorpay_exception():
    with patch.object(wc.requests, "get", side_effect=Exception("timeout"), create=True), \
         patch("razorpay_integration.RAZORPAY_KEY_ID", "k"), \
         patch("razorpay_integration.RAZORPAY_KEY_SECRET", "s"):
        assert wc._check_razorpay_link_status("lnk_err") is None


# ── force_mark_paid ───────────────────────────────────────────────────────────

def test_force_mark_paid_single_job(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('JX','91777',150,'lnk',?,'QuoteSent',NULL,NULL)", (_stale(),))
    conn.commit(); conn.close()

    item = {"job_id": "JX", "sender": "91777", "amount_quoted": 150, "is_batch": False}
    with patch("whatsapp_notify.send_payment_confirmed", return_value=True, create=True):
        wc.force_mark_paid(item, db)

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status FROM jobs WHERE job_id='JX'").fetchone()
    conn.close()
    assert row[0] == "Paid"


def test_force_mark_paid_batch(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('J10','91888',100,'l1',?,'QuoteSent',NULL,'BX')", (_stale(),))
    conn.execute("INSERT INTO jobs VALUES ('J11','91888',100,'l2',?,'QuoteSent',NULL,'BX')", (_stale(),))
    conn.execute("INSERT INTO job_batches VALUES ('BX','91888',200,'blnk',?,'awaiting_payment','J10,J11')", (_stale(),))
    conn.commit(); conn.close()

    item = {"job_id": "BX", "sender": "91888", "amount_quoted": 200, "is_batch": True}
    with patch("whatsapp_notify.send_payment_confirmed", return_value=True, create=True):
        wc.force_mark_paid(item, db)

    conn = sqlite3.connect(db)
    j10 = conn.execute("SELECT status FROM jobs WHERE job_id='J10'").fetchone()
    batch = conn.execute("SELECT status FROM job_batches WHERE batch_id='BX'").fetchone()
    conn.close()
    assert j10[0] == "Paid"
    assert batch[0] == "paid"


def test_force_mark_paid_single_no_sender(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('JY',NULL,50,'lnk',?,'QuoteSent',NULL,NULL)", (_stale(),))
    conn.commit(); conn.close()

    item = {"job_id": "JY", "sender": None, "amount_quoted": 50, "is_batch": False}
    with patch("whatsapp_notify.send_payment_confirmed", return_value=True, create=True) as m:
        wc.force_mark_paid(item, db)
    m.assert_not_called()


# ── run_check ─────────────────────────────────────────────────────────────────

def test_run_check_no_stale(tmp_path):
    wc.run_check(_make_db(tmp_path))


def test_run_check_paid_triggers_force_mark(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('JP','91555',50,'lnkP',?,'QuoteSent',NULL,NULL)", (_stale(),))
    conn.commit(); conn.close()

    with patch.object(wc, "_check_razorpay_link_status", return_value="paid"), \
         patch.object(wc, "force_mark_paid") as mock_fmp:
        wc.run_check(db)
    mock_fmp.assert_called_once()


def test_run_check_expired_triggers_staff_alert(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('JE','91444',75,'lnkE',?,'QuoteSent',NULL,NULL)", (_stale(),))
    conn.commit(); conn.close()

    with patch.object(wc, "_check_razorpay_link_status", return_value="expired"), \
         patch.object(wc, "_alert_staff_stale") as mock_alert:
        wc.run_check(db)
    mock_alert.assert_called_once()


def test_run_check_item_missing_link_id_skipped(tmp_path):
    db = _make_db(tmp_path)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO jobs VALUES ('JN','91333',30,NULL,?,'QuoteSent',NULL,NULL)", (_stale(),))
    conn.commit(); conn.close()

    with patch.object(wc, "_check_razorpay_link_status") as mock_check:
        wc.run_check(db)
    mock_check.assert_not_called()


# ── start_checker ─────────────────────────────────────────────────────────────

def test_start_checker_returns_daemon_thread(tmp_path):
    t = wc.start_checker(_make_db(tmp_path))
    assert t.is_alive()
    assert t.daemon is True
