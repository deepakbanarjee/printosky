"""Tests for chat pin + follow-up notes (SCHEMA v30).

Covers the db_cloud helpers behind the admin Conversations "pin a chat / add a
follow-up note" feature, plus the chat-audit snapshot exposing pinned chats so
the twice-daily digest can remind staff about promised follow-ups.
"""
import os
import sys

import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def mock_client():
    """Patch db_cloud._client() to return a mock Supabase client."""
    with patch("db_cloud._client") as mc:
        client = MagicMock()
        mc.return_value = client
        yield client


# ── set_contact_pin ──────────────────────────────────────────────────────────

def test_set_contact_pin_true_stamps_pinned_at(mock_client):
    from db_cloud import set_contact_pin
    table = mock_client.table.return_value
    table.upsert.return_value.execute.return_value = MagicMock()

    assert set_contact_pin("9190000001", True) is True
    payload = table.upsert.call_args[0][0]
    assert payload["phone"] == "9190000001"
    assert payload["pinned"] is True
    assert payload["pinned_at"] is not None
    assert table.upsert.call_args.kwargs.get("on_conflict") == "phone"


def test_set_contact_pin_false_clears_pinned_at(mock_client):
    from db_cloud import set_contact_pin
    table = mock_client.table.return_value
    table.upsert.return_value.execute.return_value = MagicMock()

    assert set_contact_pin("9190000001", False) is True
    payload = table.upsert.call_args[0][0]
    assert payload["pinned"] is False
    assert payload["pinned_at"] is None


def test_set_contact_pin_returns_false_on_error(mock_client):
    from db_cloud import set_contact_pin
    mock_client.table.side_effect = RuntimeError("db down")
    assert set_contact_pin("9190000001", True) is False


# ── add_contact_note ─────────────────────────────────────────────────────────

def test_add_contact_note_inserts_trimmed(mock_client):
    from db_cloud import add_contact_note
    table = mock_client.table.return_value
    table.insert.return_value.execute.return_value = MagicMock(
        data=[{"id": 1, "phone": "9190000001", "note": "call tmrw"}])

    row = add_contact_note("9190000001", "  call tmrw  ", created_by="admin")
    inserted = table.insert.call_args[0][0]
    assert inserted["phone"] == "9190000001"
    assert inserted["note"] == "call tmrw"
    assert inserted["created_by"] == "admin"
    assert row["id"] == 1


def test_add_contact_note_empty_is_noop(mock_client):
    from db_cloud import add_contact_note
    assert add_contact_note("9190000001", "   ") == {}
    mock_client.table.return_value.insert.assert_not_called()


def test_add_contact_note_truncates_long_note(mock_client):
    from db_cloud import add_contact_note
    table = mock_client.table.return_value
    table.insert.return_value.execute.return_value = MagicMock(data=[{}])

    add_contact_note("9190000001", "x" * 5000)
    inserted = table.insert.call_args[0][0]
    assert len(inserted["note"]) == 2000


def test_add_contact_note_returns_empty_on_error(mock_client):
    from db_cloud import add_contact_note
    mock_client.table.side_effect = RuntimeError("no table")
    assert add_contact_note("9190000001", "hello") == {}


# ── list_contact_notes ───────────────────────────────────────────────────────

def test_list_contact_notes_filters_by_phone_newest_first(mock_client):
    from db_cloud import list_contact_notes
    select = mock_client.table.return_value.select.return_value
    select.eq.return_value.order.return_value.limit.return_value.execute.return_value = \
        MagicMock(data=[{"id": 2}, {"id": 1}])

    notes = list_contact_notes("9190000001")
    assert [n["id"] for n in notes] == [2, 1]
    select.eq.assert_called_with("phone", "9190000001")


def test_list_contact_notes_empty_on_error(mock_client):
    from db_cloud import list_contact_notes
    mock_client.table.side_effect = RuntimeError("no table")
    assert list_contact_notes("9190000001") == []


# ── delete_contact_note ──────────────────────────────────────────────────────

def test_delete_contact_note_by_id(mock_client):
    from db_cloud import delete_contact_note
    table = mock_client.table.return_value
    table.delete.return_value.eq.return_value.execute.return_value = MagicMock()

    assert delete_contact_note(7) is True
    table.delete.return_value.eq.assert_called_with("id", 7)


def test_delete_contact_note_false_on_error(mock_client):
    from db_cloud import delete_contact_note
    mock_client.table.side_effect = RuntimeError("boom")
    assert delete_contact_note(7) is False


# ── contact_note_counts ──────────────────────────────────────────────────────

def test_contact_note_counts_aggregates_per_phone(mock_client):
    from db_cloud import contact_note_counts
    table = mock_client.table.return_value
    table.select.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"phone": "a"}, {"phone": "a"}, {"phone": "b"}])

    assert contact_note_counts() == {"a": 2, "b": 1}


def test_contact_note_counts_empty_on_error(mock_client):
    from db_cloud import contact_note_counts
    mock_client.table.side_effect = RuntimeError("no table")
    assert contact_note_counts() == {}


# ── list_pinned_contacts ─────────────────────────────────────────────────────

def test_list_pinned_contacts_builds_entries_with_age_and_last_note(mock_client):
    from db_cloud import list_pinned_contacts
    select = mock_client.table.return_value.select.return_value
    # contacts query: .select(...).eq("pinned", True).execute()
    select.eq.return_value.execute.return_value = MagicMock(data=[
        {"phone": "9190000001", "name": "Alice", "pinned": True,
         "pinned_at": "2026-06-10T06:00:00+00:00"},
    ])
    # notes query: .select(...).in_(...).order(...).execute()
    select.in_.return_value.order.return_value.execute.return_value = MagicMock(data=[
        {"phone": "9190000001", "note": "call tmrw",
         "created_at": "2026-06-12T06:00:00+00:00"},
    ])

    out = list_pinned_contacts()
    assert len(out) == 1
    entry = out[0]
    assert entry["phone"] == "9190000001"
    assert entry["last_note"] == "call tmrw"
    assert isinstance(entry["age_hours"], (int, float))


def test_list_pinned_contacts_empty_when_none_pinned(mock_client):
    from db_cloud import list_pinned_contacts
    select = mock_client.table.return_value.select.return_value
    select.eq.return_value.execute.return_value = MagicMock(data=[])
    assert list_pinned_contacts() == []


def test_list_pinned_contacts_empty_on_error(mock_client):
    from db_cloud import list_pinned_contacts
    mock_client.table.side_effect = RuntimeError("no column")
    assert list_pinned_contacts() == []


# ── chat_audit_snapshot integration ──────────────────────────────────────────

def test_chat_audit_snapshot_exposes_pinned(monkeypatch):
    import db_cloud

    client = MagicMock()
    empty = MagicMock()
    empty.data = []
    # bot_sessions: select().eq().execute()
    client.table.return_value.select.return_value.eq.return_value.execute.return_value = empty
    # conversation_log: select().gte().order().limit().execute()
    client.table.return_value.select.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value = empty

    monkeypatch.setattr(db_cloud, "_client", lambda: client)
    monkeypatch.setattr(db_cloud, "find_sla_breaches", lambda **k: [])
    monkeypatch.setattr(db_cloud, "activity_counts", lambda **k: {"inbound": 0})
    monkeypatch.setattr(
        db_cloud, "list_pinned_contacts",
        lambda: [{"phone": "p", "name": "P", "pinned_at": None,
                  "age_hours": 1.0, "last_note": "x"}],
    )

    snap = db_cloud.chat_audit_snapshot()
    assert "pinned" in snap
    assert snap["pinned"][0]["phone"] == "p"
