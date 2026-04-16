"""
Tests for b2b_manager.py

Covers:
- setup_b2b_db
- get_b2b_client (phone variants, active flag, not found)
- is_b2b
- register_b2b_client (insert + upsert)
- set_credit_limit
- record_payment (success, balance reduction, client not found)
- list_b2b_clients (empty, multiple)
- get_b2b_jobs (with and without jobs, unpaid_only, phone variants)
- print_b2b_jobs (with and without jobs)
- mark_jobs_invoiced (marks jobs, updates balance_due)
- generate_invoice_pdf (real reportlab output to tmp_path)
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import b2b_manager as b2b


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_db(tmp_path) -> str:
    db_path = str(tmp_path / "test.db")
    b2b.setup_b2b_db(db_path)
    # Add jobs table (b2b_manager queries it for get_b2b_jobs)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id          TEXT PRIMARY KEY,
            sender          TEXT,
            filename        TEXT,
            status          TEXT,
            amount_collected REAL DEFAULT 0,
            page_count      INTEGER,
            copies          INTEGER DEFAULT 1,
            size            TEXT,
            colour          TEXT,
            layout          TEXT,
            finishing       TEXT,
            delivery        INTEGER DEFAULT 0,
            received_at     TEXT DEFAULT (datetime('now')),
            invoiced        INTEGER DEFAULT 0,
            invoice_number  TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _seed_client(db_path, phone="919876543210", company="Test Corp",
                 contact="Alice", discount=10.0, balance=0.0, active=1):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO b2b_clients
        (phone, company_name, contact_name, discount_pct, balance_due, active, registered_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
    """, (phone, company, contact, discount, balance, active))
    conn.commit()
    conn.close()


def _seed_job(db_path, job_id, sender, amount=100.0, invoiced=0, filename="file.pdf"):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO jobs (job_id, sender, filename, status, amount_collected, invoiced, received_at)
        VALUES (?, ?, ?, 'Paid', ?, ?, datetime('now'))
    """, (job_id, sender, filename, amount, invoiced))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# setup_b2b_db
# ─────────────────────────────────────────────────────────────────────────────

class TestSetupB2bDb:
    def test_creates_b2b_clients_table(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        b2b.setup_b2b_db(db_path)
        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        conn.close()
        assert "b2b_clients" in tables
        assert "b2b_payments" in tables

    def test_idempotent(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        b2b.setup_b2b_db(db_path)
        b2b.setup_b2b_db(db_path)  # second call must not raise


# ─────────────────────────────────────────────────────────────────────────────
# get_b2b_client / is_b2b
# ─────────────────────────────────────────────────────────────────────────────

class TestGetB2bClient:
    def test_found_by_exact_phone(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Acme")
        result = b2b.get_b2b_client(db_path, "919876543210")
        assert result is not None
        assert result["company_name"] == "Acme"

    def test_found_with_country_code_variant(self, tmp_path):
        # Client stored without country code, lookup with it
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="9876543210")
        result = b2b.get_b2b_client(db_path, "919876543210")
        assert result is not None

    def test_found_without_country_code_variant(self, tmp_path):
        # Client stored with country code, lookup without it
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        result = b2b.get_b2b_client(db_path, "9876543210")
        assert result is not None

    def test_at_suffix_stripped(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        result = b2b.get_b2b_client(db_path, "919876543210@c.us")
        assert result is not None

    def test_inactive_client_not_returned(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", active=0)
        assert b2b.get_b2b_client(db_path, "919876543210") is None

    def test_unknown_phone_returns_none(self, tmp_path):
        db_path = _make_db(tmp_path)
        assert b2b.get_b2b_client(db_path, "91111") is None

    def test_is_b2b_true(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        assert b2b.is_b2b(db_path, "919876543210") is True

    def test_is_b2b_false(self, tmp_path):
        db_path = _make_db(tmp_path)
        assert b2b.is_b2b(db_path, "91000") is False


# ─────────────────────────────────────────────────────────────────────────────
# register_b2b_client
# ─────────────────────────────────────────────────────────────────────────────

class TestRegisterB2bClient:
    def test_new_client_inserted(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = b2b.register_b2b_client(db_path, "919876543210", "Corp A", "Bob", 5.0)
        assert "registered" in result
        assert b2b.is_b2b(db_path, "919876543210")

    def test_existing_client_updated(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Old Name")
        result = b2b.register_b2b_client(db_path, "919876543210", "New Name", "Alice", 15.0)
        assert "updated" in result
        client = b2b.get_b2b_client(db_path, "919876543210")
        assert client["company_name"] == "New Name"
        assert client["discount_pct"] == 15.0

    def test_discount_stored(self, tmp_path):
        db_path = _make_db(tmp_path)
        b2b.register_b2b_client(db_path, "919876543210", "Corp", "X", 20.0)
        client = b2b.get_b2b_client(db_path, "919876543210")
        assert client["discount_pct"] == 20.0

    def test_plus_and_spaces_stripped_from_phone(self, tmp_path):
        db_path = _make_db(tmp_path)
        b2b.register_b2b_client(db_path, "+91 9876543210", "Corp", "", 0)
        assert b2b.is_b2b(db_path, "919876543210")


# ─────────────────────────────────────────────────────────────────────────────
# set_credit_limit
# ─────────────────────────────────────────────────────────────────────────────

class TestSetCreditLimit:
    def test_sets_limit(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        b2b.set_credit_limit(db_path, "919876543210", 5000.0)
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT credit_limit FROM b2b_clients WHERE phone=?",
                           ("919876543210",)).fetchone()
        conn.close()
        assert row[0] == 5000.0

    def test_returns_confirmation_string(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        result = b2b.set_credit_limit(db_path, "919876543210", 1000.0)
        assert "1000" in result


# ─────────────────────────────────────────────────────────────────────────────
# record_payment
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordPayment:
    def test_reduces_balance_due(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", balance=500.0)
        b2b.record_payment(db_path, "919876543210", 200.0, "NEFT")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT balance_due FROM b2b_clients WHERE phone=?",
                           ("919876543210",)).fetchone()
        conn.close()
        assert row[0] == 300.0

    def test_balance_floored_at_zero(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", balance=100.0)
        b2b.record_payment(db_path, "919876543210", 500.0, "CASH")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT balance_due FROM b2b_clients WHERE phone=?",
                           ("919876543210",)).fetchone()
        conn.close()
        assert row[0] == 0.0

    def test_payment_row_inserted(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        b2b.record_payment(db_path, "919876543210", 150.0, "imps", "REF123")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT amount, mode, reference FROM b2b_payments WHERE phone=?",
                           ("919876543210",)).fetchone()
        conn.close()
        assert row[0] == 150.0
        assert row[1] == "IMPS"
        assert row[2] == "REF123"

    def test_unknown_client_returns_error(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = b2b.record_payment(db_path, "91000", 100.0, "CASH")
        assert "❌" in result or "No B2B client" in result

    def test_returns_confirmation_string(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Corp A")
        result = b2b.record_payment(db_path, "919876543210", 100.0, "upi")
        assert "Corp A" in result
        assert "100" in result


# ─────────────────────────────────────────────────────────────────────────────
# list_b2b_clients
# ─────────────────────────────────────────────────────────────────────────────

class TestListB2bClients:
    def test_empty_returns_no_clients_message(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = b2b.list_b2b_clients(db_path)
        assert "No B2B clients" in result

    def test_lists_active_clients(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Alpha Co")
        _seed_client(db_path, phone="918765432100", company="Beta Ltd")
        result = b2b.list_b2b_clients(db_path)
        assert "Alpha Co" in result
        assert "Beta Ltd" in result

    def test_inactive_excluded(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Active Co", active=1)
        _seed_client(db_path, phone="918765432100", company="Inactive Co", active=0)
        result = b2b.list_b2b_clients(db_path)
        assert "Active Co" in result
        assert "Inactive Co" not in result

    def test_balance_shown_when_due(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Debtor", balance=250.0)
        result = b2b.list_b2b_clients(db_path)
        assert "250" in result

    def test_count_in_header(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Corp A")
        _seed_client(db_path, phone="918765432100", company="Corp B")
        result = b2b.list_b2b_clients(db_path)
        assert "2" in result


# ─────────────────────────────────────────────────────────────────────────────
# get_b2b_jobs / print_b2b_jobs
# ─────────────────────────────────────────────────────────────────────────────

class TestGetB2bJobs:
    def test_returns_jobs_for_phone(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_job(db_path, "OSP-001", "919876543210", amount=50.0)
        _seed_job(db_path, "OSP-002", "919876543210", amount=75.0)
        jobs = b2b.get_b2b_jobs(db_path, "919876543210")
        assert len(jobs) == 2

    def test_returns_empty_for_unknown_phone(self, tmp_path):
        db_path = _make_db(tmp_path)
        assert b2b.get_b2b_jobs(db_path, "91000") == []

    def test_phone_variant_lookup(self, tmp_path):
        # Jobs stored with 10-digit, lookup with 12-digit
        db_path = _make_db(tmp_path)
        _seed_job(db_path, "OSP-001", "9876543210")
        jobs = b2b.get_b2b_jobs(db_path, "919876543210")
        assert len(jobs) == 1

    def test_unpaid_only_excludes_invoiced(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_job(db_path, "OSP-001", "919876543210", invoiced=0)
        _seed_job(db_path, "OSP-002", "919876543210", invoiced=1)
        jobs = b2b.get_b2b_jobs(db_path, "919876543210", unpaid_only=True)
        assert len(jobs) == 1
        assert jobs[0]["job_id"] == "OSP-001"

    def test_returns_list_of_dicts(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_job(db_path, "OSP-001", "919876543210")
        jobs = b2b.get_b2b_jobs(db_path, "919876543210")
        assert isinstance(jobs[0], dict)
        assert "job_id" in jobs[0]


class TestPrintB2bJobs:
    def test_no_jobs_returns_no_jobs_message(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = b2b.print_b2b_jobs(db_path, "91000")
        assert "No jobs" in result

    def test_shows_company_name(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="XYZ Corp")
        _seed_job(db_path, "OSP-001", "919876543210", amount=100.0)
        result = b2b.print_b2b_jobs(db_path, "919876543210")
        assert "XYZ Corp" in result

    def test_shows_total(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        _seed_job(db_path, "OSP-001", "919876543210", amount=100.0)
        _seed_job(db_path, "OSP-002", "919876543210", amount=200.0)
        result = b2b.print_b2b_jobs(db_path, "919876543210")
        assert "300" in result

    def test_shows_job_ids(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        _seed_job(db_path, "OSP-20260101-0001", "919876543210")
        result = b2b.print_b2b_jobs(db_path, "919876543210")
        assert "OSP-20260101-0001" in result


# ─────────────────────────────────────────────────────────────────────────────
# mark_jobs_invoiced
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkJobsInvoiced:
    def test_marks_uninvoiced_jobs(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        _seed_job(db_path, "OSP-001", "919876543210", invoiced=0)
        _seed_job(db_path, "OSP-002", "919876543210", invoiced=0)
        b2b.mark_jobs_invoiced(db_path, "919876543210", "INV-202601-3210")
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT invoice_number FROM jobs WHERE sender=?", ("919876543210",)
        ).fetchall()
        conn.close()
        assert all(r[0] == "INV-202601-3210" for r in rows)

    def test_does_not_remarked_already_invoiced(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        _seed_job(db_path, "OSP-001", "919876543210", invoiced=1)
        b2b.mark_jobs_invoiced(db_path, "919876543210", "INV-NEW")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT invoice_number FROM jobs WHERE job_id='OSP-001'").fetchone()
        conn.close()
        # Already invoiced → invoice_number should remain None/unchanged, not overwritten
        assert row[0] != "INV-NEW"

    def test_phone_variant_works(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210")
        _seed_job(db_path, "OSP-001", "9876543210", invoiced=0)  # stored without country code
        b2b.mark_jobs_invoiced(db_path, "919876543210", "INV-VAR")
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT invoice_number FROM jobs WHERE job_id='OSP-001'").fetchone()
        conn.close()
        assert row[0] == "INV-VAR"


# ─────────────────────────────────────────────────────────────────────────────
# generate_invoice_pdf
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerateInvoicePdf:
    def test_generates_pdf_file(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Acme Ltd",
                     contact="Bob", discount=10.0)
        _seed_job(db_path, "OSP-001", "919876543210", amount=150.0, invoiced=0)
        _seed_job(db_path, "OSP-002", "919876543210", amount=200.0, invoiced=0)

        out_path = str(tmp_path / "invoice.pdf")
        result = b2b.generate_invoice_pdf(db_path, "919876543210", out_path)

        path, grand_total, num_jobs, inv_number = result
        assert Path(path).exists()
        assert Path(path).stat().st_size > 1000  # real PDF content
        assert num_jobs == 2
        assert grand_total == pytest.approx(315.0)  # (150+200) * 0.90 = 315

    def test_raises_when_no_client(self, tmp_path):
        db_path = _make_db(tmp_path)
        with pytest.raises(ValueError, match="No B2B client"):
            b2b.generate_invoice_pdf(db_path, "91999")

    def test_raises_when_no_uninvoiced_jobs(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Corp")
        _seed_job(db_path, "OSP-001", "919876543210", invoiced=1)
        with pytest.raises(ValueError, match="No uninvoiced"):
            b2b.generate_invoice_pdf(db_path, "919876543210")

    def test_auto_output_path_in_db_directory(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Auto Corp")
        _seed_job(db_path, "OSP-001", "919876543210", amount=100.0, invoiced=0)
        path, _, _, _ = b2b.generate_invoice_pdf(db_path, "919876543210")
        assert Path(path).exists()
        assert Path(path).parent == tmp_path

    def test_invoice_number_format(self, tmp_path):
        db_path = _make_db(tmp_path)
        _seed_client(db_path, phone="919876543210", company="Inv Corp")
        _seed_job(db_path, "OSP-001", "919876543210", amount=50.0, invoiced=0)
        _, _, _, inv_number = b2b.generate_invoice_pdf(db_path, "919876543210")
        assert inv_number.startswith("INV-")
        # Last 4 digits of phone
        assert inv_number.endswith("3210")
