"""Unit tests for store_dispatch (block 5 of plan v2)."""
from __future__ import annotations

import pytest

from store_dispatch import (
    ParsedReply,
    apply_store_reply,
    build_dispatch_message,
    dispatch_job,
    parse_store_reply,
)


# ---------- parse_store_reply --------------------------------------------


class TestParseStoreReply:
    @pytest.mark.parametrize("text,expected_action", [
        ("accept",                       "ACCEPT"),
        ("ACCEPT",                       "ACCEPT"),
        ("ACCEPTED P-7K2N",              "ACCEPT"),
        ("yes accept the job",           "ACCEPT"),
        ("reject",                       "REJECT"),
        ("REJECTED",                     "REJECT"),
        ("ready",                        "READY"),
        ("READY P-7K2N",                 "READY"),
        ("delivered",                    "DELIVERED"),
        ("DELIVERY done",                "DELIVERED"),
        ("DELIVER P-WXYZ",               "DELIVERED"),
        ("query about size",             "QUERY"),
        ("queries pending?",             "QUERY"),
        ("how many copies?",             "QUERY"),
    ])
    def test_recognised_verbs(self, text, expected_action):
        r = parse_store_reply(text)
        assert r is not None
        assert r.action == expected_action

    @pytest.mark.parametrize("text", [
        "",
        "   ",
        "hello there",
        "thanks",
        "ok",
        None,
        12345,
    ])
    def test_unrecognised_returns_none(self, text):
        assert parse_store_reply(text) is None

    def test_extracts_pickup_code_anywhere_in_message(self):
        r = parse_store_reply("ok ready, P-7K2N is done")
        assert r is not None
        assert r.action == "READY"
        assert r.pickup_code == "P-7K2N"

    def test_pickup_code_normalised_to_upper(self):
        r = parse_store_reply("ready p-7k2n")
        assert r is not None
        assert r.pickup_code == "P-7K2N"

    def test_no_pickup_code_keeps_none(self):
        r = parse_store_reply("ready")
        assert r is not None
        assert r.pickup_code is None

    def test_delivered_takes_priority_over_ready(self):
        r = parse_store_reply("ready and delivered P-7K2N")
        assert r is not None
        assert r.action == "DELIVERED"


# ---------- build_dispatch_message ---------------------------------------


def test_build_dispatch_message_contains_all_fields():
    msg = build_dispatch_message(
        pickup_code="P-7K2N",
        customer_first_name="Priya",
        spec_summary="12 colour A4, spiral",
        due_by="5pm today",
        file_url="https://printosky.com/files/x.pdf",
    )
    assert "P-7K2N" in msg
    assert "Priya" in msg
    assert "12 colour A4, spiral" in msg
    assert "5pm today" in msg
    assert "https://printosky.com/files/x.pdf" in msg
    assert "ACCEPT" in msg


def test_build_dispatch_message_handles_missing_first_name():
    msg = build_dispatch_message(
        pickup_code="P-7K2N",
        customer_first_name=None,
        spec_summary="x",
        due_by="now",
        file_url="https://x",
    )
    assert "Customer" in msg


# ---------- dispatch_job --------------------------------------------------


class TestDispatchJob:
    def test_returns_false_when_partner_has_no_dispatch_whatsapp(self):
        ok = dispatch_job(
            job_row={"job_id": "J1", "pickup_code": "P-7K2N"},
            partner_row={"store_id": "OSP", "dispatch_whatsapp": ""},
            file_url="https://x",
        )
        assert ok is False

    def test_calls_whatsapp_send(self, monkeypatch):
        sent = {}
        import whatsapp_notify
        def fake_send(phone, body):
            sent["phone"] = phone
            sent["body"] = body
            return True
        monkeypatch.setattr(whatsapp_notify, "_send", fake_send)

        ok = dispatch_job(
            job_row={
                "job_id": "OSP-JOB1",
                "pickup_code": "P-7K2N",
                "customer_name": "Priya Nair",
                "page_count": 12,
                "copies": 1,
                "colour": "colour",
                "size": "A4",
                "finishing": "spiral",
            },
            partner_row={
                "store_id": "OSP",
                "dispatch_whatsapp": "919495706405",
            },
            file_url="https://printosky.com/files/job1.pdf",
        )
        assert ok is True
        assert sent["phone"] == "919495706405"
        assert "P-7K2N" in sent["body"]
        assert "Priya" in sent["body"]
        assert "spiral" in sent["body"]


# ---------- apply_store_reply (with fake DB) -----------------------------


class _FakeQuery:
    def __init__(self, parent, table_name):
        self.parent = parent
        self.table_name = table_name
        self.mode = "select"
        self.payload = None
        self.filters: list[tuple[str, str, object]] = []

    def select(self, *cols):
        self.mode = "select"
        return self

    def update(self, payload):
        self.mode = "update"
        self.payload = payload
        return self

    def insert(self, payload):
        self.mode = "insert"
        self.payload = payload
        return self

    def eq(self, col, value):
        self.filters.append(("eq", col, value))
        return self

    def neq(self, col, value):
        self.filters.append(("neq", col, value))
        return self

    def order(self, col, desc=False):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        class R:
            pass
        r = R()
        rows = list(self.parent.tables.get(self.table_name, []))
        for op, col, value in self.filters:
            if op == "eq":
                rows = [row for row in rows if row.get(col) == value]
            elif op == "neq":
                rows = [row for row in rows if row.get(col) != value]
        if self.mode == "select":
            r.data = rows
        elif self.mode == "update":
            updated = []
            for row in self.parent.tables[self.table_name]:
                if all(
                    (op != "eq") or (row.get(c) == v)
                    for op, c, v in self.filters
                ) and any(op == "eq" for op, _, _ in self.filters):
                    row.update(self.payload)
                    updated.append(row)
            r.data = updated
        elif self.mode == "insert":
            self.parent.tables.setdefault(self.table_name, []).append(self.payload)
            r.data = [self.payload]
        return r


class _FakeClient:
    def __init__(self, **tables):
        self.tables = {k: list(v) for k, v in tables.items()}

    def table(self, name):
        return _FakeQuery(self, name)


def _osp_partner():
    return {"store_id": "OSP", "dispatch_whatsapp": "919495706405",
            "kyc_status": "active", "name": "Oxygen"}


def _other_partner():
    return {"store_id": "STORE2", "dispatch_whatsapp": "918888888888",
            "kyc_status": "active", "name": "Other Print"}


def _job(**overrides):
    base = {
        "job_id": "OSP-J1", "pickup_code": "P-7K2N", "status": "Paid",
        "assigned_store_id": "OSP", "store_id": "OSP",
        "sender": "919999999999",
    }
    base.update(overrides)
    return base


class TestApplyStoreReply:
    def test_unknown_sender_rejected(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job()])
        result = apply_store_reply(
            client, "917777777777",
            ParsedReply("ACCEPT", "P-7K2N", "ACCEPT P-7K2N"),
        )
        assert result.ok is False
        assert "not a known dispatch number" in result.message

    def test_accept_transitions_paid_to_accepted(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job(status="Paid")])
        result = apply_store_reply(
            client, "919495706405",
            ParsedReply("ACCEPT", "P-7K2N", "ACCEPT P-7K2N"),
        )
        assert result.ok is True
        assert result.new_status == "Accepted"
        assert client.tables["jobs"][0]["status"] == "Accepted"

    def test_ready_writes_pickup_ready_at(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job(status="Accepted")])
        result = apply_store_reply(
            client, "919495706405",
            ParsedReply("READY", "P-7K2N", "READY P-7K2N"),
        )
        assert result.ok is True
        assert result.new_status == "Ready"
        row = client.tables["jobs"][0]
        assert row["status"] == "Ready"
        assert row.get("pickup_ready_at") is not None

    def test_delivered_writes_delivered_at(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job(status="Ready")])
        result = apply_store_reply(
            client, "919495706405",
            ParsedReply("DELIVERED", "P-7K2N", "DELIVERED P-7K2N"),
        )
        assert result.ok is True
        assert result.new_status == "Delivered"
        row = client.tables["jobs"][0]
        assert row["status"] == "Delivered"
        assert row.get("delivered_at") is not None

    def test_ready_skipping_accept_is_allowed(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job(status="Paid")])
        result = apply_store_reply(
            client, "919495706405",
            ParsedReply("READY", "P-7K2N", "READY P-7K2N"),
        )
        assert result.ok is True
        assert result.new_status == "Ready"

    def test_idempotent_repeat_accept(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job(status="Accepted")])
        result = apply_store_reply(
            client, "919495706405",
            ParsedReply("ACCEPT", "P-7K2N", "ACCEPT P-7K2N"),
        )
        assert result.ok is True
        assert result.new_status == "Accepted"
        assert "idempotent" in result.message.lower()

    def test_cannot_act_on_other_stores_job(self):
        client = _FakeClient(
            partners=[_osp_partner(), _other_partner()],
            jobs=[_job(assigned_store_id="STORE2", store_id="STORE2")],
        )
        result = apply_store_reply(
            client, "919495706405",
            ParsedReply("ACCEPT", "P-7K2N", "ACCEPT P-7K2N"),
        )
        assert result.ok is False

    def test_query_does_not_change_state(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job(status="Paid")])
        result = apply_store_reply(
            client, "919495706405",
            ParsedReply("QUERY", None, "what size?"),
        )
        assert result.ok is True
        assert result.new_status is None
        assert client.tables["jobs"][0]["status"] == "Paid"

    def test_reject_returns_ok_with_rejected_sentinel(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job(status="Accepted")])
        result = apply_store_reply(
            client, "919495706405",
            ParsedReply("REJECT", "P-7K2N", "REJECT P-7K2N"),
        )
        assert result.ok is True
        assert result.new_status == "Rejected"

    def test_phone_normalised_strips_plus_and_spaces(self):
        client = _FakeClient(partners=[_osp_partner()], jobs=[_job()])
        result = apply_store_reply(
            client, "+91 94957 06405",
            ParsedReply("ACCEPT", "P-7K2N", "ACCEPT"),
        )
        assert result.ok is True
