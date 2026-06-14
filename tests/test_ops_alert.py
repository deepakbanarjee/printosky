"""Track B: ops-alert template path.

Scheduled alerts (chat-audit digest etc.) must reach Anu even when her Meta
24h customer-service window is closed — so they go via an approved utility
template, falling back to free-form text when the template isn't approved yet.
"""
import json
import whatsapp_notify as wn


def test_send_ops_alert_flattens_newlines_into_template_param(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        wn, "_send_meta_template",
        lambda phone, name, params, lang="en": captured.update(
            phone=phone, name=name, params=params) or True,
    )
    ok = wn.send_ops_alert("919072034907", "line one\n\nline two   spaced", "14 Jun 18:00 IST")
    assert ok is True
    assert captured["name"] == wn.OPS_ALERT_TEMPLATE
    # Meta rejects newlines/tabs/runs-of-spaces in body params → must be one line
    assert captured["params"] == ["line one line two spaced", "14 Jun 18:00 IST"]
    assert "\n" not in captured["params"][0]


def test_send_ops_alert_falls_back_to_freeform_when_template_unavailable(monkeypatch):
    monkeypatch.setattr(wn, "_send_meta_template", lambda *a, **k: False)
    sent = {}
    monkeypatch.setattr(wn, "_send_meta",
                        lambda phone, body: sent.update(phone=phone, body=body) or True)
    ok = wn.send_ops_alert("919072034907", "3 waiting", "14 Jun 18:00 IST", fallback="FULL DIGEST")
    assert ok is True
    assert sent["phone"] == "919072034907"
    assert sent["body"] == "FULL DIGEST"  # provided fallback used verbatim


def test_send_meta_template_builds_template_payload(monkeypatch):
    monkeypatch.setattr(wn, "META_PHONE_ID", "PHONEID")
    monkeypatch.setattr(wn, "META_TOKEN", "TOK")
    captured = {}

    class FakeResp:
        status = 200
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=15):
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(wn.urllib.request, "urlopen", fake_urlopen)
    ok = wn._send_meta_template("9072034907", "ops_alert", ["sum", "time"])
    assert ok is True
    p = captured["body"]
    assert p["type"] == "template"
    assert p["to"] == "919072034907"  # 10-digit → 91-prefixed
    assert p["template"]["name"] == "ops_alert"
    assert p["template"]["language"]["code"] == "en"
    body = p["template"]["components"][0]
    assert body["type"] == "body"
    assert [x["text"] for x in body["parameters"]] == ["sum", "time"]
