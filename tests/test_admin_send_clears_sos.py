"""Tests for the SOS-clear behaviour on staff actions.

The "SOS" pill in the admin chat UI is driven by the bot session's
``needs_human`` flag. When staff reply to a held customer (``/admin/send`` or
``/admin/send-file``) the flag must clear so the pill disappears — while the
``staff_hold`` step is left intact so the bot stays silent until staff hand the
conversation back via ``/staff/resume``.

Each handler is exercised in isolation: ``_json_response`` and the
WhatsApp/DB seams are monkeypatched so no Meta API or Supabase is touched.
"""
import json
from unittest.mock import MagicMock

import api.index  # noqa: F401  (import first: handlers_admin <-> api.index is circular)
import db_cloud
import whatsapp_notify


def _fake_h():
    return MagicMock()


# ── /admin/send ────────────────────────────────────────────────────────────────

def test_admin_send_clears_needs_human_on_success(monkeypatch):
    import api.handlers_admin as ha
    captured = {}
    saved = []
    monkeypatch.setattr(ha, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(ha, "ADMIN_PASSWORD_HASH", ha._sha256("secret"))
    monkeypatch.setattr(whatsapp_notify, "_send", lambda phone, msg: True)
    monkeypatch.setattr(db_cloud, "log_message", lambda *a, **k: None)
    monkeypatch.setattr(db_cloud, "save_session", lambda db, phone, **kw: saved.append((phone, kw)))

    body = json.dumps({"admin_password": "secret", "phone": "919495706405",
                       "message": "On it — sending now"}).encode()
    ha._handle_admin_send(_fake_h(), body)

    assert captured["status"] == 200
    # SOS flag cleared for the right phone
    assert saved == [("919495706405", {"needs_human": False})]
    # staff_hold (step) must NOT be touched — bot stays silent until resume
    assert "step" not in saved[0][1]


def test_admin_send_does_not_clear_on_send_failure(monkeypatch):
    import api.handlers_admin as ha
    captured = {}
    saved = []
    monkeypatch.setattr(ha, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(ha, "ADMIN_PASSWORD_HASH", ha._sha256("secret"))
    monkeypatch.setattr(whatsapp_notify, "_send", lambda phone, msg: False)  # WA send fails
    monkeypatch.setattr(db_cloud, "save_session", lambda db, phone, **kw: saved.append((phone, kw)))

    body = json.dumps({"admin_password": "secret", "phone": "919495706405",
                       "message": "hello"}).encode()
    ha._handle_admin_send(_fake_h(), body)

    assert captured["status"] == 502
    assert saved == []  # nothing cleared when the message never reached the customer


def test_admin_send_rejects_bad_password(monkeypatch):
    import api.handlers_admin as ha
    captured = {}
    saved = []
    monkeypatch.setattr(ha, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(ha, "ADMIN_PASSWORD_HASH", ha._sha256("secret"))
    monkeypatch.setattr(db_cloud, "save_session", lambda db, phone, **kw: saved.append((phone, kw)))

    body = json.dumps({"admin_password": "wrong", "phone": "919495706405",
                       "message": "hello"}).encode()
    ha._handle_admin_send(_fake_h(), body)

    assert captured["status"] == 403
    assert saved == []


# ── /admin/send-file ───────────────────────────────────────────────────────────

def test_admin_send_file_clears_needs_human_on_success(monkeypatch):
    import api.handlers_admin as ha
    captured = {}
    saved = []
    monkeypatch.setattr(ha, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(ha, "_auth_admin_pw", lambda pw: True)
    monkeypatch.setattr(whatsapp_notify, "send_file", lambda *a, **k: True)
    monkeypatch.setattr(db_cloud, "log_message", lambda *a, **k: None)
    monkeypatch.setattr(db_cloud, "save_session", lambda db, phone, **kw: saved.append((phone, kw)))

    fake_client = MagicMock()
    fake_client.storage.from_.return_value.download.return_value = b"%PDF-1.4 fake"
    monkeypatch.setattr(db_cloud, "_client", lambda: fake_client)
    monkeypatch.setattr(db_cloud, "INCOMING_BUCKET", "incoming-files", raising=False)

    body = json.dumps({"admin_password": "secret", "phone": "919495706405",
                       "storage_path": "orders/u/x.pdf", "mime_type": "application/pdf",
                       "filename": "x.pdf", "caption": ""}).encode()
    ha._handle_admin_send_file(_fake_h(), body)

    assert captured["status"] == 200
    assert ("919495706405", {"needs_human": False}) in saved


# ── /staff/resume ──────────────────────────────────────────────────────────────

def test_staff_resume_clears_needs_human_and_lifts_hold(monkeypatch):
    import api.index as api_mod
    captured = {}
    saved = []
    monkeypatch.setattr(api_mod, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(api_mod, "_auth_admin_pw", lambda pw: True)

    fake_client = MagicMock()
    fake_client.table.return_value.select.return_value.eq.return_value.execute.return_value = \
        MagicMock(data=[{"prev_step": "size"}])
    monkeypatch.setattr(db_cloud, "_client", lambda: fake_client)
    monkeypatch.setattr(db_cloud, "save_session", lambda db, phone, **kw: saved.append((phone, kw)))

    body = json.dumps({"admin_password": "secret", "phone": "919495706405"}).encode()
    api_mod._handle_staff_resume(_fake_h(), body)

    assert captured["status"] == 200
    assert len(saved) == 1
    phone, kw = saved[0]
    assert phone == "919495706405"
    assert kw.get("needs_human") is False     # SOS pill clears
    assert kw.get("step") == "size"           # bot resumed at prior step


def test_staff_resume_rejects_unauthenticated(monkeypatch):
    import api.index as api_mod
    captured = {}
    saved = []
    monkeypatch.setattr(api_mod, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(api_mod, "_auth_admin_pw", lambda pw: False)
    monkeypatch.setattr(db_cloud, "save_session", lambda db, phone, **kw: saved.append((phone, kw)))

    body = json.dumps({"admin_password": "nope", "phone": "919495706405"}).encode()
    api_mod._handle_staff_resume(_fake_h(), body)

    assert captured["status"] == 403
    assert saved == []
