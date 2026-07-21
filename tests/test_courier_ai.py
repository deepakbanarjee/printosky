"""Tests for courier_ai — transliteration + manifest parse + PIN/name matching.

The live Claude calls are not exercised here (no key / network); instead the AI
functions are checked for their fail-soft behaviour and their tool-output parsing
via a fake client, and match_rows (pure logic) is tested directly.
"""
import types

import pytest

import courier_ai as ca


# ── has_malayalam ───────────────────────────────────────────────────────────
def test_has_malayalam():
    assert ca.has_malayalam("സജിത")
    assert ca.has_malayalam("Sajitha നാട്ടിക")
    assert not ca.has_malayalam("Sajitha K, Nattika 680566")
    assert not ca.has_malayalam("")
    assert not ca.has_malayalam(None)


# ── transliterate_fields ────────────────────────────────────────────────────
def test_transliterate_noop_when_all_english():
    # No Malayalam → returns unchanged, never calls the API.
    vals = ["Sajitha K", "Nattika 680566"]
    assert ca.transliterate_fields(vals) == vals


def test_transliterate_falls_back_when_no_client(monkeypatch):
    monkeypatch.setattr(ca, "_client", lambda: None)
    vals = ["സജിത", "Nattika"]
    assert ca.transliterate_fields(vals) == vals   # unchanged on missing key


class _FakeBlock:
    type = "tool_use"
    def __init__(self, data):
        self.input = data


class _FakeMsg:
    def __init__(self, data):
        self.content = [_FakeBlock(data)]


def _fake_client(capture, data):
    def create(**kw):
        capture.update(kw)
        return _FakeMsg(data)
    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def test_transliterate_applies_tool_output(monkeypatch):
    cap = {}
    monkeypatch.setattr(ca, "_client",
                        lambda: _fake_client(cap, {"items": [{"id": 0, "english": "Sajitha K"}]}))
    out = ca.transliterate_fields(["സജിത കെ", "Nattika 680566"])
    assert out == ["Sajitha K", "Nattika 680566"]      # id 0 replaced, id 1 kept


# ── parse_manifest ──────────────────────────────────────────────────────────
def test_parse_manifest_empty_without_client(monkeypatch):
    monkeypatch.setattr(ca, "_client", lambda: None)
    assert ca.parse_manifest(b"%PDF-1.4 ...", "application/pdf") == []


def test_parse_manifest_returns_rows(monkeypatch):
    cap = {}
    rows = [{"article_number": "CL622881144IN", "receiver_name": "SAJITHA K", "dest_pin": "673637"}]
    monkeypatch.setattr(ca, "_client", lambda: _fake_client(cap, {"rows": rows}))
    out = ca.parse_manifest(b"imgbytes", "image/jpeg")
    assert out == rows
    # image goes as an image block, pdf as a document block
    assert cap["messages"][0]["content"][0]["type"] == "image"


# ── match_rows ──────────────────────────────────────────────────────────────
ORD_A = {"order_code": "XTR-A", "name": "Sajitha K", "address": "Pulikkal, 673637"}
ORD_B = {"order_code": "XTR-B", "name": "Anila John", "address": "Mankamkuzhy 690558"}
ORD_C1 = {"order_code": "XTR-C1", "name": "Simi Richard", "address": "Kalamassery 683104"}
ORD_C2 = {"order_code": "XTR-C2", "name": "Deepa Menon", "address": "Ernakulam 683104"}


def test_match_unique_pin():
    rows = [{"article_number": "CL1", "receiver_name": "SAJITHA K", "dest_pin": "673637"}]
    res = ca.match_rows(rows, [ORD_A, ORD_B])
    assert len(res["matched"]) == 1
    assert res["matched"][0]["order"]["order_code"] == "XTR-A"
    assert res["matched"][0]["tracking"] == "CL1"
    assert not res["ambiguous"] and not res["unmatched"]


def test_match_unmatched_pin():
    rows = [{"article_number": "CL9", "receiver_name": "NOBODY", "dest_pin": "999999"}]
    res = ca.match_rows(rows, [ORD_A, ORD_B])
    assert res["unmatched"] and res["unmatched"][0]["tracking"] == "CL9"
    assert not res["matched"]


def test_match_same_pin_disambiguated_by_name():
    # Two orders share PIN 683104; the name clearly picks Simi.
    rows = [{"article_number": "CL2", "receiver_name": "SIMI RICHARD", "dest_pin": "683104"}]
    res = ca.match_rows(rows, [ORD_C1, ORD_C2])
    assert len(res["matched"]) == 1
    assert res["matched"][0]["order"]["order_code"] == "XTR-C1"


def test_match_same_pin_ambiguous_when_name_unclear():
    # Shared PIN, receiver name matches neither well → left for the operator.
    rows = [{"article_number": "CL3", "receiver_name": "XYZ QRS", "dest_pin": "683104"}]
    res = ca.match_rows(rows, [ORD_C1, ORD_C2])
    assert len(res["ambiguous"]) == 1
    assert len(res["ambiguous"][0]["candidates"]) == 2
    assert not res["matched"]


def test_match_pin_extracted_from_messy_dest():
    rows = [{"article_number": "CL4", "receiver_name": "Anila John", "dest_pin": "PIN: 690558"}]
    res = ca.match_rows(rows, [ORD_A, ORD_B])
    assert len(res["matched"]) == 1
    assert res["matched"][0]["order"]["order_code"] == "XTR-B"
