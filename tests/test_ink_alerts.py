"""Tests for _send_ink_alerts() in printer_poller.py."""

import sqlite3
import sys
import types
import pytest

# ── Stub out heavy dependencies before import ─────────────────────────────────
for mod in ("pysnmp", "requests"):
    if mod not in sys.modules:
        sys.modules[mod] = types.ModuleType(mod)

# Stub whatsapp_notify so no real HTTP calls happen
_wn = types.ModuleType("whatsapp_notify")
_alerts_sent: list[str] = []

def _fake_send_staff_alert(msg: str) -> bool:
    _alerts_sent.append(msg)
    return True

_wn.send_staff_alert = _fake_send_staff_alert
sys.modules["whatsapp_notify"] = _wn

from printer_poller import _send_ink_alerts, init_printer_tables, INK_ALERT_PCT


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    init_printer_tables(db)
    yield db
    db.close()


@pytest.fixture(autouse=True)
def clear_alerts():
    _alerts_sent.clear()
    yield
    _alerts_sent.clear()


def _insert_prev(conn, printer, supply_index, pct):
    """Insert a historical supply reading so _send_ink_alerts sees a previous value."""
    conn.execute("""
        INSERT INTO printer_supplies
            (polled_at, printer, supply_index, description, max_capacity, current_level, pct)
        VALUES ('2026-01-01 00:00:00', ?, ?, 'Ink Test', 100, ?, ?)
    """, (printer, supply_index, int(pct), pct))
    conn.commit()


def _insert_current(conn, printer, supply_index, pct):
    """Insert the 'just-saved' current reading."""
    conn.execute("""
        INSERT INTO printer_supplies
            (polled_at, printer, supply_index, description, max_capacity, current_level, pct)
        VALUES ('2026-01-01 00:05:00', ?, ?, 'Ink Test', 100, ?, ?)
    """, (printer, supply_index, int(pct), pct))
    conn.commit()


# ── Tests: no alert when level is healthy ────────────────────────────────────

def test_no_alert_when_healthy(conn):
    _insert_prev(conn, "epson", 1, 80.0)
    _insert_current(conn, "epson", 1, 75.0)
    supplies = [{"supply_index": 1, "description": "Ink Black 1 (K)", "pct": 75.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert _alerts_sent == []


def test_no_alert_when_already_below_threshold(conn):
    """If it was already below 10% last poll, don't re-alert."""
    _insert_prev(conn, "epson", 3, 5.0)
    _insert_current(conn, "epson", 3, 3.0)
    supplies = [{"supply_index": 3, "description": "Ink Cyan (C)", "pct": 3.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert _alerts_sent == []


def test_no_alert_when_already_empty(conn):
    """If it was already 0% last poll, don't re-alert."""
    _insert_prev(conn, "epson", 2, 0.0)
    _insert_current(conn, "epson", 2, 0.0)
    supplies = [{"supply_index": 2, "description": "Ink Black 2 (K)", "pct": 0.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert _alerts_sent == []


# ── Tests: LOW alert fires on threshold crossing ──────────────────────────────

def test_low_alert_fires_on_crossing(conn):
    _insert_prev(conn, "epson", 3, 12.0)   # was above 10%
    _insert_current(conn, "epson", 3, 8.0)  # now below 10%
    supplies = [{"supply_index": 3, "description": "Ink Cyan (C)", "pct": 8.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert len(_alerts_sent) == 1
    assert "LOW" in _alerts_sent[0]
    assert "Ink Cyan" in _alerts_sent[0]
    assert "8.0%" in _alerts_sent[0]


def test_low_alert_at_exact_threshold(conn):
    _insert_prev(conn, "epson", 4, 11.0)
    _insert_current(conn, "epson", 4, 10.0)
    supplies = [{"supply_index": 4, "description": "Ink Magenta (M)", "pct": 10.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert len(_alerts_sent) == 1
    assert "LOW" in _alerts_sent[0]


def test_no_low_alert_just_above_threshold(conn):
    _insert_prev(conn, "epson", 5, 12.0)
    _insert_current(conn, "epson", 5, 11.0)
    supplies = [{"supply_index": 5, "description": "Ink Yellow (Y)", "pct": 11.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert _alerts_sent == []


# ── Tests: EMPTY alert fires when hitting 0% ─────────────────────────────────

def test_empty_alert_fires(conn):
    _insert_prev(conn, "epson", 2, 2.0)
    _insert_current(conn, "epson", 2, 0.0)
    supplies = [{"supply_index": 2, "description": "Ink Black 2 (K)", "pct": 0.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert len(_alerts_sent) == 1
    assert "EMPTY" in _alerts_sent[0]
    assert "replace immediately" in _alerts_sent[0]


def test_empty_alert_not_low_alert(conn):
    """0% should produce EMPTY alert, not LOW alert."""
    _insert_prev(conn, "epson", 2, 2.0)
    _insert_current(conn, "epson", 2, 0.0)
    supplies = [{"supply_index": 2, "description": "Ink Black 2 (K)", "pct": 0.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert "EMPTY" in _alerts_sent[0]
    assert "LOW" not in _alerts_sent[0]


# ── Tests: first reading (no previous data) ───────────────────────────────────

def test_low_alert_on_first_reading_if_already_low(conn):
    """If no previous reading and current is already low, alert immediately."""
    _insert_current(conn, "epson", 3, 6.0)
    supplies = [{"supply_index": 3, "description": "Ink Cyan (C)", "pct": 6.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert len(_alerts_sent) == 1
    assert "LOW" in _alerts_sent[0]


def test_empty_alert_on_first_reading_if_empty(conn):
    _insert_current(conn, "epson", 2, 0.0)
    supplies = [{"supply_index": 2, "description": "Ink Black 2 (K)", "pct": 0.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert len(_alerts_sent) == 1
    assert "EMPTY" in _alerts_sent[0]


def test_no_alert_on_first_reading_if_healthy(conn):
    _insert_current(conn, "epson", 1, 80.0)
    supplies = [{"supply_index": 1, "description": "Ink Black 1 (K)", "pct": 80.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert _alerts_sent == []


# ── Tests: multiple supplies in one call ─────────────────────────────────────

def test_multiple_supplies_one_alert_message(conn):
    """Two supplies crossing threshold → one combined WhatsApp message."""
    _insert_prev(conn, "epson", 2, 2.0)
    _insert_current(conn, "epson", 2, 0.0)
    _insert_prev(conn, "epson", 3, 12.0)
    _insert_current(conn, "epson", 3, 8.0)
    supplies = [
        {"supply_index": 2, "description": "Ink Black 2 (K)", "pct": 0.0},
        {"supply_index": 3, "description": "Ink Cyan (C)",    "pct": 8.0},
    ]
    _send_ink_alerts("epson", supplies, conn)
    assert len(_alerts_sent) == 1
    assert "EMPTY" in _alerts_sent[0]
    assert "LOW" in _alerts_sent[0]


def test_only_one_supply_crossing_sends_alert(conn):
    _insert_prev(conn, "epson", 1, 80.0)
    _insert_current(conn, "epson", 1, 75.0)
    _insert_prev(conn, "epson", 3, 12.0)
    _insert_current(conn, "epson", 3, 9.0)
    supplies = [
        {"supply_index": 1, "description": "Ink Black 1 (K)", "pct": 75.0},
        {"supply_index": 3, "description": "Ink Cyan (C)",    "pct": 9.0},
    ]
    _send_ink_alerts("epson", supplies, conn)
    assert len(_alerts_sent) == 1
    assert "Cyan" in _alerts_sent[0]
    assert "Black 1" not in _alerts_sent[0]


# ── Tests: skips supplies with pct=None ──────────────────────────────────────

def test_no_alert_when_pct_is_none(conn):
    supplies = [{"supply_index": 1, "description": "Ink Black 1 (K)", "pct": None}]
    _send_ink_alerts("epson", supplies, conn)
    assert _alerts_sent == []


# ── Tests: printer name in message ───────────────────────────────────────────

def test_epson_printer_name_in_alert(conn):
    _insert_current(conn, "epson", 3, 5.0)
    supplies = [{"supply_index": 3, "description": "Ink Cyan (C)", "pct": 5.0}]
    _send_ink_alerts("epson", supplies, conn)
    assert "Epson WF-C21000" in _alerts_sent[0]


def test_konica_printer_name_in_alert(conn):
    _insert_current(conn, "konica", 1, 5.0)
    supplies = [{"supply_index": 1, "description": "Toner Black", "pct": 5.0}]
    _send_ink_alerts("konica", supplies, conn)
    assert "Konica Bizhub" in _alerts_sent[0]
