"""Unit tests for the online print-order HTTP handlers (api/handlers_order.py).

Each handler is exercised in isolation: _json_response and the DB/WhatsApp/quote
seams are monkeypatched so no Supabase, Meta API, or network is touched.
The quote test deliberately calls the REAL rate_card to lock the paper_type
token contract (B&W -> "A4_BW", colour -> "A4_col").
"""
import json
from unittest.mock import MagicMock


def _fake_h():
    return MagicMock()


def test_upload_sign_returns_signed_url(monkeypatch):
    import api.handlers_order as ho
    captured = {}
    monkeypatch.setattr(ho, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    fake_client = MagicMock()
    fake_client.storage.from_.return_value.create_signed_upload_url.return_value = {
        "signed_url": "https://x/signed", "path": "p"
    }
    monkeypatch.setattr(ho, "_get_client_bucket", lambda: (fake_client, "incoming-files"))
    ho._handle_order_upload_sign(_fake_h(), json.dumps({"filename": "a b.pdf"}).encode())
    assert captured["status"] == 200
    assert captured["data"]["signed_url"] == "https://x/signed"
    assert captured["data"]["storage_path"].startswith("orders/")
    assert captured["data"]["storage_path"].endswith("a_b.pdf")


def test_quote_uses_rate_card(monkeypatch):
    import api.handlers_order as ho
    captured = {}
    monkeypatch.setattr(ho, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    payload = {"print_items": [
        {"pages": 10, "paper_type": "A4_BW", "sides": "ss", "layout": "1-up", "copies": 1}
    ], "finishing": "none", "paper_size": "A4"}
    ho._handle_order_quote(_fake_h(), json.dumps(payload).encode())
    assert captured["status"] == 200
    assert "total" in captured["data"]
    assert captured["data"]["total"] >= 0
    assert isinstance(captured["data"]["breakdown"], list)


def test_create_inserts_job_and_settings(monkeypatch):
    import api.handlers_order as ho
    captured = {}
    monkeypatch.setattr(ho, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    calls = {}
    monkeypatch.setattr(ho, "_insert_job", lambda **kw: calls.setdefault("insert", kw))
    monkeypatch.setattr(ho, "_persist_settings", lambda job_id, **kw: calls.setdefault("settings", kw))
    monkeypatch.setattr(ho, "_send_confirmation", lambda *a, **k: calls.setdefault("wa", True))
    monkeypatch.setattr(ho, "_quote_total", lambda items, fin, size: 91.5)
    payload = {
        "customer": {"name": "Asha", "whatsapp": "919495706405", "delivery": 0},
        "file_url": "https://x/orders/u/report.pdf", "file_name": "report.pdf",
        "print_spec": {
            "file_ext": "pdf", "total_pages": 5, "pages_included": [1, 3, 4, 5],
            "colour_mode": "mixed", "colour_pages": [3], "nup": 1, "copies": 2,
            "paper_size": "A4", "sides": "single", "binding": "spiral",
            "sheet_count": 8, "price_exact": True,
        },
        "operator_note": "4 of 5 pages · SKIPPED pages: 2 · COLOUR pages: 3 · Spiral",
    }
    ho._handle_order_create(_fake_h(), json.dumps(payload).encode())
    assert captured["status"] == 200
    assert captured["data"]["job_id"].startswith("OSP-")
    assert calls["insert"]["sender"] == "919495706405"
    assert calls["settings"]["colour"] == "mixed"
    assert calls["settings"]["amount_quoted"] == 91.5
    assert calls["settings"]["page_count"] == 4          # len(pages_included [1,3,4,5])
    assert calls["settings"]["customer_name"] == "Asha"
    assert "PICKUP" in calls["settings"]["operator_note"]  # delivery=0 folded into note
    assert calls.get("wa") is True


def test_create_rejects_bad_phone(monkeypatch):
    import api.handlers_order as ho
    captured = {}
    monkeypatch.setattr(ho, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    ho._handle_order_create(_fake_h(), json.dumps({"customer": {"name": "x", "whatsapp": "123"}}).encode())
    assert captured["status"] == 400


def test_convert_docx_stub_returns_501(monkeypatch):
    import api.handlers_order as ho
    captured = {}
    monkeypatch.setattr(ho, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    ho._handle_order_convert_docx(_fake_h(), b"{}")
    assert captured["status"] == 501
    assert "not enabled" in captured["data"]["error"].lower()
