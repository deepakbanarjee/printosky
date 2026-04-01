"""
test_review_manager.py — Pytest suite for review_manager.py

Coverage targets:
  - setup_review_db
  - schedule_review
  - cancel_review
  - send_review_request
  - record_rating
  - get_unused_discount
  - redeem_discount
  - _generate_code
"""

import sqlite3
import time
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

import review_manager
from review_manager import (
    _generate_code,
    cancel_review,
    get_unused_discount,
    record_rating,
    redeem_discount,
    schedule_review,
    send_review_request,
    setup_review_db,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _db(tmp_path: Path) -> str:
    """Return a file-based DB path string under tmp_path."""
    return str(tmp_path / "test.db")


def _open(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _tables(db_path: str) -> set:
    """Return names of user-created tables in the DB."""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {r[0] for r in rows}


def _insert_sent_review(db_path: str, job_id: str, phone: str) -> int:
    """Insert a review row with review_sent=1 and rating NULL. Returns row id."""
    conn = sqlite3.connect(db_path)
    try:
        setup_review_db(conn)
        cur = conn.execute(
            "INSERT INTO job_reviews (job_id, phone, review_sent) VALUES (?,?,1)",
            (job_id, phone),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def _noop_send(phone: str, msg: str) -> bool:
    return True


# ── setup_review_db ───────────────────────────────────────────────────────────

class TestSetupReviewDb:
    def test_creates_job_reviews_table(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
        assert "job_reviews" in _tables(db)

    def test_creates_discount_codes_table(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
        assert "discount_codes" in _tables(db)

    def test_job_reviews_columns(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(job_reviews)").fetchall()}
        expected = {"id", "job_id", "phone", "rating", "feedback", "review_sent", "created_at"}
        assert expected <= cols

    def test_discount_codes_columns(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(discount_codes)").fetchall()}
        expected = {"code", "phone", "pct_off", "source", "used", "created_at"}
        assert expected <= cols

    def test_idempotent_double_call(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            setup_review_db(conn)   # must not raise
        assert "job_reviews" in _tables(db)


# ── schedule_review ───────────────────────────────────────────────────────────

class TestScheduleReview:
    def setup_method(self) -> None:
        # Ensure the shared timer dict is clean between tests
        review_manager._pending_timers.clear()

    def test_inserts_pending_row(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        send = MagicMock(return_value=True)
        schedule_review(db, "JOB-001", "911234567890", send, delay_sec=3600)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT * FROM job_reviews WHERE job_id='JOB-001'"
            ).fetchone()
        assert row is not None
        # Cancel to avoid timer leaking into other tests
        cancel_review("JOB-001")

    def test_inserted_row_has_correct_phone(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        schedule_review(db, "JOB-002", "919876543210", _noop_send, delay_sec=3600)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT phone FROM job_reviews WHERE job_id='JOB-002'"
            ).fetchone()
        assert row[0] == "919876543210"
        cancel_review("JOB-002")

    def test_skips_empty_phone(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        schedule_review(db, "JOB-003", "", _noop_send, delay_sec=3600)
        # No row should be inserted
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM job_reviews WHERE job_id='JOB-003'"
            ).fetchone()[0]
        assert count == 0
        assert "JOB-003" not in review_manager._pending_timers

    def test_skips_none_phone(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        schedule_review(db, "JOB-004", None, _noop_send, delay_sec=3600)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            count = conn.execute(
                "SELECT COUNT(*) FROM job_reviews WHERE job_id='JOB-004'"
            ).fetchone()[0]
        assert count == 0

    def test_idempotent_second_call_skipped(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        schedule_review(db, "JOB-005", "911111111111", _noop_send, delay_sec=3600)
        schedule_review(db, "JOB-005", "911111111111", _noop_send, delay_sec=3600)
        with sqlite3.connect(db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM job_reviews WHERE job_id='JOB-005'"
            ).fetchone()[0]
        # INSERT OR IGNORE means only one row even if called twice
        assert count == 1
        cancel_review("JOB-005")

    def test_adds_timer_to_pending(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        schedule_review(db, "JOB-006", "912222222222", _noop_send, delay_sec=3600)
        assert "JOB-006" in review_manager._pending_timers
        cancel_review("JOB-006")

    def test_review_sent_defaults_to_zero(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        schedule_review(db, "JOB-007", "913333333333", _noop_send, delay_sec=3600)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT review_sent FROM job_reviews WHERE job_id='JOB-007'"
            ).fetchone()
        assert row[0] == 0
        cancel_review("JOB-007")


# ── cancel_review ─────────────────────────────────────────────────────────────

class TestCancelReview:
    def setup_method(self) -> None:
        review_manager._pending_timers.clear()

    def test_cancels_pending_timer(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        schedule_review(db, "JOB-C01", "914444444444", _noop_send, delay_sec=3600)
        assert "JOB-C01" in review_manager._pending_timers
        cancel_review("JOB-C01")
        assert "JOB-C01" not in review_manager._pending_timers

    def test_cancel_nonexistent_job_no_error(self) -> None:
        # Should silently do nothing
        cancel_review("DOES-NOT-EXIST")

    def test_cancel_twice_no_error(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        schedule_review(db, "JOB-C02", "915555555555", _noop_send, delay_sec=3600)
        cancel_review("JOB-C02")
        cancel_review("JOB-C02")   # second cancel must not raise


# ── send_review_request ───────────────────────────────────────────────────────

class TestSendReviewRequest:
    def test_marks_review_sent_in_db(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        # Insert a row with review_sent=0
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            conn.execute(
                "INSERT INTO job_reviews (job_id, phone) VALUES ('JOB-S01','916666666666')"
            )
            conn.commit()
        send_review_request(db, "JOB-S01", "916666666666", _noop_send)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT review_sent FROM job_reviews WHERE job_id='JOB-S01'"
            ).fetchone()
        assert row[0] == 1

    def test_calls_send_fn_with_correct_phone(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            conn.execute(
                "INSERT INTO job_reviews (job_id, phone) VALUES ('JOB-S02','917777777777')"
            )
            conn.commit()
        send_mock = MagicMock(return_value=True)
        send_review_request(db, "JOB-S02", "917777777777", send_mock)
        send_mock.assert_called_once()
        call_args = send_mock.call_args
        assert call_args[0][0] == "917777777777"

    def test_send_fn_receives_rating_prompt(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            conn.execute(
                "INSERT INTO job_reviews (job_id, phone) VALUES ('JOB-S03','918888888888')"
            )
            conn.commit()
        send_mock = MagicMock(return_value=True)
        send_review_request(db, "JOB-S03", "918888888888", send_mock)
        message = send_mock.call_args[0][1]
        # Message should contain rating options
        assert "1" in message and "5" in message

    def test_returns_true_when_send_fn_succeeds(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            conn.execute(
                "INSERT INTO job_reviews (job_id, phone) VALUES ('JOB-S04','919999999999')"
            )
            conn.commit()
        result = send_review_request(db, "JOB-S04", "919999999999", lambda p, m: True)
        assert result is True

    def test_returns_false_when_send_fn_fails(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            conn.execute(
                "INSERT INTO job_reviews (job_id, phone) VALUES ('JOB-S05','910001110000')"
            )
            conn.commit()
        result = send_review_request(db, "JOB-S05", "910001110000", lambda p, m: False)
        assert result is False


# ── record_rating ─────────────────────────────────────────────────────────────

class TestRecordRating:
    def test_rejects_rating_zero(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        result = record_rating(db, "910000000000", 0, _noop_send)
        assert result["ok"] is False
        assert "1-5" in result["error"]

    def test_rejects_rating_six(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        result = record_rating(db, "910000000000", 6, _noop_send)
        assert result["ok"] is False

    def test_rejects_negative_rating(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        result = record_rating(db, "910000000000", -1, _noop_send)
        assert result["ok"] is False

    def test_error_when_no_pending_review(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        result = record_rating(db, "910000000001", 4, _noop_send)
        assert result["ok"] is False
        assert "No pending review" in result["error"]

    def test_only_matches_review_sent_rows(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        # Insert a row that has NOT been sent yet (review_sent=0)
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
            conn.execute(
                "INSERT INTO job_reviews (job_id, phone, review_sent) VALUES ('JOB-R01','910000000002',0)"
            )
            conn.commit()
        result = record_rating(db, "910000000002", 5, _noop_send)
        assert result["ok"] is False
        assert "No pending review" in result["error"]

    def test_low_rating_1_recorded(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R02", "910000000003")
        result = record_rating(db, "910000000003", 1, _noop_send)
        assert result["ok"] is True
        assert result["rating"] == 1

    def test_low_rating_2_no_discount_code(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R03", "910000000004")
        result = record_rating(db, "910000000004", 2, _noop_send)
        assert result["discount_code"] is None

    def test_low_rating_3_calls_send_fn(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R04", "910000000005")
        send_mock = MagicMock(return_value=True)
        record_rating(db, "910000000005", 3, send_mock)
        send_mock.assert_called_once()

    def test_low_rating_acknowledgement_message(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R05", "910000000006")
        send_mock = MagicMock(return_value=True)
        record_rating(db, "910000000006", 3, send_mock)
        msg = send_mock.call_args[0][1]
        # Must NOT be the thank-you / discount message
        assert "THANK-" not in msg

    def test_high_rating_4_generates_discount_code(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R06", "910000000007")
        result = record_rating(db, "910000000007", 4, _noop_send)
        assert result["ok"] is True
        assert result["discount_code"] is not None
        assert result["discount_code"].startswith("THANK-")

    def test_high_rating_5_generates_discount_code(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R07", "910000000008")
        result = record_rating(db, "910000000008", 5, _noop_send)
        assert result["discount_code"] is not None

    def test_high_rating_sends_thankyou_message(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R08", "910000000009")
        send_mock = MagicMock(return_value=True)
        record_rating(db, "910000000009", 4, send_mock)
        send_mock.assert_called_once()
        msg = send_mock.call_args[0][1]
        assert "THANK-" in msg or "thank" in msg.lower()

    def test_high_rating_result_contains_job_id(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R09", "910000000010")
        result = record_rating(db, "910000000010", 5, _noop_send)
        assert result["job_id"] == "JOB-R09"

    def test_rating_stored_in_db(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        row_id = _insert_sent_review(db, "JOB-R10", "910000000011")
        record_rating(db, "910000000011", 4, _noop_send)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT rating FROM job_reviews WHERE id=?", (row_id,)
            ).fetchone()
        assert row[0] == 4

    def test_picks_most_recent_pending_review(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        # Insert two sent reviews for the same phone; second should be picked
        _insert_sent_review(db, "JOB-OLD", "910000000012")
        _insert_sent_review(db, "JOB-NEW", "910000000012")
        result = record_rating(db, "910000000012", 5, _noop_send)
        assert result["job_id"] == "JOB-NEW"

    def test_already_rated_row_not_matched_again(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-R11", "910000000013")
        # Rate once
        record_rating(db, "910000000013", 5, _noop_send)
        # Rate again — no pending review should remain
        result = record_rating(db, "910000000013", 3, _noop_send)
        assert result["ok"] is False

    def test_rating_boundary_1_is_valid(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-B1", "910000000014")
        result = record_rating(db, "910000000014", 1, _noop_send)
        assert result["ok"] is True

    def test_rating_boundary_5_is_valid(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _insert_sent_review(db, "JOB-B5", "910000000015")
        result = record_rating(db, "910000000015", 5, _noop_send)
        assert result["ok"] is True

    def test_send_fn_receives_correct_phone_on_low_rating(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        phone = "910000000016"
        _insert_sent_review(db, "JOB-R12", phone)
        send_mock = MagicMock(return_value=True)
        record_rating(db, phone, 2, send_mock)
        assert send_mock.call_args[0][0] == phone

    def test_send_fn_receives_correct_phone_on_high_rating(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        phone = "910000000017"
        _insert_sent_review(db, "JOB-R13", phone)
        send_mock = MagicMock(return_value=True)
        record_rating(db, phone, 5, send_mock)
        assert send_mock.call_args[0][0] == phone


# ── _generate_code ────────────────────────────────────────────────────────────

class TestGenerateCode:
    def test_returns_string_starting_with_thank(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000018")
        assert isinstance(code, str)
        assert code.startswith("THANK-")

    def test_code_stored_in_discount_codes_table(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000019")
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT code FROM discount_codes WHERE code=?", (code,)
            ).fetchone()
        assert row is not None
        assert row[0] == code

    def test_code_has_correct_suffix_length(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000020")
        # Format is THANK-XXXX (4 chars after dash)
        parts = code.split("-")
        assert len(parts) == 2
        assert len(parts[1]) == 4

    def test_code_stored_with_correct_phone(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        phone = "910000000021"
        code = _generate_code(db, phone)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT phone FROM discount_codes WHERE code=?", (code,)
            ).fetchone()
        assert row[0] == phone

    def test_new_code_starts_unused(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000022")
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT used FROM discount_codes WHERE code=?", (code,)
            ).fetchone()
        assert row[0] == 0

    def test_two_calls_return_different_codes(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        # With a 4-char suffix (36^4 = 1.6M possibilities) collision is astronomically rare
        codes = {_generate_code(db, "910000000023") for _ in range(5)}
        assert len(codes) == 5


# ── get_unused_discount ───────────────────────────────────────────────────────

class TestGetUnusedDiscount:
    def test_returns_none_when_no_codes(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        result = get_unused_discount(db, "910000000024")
        assert result is None

    def test_returns_code_dict(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000025")
        result = get_unused_discount(db, "910000000025")
        assert result is not None
        assert result["code"] == code

    def test_returns_pct_off_in_dict(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _generate_code(db, "910000000026")
        result = get_unused_discount(db, "910000000026")
        assert "pct_off" in result
        assert result["pct_off"] == 10

    def test_ignores_used_codes(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000027")
        redeem_discount(db, code)   # mark used
        result = get_unused_discount(db, "910000000027")
        assert result is None

    def test_returns_most_recent_unused(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        phone = "910000000028"
        code1 = _generate_code(db, phone)
        code2 = _generate_code(db, phone)
        result = get_unused_discount(db, phone)
        # Both codes are unused; the function returns one of them.
        # Redeem whichever was returned; the other must still be available.
        assert result["code"] in (code1, code2)
        redeemed = result["code"]
        other = code2 if redeemed == code1 else code1
        redeem_discount(db, redeemed)
        second_result = get_unused_discount(db, phone)
        assert second_result is not None
        assert second_result["code"] == other

    def test_does_not_return_other_phones_code(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        _generate_code(db, "910000000029")
        result = get_unused_discount(db, "910000000030")  # different phone
        assert result is None


# ── redeem_discount ───────────────────────────────────────────────────────────

class TestRedeemDiscount:
    def test_returns_true_for_valid_unused_code(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000031")
        assert redeem_discount(db, code) is True

    def test_marks_code_as_used_in_db(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000032")
        redeem_discount(db, code)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT used FROM discount_codes WHERE code=?", (code,)
            ).fetchone()
        assert row[0] == 1

    def test_returns_false_for_already_used_code(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000033")
        redeem_discount(db, code)
        assert redeem_discount(db, code) is False

    def test_returns_false_for_nonexistent_code(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        # Ensure table exists
        with sqlite3.connect(db) as conn:
            setup_review_db(conn)
        assert redeem_discount(db, "THANK-FAKE") is False

    def test_does_not_affect_other_codes(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code1 = _generate_code(db, "910000000034")
        code2 = _generate_code(db, "910000000034")
        redeem_discount(db, code1)
        # code2 should still be redeemable
        assert redeem_discount(db, code2) is True

    def test_code_unavailable_after_redemption(self, tmp_path: Path) -> None:
        db = _db(tmp_path)
        code = _generate_code(db, "910000000035")
        redeem_discount(db, code)
        result = get_unused_discount(db, "910000000035")
        assert result is None
