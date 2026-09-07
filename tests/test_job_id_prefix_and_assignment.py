"""A counter job carries its own store's prefix, and a web job gets a store.

Two faults found by the acceptance run, 2026-09-06, both on the same theme: a
job id and a fulfilling store are how a person and a puller respectively say
*which shop this belongs to*, and both had quietly stopped saying it.

**The prefix.** `OSKY` was adopted for web jobs on 2026-08-13. Six days later
commit 6ba2ace — "feat(store_puller): trigger job pickup via Supabase Realtime"
— hardcoded it into `_next_job_id` as well, so the counter started issuing web
ids. No counter job existed between 08-13 and 09-03, so the drift sat unseen
until three jobs this week. A counter job belongs to the store that made it:
`OSP` at Oxygen, `PRINTK` at Nattika.

**The store.** `assigned_store_id` was written for a WhatsApp job in exactly one
place: the routing block in `update_job_paid()`, behind
`MULTISTORE_ROUTING_ENABLED`. That flag has never been on — the block records
every decision it takes and `routing_decisions` is empty — so a paid WhatsApp
job never got a store, and `store_puller.fetch_assigned_paid()` filters on
`assigned_store_id = <this store>`, which NULL never matches. 104 jobs arrived
on that path; none ever auto-printed.
"""

import sqlite3
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_STUBS = [
    "gspread", "google", "google.auth", "google.auth.transport",
    "google.auth.transport.requests", "google.oauth2", "google.oauth2.service_account",
    "websockets", "requests", "pysnmp", "pysnmp.hlapi",
    "watchdog", "watchdog.observers", "watchdog.events", "razorpay", "dotenv",
]
for _mod in _STUBS:
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)
sys.modules["dotenv"].load_dotenv = lambda *a, **k: None  # type: ignore

import pytest

import print_server
import db_cloud

TODAY = "20260906"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE jobs (job_id TEXT PRIMARY KEY)")
    return c


# ── the counter's prefix is its own store ────────────────────────────────────

@pytest.mark.parametrize("store_id,expected", [
    ("OSP", "OSP-20260906-0001"),
    ("PRINTK", "PRINTK-20260906-0001"),
    ("PRIOFF", "PRIOFF-20260906-0001"),
])
def test_a_counter_job_carries_its_own_stores_prefix(conn, monkeypatch, store_id, expected):
    monkeypatch.setattr(print_server, "_counter_job_prefix", lambda: store_id)
    assert print_server._next_job_id(conn, TODAY) == expected


def test_the_prefix_comes_from_store_config(monkeypatch):
    monkeypatch.setattr(print_server, "get_store_config",
                        lambda: types.SimpleNamespace(store_id="printk"))
    assert print_server._counter_job_prefix() == "PRINTK"


def test_an_unreadable_store_config_still_issues_a_job(monkeypatch):
    """A counter sale must never fail because a config file is unreadable."""
    def boom():
        raise RuntimeError("config gone")
    monkeypatch.setattr(print_server, "get_store_config", boom)
    assert print_server._counter_job_prefix() == "OSP"


# ── the daily sequence spans every prefix ────────────────────────────────────

def test_the_sequence_does_not_restart_when_the_prefix_changes(conn, monkeypatch):
    """The day's numbering is shared, so a corrected prefix must not reissue
    a number already handed to a customer under the old one."""
    monkeypatch.setattr(print_server, "_counter_job_prefix", lambda: "OSP")
    conn.execute("INSERT INTO jobs VALUES ('OSKY-20260906-0009')")
    assert print_server._next_job_id(conn, TODAY) == "OSP-20260906-0010"


def test_the_highest_number_wins_not_the_highest_string(conn, monkeypatch):
    """Ordering by job_id would compare the whole string, so 'OSP-…-0002' sorts
    above 'OSKY-…-0009' on the P alone and the day reissues 0003."""
    monkeypatch.setattr(print_server, "_counter_job_prefix", lambda: "OSP")
    conn.execute("INSERT INTO jobs VALUES ('OSKY-20260906-0009')")
    conn.execute("INSERT INTO jobs VALUES ('OSP-20260906-0002')")
    assert print_server._next_job_id(conn, TODAY) == "OSP-20260906-0010"


def test_a_cloud_job_in_the_same_database_does_not_break_the_counter(conn, monkeypatch):
    """`LIKE '<prefix>-<date>-%'` also matched a cloud id, and the old code then
    ran int('8c61') and raised — taking counter job creation down with it."""
    monkeypatch.setattr(print_server, "_counter_job_prefix", lambda: "OSP")
    conn.execute("INSERT INTO jobs VALUES ('OSKY-20260906-bcac-8c61')")
    conn.execute("INSERT INTO jobs VALUES ('OSKY-20260905-2033-1326-e8974b')")
    assert print_server._next_job_id(conn, TODAY) == "OSP-20260906-0001"


def test_yesterdays_numbers_do_not_carry_over(conn, monkeypatch):
    monkeypatch.setattr(print_server, "_counter_job_prefix", lambda: "OSP")
    conn.execute("INSERT INTO jobs VALUES ('OSP-20260905-0042')")
    assert print_server._next_job_id(conn, TODAY) == "OSP-20260906-0001"


# ── a web job is given a store at creation ───────────────────────────────────

@pytest.fixture
def mock_client():
    with patch("db_cloud._client") as mc:
        yield mc.return_value


def test_a_whatsapp_job_is_assigned_a_store_when_it_arrives(mock_client):
    """Without this the job is invisible to every puller for its whole life:
    fetch_assigned_paid() filters assigned_store_id = <store>, NULL matches
    nothing, and the customer's payment buys a job that cannot be collected.
    """
    db_cloud.insert_job_from_webhook("OSKY-20260906-abcd-1234", "919000000000",
                                     "notes.pdf", "https://example/notes.pdf")
    payload = mock_client.table.return_value.upsert.call_args[0][0]
    assert payload["assigned_store_id"] == db_cloud.DEFAULT_FULFILLING_STORE
    assert payload["assigned_store_id"], "an empty store is as unpullable as NULL"


def test_the_default_store_is_a_real_store():
    """OSP is where a WhatsApp job prints unless someone moves it. A typo here
    assigns every job to a store no puller answers for."""
    assert db_cloud.DEFAULT_FULFILLING_STORE == "OSP"


def test_the_webhook_still_sends_what_it_always_sent(mock_client):
    """Rule 1: the new column is additive. Everything the row carried before
    must still be written, or this fix breaks the path it is fixing."""
    db_cloud.insert_job_from_webhook("OSKY-20260906-abcd-1234", "919000000000",
                                     "notes.pdf", "https://example/notes.pdf")
    payload = mock_client.table.return_value.upsert.call_args[0][0]
    for field in ("job_id", "sender", "filename", "file_url", "status", "received_at"):
        assert field in payload, f"{field} stopped being written"
    assert payload["status"] == "Pending"
