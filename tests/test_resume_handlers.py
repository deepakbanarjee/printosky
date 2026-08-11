"""Unit tests for the resume builder AI endpoints (api/handlers_resume.py)."""
import json
import base64
from unittest.mock import MagicMock, patch
import pytest

# Fake request handler
def _fake_h():
    return MagicMock()

class _FakeBlock:
    def __init__(self, text):
        self.text = text

class _FakeMsg:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]

def _fake_client(response_text):
    create_mock = MagicMock(return_value=_FakeMsg(response_text))
    return MagicMock(messages=MagicMock(create=create_mock))

def test_resume_coach_improve_summary(monkeypatch):
    import api.handlers_resume as hr
    import api.index
    captured = {}
    monkeypatch.setattr(api.index, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(hr, "_require_admin", lambda h: True)  # auth covered by its own test
    
    mock_client = _fake_client("This is an improved professional summary text.")
    monkeypatch.setattr(hr, "_get_claude_client", lambda: mock_client)
    
    payload = {
        "action": "improve_summary",
        "data": {
            "name": "Albin Jose",
            "role": "Accountant",
            "current_summary": "I am an accountant looking for a job."
        }
    }
    
    hr._handle_resume_coach(_fake_h(), json.dumps(payload).encode())
    
    assert captured["status"] == 200
    assert captured["data"]["result"] == "This is an improved professional summary text."
    assert mock_client.messages.create.called

def test_resume_coach_suggest_skills(monkeypatch):
    import api.handlers_resume as hr
    import api.index
    captured = {}
    monkeypatch.setattr(api.index, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(hr, "_require_admin", lambda h: True)  # auth covered by its own test
    
    mock_client = _fake_client("Tally ERP, GST, Tax Filing, Excel, Communication")
    monkeypatch.setattr(hr, "_get_claude_client", lambda: mock_client)
    
    payload = {
        "action": "suggest_skills",
        "data": {
            "role": "Accountant"
        }
    }
    
    hr._handle_resume_coach(_fake_h(), json.dumps(payload).encode())
    
    assert captured["status"] == 200
    assert "Tally ERP" in captured["data"]["result"]

def test_resume_coach_optimize_ats(monkeypatch):
    import api.handlers_resume as hr
    import api.index
    captured = {}
    monkeypatch.setattr(api.index, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(hr, "_require_admin", lambda h: True)  # auth covered by its own test
    
    response_json = {
        "score": 92,
        "matching_keywords": ["SQL", "Agile"],
        "missing_keywords": ["Tableau"],
        "formatting_tips": ["Add location"],
        "content_tips": ["Use metrics"]
    }
    mock_client = _fake_client(json.dumps(response_json))
    monkeypatch.setattr(hr, "_get_claude_client", lambda: mock_client)
    
    payload = {
        "action": "optimize_ats",
        "data": {
            "resume_text": "Experienced dev skilled in SQL and Agile.",
            "job_description": "We are seeking a developer with SQL, Agile, and Tableau experience."
        }
    }
    
    hr._handle_resume_coach(_fake_h(), json.dumps(payload).encode())
    
    assert captured["status"] == 200
    assert captured["data"]["score"] == 92
    assert "SQL" in captured["data"]["matching_keywords"]

def test_resume_coach_rejects_oversized_body(monkeypatch):
    """Oversized bodies are rejected before any (billable) Claude call."""
    import api.handlers_resume as hr
    import api.index
    captured = {}
    monkeypatch.setattr(api.index, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(hr, "_require_admin", lambda h: True)  # auth covered by its own test

    client_called = {"v": False}
    def _boom():
        client_called["v"] = True
        return _fake_client("should not be reached")
    monkeypatch.setattr(hr, "_get_claude_client", _boom)

    huge = b'{"action":"improve_summary","data":{"current_summary":"' + b"x" * 200_000 + b'"}}'
    hr._handle_resume_coach(_fake_h(), huge)

    assert captured["status"] == 413
    assert client_called["v"] is False  # never spent a token


def test_resume_coach_rejects_wrong_admin_password(monkeypatch):
    """A wrong admin password 403s before any (billable) Claude call."""
    import api.handlers_resume as hr
    import api.index
    captured = {}
    monkeypatch.setattr(api.index, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(api.index, "ADMIN_PASSWORD_HASH", api.index._sha256("correct-horse"))

    called = {"v": False}
    def _boom():
        called["v"] = True
        return _fake_client("nope")
    monkeypatch.setattr(hr, "_get_claude_client", _boom)

    h = MagicMock()
    h.headers = {"X-Admin-Password": "wrong-pw"}
    payload = {"action": "improve_summary", "data": {"name": "A"}}
    hr._handle_resume_coach(h, json.dumps(payload).encode())

    assert captured["status"] == 403
    assert called["v"] is False  # never reached the client


def test_resume_coach_accepts_valid_admin_password(monkeypatch):
    """The correct admin password lets the request through to the coach."""
    import api.handlers_resume as hr
    import api.index
    captured = {}
    monkeypatch.setattr(api.index, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(api.index, "ADMIN_PASSWORD_HASH", api.index._sha256("s3cret"))
    monkeypatch.setattr(hr, "_get_claude_client", lambda: _fake_client("ok summary"))

    h = MagicMock()
    h.headers = {"X-Admin-Password": "s3cret"}
    payload = {"action": "improve_summary", "data": {"name": "A"}}
    hr._handle_resume_coach(h, json.dumps(payload).encode())

    assert captured["status"] == 200
    assert captured["data"]["result"] == "ok summary"


def test_resume_parse_pdf(monkeypatch):
    import api.handlers_resume as hr
    import api.index
    import docx_engine
    captured = {}
    monkeypatch.setattr(api.index, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    monkeypatch.setattr(hr, "_require_admin", lambda h: True)  # auth covered by its own test

    # Mock text extraction to return something. handlers_resume lazy-imports
    # docx_engine inside the handler, so patch the canonical module here.
    monkeypatch.setattr(docx_engine, "extract_text_from_pdf", lambda b: "Sample Resume Text")
    
    parsed_json = {
      "name": "Test Candidate",
      "role": "Engineer",
      "phone": "+91 99999 88888",
      "email": "test@email.com",
      "location": "Kochi",
      "linkedin": "",
      "summary": "Sample summary",
      "skills": "Python, SQL",
      "achievements": "- Cert 1",
      "languages": "English",
      "education": [],
      "experience": []
    }
    mock_client = _fake_client(json.dumps(parsed_json))
    monkeypatch.setattr(hr, "_get_claude_client", lambda: mock_client)
    
    payload = {
        "content_b64": base64.b64encode(b"%PDF-1.4 mock").decode("utf-8"),
        "filename": "resume.pdf"
    }
    
    hr._handle_resume_parse(_fake_h(), json.dumps(payload).encode())
    
    assert captured["status"] == 200
    assert captured["data"]["name"] == "Test Candidate"
    assert captured["data"]["skills"] == "Python, SQL"
