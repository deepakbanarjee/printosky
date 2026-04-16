"""
Tests for webhook_receiver.py

Coverage targets:
- _verify_meta_signature (pure HMAC)
- _extract_aisensy_fields (pure parser)
- process_aisensy_message (mocked handlers)
- process_meta_message (mocked handlers)
- _handle_aisensy_text (mocked requests)
- _handle_aisensy_file (mocked requests + tmp folder)
- process_payment (temp SQLite + mocked imports)
- _process_batch_payment (temp SQLite + mocked imports)
- HTTP GET /whatsapp-webhook (verification challenge)
- HTTP GET / (health check)
- HTTP POST /webhook/razorpay (bad sig → 400, good sig → 200)
- HTTP POST /whatsapp-webhook (200 always, sig optional)
- HTTP POST /webhook/aisensy
- HTTP POST unknown path → 404
"""

import hashlib
import hmac
import http.client
import json
import os
import sqlite3
import sys
import threading
import time
from http.server import HTTPServer
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import webhook_receiver as wr


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_db(tmp_path):
    """Create a minimal SQLite DB with jobs + job_batches tables."""
    db_path = str(tmp_path / "test.db")
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
            size TEXT,
            colour TEXT,
            layout TEXT,
            copies INTEGER,
            finishing TEXT,
            delivery INTEGER
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


def _start_server(db_path: str, port: int) -> threading.Thread:
    """Start WebhookHandler server in a daemon thread, return thread."""
    server = HTTPServer(("127.0.0.1", port), wr.WebhookHandler)
    server.db_path = db_path
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.05)   # let the server bind
    return t


def _http_get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def _http_post(port: int, path: str, body: bytes, headers: dict = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    h = {"Content-Length": str(len(body))}
    if headers:
        h.update(headers)
    conn.request("POST", path, body=body, headers=h)
    resp = conn.getresponse()
    body_resp = resp.read()
    conn.close()
    return resp.status, body_resp


# ─────────────────────────────────────────────────────────────────────────────
# _verify_meta_signature
# ─────────────────────────────────────────────────────────────────────────────

class TestVerifyMetaSignature:
    def _make_sig(self, secret: str, body: bytes) -> str:
        digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"

    def test_valid_signature(self, monkeypatch):
        monkeypatch.setattr(wr, "META_APP_SECRET", "testsecret")
        body = b'{"hello": "world"}'
        sig = self._make_sig("testsecret", body)
        assert wr._verify_meta_signature(body, sig) is True

    def test_wrong_secret(self, monkeypatch):
        monkeypatch.setattr(wr, "META_APP_SECRET", "wrongsecret")
        body = b'{"hello": "world"}'
        sig = self._make_sig("correctsecret", body)
        assert wr._verify_meta_signature(body, sig) is False

    def test_missing_sha256_prefix(self, monkeypatch):
        monkeypatch.setattr(wr, "META_APP_SECRET", "testsecret")
        assert wr._verify_meta_signature(b"body", "abcdef1234") is False

    def test_empty_sig(self, monkeypatch):
        monkeypatch.setattr(wr, "META_APP_SECRET", "testsecret")
        assert wr._verify_meta_signature(body=b"body", sig_header="") is False

    def test_body_mismatch(self, monkeypatch):
        monkeypatch.setattr(wr, "META_APP_SECRET", "testsecret")
        body = b"original"
        sig = self._make_sig("testsecret", b"tampered")
        assert wr._verify_meta_signature(body, sig) is False


# ─────────────────────────────────────────────────────────────────────────────
# _extract_aisensy_fields
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractAisensyFields:
    def test_nested_payload_structure(self):
        data = {
            "payload": {
                "type": "text",
                "payload": {"text": "hello"},
                "sender": {"phone": "919876543210"},
            }
        }
        phone, msg_type, inner = wr._extract_aisensy_fields(data)
        assert phone == "919876543210"
        assert msg_type == "text"
        assert inner == {"text": "hello"}

    def test_from_field_fallback(self):
        # When payload key exists, outer = payload dict; phone falls back to data["from"]
        data = {"payload": {"type": "document", "payload": {}, "source": "91123"}}
        phone, msg_type, inner = wr._extract_aisensy_fields(data)
        assert phone == "91123"
        assert msg_type == "document"

    def test_source_fallback(self):
        data = {"payload": {"type": "text", "payload": {}, "source": "91999"}}
        phone, _, _ = wr._extract_aisensy_fields(data)
        assert phone == "91999"

    def test_missing_sender_returns_empty_string(self):
        data = {"payload": {"type": "text", "payload": {}}}
        phone, _, _ = wr._extract_aisensy_fields(data)
        assert phone == ""

    def test_corrupt_data_returns_empty(self):
        phone, msg_type, inner = wr._extract_aisensy_fields(None)
        assert phone == ""
        assert msg_type == ""
        assert inner == {}


# ─────────────────────────────────────────────────────────────────────────────
# process_aisensy_message
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessAisensyMessage:
    def test_file_message_calls_file_handler(self, monkeypatch):
        called = {}
        monkeypatch.setattr(wr, "_handle_aisensy_file",
                            lambda s, t, i, d: called.update({"called": True}))
        data = {
            "payload": {
                "type": "document",
                "payload": {"url": "http://example.com/file.pdf"},
                "sender": {"phone": "91111"},
            }
        }
        wr.process_aisensy_message(data, ":memory:")
        assert called.get("called")

    def test_text_message_calls_text_handler(self, monkeypatch):
        called = {}
        monkeypatch.setattr(wr, "_handle_aisensy_text",
                            lambda s, t, d: called.update({"text": t}))
        data = {
            "payload": {
                "type": "text",
                "payload": {"text": "hello"},
                "sender": {"phone": "91222"},
            }
        }
        wr.process_aisensy_message(data, ":memory:")
        assert called.get("text") == "hello"

    def test_no_sender_returns_early(self, monkeypatch):
        called = {}
        monkeypatch.setattr(wr, "_handle_aisensy_text",
                            lambda s, t, d: called.update({"called": True}))
        wr.process_aisensy_message({"payload": {"type": "text", "payload": {}}}, ":memory:")
        assert not called

    def test_unknown_type_logged_no_crash(self, monkeypatch):
        monkeypatch.setattr(wr, "_handle_aisensy_file", lambda *a: None)
        monkeypatch.setattr(wr, "_handle_aisensy_text", lambda *a: None)
        data = {
            "payload": {
                "type": "reaction",
                "payload": {},
                "sender": {"phone": "91333"},
            }
        }
        wr.process_aisensy_message(data, ":memory:")  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# _handle_aisensy_text
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleAisensyText:
    def test_empty_text_returns_early(self):
        # No HTTP call should be made for empty text
        with patch("webhook_receiver.requests") as mock_req:
            wr._handle_aisensy_text("91111", "", ":memory:")
            mock_req.post.assert_not_called()

    def test_success_posts_to_bot(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("webhook_receiver.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            wr._handle_aisensy_text("91111", "hello", ":memory:")
            mock_req.post.assert_called_once()
            call_kwargs = mock_req.post.call_args
            assert "hello" in str(call_kwargs)

    def test_network_error_does_not_raise(self):
        with patch("webhook_receiver.requests") as mock_req:
            mock_req.post.side_effect = OSError("connection refused")
            wr._handle_aisensy_text("91111", "hello", ":memory:")  # must not raise

    def test_non_200_logged_no_raise(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("webhook_receiver.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            wr._handle_aisensy_text("91111", "test", ":memory:")  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# _handle_aisensy_file
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleAisensyFile:
    def test_saves_file_and_sender_sidecar(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wr, "INCOMING_FOLDER", str(tmp_path))
        mock_resp = MagicMock()
        mock_resp.content = b"PDF CONTENT"
        mock_resp.raise_for_status = lambda: None
        with patch("webhook_receiver.requests") as mock_req:
            mock_req.get.return_value = mock_resp
            inner = {
                "url": "http://example.com/test.pdf",
                "caption": "my_file.pdf",
                "mimeType": "application/pdf",
            }
            wr._handle_aisensy_file("91999", "document", inner, str(tmp_path / "jobs.db"))

        saved = list(tmp_path.glob("my_file.pdf"))
        assert saved, "PDF file should be saved"
        sender_file = tmp_path / "my_file.pdf.sender"
        assert sender_file.exists()
        assert sender_file.read_text() == "91999"

    def test_no_url_returns_early(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wr, "INCOMING_FOLDER", str(tmp_path))
        with patch("webhook_receiver.requests") as mock_req:
            wr._handle_aisensy_file("91999", "document", {}, str(tmp_path / "jobs.db"))
            mock_req.get.assert_not_called()

    def test_auto_filename_when_no_caption(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wr, "INCOMING_FOLDER", str(tmp_path))
        mock_resp = MagicMock()
        mock_resp.content = b"DATA"
        mock_resp.raise_for_status = lambda: None
        with patch("webhook_receiver.requests") as mock_req:
            mock_req.get.return_value = mock_resp
            inner = {"url": "http://example.com/f", "mimeType": "application/pdf"}
            wr._handle_aisensy_file("91111", "document", inner, str(tmp_path / "jobs.db"))
        files = list(tmp_path.glob("91111_*.pdf"))
        assert files

    def test_download_error_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(wr, "INCOMING_FOLDER", str(tmp_path))
        with patch("webhook_receiver.requests") as mock_req:
            mock_req.get.side_effect = OSError("network error")
            wr._handle_aisensy_file("91999", "document",
                                    {"url": "http://x.com/f.pdf"}, str(tmp_path / "jobs.db"))


# ─────────────────────────────────────────────────────────────────────────────
# process_meta_message
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessMetaMessage:
    def _wrap(self, sender, msg_type, payload):
        return {"entry": [{"changes": [{"value": {"messages": [{
            "from": sender,
            "type": msg_type,
            **payload,
        }]}}]}]}

    def test_text_routes_to_text_handler(self, monkeypatch):
        called = {}
        monkeypatch.setattr(wr, "_handle_aisensy_text",
                            lambda s, t, d: called.update({"text": t}))
        data = self._wrap("919876", "text", {"text": {"body": "hi"}})
        wr.process_meta_message(data, ":memory:")
        assert called.get("text") == "hi"

    def test_document_routes_to_media_handler(self, monkeypatch):
        called = {}
        monkeypatch.setattr(wr, "_handle_meta_media",
                            lambda s, t, mid, mt, fn: called.update({"called": True}))
        data = self._wrap("91999", "document",
                          {"document": {"id": "media123", "mime_type": "application/pdf",
                                        "filename": "test.pdf"}})
        wr.process_meta_message(data, ":memory:")
        assert called.get("called")

    def test_unknown_type_no_crash(self, monkeypatch):
        monkeypatch.setattr(wr, "_handle_aisensy_text", lambda *a: None)
        data = self._wrap("91999", "reaction", {"reaction": {}})
        wr.process_meta_message(data, ":memory:")  # must not raise

    def test_empty_entry_list_no_crash(self):
        wr.process_meta_message({"entry": []}, ":memory:")

    def test_empty_text_body_skipped(self, monkeypatch):
        called = {}
        monkeypatch.setattr(wr, "_handle_aisensy_text",
                            lambda s, t, d: called.update({"called": True}))
        data = self._wrap("91111", "text", {"text": {"body": ""}})
        wr.process_meta_message(data, ":memory:")
        assert not called  # empty text should not call handler

    def test_corrupt_payload_no_crash(self):
        wr.process_meta_message({"entry": "not_a_list"}, ":memory:")


# ─────────────────────────────────────────────────────────────────────────────
# process_payment — single job
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessPayment:
    def _seed_job(self, db_path: str, job_id: str, sender: str):
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO jobs (job_id, sender, filename, status) VALUES (?, ?, ?, ?)",
            (job_id, sender, "file.pdf", "Quoted"),
        )
        conn.commit()
        conn.close()

    def test_single_job_marked_paid(self, tmp_path):
        db_path = _make_db(tmp_path)
        self._seed_job(db_path, "OSP-20260101-0001", "919876543210")

        data = {"event": "payment.captured", "payload": {"payment": {"entity": {
            "id": "pay_test1", "amount": 5000, "method": "upi",
            "notes": {"job_id": "OSP-20260101-0001"},
        }}}}

        mock_payment = {
            "job_id": "OSP-20260101-0001",
            "amount": 50.0,
            "method": "upi",
            "payment_id": "pay_test1",
        }

        with patch.dict("sys.modules", {
            "razorpay_integration": MagicMock(
                verify_webhook=lambda b, s: True,
                parse_payment_webhook=lambda d: mock_payment,
            ),
            "whatsapp_notify": MagicMock(send_payment_confirmed=lambda *a: None),
        }):
            wr.process_payment(data, db_path)

        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT status, payment_mode, razorpay_payment_id FROM jobs WHERE job_id=?",
            ("OSP-20260101-0001",),
        ).fetchone()
        conn.close()
        assert row[0] == "Paid"
        assert row[1] == "upi"
        assert row[2] == "pay_test1"

    def test_ignored_event_returns_early(self, tmp_path):
        db_path = _make_db(tmp_path)
        called = {}

        with patch.dict("sys.modules", {
            "razorpay_integration": MagicMock(
                verify_webhook=lambda b, s: True,
                parse_payment_webhook=lambda d: None,  # returns None → ignored
            ),
            "whatsapp_notify": MagicMock(send_payment_confirmed=lambda *a: called.update({"called": True})),
        }):
            wr.process_payment({"event": "order.created"}, db_path)

        assert not called  # no notification sent

    def test_missing_job_id_in_db_no_crash(self, tmp_path):
        db_path = _make_db(tmp_path)
        mock_payment = {
            "job_id": "OSP-99999999-9999",
            "amount": 50.0,
            "method": "upi",
            "payment_id": "pay_x",
        }
        with patch.dict("sys.modules", {
            "razorpay_integration": MagicMock(
                verify_webhook=lambda b, s: True,
                parse_payment_webhook=lambda d: mock_payment,
            ),
            "whatsapp_notify": MagicMock(send_payment_confirmed=lambda *a: None),
        }):
            wr.process_payment({}, db_path)  # must not raise


# ─────────────────────────────────────────────────────────────────────────────
# _process_batch_payment
# ─────────────────────────────────────────────────────────────────────────────

class TestProcessBatchPayment:
    def _seed_batch(self, db_path: str, batch_id: str, phone: str, job_ids: list):
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

    def test_all_jobs_marked_paid(self, tmp_path):
        db_path = _make_db(tmp_path)
        jids = ["OSP-20260101-0001", "OSP-20260101-0002"]
        self._seed_batch(db_path, "BATCH-001", "919876543210", jids)

        batch_row = ("BATCH-001", "919876543210", ",".join(jids))
        with patch.dict("sys.modules", {
            "whatsapp_notify": MagicMock(send_payment_confirmed=lambda *a: None),
            "whatsapp_bot": MagicMock(save_customer_profile=lambda *a, **kw: None),
        }):
            wr._process_batch_payment(batch_row, 100.0, "upi", "pay_batch1", db_path)

        conn = sqlite3.connect(db_path)
        for jid in jids:
            row = conn.execute("SELECT status FROM jobs WHERE job_id=?", (jid,)).fetchone()
            assert row[0] == "Paid"
        batch = conn.execute("SELECT status FROM job_batches WHERE batch_id='BATCH-001'").fetchone()
        assert batch[0] == "paid"
        conn.close()

    def test_empty_job_ids_returns_early(self, tmp_path):
        db_path = _make_db(tmp_path)
        batch_row = ("BATCH-EMPTY", "919876543210", "")
        called = {}
        with patch.dict("sys.modules", {
            "whatsapp_notify": MagicMock(
                send_payment_confirmed=lambda *a: called.update({"called": True})
            ),
            "whatsapp_bot": MagicMock(save_customer_profile=lambda *a, **kw: None),
        }):
            wr._process_batch_payment(batch_row, 50.0, "upi", "pay_x", db_path)
        assert not called


# ─────────────────────────────────────────────────────────────────────────────
# HTTP handler tests — real server in thread
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def server_port(tmp_path_factory):
    """Start one server for all HTTP tests in this module."""
    db_path = _make_db(tmp_path_factory.mktemp("http_db"))
    port = 13901
    _start_server(db_path, port)
    return port


class TestHttpGet:
    def test_health_check(self, server_port):
        status, body = _http_get(server_port, "/")
        assert status == 200
        assert b"Printosky" in body

    def test_meta_webhook_verify_correct_token(self, server_port, monkeypatch):
        monkeypatch.setattr(wr, "META_WEBHOOK_VERIFY_TOKEN", "testtoken123")
        status, body = _http_get(
            server_port,
            "/whatsapp-webhook?hub.verify_token=testtoken123&hub.challenge=mychal"
        )
        assert status == 200
        assert body == b"mychal"

    def test_meta_webhook_verify_wrong_token(self, server_port, monkeypatch):
        monkeypatch.setattr(wr, "META_WEBHOOK_VERIFY_TOKEN", "correct")
        status, body = _http_get(
            server_port,
            "/whatsapp-webhook?hub.verify_token=wrong&hub.challenge=chal"
        )
        assert status == 403

    def test_meta_webhook_missing_challenge(self, server_port, monkeypatch):
        monkeypatch.setattr(wr, "META_WEBHOOK_VERIFY_TOKEN", "tok")
        status, _ = _http_get(server_port, "/whatsapp-webhook?hub.verify_token=tok")
        assert status == 403


class TestHttpPost:
    def test_unknown_path_returns_404(self, server_port):
        status, _ = _http_post(server_port, "/unknown", b"{}")
        assert status == 404

    def test_razorpay_bad_signature_returns_400(self, server_port):
        body = json.dumps({"event": "payment.captured"}).encode()
        with patch.dict("sys.modules", {
            "razorpay_integration": MagicMock(
                verify_webhook=lambda b, s: False,
                parse_payment_webhook=lambda d: None,
            )
        }):
            status, resp_body = _http_post(
                server_port, "/webhook/razorpay", body,
                {"X-Razorpay-Signature": "badsig"}
            )
        assert status == 400
        assert b"Invalid" in resp_body

    def test_razorpay_valid_signature_returns_200(self, server_port):
        body = json.dumps({"event": "payment.captured"}).encode()
        mock_payment = {"job_id": "OSP-20260101-0001", "amount": 50.0,
                        "method": "upi", "payment_id": "pay_t"}
        with patch.dict("sys.modules", {
            "razorpay_integration": MagicMock(
                verify_webhook=lambda b, s: True,
                parse_payment_webhook=lambda d: mock_payment,
            ),
            "whatsapp_notify": MagicMock(send_payment_confirmed=lambda *a: None),
        }):
            status, _ = _http_post(
                server_port, "/webhook/razorpay", body,
                {"X-Razorpay-Signature": "validsig"}
            )
        assert status == 200

    def test_meta_webhook_always_returns_200(self, server_port, monkeypatch):
        monkeypatch.setattr(wr, "META_APP_SECRET", "")  # skip sig check
        body = json.dumps({"entry": []}).encode()
        status, _ = _http_post(server_port, "/whatsapp-webhook", body)
        assert status == 200

    def test_aisensy_webhook_returns_200(self, server_port, monkeypatch):
        monkeypatch.setattr(wr, "_handle_aisensy_text", lambda *a: None)
        body = json.dumps({
            "payload": {"type": "text", "payload": {"text": "hi"},
                        "sender": {"phone": "91111"}}
        }).encode()
        status, _ = _http_post(server_port, "/webhook/aisensy", body)
        assert status == 200

    def test_meta_webhook_bad_sig_still_200(self, server_port, monkeypatch):
        """Meta always gets 200 — we just drop the message silently on bad sig."""
        monkeypatch.setattr(wr, "META_APP_SECRET", "secret123")
        body = b'{"entry": []}'
        status, _ = _http_post(
            server_port, "/whatsapp-webhook", body,
            {"X-Hub-Signature-256": "sha256=badsignature"}
        )
        assert status == 200
