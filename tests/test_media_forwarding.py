"""Media forwarding: a payment screenshot from a customer who owes money must be
routed to Anu AND logged with a media_url so it shows in the admin transcript.

Regression: `_handle_media` used to (a) skip the `partially_paid` status and
(b) return None for payments, so later screenshots were dropped and invisible.
"""
import importlib


def test_handle_media_forwards_partially_paid_and_returns_path(monkeypatch):
    import db_cloud
    import book_bot
    import whatsapp_notify
    api = importlib.import_module("api.index")

    # customer owes money on a partially-paid order
    monkeypatch.setattr(db_cloud, "get_active_book_order",
                        lambda phone: {"order_code": "C", "status": "partially_paid"})
    monkeypatch.setattr(db_cloud, "get_book_payments",
                        lambda code: [{"id": 7,
                                       "proof_url": "https://x.supabase.co/storage/v1/"
                                                    "object/public/incoming-files/"
                                                    "book-payments/C_1.jpg?"}])
    called = {}

    def _fake_proof(phone, content, mime):
        called["proof"] = True
        return ["ok"]

    monkeypatch.setattr(book_bot, "handle_payment_proof", _fake_proof)
    monkeypatch.setattr(api, "_download_meta_media", lambda mid: b"imgbytes")
    monkeypatch.setattr(whatsapp_notify, "_send", lambda phone, msg: None)

    path = api._handle_media("919", "image", "MID", "image/jpeg", "")
    assert called.get("proof") is True                       # forwarded to Anu
    assert path == "book-payments/C_1.jpg"                    # logged as media_url
