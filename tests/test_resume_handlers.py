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

def test_resume_parse_pdf(monkeypatch):
    import api.handlers_resume as hr
    import api.index
    captured = {}
    monkeypatch.setattr(api.index, "_json_response", lambda h, s, d: captured.update(status=s, data=d))
    
    # Mock text extraction to return something
    monkeypatch.setattr(hr.docx_engine, "extract_text_from_pdf", lambda b: "Sample Resume Text")
    
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
