"""Unit tests for WhatsApp (Meta) per-message cost tracking in db_cloud."""
import pytest

import db_cloud as d


# ── rate card ─────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_service_is_free():
    assert d.wa_estimated_cost_inr("service", True) == 0.0
    assert d.wa_estimated_cost_inr("service", False) == 0.0


@pytest.mark.unit
def test_billable_categories():
    assert d.wa_estimated_cost_inr("marketing", True) == 0.78
    assert d.wa_estimated_cost_inr("utility", True) == 0.12
    assert d.wa_estimated_cost_inr("authentication", True) == 0.12


@pytest.mark.unit
def test_not_billable_is_free_even_for_paid_category():
    # Meta's `billable` flag is authoritative (e.g. utility inside an open
    # service window is free) — category alone must not imply a charge.
    assert d.wa_estimated_cost_inr("marketing", False) == 0.0
    assert d.wa_estimated_cost_inr("utility", False) == 0.0


@pytest.mark.unit
def test_unknown_category_costs_zero():
    assert d.wa_estimated_cost_inr("weird", True) == 0.0
    assert d.wa_estimated_cost_inr(None, True) == 0.0


# ── record_wa_message_cost payload ────────────────────────────────────────────

class _FakeTable:
    def __init__(self, captured):
        self._c = captured

    def upsert(self, row, on_conflict=None):
        self._c["row"] = row
        self._c["on_conflict"] = on_conflict
        return self

    def execute(self):
        return None


class _FakeClient:
    def __init__(self, captured):
        self._c = captured

    def table(self, name):
        self._c["table"] = name
        return _FakeTable(self._c)


@pytest.mark.unit
def test_record_with_pricing_builds_full_row(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(d, "_client", lambda: _FakeClient(captured))
    d.record_wa_message_cost(
        "wamid-1", "919999999999", "sent",
        pricing={"category": "marketing", "billable": True, "pricing_model": "PMP"},
        conversation={"id": "conv-1", "origin": {"type": "marketing"}},
    )
    assert captured["table"] == "wa_message_costs"
    assert captured["on_conflict"] == "wamid"
    row = captured["row"]
    assert row["wamid"] == "wamid-1"
    assert row["recipient"] == "919999999999"
    assert row["status"] == "sent"
    assert row["category"] == "marketing"
    assert row["billable"] is True
    assert row["est_cost_inr"] == 0.78
    assert row["conversation_id"] == "conv-1"
    assert row["origin_type"] == "marketing"


@pytest.mark.unit
def test_record_without_pricing_omits_cost_fields(monkeypatch):
    # A later status (delivered/read) carries no pricing — must NOT overwrite the
    # cost recorded on 'sent'. So cost fields are simply absent from the upsert.
    captured: dict = {}
    monkeypatch.setattr(d, "_client", lambda: _FakeClient(captured))
    d.record_wa_message_cost("wamid-2", "919999999999", "delivered")
    row = captured["row"]
    assert row["status"] == "delivered"
    assert "est_cost_inr" not in row
    assert "category" not in row


@pytest.mark.unit
def test_record_empty_wamid_is_noop(monkeypatch):
    def _boom():
        raise AssertionError("_client must not be called for empty wamid")
    monkeypatch.setattr(d, "_client", _boom)
    d.record_wa_message_cost("", "919999999999", "sent")  # returns early, no DB call
