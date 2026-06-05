"""Regression tests for the two payment-path bugs fixed in webhook_receiver.py
(the store-PC Razorpay receiver — distinct from the Vercel-layer dedup covered
by test_webhook_idempotency.py):

  BUG 1 — Razorpay webhook had no idempotency guard. Redelivered events
          (which Razorpay sends on any non-2xx/timeout) re-ran the handler,
          double-notifying the customer and staff.

  BUG 2 — Batch payments never wrote `amount_collected`, so batch-paid jobs
          reported ₹0 revenue (dashboard.py sums amount_collected per job).
          The fix splits the batch total across its jobs so the SUM is exact.

Run: pytest tests/test_webhook_money_fixes.py -v
"""
import os
import sqlite3
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import webhook_receiver as wr  # noqa: E402


def _make_db(tmp_path) -> str:
    """Minimal SQLite DB with the jobs + job_batches columns the handler touches."""
    db_path = os.path.join(str(tmp_path), "jobs.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY,
            sender TEXT,
            filename TEXT,
            status TEXT,
            amount_collected REAL,
            payment_mode TEXT,
            razorpay_payment_id TEXT,
            size TEXT, colour TEXT, layout TEXT,
            copies INTEGER, finishing TEXT, delivery INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE job_batches (
            batch_id TEXT PRIMARY KEY,
            phone TEXT,
            job_ids TEXT,
            status TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _seed_single(db_path, job_id, phone):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO jobs (job_id, sender, filename, status) VALUES (?, ?, 'doc.pdf', 'Quoted')",
        (job_id, phone),
    )
    conn.commit()
    conn.close()


def _seed_batch(db_path, batch_id, phone, job_ids):
    conn = sqlite3.connect(db_path)
    for jid in job_ids:
        conn.execute(
            "INSERT INTO jobs (job_id, sender, status, size, colour, layout, copies, finishing, delivery)"
            " VALUES (?, ?, 'Quoted', 'A4', 'bw', 'single', 1, 'none', 0)",
            (jid, phone),
        )
    conn.execute(
        "INSERT INTO job_batches (batch_id, phone, job_ids, status) VALUES (?, ?, ?, 'pending')",
        (batch_id, phone, ",".join(job_ids)),
    )
    conn.commit()
    conn.close()


def _mods(notify_counter):
    """sys.modules patch dict that counts customer notifications."""
    return {
        "razorpay_integration": MagicMock(verify_webhook=lambda b, s: True),
        "whatsapp_notify": MagicMock(
            send_payment_confirmed=lambda *a, **kw: notify_counter.append(1)
        ),
        "whatsapp_bot": MagicMock(save_customer_profile=lambda *a, **kw: None),
    }


# ── BUG 1: idempotency ────────────────────────────────────────────────────────
class TestWebhookIdempotency:
    def test_single_job_redelivery_notifies_once(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_single(db_path, "OSP-20260606-0001", "919495706405")
        payment = {"job_id": "OSP-20260606-0001", "amount": 206.0,
                   "method": "upi", "payment_id": "pay_dup1"}
        notifies = []
        mods = _mods(notifies)
        mods["razorpay_integration"].parse_payment_webhook = lambda d: payment
        with patch.dict("sys.modules", mods):
            wr.process_payment({"event": "payment.captured"}, db_path)
            wr.process_payment({"event": "payment.captured"}, db_path)  # redelivery
        assert len(notifies) == 1, "redelivered webhook must not re-notify the customer"

    def test_batch_redelivery_notifies_once(self, tmp_path):
        db_path = _make_db(tmp_path)
        jids = ["OSP-20260606-0010", "OSP-20260606-0011"]
        _seed_batch(db_path, "BATCH-DUP", "919495706405", jids)
        payment = {"job_id": "BATCH-DUP", "amount": 100.0,
                   "method": "upi", "payment_id": "pay_dupbatch"}
        notifies = []
        mods = _mods(notifies)
        mods["razorpay_integration"].parse_payment_webhook = lambda d: payment
        with patch.dict("sys.modules", mods):
            wr.process_payment({"event": "payment.captured"}, db_path)
            wr.process_payment({"event": "payment.captured"}, db_path)  # redelivery
        assert len(notifies) == 1, "redelivered batch webhook must not re-notify"


# ── BUG 2: batch records amount, sum is exact ─────────────────────────────────
class TestBatchAmountRecorded:
    def test_batch_amount_is_split_and_recorded(self, tmp_path):
        db_path = _make_db(tmp_path)
        jids = ["OSP-A", "OSP-B", "OSP-C"]
        _seed_batch(db_path, "BATCH-SUM", "919495706405", jids)
        batch_row = ("BATCH-SUM", "919495706405", ",".join(jids))
        with patch.dict("sys.modules", _mods([])):
            wr._process_batch_payment(batch_row, 100.0, "upi", "pay_sum", db_path)

        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT amount_collected FROM jobs WHERE job_id IN ('OSP-A','OSP-B','OSP-C')"
        ).fetchall()
        total = conn.execute(
            "SELECT COALESCE(SUM(amount_collected),0) FROM jobs"
        ).fetchone()[0]
        conn.close()

        # No job left NULL (the old bug), and the split sums to the batch total exactly.
        assert all(r[0] is not None for r in rows), "every batch job must record an amount"
        assert round(total, 2) == 100.0, f"split must sum to the batch total, got {total}"

    def test_indivisible_total_has_no_rounding_drift(self, tmp_path):
        db_path = _make_db(tmp_path)
        jids = ["OSP-X", "OSP-Y", "OSP-Z"]
        _seed_batch(db_path, "BATCH-ODD", "919495706405", jids)
        batch_row = ("BATCH-ODD", "919495706405", ",".join(jids))
        with patch.dict("sys.modules", _mods([])):
            wr._process_batch_payment(batch_row, 100.0, "upi", "pay_odd", db_path)  # 100/3
        conn = sqlite3.connect(db_path)
        total = conn.execute("SELECT SUM(amount_collected) FROM jobs").fetchone()[0]
        conn.close()
        assert round(total, 2) == 100.0, f"₹100 across 3 jobs must total ₹100, got {total}"
