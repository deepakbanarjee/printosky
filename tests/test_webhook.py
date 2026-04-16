"""
Tests for webhook_receiver.py
Covers: _extract_aisensy_fields (pure logic, no HTTP/DB needed)
"""

import sys
import os
import types

# Stub requests before importing
for _mod in ("requests",):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import webhook_receiver as wr


class TestExtractAisensyFields:
    """_extract_aisensy_fields: parse AiSensy webhook payloads."""

    def _nested(self, msg_type="text", text="hello", phone="919876543210"):
        """Standard nested AiSensy payload."""
        return {
            "type": "message",
            "payload": {
                "type":    msg_type,
                "payload": {"text": text},
                "sender":  {"phone": phone, "name": "Test"},
            },
        }

    def test_extracts_phone(self):
        phone, _, _ = wr._extract_aisensy_fields(self._nested(phone="919999999999"))
        assert phone == "919999999999"

    def test_extracts_msg_type(self):
        _, mtype, _ = wr._extract_aisensy_fields(self._nested(msg_type="document"))
        assert mtype == "document"

    def test_extracts_inner_payload(self):
        _, _, inner = wr._extract_aisensy_fields(self._nested(text="hi"))
        assert inner.get("text") == "hi"

    def test_flat_payload_tolerated(self):
        """Flat structure: data IS the inner payload (no outer wrapper with 'payload' key)."""
        # outer = data.get("payload", data) → falls back to data when no "payload" key
        data = {
            "type":   "text",
            "sender": {"phone": "91123"},
            # no "payload" key — so outer = data itself
        }
        phone, mtype, _ = wr._extract_aisensy_fields(data)
        assert mtype == "text"
        assert phone == "91123"

    def test_phone_fallback_to_source(self):
        # "source" must be in the outer (inner payload) dict, not top-level data
        data = {
            "payload": {
                "type":    "text",
                "payload": {},
                "sender":  {},
                "source":  "91777",
            },
        }
        phone, _, _ = wr._extract_aisensy_fields(data)
        assert phone == "91777"

    def test_phone_fallback_to_from(self):
        data = {"from": "91888", "payload": {"type": "text", "payload": {}, "sender": {}}}
        phone, _, _ = wr._extract_aisensy_fields(data)
        assert phone == "91888"

    def test_empty_payload_returns_empty_strings(self):
        phone, mtype, inner = wr._extract_aisensy_fields({})
        assert phone == ""
        assert mtype == ""
        assert inner == {}

    def test_image_type(self):
        _, mtype, _ = wr._extract_aisensy_fields(self._nested(msg_type="image"))
        assert mtype == "image"

    def test_returns_tuple_of_three(self):
        result = wr._extract_aisensy_fields(self._nested())
        assert len(result) == 3

    def test_phone_from_nested_sender_takes_priority(self):
        data = {
            "from": "91_fallback",
            "source": "91_source",
            "payload": {
                "type": "text",
                "payload": {"text": "x"},
                "sender": {"phone": "91_primary"},
            },
        }
        phone, _, _ = wr._extract_aisensy_fields(data)
        assert phone == "91_primary"
