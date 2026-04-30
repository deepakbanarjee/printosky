"""Tests for db_cloud contacts and media_url functions."""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_client():
    """Patch db_cloud._client() to return a mock Supabase client."""
    with patch("db_cloud._client") as mc:
        client = MagicMock()
        mc.return_value = client
        yield client


def test_log_message_includes_media_url(mock_client):
    """log_message must pass media_url to the insert dict."""
    from db_cloud import log_message
    table = mock_client.table.return_value
    table.insert.return_value.execute.return_value = MagicMock()

    log_message("919495706405", "inbound", "photo.jpg",
                message_type="image/jpeg", filename="photo.jpg",
                media_url="WA_2026-04-30_photo.jpg")

    inserted = table.insert.call_args[0][0]
    assert inserted["media_url"] == "WA_2026-04-30_photo.jpg"


def test_log_message_media_url_defaults_none(mock_client):
    """log_message without media_url should insert NULL (backward compat)."""
    from db_cloud import log_message
    table = mock_client.table.return_value
    table.insert.return_value.execute.return_value = MagicMock()

    log_message("919495706405", "inbound", "hello")

    inserted = table.insert.call_args[0][0]
    assert inserted.get("media_url") is None


def test_get_media_url_calls_public_url(mock_client):
    """get_media_url must delegate to Supabase get_public_url."""
    from db_cloud import get_media_url
    storage = mock_client.storage.from_.return_value
    storage.get_public_url.return_value = "https://supabase.co/.../photo.jpg"

    result = get_media_url("WA_2026-04-30_photo.jpg")

    storage.get_public_url.assert_called_once_with("WA_2026-04-30_photo.jpg")
    assert result == "https://supabase.co/.../photo.jpg"


def test_upsert_contact_with_name(mock_client):
    """upsert_contact with a name must include name in the upsert payload."""
    from db_cloud import upsert_contact
    table = mock_client.table.return_value
    table.upsert.return_value.execute.return_value = MagicMock()

    upsert_contact("919495706405", name="Alice")

    upserted = table.upsert.call_args[0][0]
    assert upserted["phone"] == "919495706405"
    assert upserted["name"] == "Alice"


def test_upsert_contact_without_name(mock_client):
    """upsert_contact without name must NOT include name key."""
    from db_cloud import upsert_contact
    table = mock_client.table.return_value
    table.upsert.return_value.execute.return_value = MagicMock()

    upsert_contact("919495706405")

    upserted = table.upsert.call_args[0][0]
    assert "name" not in upserted


def test_mark_contact_seen_upserts(mock_client):
    """mark_contact_seen must upsert an ISO timestamp for last_seen_at."""
    from db_cloud import mark_contact_seen
    table = mock_client.table.return_value
    table.upsert.return_value.execute.return_value = MagicMock()

    mark_contact_seen("919495706405")

    upserted = table.upsert.call_args[0][0]
    assert upserted["phone"] == "919495706405"
    assert "last_seen_at" in upserted
    # Must be an ISO timestamp string, not the literal "now()"
    assert upserted["last_seen_at"] != "now()"
    assert "T" in upserted["last_seen_at"]  # ISO format contains T separator
