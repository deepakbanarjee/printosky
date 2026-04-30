"""Tests for whatsapp_notify.send_file() and helpers."""
import pytest
from unittest.mock import MagicMock, patch


def test_mime_to_wa_type_image():
    from whatsapp_notify import _mime_to_wa_type
    assert _mime_to_wa_type("image/jpeg") == "image"
    assert _mime_to_wa_type("image/png") == "image"


def test_mime_to_wa_type_audio():
    from whatsapp_notify import _mime_to_wa_type
    assert _mime_to_wa_type("audio/ogg") == "audio"
    assert _mime_to_wa_type("audio/mpeg") == "audio"


def test_mime_to_wa_type_video():
    from whatsapp_notify import _mime_to_wa_type
    assert _mime_to_wa_type("video/mp4") == "video"


def test_mime_to_wa_type_document():
    from whatsapp_notify import _mime_to_wa_type
    assert _mime_to_wa_type("application/pdf") == "document"
    assert _mime_to_wa_type("application/msword") == "document"


def test_send_file_success():
    """send_file returns True when both Meta upload and send succeed."""
    mock_upload_resp = MagicMock()
    mock_upload_resp.raise_for_status.return_value = None
    mock_upload_resp.json.return_value = {"id": "media-id-123"}

    mock_send_resp = MagicMock()
    mock_send_resp.ok = True

    with patch("whatsapp_notify._requests") as mock_req:
        mock_req.post.side_effect = [mock_upload_resp, mock_send_resp]
        from whatsapp_notify import send_file
        result = send_file("919495706405", b"fake-pdf", "application/pdf", "inv.pdf", "Invoice")

    assert result is True


def test_send_file_returns_false_on_upload_error():
    """send_file returns False (does not raise) when Meta upload fails."""
    with patch("whatsapp_notify._requests") as mock_req:
        mock_req.post.side_effect = Exception("network error")
        from whatsapp_notify import send_file
        result = send_file("919495706405", b"data", "image/jpeg", "photo.jpg")

    assert result is False


def test_send_file_document_includes_filename():
    """_send_meta_media must include filename field for document type."""
    mock_upload_resp = MagicMock()
    mock_upload_resp.raise_for_status.return_value = None
    mock_upload_resp.json.return_value = {"id": "mid-abc"}

    mock_send_resp = MagicMock()
    mock_send_resp.ok = True
    captured_payload = {}

    def fake_post(url, **kwargs):
        if "messages" in url:
            captured_payload.update(kwargs.get("json", {}))
            return mock_send_resp
        return mock_upload_resp

    with patch("whatsapp_notify._requests") as mock_req:
        mock_req.post.side_effect = fake_post
        from whatsapp_notify import send_file
        send_file("919495706405", b"pdf", "application/pdf", "invoice.pdf", "")

    doc = captured_payload.get("document", {})
    assert doc.get("filename") == "invoice.pdf"
