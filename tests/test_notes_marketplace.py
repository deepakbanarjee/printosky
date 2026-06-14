"""tests/test_notes_marketplace.py — Unit tests for Student Notes Marketplace.

Tests are isolated from Supabase and WhatsApp — all external calls are mocked.
Run with: pytest tests/test_notes_marketplace.py -v
"""
from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── stub heavy dependencies before importing project modules ──────────────────

def _stub_module(name: str, **attrs):
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod


# Preserve the real modules first. Every test file is imported at collection
# time, so an unconditional sys.modules swap below would replace db_cloud with a
# stub for the ENTIRE run and break every other file's db_cloud-dependent tests.
# We let handlers_notes bind to the stubs, then restore the real modules.
_REAL_MODULES = {n: sys.modules.get(n) for n in ("pdfplumber", "db_cloud")}

# pdfplumber stub
_pdf_stub = _stub_module("pdfplumber")
_pdf_stub.open = MagicMock()

# db_cloud stub (monkey-patched per test via setup_method)
_db_stub = _stub_module(
    "db_cloud",
    get_session=MagicMock(return_value={}),
    save_session=MagicMock(),
    upload_note_pdf=MagicMock(return_value="notes/NOTE-20260101-TEST.pdf"),
    create_note=MagicMock(return_value={"note_code": "NOTE-20260101-TEST"}),
    get_note=MagicMock(return_value={}),
    publish_note=MagicMock(return_value=True),
    reject_note=MagicMock(return_value=True),
    wallet_balance=MagicMock(return_value=0),
    wallet_add_credit=MagicMock(return_value=True),
    wallet_redeem=MagicMock(return_value=True),
    note_subscription_status=MagicMock(return_value={}),
    pending_notes_queue=MagicMock(return_value=[]),
    note_signed_url=MagicMock(return_value="https://example.com/signed/test.pdf"),
    NOTES_BUCKET="notes",
    NOTE_PRINT_PRICE_PAISE=100,
    NOTE_CREDIT_PAISE_PER_PAGE=10,
)

from api.handlers_notes import (  # noqa: E402
    is_notes_trigger,
    is_print_note_trigger,
    maybe_handle_notes,
    handle_notes_pdf,
    _gen_note_code,
    _count_pdf_pages,
    _match_category,
)

# handlers_notes has now captured the stubs above (its db_cloud / pdfplumber
# bindings stay stubbed for THIS file's tests). Restore the real modules in
# sys.modules so no other test file inherits the stub — this was corrupting
# db_cloud for the whole suite (39 failures + 17 errors in CI).
for _name, _mod in _REAL_MODULES.items():
    if _mod is not None:
        sys.modules[_name] = _mod
    else:
        sys.modules.pop(_name, None)


@pytest.fixture(autouse=True)
def _stub_db_cloud_for_notes(monkeypatch):
    """handlers_notes lazily imports db_cloud inside its functions, so re-install
    the stubs in sys.modules for the duration of each test in THIS file only.
    monkeypatch restores the real modules afterwards, so other test files keep
    the genuine db_cloud (this file used to leak its stub across the whole run)."""
    monkeypatch.setitem(sys.modules, "db_cloud", _db_stub)
    monkeypatch.setitem(sys.modules, "pdfplumber", _pdf_stub)
    yield


# ── is_notes_trigger ──────────────────────────────────────────────────────────

class TestIsNotesTrigger:
    def test_exact_match(self):
        assert is_notes_trigger("upload notes") is True
        assert is_notes_trigger("sell notes") is True
        assert is_notes_trigger("share notes") is True

    def test_case_insensitive(self):
        assert is_notes_trigger("Upload Notes") is True
        assert is_notes_trigger("SELL NOTES") is True

    def test_prefix_match(self):
        assert is_notes_trigger("upload notes please") is True

    def test_non_trigger(self):
        assert is_notes_trigger("print notes") is False
        assert is_notes_trigger("buy notes") is False
        assert is_notes_trigger("hello") is False
        assert is_notes_trigger("") is False


# ── is_print_note_trigger ─────────────────────────────────────────────────────

class TestIsPrintNoteTrigger:
    def test_valid(self):
        assert is_print_note_trigger("print note NOTE-20260101-AB1C") == "NOTE-20260101-AB1C"

    def test_case_insensitive_command(self):
        assert is_print_note_trigger("Print Note NOTE-20260101-AB1C") == "NOTE-20260101-AB1C"

    def test_lowercase_note_code_uppercased(self):
        assert is_print_note_trigger("print note note-20260101-ab1c") == "NOTE-20260101-AB1C"

    def test_not_a_note_prefix(self):
        assert is_print_note_trigger("print note") is None

    def test_non_note_code(self):
        assert is_print_note_trigger("print note ABC") is None   # no NOTE- prefix

    def test_unrelated_text(self):
        assert is_print_note_trigger("hello") is None


# ── _gen_note_code ────────────────────────────────────────────────────────────

class TestGenNoteCode:
    def test_format(self):
        code = _gen_note_code()
        parts = code.split("-")
        assert parts[0] == "NOTE"
        assert len(parts[1]) == 8     # YYYYMMDD
        assert len(parts[2]) == 4     # random suffix

    def test_starts_with_NOTE(self):
        assert _gen_note_code().startswith("NOTE-")

    def test_uniqueness(self):
        codes = {_gen_note_code() for _ in range(50)}
        assert len(codes) >= 40       # ≥40 unique in 50 attempts


# ── _match_category ───────────────────────────────────────────────────────────

class TestMatchCategory:
    def test_exact_key(self):
        assert _match_category("kerala_university") == "kerala_university"
        assert _match_category("cusat") == "cusat"

    def test_display_label(self):
        assert _match_category("Kerala University") == "kerala_university"
        assert _match_category("CUSAT") == "cusat"

    def test_partial_match(self):
        assert _match_category("kerala") == "kerala_university"
        assert _match_category("calicut") == "calicut_university"

    def test_no_match_returns_none(self):
        assert _match_category("oxford") is None
        assert _match_category("") is None


# ── _count_pdf_pages ──────────────────────────────────────────────────────────

class TestCountPdfPages:
    def test_valid_pdf_returns_page_count(self):
        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [MagicMock()] * 12
        with patch("api.handlers_notes.pdfplumber") as mock_plumber:
            mock_plumber.open.return_value = mock_pdf
            result = _count_pdf_pages(b"fake pdf bytes")
        assert result == 12

    def test_broken_pdf_returns_zero(self):
        with patch("api.handlers_notes.pdfplumber") as mock_plumber:
            mock_plumber.open.side_effect = Exception("corrupt pdf")
            result = _count_pdf_pages(b"garbage")
        assert result == 0


# ── maybe_handle_notes ────────────────────────────────────────────────────────

class TestMaybeHandleNotes:
    def setup_method(self):
        _db_stub.get_session.return_value = {}
        _db_stub.save_session.reset_mock()

    def test_returns_none_for_non_trigger_idle_user(self):
        result = maybe_handle_notes("91900000001", "hello there", "Alice")
        assert result is None

    def test_trigger_starts_flow_and_saves_session(self):
        result = maybe_handle_notes("91900000001", "upload notes", "Alice")
        assert result is not None
        assert len(result) == 1
        body = result[0]["text"]["body"]
        assert "PDF" in body or "pdf" in body.lower()
        saved_step = _db_stub.save_session.call_args[0][1]["step"]
        assert saved_step == "note_await_pdf"

    def test_in_flow_text_while_awaiting_pdf_asks_for_file(self):
        _db_stub.get_session.return_value = {
            "step": "note_await_pdf", "name": "Alice",
            "updated_at": "2026-01-01 10:00:00",
        }
        result = maybe_handle_notes("91900000001", "hello", "Alice")
        assert result is not None
        assert "PDF" in result[0]["text"]["body"]

    def test_title_too_short_rejected(self):
        _db_stub.get_session.return_value = {
            "step": "note_title", "name": "Alice",
            "updated_at": "2026-01-01 10:00:00",
        }
        result = maybe_handle_notes("91900000001", "Hi", "Alice")
        assert "5 characters" in result[0]["text"]["body"]

    def test_valid_title_advances_to_category_list(self):
        _db_stub.get_session.return_value = {
            "step": "note_title", "name": "Alice",
            "note_code": "NOTE-20260101-TEST",
            "note_pages": 10,
            "note_storage_path": "notes/x.pdf",
            "updated_at": "2026-01-01 10:00:00",
        }
        result = maybe_handle_notes("91900000001", "B.Com Accounting Notes", "Alice")
        assert result is not None
        assert result[0]["type"] == "interactive"
        assert result[0]["interactive"]["type"] == "list"

    def test_invalid_category_re_asks(self):
        _db_stub.get_session.return_value = {
            "step": "note_category", "name": "Alice",
            "note_code": "NOTE-TEST", "note_title": "Test Notes",
            "note_pages": 5, "note_storage_path": "notes/x.pdf",
            "updated_at": "2026-01-01 10:00:00",
        }
        result = maybe_handle_notes("91900000001", "harvard", "Alice")
        assert result is not None
        # Should be a list interactive (re-ask)
        assert result[0]["type"] == "interactive"

    def test_cancel_in_confirm_clears_session(self):
        _db_stub.get_session.return_value = {
            "step": "note_confirm", "name": "Alice",
            "note_code": "NOTE-TEST", "note_title": "Test Notes",
            "note_category": "cusat", "note_subject": "Physics",
            "note_pages": 5, "note_storage_path": "notes/x.pdf",
            "updated_at": "2026-01-01 10:00:00",
        }
        result = maybe_handle_notes("91900000001", "no", "Alice")
        assert result is not None
        saved = _db_stub.save_session.call_args[0][1]
        assert saved == {}   # session cleared


# ── handle_notes_pdf ─────────────────────────────────────────────────────────

class TestHandleNotesPdf:
    def setup_method(self):
        _db_stub.get_session.return_value = {
            "step": "note_await_pdf", "name": "Alice",
        }
        _db_stub.save_session.reset_mock()
        _db_stub.upload_note_pdf.return_value = "notes/NOTE-TEST.pdf"

    def test_zero_pages_returns_error_message(self):
        with patch("api.handlers_notes._count_pdf_pages", return_value=0):
            result = handle_notes_pdf("91900000001", b"bad pdf", "bad.pdf")
        assert len(result) == 1
        assert "couldn't read" in result[0]["text"]["body"].lower()

    def test_upload_failure_returns_error_message(self):
        _db_stub.upload_note_pdf.return_value = ""
        with patch("api.handlers_notes._count_pdf_pages", return_value=10):
            result = handle_notes_pdf("91900000001", b"pdf", "notes.pdf")
        assert "failed" in result[0]["text"]["body"].lower()

    def test_success_saves_session_with_title_step(self):
        with patch("api.handlers_notes._count_pdf_pages", return_value=25), \
             patch("api.handlers_notes._gen_note_code", return_value="NOTE-20260101-TEST"):
            result = handle_notes_pdf("91900000001", b"pdf", "notes.pdf")
        assert result is not None
        assert "25" in result[0]["text"]["body"]
        saved = _db_stub.save_session.call_args[0][1]
        assert saved["step"] == "note_title"
        assert saved["note_pages"] == 25
        assert saved["note_code"] == "NOTE-20260101-TEST"


# ── wallet constants (from db_cloud) ─────────────────────────────────────────

class TestWalletConstants:
    def test_price_constants(self):
        from db_cloud import NOTE_PRINT_PRICE_PAISE, NOTE_CREDIT_PAISE_PER_PAGE
        assert NOTE_PRINT_PRICE_PAISE == 100      # ₹1.00 per page
        assert NOTE_CREDIT_PAISE_PER_PAGE == 10   # ₹0.10 = 10% commission

    def test_credit_math_50_pages(self):
        """50-page notes → 50 × 10 paise = 500 paise = ₹5.00 per copy printed."""
        from db_cloud import NOTE_CREDIT_PAISE_PER_PAGE
        pages = 50
        credit_paise = pages * NOTE_CREDIT_PAISE_PER_PAGE
        assert credit_paise == 500
        assert credit_paise / 100 == 5.0

    def test_credit_never_float(self):
        """All arithmetic stays in integer paise to avoid floating-point drift."""
        from db_cloud import NOTE_CREDIT_PAISE_PER_PAGE, NOTE_PRINT_PRICE_PAISE
        assert isinstance(NOTE_CREDIT_PAISE_PER_PAGE, int)
        assert isinstance(NOTE_PRINT_PRICE_PAISE, int)
